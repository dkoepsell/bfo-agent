"""Amortized save (SPEC-bfo-agent-speed.md change 7, SAVE_EVERY_COMMIT).

With SAVE_EVERY_COMMIT off, the per-claim commit skips the O(N) RDF/XML
serialization and the per-claim git commit; the working file is written at
checkpoint boundaries, the final pass, and runner exit, and crash recovery
resets claims committed after the last on-disk save back to pending. These
tests pin the config guard, the manager's skip-save behavior, the flush
points and watermark bookkeeping, and the resume reset.
"""
from pathlib import Path

import pytest

from app import config
from app import job_runner
from app import jobs as jobs_store
from app import orchestrator
from app.schema import Proposal

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"

OK_REPORT = {"ok": True, "unsat_classes": [], "detail": "",
             "duration_ms": 1.0, "classes": 0, "individuals": 0}


class FakeManager:
    """Just enough OntologyManager surface for _feed_one_core + flush."""

    def __init__(self, tmp_path):
        self.working_path = tmp_path / "working.owl"
        self.working_path.write_text("<placeholder/>")
        self.commits_since_full_verify = 0
        self.unsaved_commits = 0
        self.last_commit_incoherence = None
        self.commit_calls: list[dict] = []
        self.save_calls = 0
        self.verify_full_calls: list = []
        self._verify_report = dict(OK_REPORT)

    def summary_for_proposer(self, utterance=None, **kw):
        return {"working_classes": [], "known_individuals": []}

    def check_consistency_dry_run(self, proposal):
        return True, ""

    def commit_proposal(self, proposal, verify=True, faithful=False,
                        exclude_axioms=None):
        self.commit_calls.append({"verify": verify, "faithful": faithful})
        if not verify:
            self.commits_since_full_verify += 1
            if not config.SAVE_EVERY_COMMIT:
                self.unsaved_commits += 1
        return []

    def save(self):
        self.save_calls += 1
        self.unsaved_commits = 0

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
    """_feed_one_core wired to fakes, SAVE_EVERY_COMMIT off, 3-claim job."""
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(jobs_store, "JOBS_DIR", jobs_dir)

    mgr = FakeManager(tmp_path)
    events: list[tuple[str, dict]] = []
    git_calls: list[tuple] = []

    monkeypatch.setattr(orchestrator, "_get_manager", lambda: mgr)
    monkeypatch.setattr(orchestrator, "_get_proposer", lambda: FakeProposer())
    monkeypatch.setattr(orchestrator, "_active_is_finalized", lambda: False)
    monkeypatch.setattr(orchestrator, "_active_fidelity", lambda: "curated")
    monkeypatch.setattr(orchestrator, "_apply_scaffolding",
                        lambda p, m, s: [])
    monkeypatch.setattr(orchestrator, "_budget_and_kext_notes",
                        lambda p, m: [])
    monkeypatch.setattr(orchestrator, "git_commit_working_ontology",
                        lambda *a, **k: git_calls.append(a))
    monkeypatch.setattr(orchestrator.gate_mod, "scaffolding_directives",
                        lambda p, m: [])
    monkeypatch.setattr(
        orchestrator, "log_event",
        lambda session_id, kind, payload=None: events.append((kind, payload)),
    )
    monkeypatch.setattr(orchestrator.config, "ENABLE_COHERENCE_GATE", False)
    monkeypatch.setattr(orchestrator.config, "VERIFY_EVERY_COMMIT", False)
    monkeypatch.setattr(orchestrator.config, "SAVE_EVERY_COMMIT", False)
    monkeypatch.setattr(orchestrator.config, "FULL_VERIFY_EVERY_K", 2)
    monkeypatch.setattr(orchestrator.config, "FINALIZE_REQUIRES_FULL_VERIFY",
                        True)

    job = jobs_store.create_job("amortized-save-test")
    jobs_store.append_claims(job["job_id"], [
        {"claim": f"claim {i}", "confidence": "high"} for i in range(3)
    ])
    jobs_store.set_job_status(job["job_id"], "feeding")
    return mgr, events, git_calls, jobs_store.load_job(job["job_id"])


def _events_of(events, kind):
    return [p for k, p in events if k == kind]


def _feed_state(job_id):
    return jobs_store.get_feed_state(jobs_store.load_job(job_id))


