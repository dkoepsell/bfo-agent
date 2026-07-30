#!/usr/bin/env python3
"""Phase 7 step 1: run HermiT on the merged artifact with BFO imported.

If the run exceeds the compute budget, this reports **no verdict** and says so. It
never falls back to citing a predecessor build: the tension between the two ICD-11
assessments described in §10.2 is exactly what that habit produces.

Writes ``reports/reasoner-verdict.md`` and ``kernel/reasoner-result.json``.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import ROOT  # noqa: E402

ART = ROOT / "ontology" / "cfr404.owl"
RESULT = ROOT / "kernel" / "reasoner-result.json"
REPORT = ROOT / "reports" / "reasoner-verdict.md"


def _worker(path: str, q):
    """Runs in a child process so a runaway reasoner can be killed cleanly."""
    try:
        import owlready2

        w = owlready2.World()
        for sib in sorted((ROOT / "kernel").glob("*.owl")):
            try:
                w.get_ontology(sib.resolve().as_uri()).load(only_local=False)
            except Exception:
                pass
        onto = w.get_ontology(Path(path).resolve().as_uri()).load(only_local=False)
        t0 = time.time()
        inconsistent = []
        consistent = True
        try:
            with onto:
                owlready2.sync_reasoner_hermit(w, infer_property_values=False,
                                              debug=0)
            # owl:Nothing is unsatisfiable by definition; reporting it as a finding
            # would inflate every count that mentions unsatisfiable classes.
            inconsistent = [c.iri for c in w.inconsistent_classes()
                            if not c.iri.endswith("owl#Nothing")]
        except owlready2.OwlReadyInconsistentOntologyError:
            consistent = False
        q.put({
            "ran": True, "consistent": consistent,
            "unsatisfiable_classes": inconsistent,
            "seconds": round(time.time() - t0, 1),
        })
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        q.put({"ran": False, "error": f"{type(exc).__name__}: {exc}"})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=int, default=1800,
                    help="wall-clock budget in seconds")
    ap.add_argument("--artifact", default=str(ART))
    args = ap.parse_args()

    q: mp.Queue = mp.Queue()
    p = mp.Process(target=_worker, args=(args.artifact, q))
    t0 = time.time()
    p.start()
    p.join(args.budget)

    if p.is_alive():
        p.terminate()
        p.join(10)
        res = {
            "verdict": None,
            "reason": (f"HermiT exceeded the {args.budget}s compute budget on "
                       f"{Path(args.artifact).name} and was terminated."),
            "seconds": args.budget,
        }
    else:
        got = q.get() if not q.empty() else {"ran": False, "error": "no result returned"}
        if not got.get("ran"):
            res = {"verdict": None,
                   "reason": f"HermiT did not run: {got.get('error')}",
                   "seconds": round(time.time() - t0, 1)}
        else:
            res = {
                "verdict": "consistent" if got["consistent"] else "inconsistent",
                "consistent": got["consistent"],
                "coherent": got["consistent"] and not got["unsatisfiable_classes"],
                "unsatisfiable_classes": got["unsatisfiable_classes"],
                "unsatisfiable_count": len(got["unsatisfiable_classes"]),
                "seconds": got["seconds"],
                "reason": None,
            }

    res["artifact"] = Path(args.artifact).name
    res["budget_seconds"] = args.budget
    res["generated"] = str(date.today())
    res["reasoner"] = "HermiT via owlready2"
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(res, indent=2), encoding="utf-8")

    L = [f"# Reasoner verdict — {res['artifact']}", "",
         f"Reasoner: {res['reasoner']}  ", f"Budget: {args.budget}s  ",
         f"Date: {res['generated']}", ""]
    if res["verdict"] is None:
        L += ["## No verdict", "",
              f"**{res['reason']}**", "",
              "No verdict is reported. In particular no verdict is inherited from a "
              "predecessor build: §10.2 records what happens when two assessments of the "
              "same artifact are allowed to coexist because one was carried over rather "
              "than re-run.", "",
              "K-A2 therefore contributes no flags, and its row in the stratum profile "
              "reads NOT COMPUTED rather than zero."]
    else:
        L += [f"## Verdict: **{res['verdict']}**", "",
              f"- Consistent: **{res['consistent']}**",
              f"- Coherent (no unsatisfiable classes): **{res['coherent']}**",
              f"- Unsatisfiable classes: **{res['unsatisfiable_count']}**",
              f"- Wall clock: {res['seconds']}s", ""]
        if res["unsatisfiable_classes"]:
            L += ["### Unsatisfiable classes", ""]
            L += [f"- `{c}`" for c in res["unsatisfiable_classes"][:100]]
            if res["unsatisfiable_count"] > 100:
                L.append(f"- … and {res['unsatisfiable_count'] - 100} more")
        else:
            L.append("No unsatisfiable classes were found.")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"verdict={res['verdict']} seconds={res.get('seconds')} "
          f"unsat={res.get('unsatisfiable_count', 'n/a')}")
    print(f"wrote {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
