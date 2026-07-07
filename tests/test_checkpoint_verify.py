"""Checkpointed full verification (SPEC-bfo-agent-speed.md change 6).

With VERIFY_EVERY_COMMIT off, per-claim commits skip the full-graph
post-commit reasoner pass; a full HermiT certificate runs every
FULL_VERIFY_EVERY_K commits and mandatorily at job completion. These tests
pin the bookkeeping (feed_state), the K-fire, the curated pause with a
suspect window, the faithful ledger-and-continue policy, the mandatory
final pass, the finalize guard, and a real verify_full smoke run.
"""
from pathlib import Path

import pytest

from app import incoherence_ledger as ledger_mod
from app import jobs as jobs_store
from app import orchestrator
from app.registry import FinalizeVerificationError, OntologyRegistry
from app.schema import Proposal

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"

OK_REPORT = {"ok": True, "unsat_classes": [], "detail": "",
             "duration_ms": 1.0, "classes": 0, "individuals": 0}
BAD_REPORT = {"ok": False, "unsat_classes": ["working#Broken"],
              "detail": "unsatisfiable classes: working#Broken",
              "duration_ms": 1.0, "classes": 0, "individuals": 0}


class FakeManager:
    """Just enough OntologyManager surface for _feed_one_core."""

    def __init__(self, tmp_path, verify_report=None):
        self.working_path = tmp_path / "working.owl"
        self.working_path.write_text("<placeholder/>")
        self.commits_since_full_verify = 0
        self.last_commit_incoherence = None
        self.commit_calls: list[dict] = []
        self.verify_full_calls: list = []
        self._verify_report = dict(verify_report or OK_REPORT)

    def summary_for_proposer(self, utterance=None, **kw):
        return {"working_classes": [], "known_individuals": []}

    def check_consistency_dry_run(self, proposal):
        return True, ""

    def commit_proposal(self, proposal, verify=True, faithful=False,
                        exclude_axioms=None):
        self.commit_calls.append({"verify": verify, "faithful": faithful})
        if not verify:
            self.commits_since_full_verify += 1
        return []

    def verify_full(self, exclude_axioms=None):
        self.verify_full_calls.append(exclude_axioms)
        self.commits_since_full_verify = 0
        return dict(self._verify_report)

    def stats(self):
        return {"num_classes": 0, "num_individuals": 0}


class FakeProposer:
    def propose(self, utterance, session_id, working_classes,
                known_individuals, relevant_classes=None):
        return Proposal(session_id=session_id, utterance=utterance)


@pytest.fixture
def feed_env(tmp_path, monkeypatch):
    """Wire _feed_one_core to a fake manager/proposer and a tmp jobs dir.

    Returns (mgr, events, job) with a 3-claim approved job in "feeding".
    """
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(jobs_store, "JOBS_DIR", jobs_dir)

    mgr = FakeManager(tmp_path)
    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(orchestrator, "_get_manager", lambda: mgr)
    monkeypatch.setattr(orchestrator, "_get_proposer", lambda: FakeProposer())
    monkeypatch.setattr(orchestrator, "_active_is_finalized", lambda: False)
    monkeypatch.setattr(orchestrator, "_active_fidelity", lambda: "curated")
    monkeypatch.setattr(orchestrator, "_apply_scaffolding",
                        lambda p, m, s: [])
    monkeypatch.setattr(orchestrator, "_budget_and_kext_notes",
                        lambda p, m: [])
    monkeypatch.setattr(orchestrator, "git_commit_working_ontology",
                        lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.gate_mod, "scaffolding_directives",
                        lambda p, m: [])
    monkeypatch.setattr(
        orchestrator, "log_event",
        lambda session_id, kind, payload=None: events.append((kind, payload)),
    )
    monkeypatch.setattr(orchestrator.config, "ENABLE_COHERENCE_GATE", False)
    monkeypatch.setattr(orchestrator.config, "VERIFY_EVERY_COMMIT", False)
    monkeypatch.setattr(orchestrator.config, "FULL_VERIFY_EVERY_K", 2)
    monkeypatch.setattr(orchestrator.config, "CHECKPOINT_FAIL_MARK_REVIEW",
                        False)
    monkeypatch.setattr(orchestrator.config, "FINALIZE_REQUIRES_FULL_VERIFY",
                        True)

    job = jobs_store.create_job("checkpoint-test")
    jobs_store.append_claims(job["job_id"], [
        {"claim": f"claim {i}", "confidence": "high"} for i in range(3)
    ])
    jobs_store.set_job_status(job["job_id"], "feeding")
    return mgr, events, jobs_store.load_job(job["job_id"])


def _events_of(events, kind):
    return [p for k, p in events if k == kind]


