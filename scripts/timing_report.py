"""Aggregate per-claim phase timings from a session event log.

Reads the ``claim_timing`` events that the orchestrator emits per processed
claim (SPEC-bfo-agent-speed.md Step 0; see app/timing.py) and prints:

  1. A per-phase table: count, mean, p50, p95, and each phase's share of the
     summed wall-clock total across claims.
  2. A first-quartile vs last-quartile comparison of mean per-phase ms (by
     claim order), with the growth factor -- this is what exposes the O(N)
     term: phases whose cost grows with ontology size get slower as the run
     progresses.

Usage:
  python scripts/timing_report.py <path-to-session-jsonl>
  python scripts/timing_report.py <session_id>     # resolved in SESSIONS_DIR

Stdlib only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_timing_events(path: Path) -> list[dict]:
    """Parse a session JSONL and return the claim_timing payloads, in order.

    Event records are shaped by app/storage.py::log_event:
    ``{"ts", "session_id", "event_type", "payload"}``.
    """
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("event_type") != "claim_timing":
                continue
            payload = record.get("payload") or {}
            if isinstance(payload.get("ms"), dict):
                events.append(payload)
    return events


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list."""
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1,
                   int(round(pct / 100.0 * (len(sorted_values) - 1)))))
    return sorted_values[k]


def aggregate(events: list[dict]) -> dict[str, dict]:
    """Per-phase stats across events' ``ms`` dicts.

    Returns ``{phase: {count, mean, p50, p95, sum, share}}`` where ``share``
    is the phase's summed ms over the summed wall-clock total (falls back to
    the sum of all non-total phases when no ``total`` key was recorded).
    """
    per_phase: dict[str, list[float]] = {}
    for ev in events:
        for phase, ms in ev["ms"].items():
            per_phase.setdefault(phase, []).append(float(ms))

    total_wall = sum(per_phase.get("total", []))
    if total_wall <= 0:
        total_wall = sum(
            v for k, vals in per_phase.items() if k != "total" for v in vals
        )

    stats: dict[str, dict] = {}
    for phase, values in per_phase.items():
        ordered = sorted(values)
        phase_sum = sum(values)
        stats[phase] = {
            "count": len(values),
            "mean": phase_sum / len(values),
            "p50": _percentile(ordered, 50),
            "p95": _percentile(ordered, 95),
            "sum": phase_sum,
            "share": (phase_sum / total_wall) if total_wall > 0 else 0.0,
        }
    return stats


def quartile_growth(events: list[dict]) -> list[dict]:
    """Mean per-phase ms in the first vs last quartile of claims (by order).

    Returns rows ``{phase, first_mean, last_mean, growth}`` where ``growth``
    is last/first (None when the first-quartile mean is zero). A growth
    factor well above 1 marks a phase whose cost scales with ontology size.
    """
    n = len(events)
    if n < 2:
        return []
    q = max(1, n // 4)
    first, last = events[:q], events[-q:]

    def _means(chunk: list[dict]) -> dict[str, float]:
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for ev in chunk:
            for phase, ms in ev["ms"].items():
                sums[phase] = sums.get(phase, 0.0) + float(ms)
                counts[phase] = counts.get(phase, 0) + 1
        return {p: sums[p] / counts[p] for p in sums}

    first_means, last_means = _means(first), _means(last)
    rows = []
    for phase in sorted(set(first_means) | set(last_means)):
        f = first_means.get(phase, 0.0)
        l = last_means.get(phase, 0.0)
        rows.append({
            "phase": phase,
            "first_mean": f,
            "last_mean": l,
            "growth": (l / f) if f > 0 else None,
        })
    rows.sort(key=lambda r: r["last_mean"], reverse=True)
    return rows


def _resolve_path(arg: str) -> Path:
    """Existing file path as-is; otherwise ``SESSIONS_DIR/<arg>.jsonl``."""
    p = Path(arg)
    if p.is_file():
        return p
    sys.path.insert(0, str(ROOT))
    from app import config  # noqa: PLC0415 — only needed for session-id form
    return Path(config.SESSIONS_DIR) / f"{arg}.jsonl"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip())
        return 2
    path = _resolve_path(argv[0])
    if not path.is_file():
        print(f"No such session log: {path}")
        return 1

    events = load_timing_events(path)
    if not events:
        print(f"No claim_timing events found in {path}. "
              f"Is TIMING_INSTRUMENTATION enabled and has a feed run?")
        return 0

    stats = aggregate(events)
    print(f"{len(events)} claim_timing events from {path}\n")
    header = (f"{'phase':<16} {'count':>6} {'mean ms':>10} "
              f"{'p50 ms':>10} {'p95 ms':>10} {'share':>7}")
    print(header)
    print("-" * len(header))
    for phase, s in sorted(stats.items(), key=lambda kv: kv[1]["share"],
                           reverse=True):
        print(f"{phase:<16} {s['count']:>6} {s['mean']:>10.1f} "
              f"{s['p50']:>10.1f} {s['p95']:>10.1f} {s['share']:>6.1%}")

    rows = quartile_growth(events)
    if rows:
        q = max(1, len(events) // 4)
        print(f"\nFirst {q} vs last {q} claims (mean ms per phase):\n")
        header = (f"{'phase':<16} {'first-q ms':>12} {'last-q ms':>12} "
                  f"{'growth':>8}")
        print(header)
        print("-" * len(header))
        for r in rows:
            growth = f"{r['growth']:>7.2f}x" if r["growth"] is not None else \
                f"{'--':>8}"
            print(f"{r['phase']:<16} {r['first_mean']:>12.1f} "
                  f"{r['last_mean']:>12.1f} {growth}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