# ------------------------------------------------------------- config guard
def test_config_refuses_save_off_without_prereqs():
    ok = config._sanitize_save_every_commit
    assert ok(True, True, False) is True          # default stays default
    assert ok(False, False, True) is False        # prerequisites met
    assert ok(False, True, True) is True          # verify-per-commit on
    assert ok(False, False, False) is True        # legacy dry-run reloads


# ------------------------------------------------------ per-claim hot path
def test_no_git_and_no_save_per_commit(feed_env):
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]

    res = orchestrator._feed_one_core(job_id)
    assert res["committed"] is True
    assert mgr.commit_calls[-1]["verify"] is False
    assert mgr.save_calls == 0          # nothing written on the hot path
    assert git_calls == []              # nothing on disk for git to record
    assert mgr.unsaved_commits == 1
    # Watermark NOT advanced: the claim is not on disk.
    assert _feed_state(job_id)["last_saved_claim_id"] is None


def test_per_claim_git_and_watermark_with_save_on(feed_env, monkeypatch):
    mgr, events, git_calls, job = feed_env
    monkeypatch.setattr(orchestrator.config, "SAVE_EVERY_COMMIT", True)
    job_id = job["job_id"]

    res = orchestrator._feed_one_core(job_id)
    assert res["committed"] is True
    assert len(git_calls) == 1          # legacy per-claim git commit
    # Piggybacked watermark: per-commit save means the claim is on disk.
    assert _feed_state(job_id)["last_saved_claim_id"] == 0


# ------------------------------------------------------------- flush points
def test_checkpoint_flushes_and_advances_watermark(feed_env):
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]

    orchestrator._feed_one_core(job_id)
    assert mgr.save_calls == 0
    orchestrator._feed_one_core(job_id)  # K=2: checkpoint fires

    assert len(mgr.verify_full_calls) == 1
    assert mgr.save_calls == 1           # flushed at the boundary
    assert mgr.unsaved_commits == 0
    assert len(git_calls) == 1           # one batched git commit
    fs = _feed_state(job_id)
    assert fs["last_saved_claim_id"] == 1
    (ev,) = _events_of(events, "amortized_save")
    assert ev["reason"] == "checkpoint"
    assert ev["unsaved_commits"] == 2


def test_final_pass_flushes_tail_window(feed_env, monkeypatch):
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]
    monkeypatch.setattr(orchestrator.config, "FULL_VERIFY_EVERY_K", 100)

    for _ in range(3):
        assert orchestrator._feed_one_core(job_id)["committed"] is True
    assert mgr.save_calls == 0

    res = orchestrator._feed_one_core(job_id)  # no pending -> final pass
    assert res == {"done": True, "remaining": 0}
    assert mgr.save_calls == 1
    assert mgr.unsaved_commits == 0
    fs = _feed_state(job_id)
    assert fs["last_saved_claim_id"] == 2
    (ev,) = _events_of(events, "amortized_save")
    assert ev["reason"] == "final"
    assert jobs_store.load_job(job_id)["status"] == "completed"


def test_flush_working_ontology_on_runner_exit(feed_env):
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]

    orchestrator._feed_one_core(job_id)
    assert mgr.unsaved_commits == 1

    assert orchestrator.flush_working_ontology(job_id, reason="runner-exit")
    assert mgr.save_calls == 1
    assert mgr.unsaved_commits == 0
    assert _feed_state(job_id)["last_saved_claim_id"] == 0
    # Idempotent: nothing left to flush.
    assert not orchestrator.flush_working_ontology(job_id)
    assert mgr.save_calls == 1


def test_flush_noop_when_save_every_commit(feed_env, monkeypatch):
    mgr, events, git_calls, job = feed_env
    monkeypatch.setattr(orchestrator.config, "SAVE_EVERY_COMMIT", True)
    assert orchestrator.flush_working_ontology(job["job_id"]) is False
    assert mgr.save_calls == 0


def test_runner_calls_flush_fn_on_exit(feed_env, monkeypatch):
    """job_runner._run invokes flush_fn in its finally on any outcome."""
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]
    monkeypatch.setattr(job_runner, "log_event", lambda *a, **k: None)
    flushed = []

    jobs_store.set_job_status(job_id, "paused")  # loop exits immediately
    state = {"running": True, "processed": 0, "committed": 0, "flagged": 0,
             "inconsistent": 0, "needs_review": 0, "errors": 0,
             "remaining": None, "current_claim": None, "last_error": None,
             "avg_seconds_per_claim": None, "outcome": None,
             "finished_at": None, "last_activity_at": None}
    job_runner._run(job_id, orchestrator._feed_one_core, True, state,
                    flush_fn=lambda jid: flushed.append(jid))
    assert state["outcome"] == "paused"
    assert flushed == [job_id]


