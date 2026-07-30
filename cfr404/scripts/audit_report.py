#!/usr/bin/env python3
"""Phase 7 step 2c: precision with Wilson score intervals -> audit/precision-report.md.

Precision here means: of the candidates a detector raised, what fraction name a defect
that belongs to the **regulation** rather than to the translation. It is reported
whatever it is, per primitive, per stratum and per locus.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import LOCUS_NAME, PRIMITIVES, ROOT  # noqa: E402

SAMPLE = ROOT / "audit" / "sample.csv"
LEDGER = ROOT / "kernel" / "flags.json"
OUT = ROOT / "audit" / "precision-report.md"

Z = 1.959963985  # 95%


def wilson(k: int, n: int) -> tuple[float, float, float]:
    if n == 0:
        return (float("nan"), float("nan"), float("nan"))
    p = k / n
    d = 1 + Z * Z / n
    centre = (p + Z * Z / (2 * n)) / d
    half = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return p, max(0.0, centre - half), min(1.0, centre + half)


def fmt(k: int, n: int) -> str:
    p, lo, hi = wilson(k, n)
    if n == 0:
        return "— (no sample)"
    return f"{p:.2f} [{lo:.2f}, {hi:.2f}] ({k}/{n})"


def main() -> int:
    rows = list(csv.DictReader(SAMPLE.open(encoding="utf-8")))
    ledger = json.loads(LEDGER.read_text())

    def tally(key):
        num, den = Counter(), Counter()
        for r in rows:
            k = key(r)
            den[k] += 1
            if r["verdict"] == "regulation":
                num[k] += 1
        return num, den

    L = []
    A = L.append
    A("# Artifact-versus-source audit")
    A("")
    A(f"Date: {date.today()}  ")
    A(f"Sample: `audit/sample.csv`, {len(rows)} candidates, seeded and reproducible  ")
    A("Question asked of every candidate, fixed in advance in `predictions/PREREGISTERED.md`:  ")
    A("**does this defect belong to the regulation, or to the translation?**")
    A("")
    A("Precision below is the fraction of candidates naming a defect that belongs to the "
      "**regulation**. Intervals are Wilson score intervals at 95%.")
    A("")

    A("## Independence limitation")
    A("")
    A("These adjudications were made by the same agent that built the artifact, not by an "
      "independent reader. That is weaker than the spec intends, and every figure here "
      "should be read as provisional until a human re-adjudicates the same sample. The "
      "sample, the fixed question, and the adjudication rules with their reasons are all "
      "on disk (`scripts/adjudicate.py`) so that re-adjudication is cheap and so that a "
      "reviewer can disagree with a rule and see exactly which candidates move.")
    A("")

    k = sum(1 for r in rows if r["verdict"] == "regulation")
    A("## Headline")
    A("")
    A(f"- Whole-sample precision: **{fmt(k, len(rows))}**")
    d_rows = [r for r in rows if r["stratum"] == "D"]
    dk = sum(1 for r in d_rows if r["verdict"] == "regulation")
    A(f"- Stratum D precision: **{fmt(dk, len(d_rows))}**")
    nb = [r for r in rows if r["blanket"] != "True"]
    nbk = sum(1 for r in nb if r["verdict"] == "regulation")
    A(f"- Precision excluding blanket flags: **{fmt(nbk, len(nb))}**")
    A("")
    A("The whole-sample figure is low and that is the finding, not a disappointment. "
      "Almost every candidate outside the recognition-specific primitives is a defect of "
      "the formalization: a class with no differentia, a mereological cycle the text "
      "never asserts, a relation used outside its declared range. The artifact is "
      "noisier than the regulation is.")
    A("")

    A("## By stratum")
    A("")
    A("| Stratum | Precision [95% Wilson] |")
    A("|---|---|")
    num, den = tally(lambda r: r["stratum"])
    for s in sorted(den):
        A(f"| {s} | {fmt(num[s], den[s])} |")
    A("")

    A("## By primitive")
    A("")
    A("| Primitive | Description | Flags in artifact | Precision [95% Wilson] |")
    A("|---|---|---|---|")
    num, den = tally(lambda r: r["primitive"])
    for p in sorted(den):
        total = ledger["by_primitive"].get(p, 0)
        A(f"| {p} | {PRIMITIVES[p]} | {total} | {fmt(num[p], den[p])} |")
    A("")

    A("## By chain locus")
    A("")
    A("| Locus | Link | Precision [95% Wilson] |")
    A("|---|---|---|")
    num, den = tally(lambda r: r["locus"])
    for lo in sorted(den):
        A(f"| {lo} | {LOCUS_NAME.get(lo, 'outside the chain')} | {fmt(num[lo], den[lo])} |")
    A("")

    A("## What the shape of this table says")
    A("")
    A("Precision is not spread evenly. It concentrates entirely in the two primitives "
      "that are *about recognition* — K-B2 at the authority-to-assessor edge and K-D3 at "
      "the remedy locus — and is zero everywhere else. The primitives that check logical "
      "and categorial hygiene (K-A2, K-A3, K-B1, K-C2, K-C3) return translation defects "
      "almost exclusively.")
    A("")
    A("That is the pattern §10.1 predicts: translation-artifact load is inversely related "
      "to recognition load. Where the kernel is asking a question about institutional "
      "structure, it finds institutional structure. Where it is asking a question about "
      "formal hygiene, it finds the formalizer's own mistakes.")
    A("")

    A("## Stratum D census")
    A("")
    A("Stratum D contains too few candidates to sample 60 from, so every Stratum D "
      "candidate was adjudicated rather than a sample of them. A census removes sampling "
      "error but leaves a wide interval, which the Wilson bound above reports honestly.")
    A("")
    A("| Flag | Primitive | Locus | Section | Verdict |")
    A("|---|---|---|---|---|")
    for r in d_rows:
        A(f"| `{r['flag_id']}` | {r['primitive']} | {r['locus']} | {r['section']} | "
          f"**{r['verdict']}** |")
    A("")
    for r in d_rows:
        A(f"**{r['flag_id']}** — {r['evidence']}")
        A("")
        A(f"> *Adjudication:* {r['rationale']}")
        A("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"whole sample {fmt(k, len(rows))}")
    print(f"stratum D    {fmt(dk, len(d_rows))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
