"""Run evaluation N times per condition and aggregate mean/stddev.

Produces a summary table with baseline and agent numbers for consistency,
grounded accuracy, IRI-refusal accuracy, and prose-rescored refusal
accuracy. Also writes all raw reports under evaluation/variance/.

Usage:
  python scripts/run_variance.py --n 3
  python scripts/run_variance.py --n 5 --baseline-only
  python scripts/run_variance.py --n 3 --skip-baseline

Baseline runs take ~10-15 minutes each, agent runs ~25-40 minutes.
Total for n=3 both conditions: ~3 hours, ~$60.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "evaluation" / "variance"
OUT_DIR.mkdir(parents=True, exist_ok=True)

REFUSAL_PATTERN = re.compile(
    r"does not contain|"
    r"does not list|"
    r"does not have|"
    r"no (?:class|individual|information|data|specific)|"
    r"not (?:present|available|specified|documented)|"
    r"insufficient information|"
    r"cannot (?:answer|determine|be answered)|"
    r"no\s.{1,20}(?:information|data|statutes)",
    re.IGNORECASE,
)


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def run_eval(mode: str, out_path: Path) -> bool:
    """Run evaluate.py once for the given mode. Returns True on success."""
    cmd = [
        sys.executable,
        str(ROOT / "evaluation" / "evaluate.py"),
        f"--{mode}",
        "--out",
        str(out_path),
    ]
    print(f"  running: {' '.join(cmd)}", flush=True)
    r = subprocess.run(cmd, cwd=ROOT)
    return r.returncode == 0


def rescore_refusal(report: dict) -> float:
    """Apply prose-based refusal detection to the ungrounded probes."""
    results = report.get("grounding", {}).get("ungrounded_results", [])
    if not results:
        return 0.0
    correct = 0
    for item in results:
        ans = item.get("response", {}).get("answer", "")
        original_grounded = item.get("response", {}).get("grounded", True)
        has_marker = bool(REFUSAL_PATTERN.search(ans))
        # "Refused" if either the harness tagged it ungrounded OR the prose
        # contains an explicit refusal marker.
        if (not original_grounded) or has_marker:
            correct += 1
    return correct / len(results)


def extract_numbers(report: dict) -> dict:
    return {
        "consistency": report.get("consistency", {}).get("mean_agreement", 0.0),
        "grounded": report.get("grounding", {}).get("grounded_accuracy", 0.0),
        "refusal_iri": report.get("grounding", {}).get("refusal_accuracy", 0.0),
        "refusal_prose": rescore_refusal(report),
    }


def mean_sd(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    m = statistics.mean(values)
    sd = statistics.stdev(values) if len(values) > 1 else 0.0
    return m, sd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="Runs per condition")
    ap.add_argument("--baseline-only", action="store_true")
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--label", default=None, help="Tag prefix for this batch")
    args = ap.parse_args()

    label = args.label or stamp()
    do_baseline = not args.skip_baseline
    do_agent = not args.baseline_only

    runs: dict[str, list[dict]] = {"baseline": [], "agent": []}

    if do_baseline:
        print(f"\n=== BASELINE x {args.n} ===")
        for i in range(args.n):
            print(f"\n[baseline run {i+1}/{args.n}]")
            out = OUT_DIR / f"{label}_baseline_{i+1}.json"
            if run_eval("baseline", out):
                runs["baseline"].append(json.loads(out.read_text()))
            else:
                print(f"  FAILED")

    if do_agent:
        print(f"\n=== AGENT x {args.n} ===")
        for i in range(args.n):
            print(f"\n[agent run {i+1}/{args.n}]")
            out = OUT_DIR / f"{label}_agent_{i+1}.json"
            if run_eval("agent", out):
                runs["agent"].append(json.loads(out.read_text()))
            else:
                print(f"  FAILED")

    # Aggregate
    summary = {"label": label, "n_runs": args.n, "conditions": {}}
    for cond, reports in runs.items():
        if not reports:
            continue
        nums = [extract_numbers(r) for r in reports]
        agg = {}
        for k in ("consistency", "grounded", "refusal_iri", "refusal_prose"):
            vals = [n[k] for n in nums]
            m, sd = mean_sd(vals)
            agg[k] = {"mean": round(m, 3), "sd": round(sd, 3),
                      "n": len(vals), "values": vals}
        summary["conditions"][cond] = agg

    summary_path = OUT_DIR / f"{label}_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))

    # Pretty print
    print("\n\n" + "=" * 70)
    print(f"VARIANCE SUMMARY  ({label}, n={args.n} per condition)")
    print("=" * 70)
    header = f"{'metric':<28} {'baseline':<18} {'agent':<18} {'delta':<10}"
    print(header)
    print("-" * len(header))
    metric_labels = {
        "consistency": "consistency (modal agree)",
        "grounded": "grounded accuracy",
        "refusal_iri": "refusal (IRI-based)",
        "refusal_prose": "refusal (prose-rescored)",
    }
    b = summary["conditions"].get("baseline", {})
    a = summary["conditions"].get("agent", {})
    for key, lbl in metric_labels.items():
        bv = b.get(key)
        av = a.get(key)
        b_str = f"{bv['mean']:.3f} ± {bv['sd']:.3f}" if bv else "     (n/a)       "
        a_str = f"{av['mean']:.3f} ± {av['sd']:.3f}" if av else "     (n/a)       "
        if bv and av:
            delta = av["mean"] - bv["mean"]
            d_str = f"{delta:+.3f}"
        else:
            d_str = ""
        print(f"{lbl:<28} {b_str:<18} {a_str:<18} {d_str:<10}")
    print()
    print(f"Full results:   {summary_path}")
    print(f"Raw reports:    {OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