# ------------------------------------------------------------ crash recovery
def test_resume_resets_claims_beyond_watermark(feed_env):
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]

    for _ in range(2):  # claim 1 hits K=2 -> checkpoint flush, watermark=1
        orchestrator._feed_one_core(job_id)
    orchestrator._feed_one_core(job_id)  # claim 2 stays unsaved
    assert _feed_state(job_id)["last_saved_claim_id"] == 1

    # Simulate a process death: the in-memory unsaved commit is gone.
    mgr.unsaved_commits = 0

    reset = orchestrator._prepare_amortized_resume(job_id)
    assert reset == [2]
    claims = jobs_store.load_job(job_id)["claims"]
    assert [c["status"] for c in claims] == \
        ["committed", "committed", "pending"]
    (ev,) = _events_of(events, "resume_replay_reset")
    assert ev["claim_ids"] == [2]
    assert ev["last_saved_claim_id"] == 1


def test_resume_no_reset_when_memory_alive(feed_env):
    """Plain pause/resume without a restart: live memory still holds the
    unsaved commits, so nothing was lost and nothing is reset."""
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]

    for _ in range(3):
        orchestrator._feed_one_core(job_id)
    assert mgr.unsaved_commits == 1  # claim 2, after the K=2 flush

    assert orchestrator._prepare_amortized_resume(job_id) == []
    statuses = [c["status"] for c in jobs_store.load_job(job_id)["claims"]]
    assert statuses == ["committed"] * 3


def test_resume_initializes_watermark_first_run(feed_env):
    """A None watermark means first run under amortized save: the current
    committed set becomes the saved baseline, nothing is reset."""
    mgr, events, git_calls, job = feed_env
    job_id = job["job_id"]
    jobs_store.update_claim_status(job_id, 0, "committed")

    assert orchestrator._prepare_amortized_resume(job_id) == []
    assert _feed_state(job_id)["last_saved_claim_id"] == 0
    statuses = [c["status"] for c in jobs_store.load_job(job_id)["claims"]]
    assert statuses[0] == "committed"


def test_reset_claims_pending_only_touches_committed(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs_store, "JOBS_DIR", tmp_path)
    job = jobs_store.create_job("t")
    jobs_store.append_claims(job["job_id"], [
        {"claim": f"c{i}", "confidence": "high"} for i in range(3)
    ])
    jobs_store.update_claim_status(job["job_id"], 0, "committed")
    jobs_store.update_claim_status(job["job_id"], 1, "inconsistent")

    reset = jobs_store.reset_claims_pending(job["job_id"], [0, 1, 2])
    assert reset == [0]
    statuses = [c["status"] for c in
                jobs_store.load_job(job["job_id"])["claims"]]
    assert statuses == ["pending", "inconsistent", "pending"]


# --------------------------------------------------- real manager skip-save
def test_manager_skips_disk_write_until_save(tmp_path, monkeypatch):
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    from app.ontology_manager import OntologyManager
    from app.schema import Entity

    monkeypatch.setattr(config, "SAVE_EVERY_COMMIT", False)
    monkeypatch.setattr(config, "INMEM_DRY_RUN", True)
    mgr = OntologyManager(bfo_path=BFO_PATH,
                          working_path=tmp_path / "working.owl")
    proposal = Proposal(
        session_id="t", utterance="Mass is a quality",
        entities=[Entity(label="Mass", iri_suggestion="working:Mass",
                         kind="class", bfo_type="BFO_0000019",
                         bfo_label="quality", rationale="t")],
    )
    baseline = mgr.working_path.read_bytes()  # _load wrote the empty shell
    stat0 = mgr.working_path.stat()

    mgr.commit_proposal(proposal, verify=False)
    assert mgr.unsaved_commits == 1
    # Nothing reached disk: same bytes, same mtime.
    assert mgr.working_path.read_bytes() == baseline
    assert mgr.working_path.stat().st_mtime_ns == stat0.st_mtime_ns
    assert b"Mass" not in baseline

    mgr.save()
    assert mgr.unsaved_commits == 0
    assert b"Mass" in mgr.working_path.read_bytes()
