"""job_health.diagnose: plain-language verdicts for feed jobs."""
import json
from datetime import datetime, timedelta, timezone

from app import job_health

NOW = datetime(2026, 11, 27, 5, 0, tzinfo=timezone.utc)


def _job(status, pending=3):
    return {"status": status, "claims": [
        {"status": "pending", "approved": True} for _ in range(pending)]}


def _runner(running=True, idle_s=10, outcome=None, last_error=None):
    return {"running": running, "outcome": outcome, "last_error": last_error,
            "last_activity_at": (NOW - timedelta(seconds=idle_s)).isoformat()}


def _ev(kind, **payload):
    return {"ts": NOW.isoformat(), "event_type": kind, "payload": payload}


def _diag(job, runner=None, events=(), fs=None, fidelity="curated",
          stall=1800):
    return job_health.diagnose(job, runner, list(events), fs or {},
                               fidelity, stall, now=NOW)


def _ids(v):
    return [r["id"] for r in v["remedies"]]


def test_healthy_feeding():
    v = _diag(_job("feeding"), _runner())
    assert v["state"] == "feeding" and v["level"] == "ok"


def test_self_heal_in_flight_offers_faithful():
    events = [_ev("commit"), _ev("checkpoint_verify", ok=False,
                                 unsat_classes=["a", "b"])]
    v = _diag(_job("feeding"), _runner(idle_s=900), events)
    assert v["state"] == "self_healing"
    assert "2 unsatisfiable" in v["detail"]
    assert "continue_faithful" in _ids(v)


def test_self_heal_finished_is_not_in_flight():
    events = [_ev("checkpoint_verify", ok=False, unsat_classes=["a"]),
              _ev("checkpoint_self_heal", ok=True), _ev("claim_timing")]
    assert _diag(_job("feeding"), _runner(), events)["state"] == "feeding"


def test_slow_when_idle_past_half_stall_timeout():
    v = _diag(_job("feeding"), _runner(idle_s=1000))
    assert v["state"] == "slow" and v["level"] == "warn"


def test_orphaned_feeding_without_runner():
    v = _diag(_job("feeding"), None)
    assert v["state"] == "orphaned" and _ids(v) == ["resume"]


def test_stalled_by_backstop():
    v = _diag(_job("paused"), _runner(running=False, outcome="stalled",
                                      last_error="stall backstop: ..."))
    assert v["state"] == "stalled" and _ids(v) == ["resume"]


def test_checkpoint_loop_after_failed_heal():
    fs = {"last_checkpoint_ok": False, "checkpoint_fail_streak": 1,
          "self_heal_failed_at": 275}
    events = [_ev("checkpoint_verify", ok=False, unsat_classes=["x"] * 60)]
    v = _diag(_job("paused"), _runner(running=False, outcome="failed"),
              events, fs)
    assert v["state"] == "checkpoint_loop" and v["level"] == "error"
    assert _ids(v)[0] == "continue_faithful"


def test_single_checkpoint_failure_suggests_resume_first():
    fs = {"last_checkpoint_ok": False, "checkpoint_fail_streak": 1}
    v = _diag(_job("paused"), _runner(running=False, outcome="failed"),
              [], fs)
    assert v["state"] == "checkpoint_failed" and _ids(v)[0] == "resume"


def test_faithful_ontology_never_reports_checkpoint_loop():
    fs = {"last_checkpoint_ok": False, "checkpoint_fail_streak": 3,
          "self_heal_failed_at": 1}
    v = _diag(_job("paused"), _runner(running=False), [], fs,
              fidelity="faithful")
    assert v["state"] == "paused"


def test_completed():
    assert _diag(_job("completed", 0))["state"] == "completed"


def test_tail_events_reads_last_lines(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps({"event_type": f"e{i}"})
                           for i in range(5000)) + "\n")
    ev = job_health.tail_events(p, max_bytes=2000)
    assert ev and ev[-1]["event_type"] == "e4999"
    assert job_health.tail_events(tmp_path / "missing.jsonl") == []


def test_unbound_job_offers_bind():
    v = job_health.diagnose(_job("paused"), None, [], {}, "curated", 1800,
                            now=NOW, active_ontology="Tractatus")
    assert v["state"] == "unbound" and _ids(v) == ["bind_to_active"]
    assert "Tractatus" in v["detail"]


def test_wrong_ontology_offers_switch_back():
    j = {**_job("paused"), "ontology": "ICD"}
    v = job_health.diagnose(j, None, [], {}, "curated", 1800, now=NOW,
                            active_ontology="Tractatus")
    assert v["state"] == "wrong_ontology"
    assert _ids(v) == ["activate_job_ontology"]


def test_bound_to_active_is_plain_paused():
    j = {**_job("paused"), "ontology": "Tractatus"}
    v = job_health.diagnose(j, None, [], {}, "curated", 1800, now=NOW,
                            active_ontology="Tractatus")
    assert v["state"] == "paused"
