"""Batch propose + commit-time IRI reservation (SPEC-bfo-agent-speed.md
change 5).

No network: the anthropic client is mocked. Covers (1) request-building
parity with the live prompt blocks, (2) prepare_job_proposals persisting
parsed proposals per-claim with correct counts/meta, (3) _feed_one_core
consuming a stored proposal (consume-once) with live fallback, (4) gate
resamples staying live even when the initial proposal was precomputed,
(5) stable_iri.canonical_key, and (6) OntologyManager._reserve_iris reusing
an already-committed IRI with relations remapped in lockstep.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import batch_propose
from app import config
from app import jobs as jobs_store
from app import llm_proposer
from app import orchestrator
from app import stable_iri
from app.coherence_gate import GateOutcome, GateResult, GateRun, GateTier
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"

PROPOSAL_JSON = (
    '{"entities": [], "relations": [], "open_questions": [],'
    ' "rationale_summary": "from-batch"}'
)


# ------------------------------------------------------------ fakes
class SnapshotManager:
    """Just enough OntologyManager surface for batch_propose."""

    def __init__(self, classes=None, individuals=None):
        self._classes = classes or []
        self._individuals = individuals or []

    def list_working_classes(self):
        return list(self._classes)

    def list_individuals(self):
        return list(self._individuals)


class FakeManager:
    """Just enough OntologyManager surface for _feed_one_core."""

    def __init__(self, tmp_path):
        self.working_path = tmp_path / "working.owl"
        self.working_path.write_text("<placeholder/>")
        self.commits_since_full_verify = 0
        self.last_commit_incoherence = None
        self.commit_calls = []

    def summary_for_proposer(self, utterance=None, **kw):
        return {"working_classes": [], "known_individuals": []}

    def commit_proposal(self, proposal, verify=True, faithful=False,
                        exclude_axioms=None):
        self.commit_calls.append({"verify": verify})
        return []

    def stats(self):
        return {"num_classes": 0, "num_individuals": 0}


class FakeProposer:
    def __init__(self):
        self.calls = 0
        self.fail = False

    def propose(self, utterance, session_id, working_classes,
                known_individuals, relevant_classes=None):
        self.calls += 1
        if self.fail:
            raise AssertionError("live propose must not be called here")
        return Proposal(session_id=session_id, utterance=utterance)


class FakeBatches:
    """messages.batches: create -> retrieve (2 polls) -> results."""

    def __init__(self, error_claim_ids=()):
        self.error_ids = set(error_claim_ids)
        self.created = None
        self.retrieve_calls = 0

    def create(self, requests):
        self.created = requests
        return SimpleNamespace(id="batch_1", processing_status="in_progress")

    def retrieve(self, batch_id):
        assert batch_id == "batch_1"
        self.retrieve_calls += 1
        status = "ended" if self.retrieve_calls >= 2 else "in_progress"
        return SimpleNamespace(
            processing_status=status,
            request_counts=SimpleNamespace(processing=0, succeeded=2,
                                           errored=1),
        )

    def results(self, batch_id):
        # deliberately reversed: results arrive in any order
        for req in reversed(self.created):
            cid = int(req["custom_id"].rsplit(":", 1)[1])
            if cid in self.error_ids:
                yield SimpleNamespace(
                    custom_id=req["custom_id"],
                    result=SimpleNamespace(
                        type="errored",
                        error=SimpleNamespace(type="invalid_request"),
                    ),
                )
            else:
                msg = SimpleNamespace(
                    content=[SimpleNamespace(text=PROPOSAL_JSON)],
                    usage=SimpleNamespace(
                        input_tokens=10, output_tokens=5,
                        cache_read_input_tokens=2,
                        cache_creation_input_tokens=1,
                    ),
                )
                yield SimpleNamespace(
                    custom_id=req["custom_id"],
                    result=SimpleNamespace(type="succeeded", message=msg),
                )


class FakeClient:
    def __init__(self, batches):
        self.messages = SimpleNamespace(batches=batches)


def _gate_result(outcome, reason=""):
    return GateResult(outcome=outcome, tier=GateTier.LINT, reason=reason)


def _accepting_gate(proposal, manager, **kw):
    return GateRun(outcome=GateOutcome.ACCEPT, proposal=proposal,
                   result=_gate_result(GateOutcome.ACCEPT), events=[],
                   attempts=0)


# ------------------------------------------------------------ fixtures
@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    d = tmp_path / "jobs"
    d.mkdir()
    monkeypatch.setattr(jobs_store, "JOBS_DIR", d)
    return d


def _make_job():
    job = jobs_store.create_job("batch-test")
    jobs_store.append_claims(job["job_id"], [
        {"claim": f"claim {i}", "confidence": "high"} for i in range(3)
    ])
    return jobs_store.load_job(job["job_id"])


@pytest.fixture
def feed_env(tmp_path, monkeypatch, jobs_dir):
    """Wire _feed_one_core to fakes: gate ON (accepting stub), batch propose
    ON. Returns (mgr, proposer, events, job) with a 3-claim approved job."""
    mgr = FakeManager(tmp_path)
    proposer = FakeProposer()
    events = []

    monkeypatch.setattr(orchestrator, "_get_manager", lambda: mgr)
    monkeypatch.setattr(orchestrator, "_get_proposer", lambda: proposer)
    monkeypatch.setattr(orchestrator, "_active_is_finalized", lambda: False)
    monkeypatch.setattr(orchestrator, "_active_fidelity", lambda: "curated")
    monkeypatch.setattr(orchestrator, "_apply_scaffolding",
                        lambda p, m, s: [])
    monkeypatch.setattr(orchestrator, "_budget_and_kext_notes",
                        lambda p, m: [])
    monkeypatch.setattr(orchestrator, "git_commit_working_ontology",
                        lambda *a, **k: None)
    monkeypatch.setattr(
        orchestrator, "log_event",
        lambda session_id, kind, payload=None: events.append((kind, payload)),
    )
    monkeypatch.setattr(orchestrator, "log_gate_events", lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.config, "ENABLE_COHERENCE_GATE", True)
    monkeypatch.setattr(orchestrator.config, "VERIFY_EVERY_COMMIT", True)
    monkeypatch.setattr(orchestrator.config, "BATCH_PROPOSE_ENABLED", True)
    monkeypatch.setattr(orchestrator.gate_mod, "run_with_policy",
                        _accepting_gate)

    job = _make_job()
    jobs_store.set_job_status(job["job_id"], "feeding")
    return mgr, proposer, events, jobs_store.load_job(job["job_id"])


# ------------------------------------------------- request construction
def test_build_request_matches_live_prompt_blocks():
    classes = [{"iri": f"http://x#Klass{i}", "label": f"Klass{i}",
                "parents": []} for i in range(45)]
    indivs = [{"iri": "http://x#ind1", "label": "ind1", "types": ["Klass1"]}]
    ctx = batch_propose.snapshot_context(SnapshotManager(classes, indivs))
    assert len(ctx["working_classes"]) == 40
    assert len(ctx["all_classes"]) == 45

    claim = {"id": 7, "claim": "an utterance mentioning Klass44 explicitly"}
    req = batch_propose.build_request(claim, ctx, "model-x", "5m", "job_abc")

    system, user = llm_proposer.build_prompt_blocks(
        utterance=claim["claim"],
        working_classes=ctx["working_classes"],
        known_individuals=ctx["known_individuals"],
        relevant_classes=batch_propose._relevant_for(claim, ctx),
        ttl="5m",
    )
    assert req["custom_id"] == "job_abc:7"
    assert req["params"]["model"] == "model-x"
    assert req["params"]["max_tokens"] == 4000
    assert req["params"]["system"] == system  # byte-identical blocks
    assert req["params"]["messages"] == [{"role": "user", "content": user}]
    # the tail (non-head) class lexically matching the claim is offered
    # as a reuse candidate, exactly like summary_for_proposer would
    rel = batch_propose._relevant_for(claim, ctx)
    assert [c["label"] for c in rel] == ["Klass44"]


def test_live_propose_uses_the_shared_blocks(monkeypatch):
    """The live path calls the same helper, so batch entries share its
    prompt-cache prefix byte-for-byte."""
    calls = []

    class _FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            block = SimpleNamespace(text=PROPOSAL_JSON)
            return SimpleNamespace(content=[block], usage=None)

    class _FakeAnthropic:
        def __init__(self, api_key=None, timeout=None):
            self.messages = _FakeMessages()

    monkeypatch.setattr(llm_proposer, "Anthropic", _FakeAnthropic)
    p = llm_proposer.LLMProposer(model="m", api_key="k")
    wc = [{"iri": "http://x#A", "label": "A", "parents": []}]
    rel = [{"iri": "http://x#B", "label": "B", "parents": []}]
    prop = p.propose("utt", "sess", wc, [], rel)

    system, user = llm_proposer.build_prompt_blocks("utt", wc, [], rel, p.ttl)
    assert calls[0]["system"] == system
    assert calls[0]["messages"] == [{"role": "user", "content": user}]
    assert isinstance(prop, Proposal)
    assert prop.session_id == "sess"
    assert prop.rationale_summary == "from-batch"


# ------------------------------------------------- prepare_job_proposals
def test_prepare_job_proposals_persists_counts_and_meta(jobs_dir, monkeypatch):
    job = _make_job()
    monkeypatch.setattr(batch_propose, "_active_manager",
                        lambda: SnapshotManager())
    out = batch_propose.prepare_job_proposals(
        job["job_id"], poll_secs=0,
        client=FakeClient(FakeBatches(error_claim_ids={1})),
    )
    assert out["submitted"] == 3
    assert out["succeeded"] == 2
    assert out["errored"] == 1
    assert out["batch_id"] == "batch_1"
    assert out["usage"]["input"] == 20
    assert out["usage"]["output"] == 10
    assert out["usage"]["cache_read"] == 4
    assert out["usage"]["cache_write"] == 2

    saved = jobs_store.load_job(job["job_id"])
    by_id = {c["id"]: c for c in saved["claims"]}
    assert by_id[0]["proposal"]["rationale_summary"] == "from-batch"
    assert by_id[0]["proposal"]["utterance"] == "claim 0"
    assert by_id[0]["proposal_source"] == "batch"
    assert by_id[2]["proposal"] is not None
    assert "proposal" not in by_id[1]  # errored entry left for live fallback

    bp = saved["meta"]["batch_propose"]
    assert bp["batch_id"] == "batch_1"
    assert bp["status"] == "ended"
    assert bp["submitted"] == 3
    assert bp["succeeded"] == 2
    assert bp["errored"] == 1
    assert bp["started_at"] and bp["ended_at"]


def test_prepare_skips_claims_with_stored_proposal(jobs_dir, monkeypatch):
    job = _make_job()
    jobs_store.set_claim_proposals(job["job_id"], {
        0: {"proposal": {"x": 1}, "proposal_source": "batch"},
    })
    monkeypatch.setattr(batch_propose, "_active_manager",
                        lambda: SnapshotManager())
    batches = FakeBatches()
    batch_propose.prepare_job_proposals(
        job["job_id"], poll_secs=0, client=FakeClient(batches))
    ids = sorted(int(r["custom_id"].rsplit(":", 1)[1])
                 for r in batches.created)
    assert ids == [1, 2]  # claim 0 already prepared


# ------------------------------------------------- feed consumption
def test_feed_consumes_stored_proposal_and_clears_it(feed_env):
    mgr, proposer, events, job = feed_env
    proposer.fail = True  # any live propose call is a test failure
    stored = Proposal(session_id="other_session", utterance="claim 0",
                      rationale_summary="from-batch")
    jobs_store.set_claim_proposals(job["job_id"], {
        0: {"proposal": stored.model_dump(), "proposal_source": "batch"},
    })

    out = orchestrator._feed_one_core(job["job_id"], auto_accept=True)
    assert out["committed"] is True
    # determinism: the stored proposal_id is reused, session_id rebound
    assert out["proposal"]["proposal_id"] == stored.proposal_id
    assert out["proposal"]["session_id"] == job["session_id"]

    saved = jobs_store.load_job(job["job_id"])
    c0 = next(c for c in saved["claims"] if c["id"] == 0)
    assert c0["status"] == "committed"
    assert c0["proposal_id"] == stored.proposal_id
    assert "proposal" not in c0  # consume-once: cleared in the same write

    propose_events = [p for k, p in events if k == "propose"]
    assert propose_events
    assert propose_events[0].get("proposal_source") == "batch"


def test_feed_falls_back_to_live_propose_without_stored(feed_env):
    mgr, proposer, events, job = feed_env
    out = orchestrator._feed_one_core(job["job_id"], auto_accept=True)
    assert proposer.calls == 1
    assert out["committed"] is True
    propose_events = [p for k, p in events if k == "propose"]
    assert "proposal_source" not in propose_events[0]


def test_feed_ignores_stored_proposal_when_flag_off(feed_env, monkeypatch):
    mgr, proposer, events, job = feed_env
    monkeypatch.setattr(orchestrator.config, "BATCH_PROPOSE_ENABLED", False)
    stored = Proposal(session_id="s", utterance="claim 0")
    jobs_store.set_claim_proposals(job["job_id"], {
        0: {"proposal": stored.model_dump(), "proposal_source": "batch"},
    })
    out = orchestrator._feed_one_core(job["job_id"], auto_accept=True)
    assert proposer.calls == 1  # live propose despite the stored proposal
    assert out["proposal"]["proposal_id"] != stored.proposal_id


def test_resample_is_live_even_with_stored_proposal(feed_env, monkeypatch):
    mgr, proposer, events, job = feed_env
    monkeypatch.setattr(orchestrator.config, "GATE_POLICY", "reject_resample")

    def rejecting_gate(proposal, manager, policy=None, resample_fn=None, **kw):
        # gate fires -> policy resamples once (through the LIVE proposer),
        # still rejects.
        assert resample_fn is not None
        resampled = resample_fn(
            proposal, _gate_result(GateOutcome.REJECT, "straddle"), False)
        assert resampled is not None
        return GateRun(outcome=GateOutcome.REJECT, proposal=resampled,
                       result=_gate_result(GateOutcome.REJECT, "straddle"),
                       events=[], attempts=1)

    monkeypatch.setattr(orchestrator.gate_mod, "run_with_policy",
                        rejecting_gate)
    stored = Proposal(session_id="s", utterance="claim 0")
    jobs_store.set_claim_proposals(job["job_id"], {
        0: {"proposal": stored.model_dump(), "proposal_source": "batch"},
    })

    out = orchestrator._feed_one_core(job["job_id"], auto_accept=True)
    assert proposer.calls == 1  # the resample went through live propose
    assert out["committed"] is False

    saved = jobs_store.load_job(job["job_id"])
    c0 = next(c for c in saved["claims"] if c["id"] == 0)
    assert c0["status"] == "inconsistent"
    assert "proposal" not in c0  # consumed even on rejection


# ------------------------------------------------- canonical_key
def test_canonical_key():
    ck = stable_iri.canonical_key
    assert ck("Empathy Deficits") == "empathy deficit"
    assert ck("empathy deficit") == "empathy deficit"
    assert ck("  Empathy--Deficits! ") == "empathy deficit"
    # 'ss' guard: never singularized down to a nonsense stem
    assert ck("Consciousness") == "consciousness"
    assert ck("Stress") == "stress"
    # naive rule: 'Stresses' strips ONE trailing 's' only
    assert ck("Stresses") != ck("Stress")
    assert ck("") == ""


# ------------------------------------------------- IRI reservation
def _make_manager(tmp_path):
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    from app.ontology_manager import OntologyManager
    return OntologyManager(bfo_path=BFO_PATH,
                           working_path=tmp_path / "working.owl")


def _cls(label, name, bfo_type="BFO_0000016", bfo_label="disposition"):
    return Entity(label=label, iri_suggestion=f"working:{name}", kind="class",
                  bfo_type=bfo_type, bfo_label=bfo_label, rationale="t")


def test_reserve_iris_reuses_committed_class(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "IRI_RESERVATION_ENABLED", True)
    mgr = _make_manager(tmp_path)

    first = Proposal(session_id="t", utterance="u", entities=[
        _cls("Empathy Deficit", "EmpathyDeficit"),
    ])
    mgr.commit_proposal(first, verify=False)
    assert "class|empathy deficit" in mgr._iri_reservations

    second = Proposal(
        session_id="t", utterance="u2",
        entities=[
            _cls("empathy deficits", "EmpathyDeficits"),
            Entity(label="Alice", iri_suggestion="working:Alice",
                   kind="individual", bfo_type="BFO_0000040",
                   bfo_label="material entity", rationale="t"),
        ],
        relations=[
            Relation(s="working:Alice", p="rdf:type",
                     o="working:EmpathyDeficits", rationale="t"),
        ],
    )
    reserved = mgr._reserve_iris(second)
    ent = next(e for e in reserved.entities if e.label == "empathy deficits")
    assert ent.is_new is False
    assert ent.existing_iri.endswith("EmpathyDeficit")
    assert ent.iri_suggestion == "working:EmpathyDeficit"
    # relation endpoints remapped in lockstep
    assert reserved.relations[0].o == "working:EmpathyDeficit"
    assert reserved.relations[0].s == "working:Alice"  # untouched
    # pure: the input proposal is untouched
    assert second.entities[0].is_new is True
    assert second.relations[0].o == "working:EmpathyDeficits"

    # applying the original proposal goes through the same rewrite: no
    # duplicate class is created
    mgr.commit_proposal(second, verify=False)
    names = [c.name for c in mgr.working.classes()
             if c.name.startswith("EmpathyDeficit")]
    assert names == ["EmpathyDeficit"]


def test_reserve_iris_off_by_default(tmp_path, monkeypatch):
    """With the flag off, apply_proposal never rewrites. Pin the flag rather
    than asserting the ambient default: a deployed box's .env may correctly
    have IRI_RESERVATION_ENABLED flipped on while running the suite."""
    monkeypatch.setattr(config, "IRI_RESERVATION_ENABLED", False)
    mgr = _make_manager(tmp_path)
    mgr.commit_proposal(
        Proposal(session_id="t", utterance="u",
                 entities=[_cls("Empathy Deficit", "EmpathyDeficit")]),
        verify=False,
    )
    mgr.commit_proposal(
        Proposal(session_id="t", utterance="u2",
                 entities=[_cls("empathy deficits", "EmpathyDeficits")]),
        verify=False,
    )
    names = sorted(c.name for c in mgr.working.classes()
                   if c.name.startswith("EmpathyDeficit"))
    assert names == ["EmpathyDeficit", "EmpathyDeficits"]
