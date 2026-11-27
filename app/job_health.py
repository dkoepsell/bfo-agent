"""Feed-job health diagnosis: is this job working, slow, stalled, or stuck?

/jobs/<id>/progress used to report raw counters plus "feeding on the server
-- safe to close this tab", even when the run had made no progress for an
hour (Tractatus, 2026-11-27: a failed curated checkpoint re-ran a ~25 min
self-heal on every claim). This module turns the job file, the in-memory
runner state and the tail of the session log into one plain-language verdict
plus the remedies that apply, so the UI can say what is wrong and offer a
button to fix it.

Pure functions only: ``diagnose`` takes everything it needs as arguments and
never touches the reasoner, the ontology or the module lock, so it is cheap
enough to run on every progress poll.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Remedy catalog. ``id`` is what POST /jobs/<id>/remedy accepts.
REMEDIES = {
    "resume": {
        "id": "resume",
        "label": "Resume",
        "description": "Start the server-side runner again from the next "
                       "pending claim.",
    },
    "continue_faithful": {
        "id": "continue_faithful",
        "label": "Continue in faithful mode",
        "description": "Switch this ontology to faithful fidelity: failed "
                       "full-graph checks are recorded in the incoherence "
                       "ledger as evidence instead of stopping the run, so "
                       "the remaining claims can finish. Takes effect even "
                       "if a self-heal is running right now.",
    },
    "bind_to_active": {
        "id": "bind_to_active",
        "label": "Bind to the active ontology",
        "description": "This job predates job-ontology binding. Confirm that "
                       "its claims belong to the currently active ontology; "
                       "it will then only ever feed into that ontology.",
    },
    "activate_job_ontology": {
        "id": "activate_job_ontology",
        "label": "Switch to this job's ontology",
        "description": "Make the job's own ontology active again and resume.",
    },
    "pause": {
        "id": "pause",
        "label": "Pause",
        "description": "Stop after the current step finishes.",
    },
}

_TAIL_BYTES = 256_000
# Events that mean "the feed moved forward", so an earlier failed
# checkpoint is no longer the thing the runner is busy with.
_PROGRESS_EVENTS = {"claim_timing", "commit", "checkpoint_self_heal",
                    "feed_run_end", "feed_run_start", "feed_stall_paused",
                    "checkpoint_self_heal_skipped"}


def tail_events(path: Path, max_bytes: int = _TAIL_BYTES) -> list[dict]:
    """Last events of a session log, reading at most ``max_bytes``. The
    first (possibly partial) line of the window is dropped."""
    try:
        size = path.stat().st_size
    except OSError:
        return []
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            f.readline()
        raw = f.read().decode("utf-8", errors="replace")
    out = []
    for line in raw.splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _age_seconds(iso: Optional[str], now: datetime) -> Optional[float]:
    if not iso:
        return None
    try:
        return (now - datetime.fromisoformat(iso)).total_seconds()
    except (TypeError, ValueError):
        return None


def _fmt_age(seconds: float) -> str:
    m = int(seconds // 60)
    if m >= 60:
        return f"{m // 60}h {m % 60}m"
    if m:
        return f"{m} min"
    return f"{int(seconds)}s"


def _last_failed_checkpoint(events: list[dict]) -> Optional[dict]:
    """The most recent checkpoint_verify that failed, or None."""
    for e in reversed(events):
        if e.get("event_type") == "checkpoint_verify":
            p = e.get("payload") or {}
            return None if p.get("ok") else {**p, "ts": e.get("ts")}
    return None


def _heal_in_flight(events: list[dict]) -> Optional[dict]:
    """A failed checkpoint with nothing after it that shows progress: the
    runner is inside the self-heal (or the post-failure bookkeeping)."""
    for e in reversed(events):
        kind = e.get("event_type")
        if kind in _PROGRESS_EVENTS:
            return None
        if kind == "checkpoint_verify":
            p = e.get("payload") or {}
            return None if p.get("ok") else {**p, "ts": e.get("ts")}
    return None


def _unsat_summary(cp: Optional[dict]) -> str:
    if not cp:
        return ""
    n = len(cp.get("unsat_classes") or [])
    if n:
        return f"{n} unsatisfiable class(es)"
    return "the ontology is logically inconsistent"


def _verdict(state: str, level: str, headline: str, detail: str,
             remedies: list[str], **extra) -> dict:
    return {
        "state": state,
        "level": level,  # ok | warn | error
        "headline": headline,
        "detail": detail,
        "remedies": [REMEDIES[r] for r in remedies],
        **extra,
    }


def diagnose(job: dict, runner: Optional[dict], events: list[dict],
             feed_state: dict, fidelity: str, stall_timeout: float,
             now: Optional[datetime] = None,
             active_ontology: Optional[str] = None) -> dict:
    """One plain-language verdict about a feed job.

    ``runner`` is job_runner.status() (None if no run since boot),
    ``events`` the tail of the session log, ``feed_state`` the job's
    persisted checkpoint bookkeeping, ``stall_timeout`` the stall backstop
    ceiling (seconds; <=0 means off).
    """
    now = now or datetime.now(timezone.utc)
    status = job.get("status", "draft")
    runner = runner or {}
    running = bool(runner.get("running"))
    pending = sum(1 for c in job.get("claims", [])
                  if c.get("status") == "pending" and c.get("approved"))

    if status == "completed":
        return _verdict("completed", "ok", "Finished",
                        "All approved claims have been processed.", [])

    # Ontology binding (feeding writes into the ACTIVE ontology, so a job
    # must never run while another one is active).
    bound = job.get("ontology")
    if active_ontology is not None and not running:
        if bound is None and pending:
            return _verdict(
                "unbound", "error", "Not tied to an ontology",
                "This job was created before jobs recorded which ontology "
                "they belong to, so it won't feed until you confirm it. "
                f"The active ontology is {active_ontology!r}. Bind it only "
                "if these claims belong there; otherwise activate the right "
                "ontology first.",
                ["bind_to_active"], ontology=None, active=active_ontology)
        if bound is not None and bound != active_ontology and pending:
            return _verdict(
                "wrong_ontology", "error",
                f"Belongs to {bound!r}, but {active_ontology!r} is active",
                "Feeding writes into the active ontology, so this job is "
                f"blocked to keep its claims out of {active_ontology!r}. "
                f"Switch back to {bound!r} to continue.",
                ["activate_job_ontology"], ontology=bound,
                active=active_ontology)

    if running:
        idle = _age_seconds(runner.get("last_activity_at"), now) or 0.0
        heal = _heal_in_flight(events)
        if heal is not None and fidelity != "faithful":
            return _verdict(
                "self_healing", "warn",
                f"Repairing the ontology ({_fmt_age(idle)} so far)",
                "The periodic full-graph check failed "
                f"({_unsat_summary(heal)}), and the runner is now trying to "
                "repair it automatically by removing the offending classes. "
                "On a large ontology this can take 30 minutes or more, and "
                "if it fails the job will pause. To make sure the run keeps "
                "going afterwards, switch to faithful mode.",
                ["continue_faithful", "pause"], idle_seconds=round(idle))
        if stall_timeout and stall_timeout > 0 and idle >= stall_timeout / 2:
            return _verdict(
                "slow", "warn",
                f"No progress for {_fmt_age(idle)}",
                "The runner is alive but no claim has finished for "
                f"{_fmt_age(idle)}. If nothing finishes within "
                f"{_fmt_age(stall_timeout)} of the last claim, the stall "
                "backstop pauses the job and sends a notification.",
                ["pause"], idle_seconds=round(idle))
        return _verdict(
            "feeding", "ok", "Feeding",
            "Running on the server; it's safe to close this tab.", [],
            idle_seconds=round(idle))

    if status == "feeding":
        return _verdict(
            "orphaned", "error", "Marked as feeding, but nothing is running",
            "The job says it is feeding, but no runner is working on it "
            "(usually because the server restarted without auto-resume). "
            "Resume it to pick up where it left off.",
            ["resume"])

    if status != "paused":
        return _verdict("idle", "ok", status.capitalize(), "", [])

    outcome = runner.get("outcome")
    last_error = runner.get("last_error") or ""

    if outcome == "stalled":
        return _verdict(
            "stalled", "error", "Stalled: paused by the stall backstop",
            f"{last_error} One step hung (usually a reasoner or LLM call). "
            "Resume to retry it.",
            ["resume"])

    if feed_state.get("last_checkpoint_ok") is False and fidelity != "faithful":
        cp = _last_failed_checkpoint(events)
        streak = int(feed_state.get("checkpoint_fail_streak") or 0)
        heal_failed = feed_state.get("self_heal_failed_at") is not None
        looping = streak >= 2 or heal_failed
        what = _unsat_summary(cp) or "the full-graph check failed"
        if looping:
            return _verdict(
                "checkpoint_loop", "error",
                "Stuck: the ontology fails its full-graph check",
                f"The periodic full-graph check fails ({what}) and automatic "
                "repair could not fix it. In curated mode every new claim "
                "re-runs that check, so resuming would advance about one "
                "claim and stop again. Continue in faithful mode to finish "
                "the run with the incoherence recorded as evidence, or fix "
                "the named classes and resume.",
                ["continue_faithful", "resume"],
                checkpoint_fail_streak=streak, unsat_classes=(
                    (cp or {}).get("unsat_classes") or [])[:50])
        return _verdict(
            "checkpoint_failed", "error",
            "Paused: the ontology failed its full-graph check",
            f"The periodic full-graph check failed ({what}). Resume to retry, "
            "or continue in faithful mode to record it as evidence and keep "
            "going.",
            ["resume", "continue_faithful"],
            checkpoint_fail_streak=streak, unsat_classes=(
                (cp or {}).get("unsat_classes") or [])[:50])

    if outcome == "failed":
        return _verdict("failed", "error", "Run stopped",
                        last_error or "The run stopped with an error.",
                        ["resume"])

    if pending == 0:
        return _verdict("paused", "ok", "Paused (no claims left)",
                        "Resume to run the final full-graph check and finish.",
                        ["resume"])
    return _verdict("paused", "ok", "Paused",
                    f"{pending} approved claim(s) still to process.",
                    ["resume"])
