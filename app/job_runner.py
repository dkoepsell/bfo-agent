"""Server-side job feeder.

The browser used to drive feeding (one POST /feed_one per claim), so
closing the tab or logging out silently stopped the run. This module
owns the loop instead: start() spawns a daemon thread that keeps calling
the orchestrator's feed function until the job completes, is paused, or
fails repeatedly. Per-claim state is already persisted by jobs.py after
every claim, so a process restart loses nothing; the orchestrator calls
resume_incomplete() at boot to pick up any job still marked "feeding".

Progress lives in memory (status()) and is exposed by
GET /jobs/<id>/progress. On completion or failure a best-effort
plain-text POST goes to config.NOTIFY_URL if set (ntfy.sh-compatible:
body = message, "Title" header = subject).
"""

from __future__ import annotations

import threading
import time
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from . import config
from . import jobs as jobs_store
from .storage import log_event

# Stop a run (and flag the job paused) after this many claims in a row
# end in error — that pattern means the API/reasoner is down, not that
# individual claims are bad.
MAX_CONSECUTIVE_ERRORS = 5

_runners: dict[str, dict] = {}
_threads: dict[str, threading.Thread] = {}
_registry_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def start(job_id: str, feed_fn: Callable[[str, bool], dict],
          auto_accept: bool = True,
          on_complete: Callable[[str, dict], str | None] | None = None) -> dict:
    """Start the feeder thread for a job. Idempotent: if a runner is
    already alive for this job, return its status instead of starting a
    second one.

    ``on_complete(job_id, state)`` runs best-effort after a job completes
    (not on pause/fail) -- e.g. the end-of-job FOL audit (fol-gate-spec.md
    FG-2). Whatever short string it returns is appended to the completion
    notification. It must never raise into the runner; we guard anyway.
    """
    with _registry_lock:
        t = _threads.get(job_id)
        if t is not None and t.is_alive():
            return status(job_id)
        state = {
            "job_id": job_id,
            "running": True,
            "auto_accept": auto_accept,
            "started_at": _now(),
            "last_activity_at": _now(),
            "finished_at": None,
            "outcome": None,  # completed | paused | failed
            "processed": 0,
            "committed": 0,
            # Faithful-mode claims committed as-asserted with an incoherence
            # ledger entry (fidelity-mode-spec.md FM-11 visibility).
            "flagged": 0,
            "inconsistent": 0,
            "needs_review": 0,
            "errors": 0,
            "remaining": None,
            "current_claim": None,
            "last_error": None,
            "avg_seconds_per_claim": None,
        }
        _runners[job_id] = state
        t = threading.Thread(
            target=_run, args=(job_id, feed_fn, auto_accept, state,
                               on_complete),
            name=f"job-runner-{job_id}", daemon=True,
        )
        _threads[job_id] = t
        t.start()
    return status(job_id)


def status(job_id: str) -> dict | None:
    """Snapshot of the (current or most recent) run for this job, or
    None if no runner has been started since boot."""
    state = _runners.get(job_id)
    if state is None:
        return None
    out = dict(state)
    t = _threads.get(job_id)
    out["running"] = bool(t and t.is_alive())
    return out


def resume_incomplete(feed_fn: Callable[[str, bool], dict],
                      on_complete=None) -> list[str]:
    """Restart runners for jobs left in status "feeding" (a run that a
    server restart or crash interrupted). Returns the resumed job ids."""
    resumed = []
    for j in jobs_store.list_jobs():
        if j.get("status") == "feeding":
            start(j["job_id"], feed_fn, on_complete=on_complete)
            resumed.append(j["job_id"])
    return resumed


