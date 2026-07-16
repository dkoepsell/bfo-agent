"""Unit tests for the feed stall backstop (app/job_runner._stall_monitor).

The per-operation guards (reasoner watchdog, LLM client timeout) bound each
blocking call, but a kill-miss / lock deadlock / unforeseen blocking I/O could
still wedge the runner mid-claim and -- because the runner never reaches its
``finally`` -- leave the job silently "feeding" forever. The stall monitor
turns that into a visible, resumable, notified pause. These tests pin that
contract: it pauses a wedged run, and it never touches a run that has already
left the "feeding" state (completion audit / resume-verify windows)."""
from datetime import datetime, timedelta, timezone

from app import job_runner


class _FakeJobsStore:
    def __init__(self, status):
        self._status = status
        self.set_calls = []

    def load_job(self, job_id):
        return {"status": self._status, "name": "ICD-11 regate"}

    def set_job_status(self, job_id, status):
        self.set_calls.append(status)
        self._status = status


def _state(seconds_since_activity):
    ts = (datetime.now(timezone.utc)
          - timedelta(seconds=seconds_since_activity)).isoformat()
    return {
        "running": True,
        "last_activity_at": ts,
        "current_claim": {"id": 42},
        "processed": 7,
    }


def _patch(monkeypatch, jobs_store, ceiling=0.2):
    notes = []
    monkeypatch.setattr(job_runner.config,
                        "FEED_STALL_TIMEOUT_SECONDS", ceiling)
    monkeypatch.setattr(job_runner, "jobs_store", jobs_store)
    monkeypatch.setattr(job_runner, "_notify",
                        lambda t, m: notes.append((t, m)))
    monkeypatch.setattr(job_runner, "log_event", lambda *a, **k: None)
    return notes


def test_stall_monitor_pauses_wedged_feeding_job(monkeypatch):
    store = _FakeJobsStore("feeding")
    notes = _patch(monkeypatch, store)
    state = _state(seconds_since_activity=1000)  # far past the 0.2s ceiling

    job_runner._stall_monitor("job1", "ICD-11 regate", "sess", state)

    assert store.set_calls == ["paused"]
    assert state["outcome"] == "stalled"
    assert "stall backstop" in state["last_error"]
    assert len(notes) == 1
    assert "stalled" in notes[0][0].lower()


def test_stall_monitor_ignores_non_feeding_job(monkeypatch):
    # Completion audit / resume-verify: status already moved off "feeding".
    store = _FakeJobsStore("completed")
    notes = _patch(monkeypatch, store)
    state = _state(seconds_since_activity=1000)

    job_runner._stall_monitor("job1", "ICD-11 regate", "sess", state)

    assert store.set_calls == []          # never paused a done job
    assert notes == []
    assert state.get("outcome") != "stalled"


def test_stall_monitor_exits_when_run_finished(monkeypatch):
    store = _FakeJobsStore("feeding")
    notes = _patch(monkeypatch, store)
    state = _state(seconds_since_activity=1000)
    state["running"] = False              # runner already wrapped up cleanly

    job_runner._stall_monitor("job1", "ICD-11 regate", "sess", state)

    assert store.set_calls == []
    assert notes == []


def test_stall_monitor_disabled_when_ceiling_zero(monkeypatch):
    store = _FakeJobsStore("feeding")
    notes = _patch(monkeypatch, store, ceiling=0)
    state = _state(seconds_since_activity=1000)

    job_runner._stall_monitor("job1", "ICD-11 regate", "sess", state)

    assert store.set_calls == []
    assert notes == []
