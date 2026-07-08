"""Tests for the Step 0 speed instrumentation (SPEC-bfo-agent-speed.md).

Covers the thread-local phase accumulator (app/timing.py) and the offline
aggregation in scripts/timing_report.py. Deliberately does NOT spin up the
app or HermiT -- this is pure instrumentation plumbing.
"""
from __future__ import annotations

import importlib.util
import json
import threading
from pathlib import Path

import pytest

from app import timing

ROOT = Path(__file__).resolve().parent.parent


def _load_report_module():
    spec = importlib.util.spec_from_file_location(
        "timing_report", ROOT / "scripts" / "timing_report.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------- accumulator

def test_phase_accumulates():
    timing.start_claim()
    with timing.phase("propose"):
        pass
    snap = timing.snapshot()
    assert "propose" in snap
    assert snap["propose"] >= 0.0


def test_repeated_phases_sum():
    timing.start_claim()
    timing.add("reason_dry_run", 10.0)
    timing.add("reason_dry_run", 5.5)
    assert timing.snapshot()["reason_dry_run"] == 15.5


def test_snapshot_rounds_to_one_decimal():
    timing.start_claim()
    timing.add("save", 1.23456)
    assert timing.snapshot()["save"] == 1.2


def test_snapshot_is_a_copy():
    timing.start_claim()
    timing.add("git", 3.0)
    snap = timing.snapshot()
    snap["git"] = 999.0
    assert timing.snapshot()["git"] == 3.0


def test_start_claim_resets():
    timing.start_claim()
    timing.add("apply", 7.0)
    timing.start_claim()
    assert timing.snapshot() == {}


def test_add_without_start_claim_is_safe():
    # Fresh thread, so start_claim has never run there.
    result = {}

    def worker():
        timing.add("reload", 2.0)
        result["snap"] = timing.snapshot()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert result["snap"] == {"reload": 2.0}


def test_phase_records_time_and_reraises_on_exception():
    timing.start_claim()
    with pytest.raises(ValueError, match="boom"):
        with timing.phase("commit_total"):
            raise ValueError("boom")
    assert "commit_total" in timing.snapshot()


def test_thread_locality():
    timing.start_claim()
    timing.add("propose", 100.0)
    other: dict = {}

    def worker():
        timing.start_claim()
        timing.add("gate_total", 1.0)
        other["snap"] = timing.snapshot()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    # The worker never saw the main thread's phase, and vice versa.
    assert other["snap"] == {"gate_total": 1.0}
    assert timing.snapshot() == {"propose": 100.0}


# ------------------------------------------------------------- report script

def _write_session(tmp_path: Path, ms_dicts: list[dict]) -> Path:
    """Write a synthetic session JSONL shaped like storage.log_event output."""
    path = tmp_path / "sess.jsonl"
    with path.open("w", encoding="utf-8") as f:
        # Non-timing noise the loader must skip.
        f.write(json.dumps({"event_type": "propose", "payload": {}}) + "\n")
        for i, ms in enumerate(ms_dicts):
            record = {
                "ts": "2026-07-07T00:00:00+00:00",
                "session_id": "sess",
                "event_type": "claim_timing",
                "payload": {
                    "job_id": "j1", "claim_id": i, "verdict": "consistent",
                    "committed": True, "gate_attempts": 0, "ms": ms,
                    "stats": {"classes": 10 + i, "individuals": i},
                },
            }
            f.write(json.dumps(record) + "\n")
    return path


def test_report_aggregate_basic(tmp_path):
    report = _load_report_module()
    path = _write_session(tmp_path, [
        {"propose": 100.0, "gate_total": 300.0, "total": 500.0},
        {"propose": 200.0, "gate_total": 500.0, "total": 900.0},
        {"propose": 300.0, "gate_total": 700.0, "total": 1300.0},
    ])
    events = report.load_timing_events(path)
    assert len(events) == 3

    stats = report.aggregate(events)
    assert stats["propose"]["count"] == 3
    assert stats["propose"]["mean"] == pytest.approx(200.0)
    assert stats["propose"]["p50"] == pytest.approx(200.0)
    assert stats["gate_total"]["sum"] == pytest.approx(1500.0)
    # Share is against the summed wall-clock total (500+900+1300 = 2700).
    assert stats["gate_total"]["share"] == pytest.approx(1500.0 / 2700.0)
    assert stats["total"]["share"] == pytest.approx(1.0)


def test_report_quartile_growth(tmp_path):
    report = _load_report_module()
    # 8 claims whose gate time grows linearly: first quartile (2) vs last (2).
    ms_dicts = [
        {"gate_total": 100.0 * (i + 1), "total": 100.0 * (i + 1)}
        for i in range(8)
    ]
    path = _write_session(tmp_path, ms_dicts)
    rows = report.quartile_growth(report.load_timing_events(path))
    by_phase = {r["phase"]: r for r in rows}
    # first quartile mean = (100+200)/2 = 150; last = (700+800)/2 = 750.
    assert by_phase["gate_total"]["first_mean"] == pytest.approx(150.0)
    assert by_phase["gate_total"]["last_mean"] == pytest.approx(750.0)
    assert by_phase["gate_total"]["growth"] == pytest.approx(5.0)


def test_report_no_events(tmp_path):
    report = _load_report_module()
    path = tmp_path / "empty.jsonl"
    path.write_text(json.dumps({"event_type": "propose", "payload": {}}) + "\n")
    assert report.load_timing_events(path) == []
    assert report.aggregate([]) == {}
    assert report.quartile_growth([]) == []