# ------------------------------------------------------------ feed_state
def test_feed_state_defaults_on_new_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_store, "JOBS_DIR", tmp_path)
    job = jobs_store.create_job("t")
    assert job["feed_state"] == {
        "commits_since_checkpoint": 0,
        "last_checkpoint_at": None,
        "last_checkpoint_ok": None,
        "last_verified_claim_id": None,
        "last_saved_claim_id": None,
    }
    # Pre-existing job files without the field read as the defaults.
    legacy = {"job_id": "job_old", "claims": []}
    assert jobs_store.get_feed_state(legacy)["commits_since_checkpoint"] == 0
    assert jobs_store.get_feed_state(legacy)["last_verified_claim_id"] is None
    # bump: increments and absolute updates both persist.
    fs = jobs_store.bump_feed_state(
        job["job_id"], increment={"commits_since_checkpoint": 1})
    assert fs["commits_since_checkpoint"] == 1
    fs = jobs_store.bump_feed_state(
        job["job_id"], commits_since_checkpoint=0, last_checkpoint_ok=True)
    assert fs["commits_since_checkpoint"] == 0
    assert jobs_store.get_feed_state(
        jobs_store.load_job(job["job_id"]))["last_checkpoint_ok"] is True


# ------------------------------------------------------- counter + K-fire
def test_counter_increments_and_checkpoint_fires_at_k(feed_env):
    mgr, events, job = feed_env
    job_id = job["job_id"]

    res = orchestrator._feed_one_core(job_id)
    assert res["committed"] is True
    # Hot path forced verify=False (VERIFY_EVERY_COMMIT off).
    assert mgr.commit_calls[-1]["verify"] is False
    fs = jobs_store.get_feed_state(jobs_store.load_job(job_id))
    assert fs["commits_since_checkpoint"] == 1
    assert mgr.verify_full_calls == []

    res = orchestrator._feed_one_core(job_id)
    assert res["committed"] is True
    assert len(mgr.verify_full_calls) == 1  # fired at K=2
    fs = jobs_store.get_feed_state(jobs_store.load_job(job_id))
    assert fs["commits_since_checkpoint"] == 0
    assert fs["last_checkpoint_ok"] is True
    assert fs["last_verified_claim_id"] == 1
    (cp,) = _events_of(events, "checkpoint_verify")
    assert cp["ok"] is True
    assert cp["suspect_from"] is None and cp["suspect_to"] == 1


def test_default_verify_every_commit_unchanged(feed_env, monkeypatch):
    """VERIFY_EVERY_COMMIT=true (the default) keeps today's hot path."""
    mgr, events, job = feed_env
    monkeypatch.setattr(orchestrator.config, "VERIFY_EVERY_COMMIT", True)

    res = orchestrator._feed_one_core(job["job_id"])
    assert res["committed"] is True
    assert mgr.commit_calls[-1]["verify"] is True
    assert mgr.verify_full_calls == []
    fs = jobs_store.get_feed_state(jobs_store.load_job(job["job_id"]))
    assert fs["commits_since_checkpoint"] == 0


# --------------------------------------------------- checkpoint failure
def test_checkpoint_failure_curated_pauses_job(feed_env):
    mgr, events, job = feed_env
    job_id = job["job_id"]
    mgr._verify_report = dict(BAD_REPORT)

    orchestrator._feed_one_core(job_id)
    res = orchestrator._feed_one_core(job_id)  # K=2: checkpoint fires+fails

    assert res.get("fatal") == "checkpoint_verify_failed"
    assert "suspect window" in res["error"]
    assert jobs_store.load_job(job_id)["status"] == "paused"
    (cp,) = _events_of(events, "checkpoint_verify")
    assert cp["ok"] is False
    assert cp["suspect_from"] is None and cp["suspect_to"] == 1
    # Flag off: claims stay committed (evidence-first, git bisection).
    statuses = [c["status"] for c in jobs_store.load_job(job_id)["claims"]]
    assert statuses[:2] == ["committed", "committed"]
    fs = jobs_store.get_feed_state(jobs_store.load_job(job_id))
    assert fs["last_checkpoint_ok"] is False
    # Window stays unverified so a resume re-runs the certificate.
    assert fs["commits_since_checkpoint"] == 2
    assert fs["last_verified_claim_id"] is None


def test_checkpoint_failure_marks_review_when_flagged(feed_env, monkeypatch):
    mgr, events, job = feed_env
    job_id = job["job_id"]
    mgr._verify_report = dict(BAD_REPORT)
    monkeypatch.setattr(orchestrator.config, "CHECKPOINT_FAIL_MARK_REVIEW",
                        True)

    orchestrator._feed_one_core(job_id)
    res = orchestrator._feed_one_core(job_id)

    assert res.get("fatal") == "checkpoint_verify_failed"
    statuses = [c["status"] for c in jobs_store.load_job(job_id)["claims"]]
    assert statuses[:2] == ["needs_review", "needs_review"]