def _run(job_id: str, feed_fn, auto_accept: bool, state: dict,
         on_complete=None) -> None:
    session_id = None
    job_name = job_id
    consecutive_errors = 0
    t0 = time.monotonic()
    try:
        job = jobs_store.load_job(job_id)
        session_id = job.get("session_id")
        job_name = job.get("name") or job_id
        log_event(session_id, "feed_run_start",
                  {"job_id": job_id, "auto_accept": auto_accept})

        while True:
            # Reload status each iteration so POST /pause (or a status
            # change from anywhere else) stops the loop between claims.
            job = jobs_store.load_job(job_id)
            if job.get("status") != "feeding":
                state["outcome"] = "paused"
                break

            try:
                res = feed_fn(job_id, auto_accept)
            except Exception as e:
                consecutive_errors += 1
                state["errors"] += 1
                state["last_error"] = str(e)
                state["last_activity_at"] = _now()
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _fail(job_id, job_name, state,
                          f"{consecutive_errors} consecutive errors; "
                          f"last: {e}")
                    break
                time.sleep(min(60, 5 * consecutive_errors))
                continue

            state["last_activity_at"] = _now()

            if res.get("fatal"):
                _fail(job_id, job_name, state,
                      res.get("error") or str(res.get("fatal")))
                break

            if res.get("done"):
                state["outcome"] = "completed"
                state["remaining"] = 0
                extra = ""
                if on_complete is not None:
                    try:
                        extra = on_complete(job_id, state) or ""
                    except Exception as e:  # never let the hook kill the run
                        extra = f"(post-run hook failed: {e})"
                flagged_note = (
                    f"flagged {state['flagged']}, " if state["flagged"] else ""
                )
                _notify(
                    f"BFO job finished: {job_name}",
                    f"All approved claims processed. "
                    f"committed {state['committed']}, "
                    f"{flagged_note}"
                    f"inconsistent {state['inconsistent']}, "
                    f"needs review {state['needs_review']}, "
                    f"errors {state['errors']} "
                    f"({state['processed']} this run)."
                    + (f"\n{extra}" if extra else ""),
                )
                break

            claim = res.get("claim") or {}
            st = claim.get("status")
            state["processed"] += 1
            if st == "committed":
                state["committed"] += 1
                if claim.get("verdict") == "flagged":
                    state["flagged"] += 1
            elif st == "inconsistent":
                state["inconsistent"] += 1
            elif st == "needs_review":
                state["needs_review"] += 1
            elif st == "error":
                state["errors"] += 1

            if st == "error" or res.get("error"):
                consecutive_errors += 1
                state["last_error"] = str(res.get("error") or "claim error")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    _fail(job_id, job_name, state,
                          f"{consecutive_errors} consecutive claim errors; "
                          f"last: {state['last_error']}")
                    break
            else:
                consecutive_errors = 0

            state["remaining"] = res.get("remaining")
            state["current_claim"] = {
                "id": claim.get("id"),
                "status": st,
                "text": (claim.get("claim") or "")[:120],
            }
            state["avg_seconds_per_claim"] = round(
                (time.monotonic() - t0) / state["processed"], 1
            )
    except Exception as e:
        # Anything unexpected (job file unreadable, etc.): flag and stop.
        state["last_error"] = str(e)
        _fail(job_id, job_name, state, str(e))
    finally:
        state["running"] = False
        state["finished_at"] = _now()
        if session_id:
            try:
                log_event(session_id, "feed_run_end", {
                    "job_id": job_id,
                    "outcome": state.get("outcome"),
                    "processed": state.get("processed"),
                    "committed": state.get("committed"),
                    "inconsistent": state.get("inconsistent"),
                    "needs_review": state.get("needs_review"),
                    "errors": state.get("errors"),
                    "last_error": state.get("last_error"),
                })
            except Exception:
                pass


def _fail(job_id: str, job_name: str, state: dict, reason: str) -> None:
    state["outcome"] = "failed"
    state["last_error"] = reason
    try:
        jobs_store.set_job_status(job_id, "paused")
    except Exception:
        pass
    _notify(
        f"BFO job stopped: {job_name}",
        f"Feed run stopped after {state['processed']} claims "
        f"(committed {state['committed']}). Reason: {reason}. "
        f"Job is paused; hit Resume to retry.",
    )


def _notify(title: str, message: str) -> None:
    """Best-effort push notification; never raises into the runner."""
    url = config.NOTIFY_URL
    if not url:
        return
    try:
        req = urllib.request.Request(
            url,
            data=message.encode("utf-8"),
            headers={"Title": title,
                     "Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass
