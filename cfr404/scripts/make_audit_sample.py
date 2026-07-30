#!/usr/bin/env python3
"""Phase 7 step 2: draw the artifact-versus-source audit sample.

The spec asks for at least 60 Stratum D flags stratified across primitives and loci.
Stratum D does not contain 60 candidates in this artifact, so two things are done
instead of one, and the shortfall is reported rather than papered over:

* **Stratum D census** - every Stratum D candidate is adjudicated, not a sample of
  them. A census is strictly stronger than a sample; it is only weaker in that the
  interval around it is wide, which the Wilson interval already expresses.
* **Whole-ledger sample** - at least 60 candidates drawn across all strata, stratified
  by primitive and by locus, so that precision can also be reported for the artifact
  as a whole.

Sampling is seeded and therefore reproducible.

Writes ``audit/sample.csv`` with the verdict column blank, ready for adjudication.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import ROOT  # noqa: E402

LEDGER = ROOT / "kernel" / "flags.json"
CHUNKS = ROOT / "corpus" / "chunks"
OUT = ROOT / "audit" / "sample.csv"

FIELDS = ["flag_id", "primitive", "stratum", "locus", "edge", "term_label", "section",
          "blanket", "in_stratum_d_census", "evidence", "section_heading",
          "section_excerpt", "verdict", "rationale"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=20260730)
    args = ap.parse_args()

    ledger = json.loads(LEDGER.read_text())
    flags = ledger["flags"]
    for i, f in enumerate(flags):
        f["flag_id"] = f"F{i:05d}"

    chunks = {}
    for p in CHUNKS.glob("*.json"):
        c = json.loads(p.read_text())
        chunks[c["chunk_id"]] = c

    rng = random.Random(args.seed)

    stratum_d = [f for f in flags if f["stratum"] == "D"]

    # Stratified over (primitive, locus) so no single shape can dominate the sample.
    strata = defaultdict(list)
    for f in flags:
        strata[(f["primitive"], f["locus"])].append(f)

    picked = {f["flag_id"] for f in stratum_d}
    sample = list(stratum_d)

    keys = sorted(strata)
    # one per cell first, then round-robin until the target is met
    for k in keys:
        pool = [f for f in strata[k] if f["flag_id"] not in picked]
        if pool:
            f = rng.choice(pool)
            picked.add(f["flag_id"])
            sample.append(f)
    while len(sample) < args.n:
        rng.shuffle(keys)
        added = False
        for k in keys:
            pool = [f for f in strata[k] if f["flag_id"] not in picked]
            if not pool:
                continue
            f = rng.choice(pool)
            picked.add(f["flag_id"])
            sample.append(f)
            added = True
            if len(sample) >= args.n:
                break
        if not added:
            break

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for f in sorted(sample, key=lambda x: (x["stratum"], x["primitive"], x["flag_id"])):
            ch = chunks.get(f["section"], {})
            w.writerow({
                "flag_id": f["flag_id"], "primitive": f["primitive"],
                "stratum": f["stratum"], "locus": f["locus"], "edge": f["edge"] or "",
                "term_label": f["term_label"], "section": f["section"],
                "blanket": f["blanket"],
                "in_stratum_d_census": f["stratum"] == "D",
                "evidence": f["evidence"],
                "section_heading": ch.get("heading", ""),
                "section_excerpt": " ".join(ch.get("text", "").split())[:700],
                "verdict": "", "rationale": "",
            })

    print(f"wrote {OUT.relative_to(ROOT)}: {len(sample)} rows "
          f"({len(stratum_d)} Stratum D census + {len(sample) - len(stratum_d)} sampled)")
    print(f"cells covered: {len({(f['primitive'], f['locus']) for f in sample})} "
          f"of {len(strata)}")
    if len(stratum_d) < args.n:
        print(f"NOTE: Stratum D contains {len(stratum_d)} candidates, fewer than the "
              f"{args.n} the spec asks for. All of them are in the sample; the shortfall "
              f"is reported in the precision report.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
