"""Compare gate policies on a fixed straddle seed (SPEC Task 3 acceptance).

Runs the same generation seed (the Force/Drag/*Load straddle family) under each
GatePolicy and tabulates, per policy: axioms proposed, clashes caught at the
lint tier, clashes caught at the reasoner tier, resamples/repairs, and the final
unsatisfiable-class count (target zero).

This is offline and deterministic: it uses a fixed clean resampler in place of
the live proposer so the comparison is reproducible without the Anthropic API.

Usage:
  python scripts/compare_gate_policies.py [--reasoner] [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import coherence_gate as cg          # noqa: E402
from app.ontology_manager import OntologyManager  # noqa: E402
from app.schema import Entity, Proposal, Relation  # noqa: E402
from evaluation import coherence_scorer as scorer   # noqa: E402

BFO_PATH = ROOT / "ontology" / "bfo.owl"

SEED = [
    "Force", "Weight", "Drag", "Lift", "Toughness",
    "NoiseNuisance", "InducedDrag", "WingLoad", "GustLoad", "LandingLoad",
]


def _clean_resampler(prev, result, neighborhood):
    """Model the proposer self-correcting: re-propose nothing contentious."""
    return Proposal(session_id="compare", utterance="corrected")


def run_policy(policy: cg.GatePolicy, run_reasoner: bool, max_attempts: int) -> dict:
    with tempfile.TemporaryDirectory() as td:
        mgr = OntologyManager(bfo_path=BFO_PATH, working_path=Path(td) / "working.owl")
        proposed = lint_clashes = reasoner_clashes = recoveries = 0

        for name in SEED:
            # 1. clean quality typing
            pq = Proposal(
                session_id="compare", utterance=f"{name} is a quality",
                entities=[Entity(label=name, iri_suggestion=f"working:{name}",
                                 bfo_type="BFO_0000019", bfo_label="quality",
                                 kind="class", rationale="r")],
            )
            run = cg.run_with_policy(pq, mgr, policy, resample_fn=_clean_resampler,
                                     run_reasoner=run_reasoner, max_attempts=max_attempts)
            proposed += 1
            if run.outcome == cg.GateOutcome.ACCEPT:
                mgr.commit_proposal(run.proposal)

            # 2. incoherent disposition straddle
            ps = Proposal(
                session_id="compare", utterance=f"{name} is a disposition",
                relations=[Relation(s=f"working:{name}", p="rdfs:subClassOf",
                                    o="bfo:BFO_0000016", rationale="r")],
            )
            run = cg.run_with_policy(ps, mgr, policy, resample_fn=_clean_resampler,
                                     run_reasoner=run_reasoner, max_attempts=max_attempts)
            proposed += 1
            fired_lint = any(e.get("tier") == "lint" and e.get("outcome") != "accept"
                             for e in run.events)
            fired_reasoner = any(e.get("tier") == "reasoner" and e.get("outcome") != "accept"
                                 for e in run.events)
            lint_clashes += int(fired_lint)
            reasoner_clashes += int(fired_reasoner)
            if (fired_lint or fired_reasoner) and run.outcome == cg.GateOutcome.ACCEPT:
                recoveries += 1
            if run.outcome == cg.GateOutcome.ACCEPT:
                mgr.commit_proposal(run.proposal)

        mgr.save()
        field = scorer.coherence_field(mgr.working_path, BFO_PATH)
        return {
            "policy": policy.value,
            "axioms_proposed": proposed,
            "lint_tier_clashes": lint_clashes,
            "reasoner_tier_clashes": reasoner_clashes,
            "resamples_or_repairs": recoveries,
            "final_unsatisfiable_classes": len(field["unsatisfiable_classes"]),
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reasoner", action="store_true",
                    help="Also run the reasoner tier (slower)")
    ap.add_argument("--max-attempts", type=int, default=2)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rows = [run_policy(p, args.reasoner, args.max_attempts) for p in cg.GatePolicy]

    cols = ["policy", "axioms_proposed", "lint_tier_clashes",
            "reasoner_tier_clashes", "resamples_or_repairs",
            "final_unsatisfiable_classes"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("-" * len(header))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))

    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=2))
        print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
