"""Thread-local per-claim phase timing accumulator.

Feeds the ``claim_timing`` events logged per processed claim into the session
event log (SPEC-bfo-agent-speed.md Step 0): the orchestrator calls
:func:`start_claim` when a claim is selected, the expensive operations
(propose, gate, reasoner passes, reload, save, commit, git) wrap themselves
in :func:`phase`, and the orchestrator emits :func:`snapshot` as the event's
``ms`` dict. Thread-local so a job-runner thread and an interactive request
never mix their timings. Pure instrumentation -- no behavior change.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

_local = threading.local()


def _acc() -> dict:
    """Current thread's accumulator, created on demand.

    Makes :func:`add`/:func:`phase` safe even when :func:`start_claim` was
    never called on this thread (e.g. an interactive /propose request).
    """
    acc = getattr(_local, "acc", None)
    if acc is None:
        acc = {}
        _local.acc = acc
    return acc


def start_claim() -> None:
    """Reset this thread's accumulator; call once per claim before timing."""
    _local.acc = {}


def add(phase_name: str, ms: float) -> None:
    """Accumulate ``ms`` milliseconds under ``phase_name`` (repeats sum)."""
    acc = _acc()
    acc[phase_name] = acc.get(phase_name, 0.0) + ms


@contextmanager
def phase(name: str):
    """Time the body with ``time.perf_counter()`` and accumulate under ``name``.

    Exceptions are re-raised unchanged; the elapsed time is recorded even
    when the body raises (try/finally), so failed reasoner passes still show
    up in the timings.
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        add(name, (time.perf_counter() - t0) * 1000.0)


def snapshot() -> dict[str, float]:
    """Copy of the current accumulator, values rounded to 0.1 ms.

    Empty dict if nothing was recorded on this thread.
    """
    return {k: round(v, 1) for k, v in _acc().items()}
