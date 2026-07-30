#!/usr/bin/env python3
"""Phase 7 step 2b: record the artifact-versus-source adjudication.

The question, fixed in advance in PREREGISTERED.md and asked of every sampled flag:

    does this defect belong to the regulation, or to the translation?

Verdicts are recorded here as explicit rules rather than as 60 loose opinions, so a
reviewer can disagree with a *rule* and see immediately which flags move. Each rule
states its reason. Rules are applied in order; the first that matches wins.

**Independence limitation, stated plainly:** these adjudications were made by the same
agent that built the artifact, not by an independent reader. That is weaker than the
spec intends and the precision figures below should be read as provisional until a
human adjudicates the same sample. The sample, the question and the rules are all on
disk precisely so that re-adjudication is cheap.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import ROOT  # noqa: E402

SAMPLE = ROOT / "audit" / "sample.csv"

REGULATION = "regulation"
TRANSLATION = "translation"

# (predicate over the row) -> (verdict, rationale)
RULES = [
    (lambda r: r["primitive"] == "K-B2",
     (REGULATION,
      "404.1546 really does assign responsibility for one and the same residual "
      "functional capacity assessment to State agency medical and psychological "
      "consultants, to a disability examiner alone, to a disability hearing officer, to "
      "the Associate Commissioner or a delegate, to an administrative law judge and to "
      "an administrative appeals judge. Those occupants do not hold one grant: the State "
      "agency acts under a delegation from the Commissioner (404.1503), the Appeals "
      "Council under its own review authority (404.967). The regulation, not the "
      "translation, assigns one function across unequal grants of authority.")),

    (lambda r: r["primitive"] == "K-D3",
     (REGULATION,
      "404.957(c)(1) directs dismissal of a request for a hearing where a previous "
      "determination on the same facts and issues has become final, while 404.988 makes "
      "that same finality the precondition for reopening the determination. One "
      "provision treats finality as the reason to refuse further review and the other "
      "treats it as the occasion for it. Both texts are in the corpus and neither is an "
      "artefact of how they were formalized.")),

    (lambda r: r["primitive"] == "K-A2",
     (TRANSLATION,
      "The class is unsatisfiable because the extraction applied 'is concretized by' to "
      "a filler that is not the kind of entity that relation accepts. 20 CFR Part 404 "
      "says nothing that could make a cardiac laboratory finding impossible; the "
      "impossibility is manufactured by the formalization.")),

    (lambda r: r["primitive"] == "K-B1",
     (TRANSLATION,
      "A mereological cycle - the brain a part of its own bulbar region, mental "
      "functioning a part of itself - is not something Appendix 1 states. It is an "
      "artefact of the extractor asserting 'has part' in both directions.")),

    (lambda r: r["primitive"] == "K-C2",
     (TRANSLATION,
      "The regulation describes organs, orthoses and anatomical abnormalities; it does "
      "not claim that a heart is realized in cardiac pumping. Confusing a bearer with "
      "the realizable it bears is a modelling error introduced in translation. What the "
      "text supports is that the heart has a function which is realized in pumping.")),

    (lambda r: r["primitive"] == "K-C3",
     (TRANSLATION,
      "A restriction used outside the domain or range its own property declares is a "
      "defect of the formalization. It is compounded here by the baseline's use of the "
      "BFO 2.0 relation IRIs BFO_0000196 and BFO_0000197, which BFO 2020 does not "
      "declare at all.")),

    (lambda r: r["primitive"] == "K-A3" and r["term_label"] in {
        "institutional criterion", "institutional effect", "recognition act",
        "remedy process", "presented fact", "assessor role", "authority role"},
     (TRANSLATION,
      "An abstract spine class authored by this build in Phase 4. It carries a BFO "
      "parent and no differentia because its job is to hold a branch together. That is a "
      "design decision of the artifact, so the vacuity belongs to the translation.")),

    (lambda r: r["primitive"] == "K-A3" and r["section"] == "UNATTRIBUTED",
     (TRANSLATION,
      "A bare class with no differentia and no trace to any section of the acquired "
      "corpus. Nothing in the regulation corresponds to it, so the defect cannot belong "
      "to the regulation.")),

    (lambda r: r["primitive"] == "K-A3",
     (TRANSLATION,
      "The regulation names this entity - a State education agency, a licensing board, a "
      "court - and says what it does in prose. The artifact records only the name under "
      "a BFO root, with no differentia, no restriction and no disjointness. The emptiness "
      "is the formalization's, not the text's.")),
]


def main() -> int:
    rows = list(csv.DictReader(SAMPLE.open(encoding="utf-8")))
    if not rows:
        print("no sample rows", file=sys.stderr)
        return 1

    counts = {REGULATION: 0, TRANSLATION: 0, "unclassified": 0}
    for r in rows:
        for pred, (verdict, rationale) in RULES:
            if pred(r):
                r["verdict"] = verdict
                r["rationale"] = rationale
                counts[verdict] += 1
                break
        else:
            r["verdict"] = ""
            r["rationale"] = "no rule matched; requires individual adjudication"
            counts["unclassified"] += 1

    with SAMPLE.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"adjudicated {len(rows)} rows: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0 if counts["unclassified"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
