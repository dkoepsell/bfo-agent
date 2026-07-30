#!/usr/bin/env python3
"""Compare the deterministic extraction arm against the hosted-API arm.

The rules arm is the artifact's provenance; the hosted arm exists only to answer
"what would a hosted model have done differently?". Writes
``reports/extraction-arm-comparison.md``.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import CFR, ROOT, load, local  # noqa: E402
from rdflib.namespace import RDFS  # noqa: E402

RULES = ROOT / "ontology" / "cfr404-adjudication.owl"
HOSTED = ROOT / "ontology" / "cfr404-adjudication-anthropic.owl"
OUT = ROOT / "reports" / "extraction-arm-comparison.md"


def summarize(path: Path) -> dict:
    g = load(path)
    terms = {}
    for c in set(g.subjects(None, None)):
        if not str(c).startswith(str(CFR)):
            continue
        labels = list(g.objects(c, RDFS.label))
        if not labels:
            continue
        terms[local(c)] = {
            "label": str(labels[0]),
            "locus": next((str(x) for x in g.objects(c, CFR.chainLocus)), None),
            "section": next((str(x) for x in g.objects(c, CFR.sourceSection)), None),
        }
    return terms


def pct(n, d):
    return f"{100 * n / d:.1f}%" if d else "—"


def main() -> int:
    if not HOSTED.exists():
        print("hosted arm not present; run: scripts/extract.py --backend anthropic",
              file=sys.stderr)
        return 1

    r = summarize(RULES)
    h = summarize(HOSTED)
    rm = json.loads((ROOT / "ontology" / "phase5-extraction-manifest.json").read_text())
    hm = json.loads(
        (ROOT / "ontology" / "phase5-extraction-manifest-anthropic.json").read_text())
    hb = hm["backend_meta"]

    both = set(r) & set(h)
    only_r = set(r) - set(h)
    only_h = set(h) - set(r)
    jaccard = len(both) / len(set(r) | set(h))

    # Where the two arms both minted a term, did they agree on its locus?
    agree = sum(1 for k in both if r[k]["locus"] == h[k]["locus"])

    rl = Counter(v["locus"] for v in r.values())
    hl = Counter(v["locus"] for v in h.values())

    L = []
    A = L.append
    A("# Extraction arm comparison")
    A("")
    A("Two extractors were run over the same 305-chunk corpus.")
    A("")
    A("- **`rules`** — deterministic, seedless, no model. **This arm is the provenance "
      "of `ontology/cfr404.owl`.** Spec §3 and §15 require extraction to run on local "
      "components so the artifact is reproducible; a rule set satisfies that more "
      "strongly than any model, local or hosted.")
    A(f"- **`anthropic`** — hosted API, `{hb['model']}`, temperature 0. Run at the "
      "owner's explicit request as a labelled comparison arm. Its classes are written "
      "to `ontology/cfr404-adjudication-anthropic.owl` and **never enter the release "
      "artifact**, because spec §3 forbids hosted APIs for corpus extraction.")
    A("")
    A("## Yield")
    A("")
    A("| | rules | anthropic |")
    A("|---|---|---|")
    A(f"| Classes minted | {len(r):,} | {len(h):,} |")
    A(f"| Sections processed | {rm['corpus_chunks']} | {hb['sections_sent']} |")
    A(f"| Sections truncated | 0 | {hb['sections_truncated']} |")
    A(f"| Input tokens | 0 | {hb['input_tokens']:,} |")
    A(f"| Output tokens | 0 | {hb['output_tokens']:,} |")
    A("")
    A("## Agreement")
    A("")
    A(f"- Terms minted by both arms: **{len(both):,}**")
    A(f"- Only `rules`: {len(only_r):,}")
    A(f"- Only `anthropic`: {len(only_h):,}")
    A(f"- Jaccard similarity: **{jaccard:.3f}**")
    A(f"- Of the {len(both):,} shared terms, the two arms assigned the same chain locus "
      f"for **{agree:,}** ({pct(agree, len(both))}).")
    A("")
    A("Locus agreement is high because both arms take the locus from the same Phase 3 "
      "section-to-link mapping. That is by design: the mapping, not the extractor, is "
      "what fixes locus, which is the whole point of authoring the chain before "
      "extracting.")
    A("")
    A("## Locus distribution")
    A("")
    A("| Locus | rules | anthropic |")
    A("|---|---|---|")
    for k in sorted(set(rl) | set(hl)):
        A(f"| {k} | {rl.get(k, 0)} | {hl.get(k, 0)} |")
    A("")
    A("## What the hosted arm did that the rules arm cannot")
    A("")
    A(f"- It proposed **{hb['rejected_not_verbatim_in_section']}** terms whose label does "
      "not occur verbatim in the section it was given. These were rejected by the same "
      "guard both arms run under. A rule-based extractor cannot make this error at all: "
      "it only ever copies spans out of the text. This is the single clearest reason the "
      "spec's reproducibility constraint is not merely bureaucratic.")
    A(f"- It emitted **{hb['rejected_deontic']}** terms that tripped the deontic-inflation "
      "guard despite the prompt forbidding them.")
    A(f"- **{hb['sections_truncated']}** long sections had to be truncated to fit the "
      "context budget, so the hosted arm did not see all of the corpus. The rules arm "
      "read every character of every chunk.")
    A("")
    A("## What the rules arm did that the hosted arm cannot")
    A("")
    A(f"- It minted {len(only_r):,} terms the hosted arm missed, largely because it applies "
      "its term-of-art test over corpus-wide occurrence counts. A per-section model has no "
      "view of how often a phrase recurs elsewhere.")
    A("- It is exactly reproducible: same corpus hashes in, same classes out, no seed, no "
      "temperature, no provider. Re-running the hosted arm can return a different set.")
    A("")
    A("## Reading this comparison honestly")
    A("")
    A("This is not a quality ranking. The hosted arm surfaced institutional terms in "
      f"prose the rules arm's head-noun list does not cover ({len(only_h):,} of them), and "
      "some are good. The claim being made is narrower: the release artifact needs a "
      "provenance that a reader can re-derive from the corpus alone, and only the "
      "deterministic arm provides one. The hosted arm is reported, kept on disk, and "
      "excluded from the artifact.")
    A("")
    A("### Terms only the hosted arm found (first 30)")
    A("")
    for k in sorted(only_h)[:30]:
        A(f"- `{h[k]['label']}` ({h[k]['locus']}, {h[k]['section']})")
    A("")
    A("### Terms only the rules arm found (first 30)")
    A("")
    for k in sorted(only_r)[:30]:
        A(f"- `{r[k]['label']}` ({r[k]['locus']}, {r[k]['section']})")
    A("")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
    print(f"rules {len(r)}, anthropic {len(h)}, shared {len(both)}, jaccard {jaccard:.3f}, "
          f"locus agreement {pct(agree, len(both))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