def test_checkpoint_failure_faithful_ledgers_and_continues(feed_env,
                                                           monkeypatch):
    mgr, events, job = feed_env
    job_id = job["job_id"]
    mgr._verify_report = dict(BAD_REPORT)
    monkeypatch.setattr(orchestrator, "_active_fidelity", lambda: "faithful")

    orchestrator._feed_one_core(job_id)
    res = orchestrator._feed_one_core(job_id)

    assert "fatal" not in res  # never halts a faithful run
    assert jobs_store.load_job(job_id)["status"] == "feeding"
    entries = ledger_mod.read_all(mgr.working_path)
    assert len(entries) == 1
    assert entries[0]["provenance"]["checkpoint"] is True
    assert entries[0]["provenance"]["claim_window"] == [None, 1]
    assert entries[0]["subjects"] == ["working#Broken"]
    fs = jobs_store.get_feed_state(jobs_store.load_job(job_id))
    assert fs["commits_since_checkpoint"] == 0
    assert fs["last_checkpoint_ok"] is False
    assert fs["last_verified_claim_id"] == 1


# ------------------------------------------------------------ final pass
def test_final_verify_runs_before_completed(feed_env, monkeypatch):
    mgr, events, job = feed_env
    job_id = job["job_id"]
    monkeypatch.setattr(orchestrator.config, "FULL_VERIFY_EVERY_K", 100)

    for _ in range(3):
        assert orchestrator._feed_one_core(job_id)["committed"] is True
    assert mgr.verify_full_calls == []  # K never reached mid-run

    res = orchestrator._feed_one_core(job_id)  # no pending -> final pass
    assert res == {"done": True, "remaining": 0}
    assert len(mgr.verify_full_calls) == 1
    (fv,) = _events_of(events, "final_verify")
    assert fv["ok"] is True
    assert jobs_store.load_job(job_id)["status"] == "completed"
    fs = jobs_store.get_feed_state(jobs_store.load_job(job_id))
    assert fs["commits_since_checkpoint"] == 0
    assert fs["last_checkpoint_ok"] is True
    assert fs["last_verified_claim_id"] == 2


def test_final_verify_failure_pauses_not_completes(feed_env, monkeypatch):
    mgr, events, job = feed_env
    job_id = job["job_id"]
    monkeypatch.setattr(orchestrator.config, "FULL_VERIFY_EVERY_K", 100)

    for _ in range(3):
        orchestrator._feed_one_core(job_id)
    mgr._verify_report = dict(BAD_REPORT)

    res = orchestrator._feed_one_core(job_id)
    assert res.get("fatal") == "final_verify_failed"
    assert jobs_store.load_job(job_id)["status"] == "paused"
    (fv,) = _events_of(events, "final_verify")
    assert fv["ok"] is False


# -------------------------------------------------------- finalize guard
def _bare_registry(tmp_path, mgr, fidelity=None):
    reg = object.__new__(OntologyRegistry)
    manifest = {"name": "t"}
    if fidelity:
        manifest["fidelity"] = fidelity
    reg._manifests = {"t": manifest}
    reg._managers = {"t": mgr}
    reg._active_name = "t"
    reg._library_root = tmp_path / "library"
    (reg._library_root / "t").mkdir(parents=True)
    return reg


def test_finalize_refused_when_unverified_commits_fail(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "FINALIZE_REQUIRES_FULL_VERIFY", True)
    mgr = FakeManager(tmp_path, verify_report=BAD_REPORT)
    mgr.commits_since_full_verify = 3
    reg = _bare_registry(tmp_path, mgr)
    with pytest.raises(FinalizeVerificationError) as exc:
        reg.finalize("t")
    assert exc.value.report["ok"] is False
    assert reg._manifests["t"].get("status") != "finalized"


def test_finalize_passes_with_fresh_certificate(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "FINALIZE_REQUIRES_FULL_VERIFY", True)
    mgr = FakeManager(tmp_path)
    mgr.commits_since_full_verify = 3
    reg = _bare_registry(tmp_path, mgr)
    manifest = reg.finalize("t")
    assert manifest["status"] == "finalized"
    assert len(mgr.verify_full_calls) == 1


def test_finalize_faithful_skips_certificate(tmp_path, monkeypatch):
    from app import config
    monkeypatch.setattr(config, "FINALIZE_REQUIRES_FULL_VERIFY", True)
    mgr = FakeManager(tmp_path, verify_report=BAD_REPORT)
    mgr.commits_since_full_verify = 3
    reg = _bare_registry(tmp_path, mgr, fidelity="faithful")
    manifest = reg.finalize("t")  # ledger is the annotation; no refusal
    assert manifest["status"] == "finalized"
    assert mgr.verify_full_calls == []


# -------------------------------------------------- verify_full (real run)
def test_verify_full_real_smoke(tmp_path):
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    from app.ontology_manager import OntologyManager
    from app.schema import Entity

    mgr = OntologyManager(bfo_path=BFO_PATH,
                          working_path=tmp_path / "working.owl")
    proposal = Proposal(
        session_id="t", utterance="Mass is a quality",
        entities=[Entity(label="Mass", iri_suggestion="working:Mass",
                         kind="class", bfo_type="BFO_0000019",
                         bfo_label="quality", rationale="t")],
    )
    mgr.commit_proposal(proposal, verify=False)
    assert mgr.commits_since_full_verify == 1

    report = mgr.verify_full()
    assert report["ok"] is True, report["detail"]
    assert report["unsat_classes"] == []
    assert report["duration_ms"] > 0
    assert report["classes"] >= 1
    assert mgr.commits_since_full_verify == 0
