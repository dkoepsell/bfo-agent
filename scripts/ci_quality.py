#!/usr/bin/env python3
"""CI quality check (SPEC-bfo-agent-quality.md QS-G4).

1. Builds the test suite's clean fixture ontology and runs the audit with
   every QS-G3 construction gate; fails on any gate.
2. Builds the defective fixture and requires the same gates to FAIL (so a
   broken audit cannot pass CI vacuously).
3. Runs the gate-recall benchmark at a fixed seed and fails below
   ``--min-recall``.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import quality_gates  # noqa: E402
from tests.quality_fixture import clean_graph, defective_graph, write  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--min-recall", type=float, default=1.0)
    ap.add_argument("--no-reasoner", action="store_true")
    a = ap.parse_args(argv)

    with tempfile.TemporaryDirectory() as td:
        clean = quality_gates.run_audit(write(clean_graph(), Path(td) / "c.owl"))
        bad = quality_gates.run_audit(write(defective_graph(), Path(td) / "d.owl"))
    fails = quality_gates.construction_failures(clean)
    print("clean fixture:", "pass" if not fails else fails)
    if fails:
        return 1
    if not quality_gates.construction_failures(bad):
        print("defective fixture passed the gates: the audit is broken")
        return 1

    from evaluation import gate_recall

    res = gate_recall.run(seed=a.seed, reasoner=not a.no_reasoner,
                          with_profiles=True)
    print(f"gate recall (seed {a.seed}): {res['recall']}")
    for r in res["rows"]:
        print(f"  {r['id']:<20} expected {r['expected']:<13} -> {r['caught_by']}")
    return 0 if res["recall"] >= a.min_recall else 1


if __name__ == "__main__":
    sys.exit(main())
