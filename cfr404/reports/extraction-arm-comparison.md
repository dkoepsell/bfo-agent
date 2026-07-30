# Extraction arm comparison

Two extractors were run over the same 305-chunk corpus.

- **`rules`** — deterministic, seedless, no model. **This arm is the provenance of `ontology/cfr404.owl`.** Spec §3 and §15 require extraction to run on local components so the artifact is reproducible; a rule set satisfies that more strongly than any model, local or hosted.
- **`anthropic`** — hosted API, `claude-haiku-4-5-20251001`, temperature 0. Run at the owner's explicit request as a labelled comparison arm. Its classes are written to `ontology/cfr404-adjudication-anthropic.owl` and **never enter the release artifact**, because spec §3 forbids hosted APIs for corpus extraction.

## Yield

| | rules | anthropic |
|---|---|---|
| Classes minted | 1,378 | 924 |
| Sections processed | 305 | 301 |
| Sections truncated | 0 | 33 |
| Input tokens | 0 | 335,197 |
| Output tokens | 0 | 100,491 |

## Agreement

- Terms minted by both arms: **121**
- Only `rules`: 1,257
- Only `anthropic`: 803
- Jaccard similarity: **0.055**
- Of the 121 shared terms, the two arms assigned the same chain locus for **82** (67.8%).

Locus agreement is high because both arms take the locus from the same Phase 3 section-to-link mapping. That is by design: the mapping, not the extractor, is what fixes locus, which is the whole point of authoring the chain before extracting.

## Locus distribution

| Locus | rules | anthropic |
|---|---|---|
| L0 | 10 | 46 |
| L1 | 70 | 95 |
| L2 | 810 | 283 |
| L3 | 6 | 31 |
| L4 | 125 | 195 |
| L5 | 168 | 154 |
| L6 | 70 | 39 |
| L7 | 119 | 81 |

## What the hosted arm did that the rules arm cannot

- It proposed **46** terms whose label does not occur verbatim in the section it was given. These were rejected by the same guard both arms run under. A rule-based extractor cannot make this error at all: it only ever copies spans out of the text. This is the single clearest reason the spec's reproducibility constraint is not merely bureaucratic.
- It emitted **5** terms that tripped the deontic-inflation guard despite the prompt forbidding them.
- **33** long sections had to be truncated to fit the context budget, so the hosted arm did not see all of the corpus. The rules arm read every character of every chunk.

## What the rules arm did that the hosted arm cannot

- It minted 1,257 terms the hosted arm missed, largely because it applies its term-of-art test over corpus-wide occurrence counts. A per-section model has no view of how often a phrase recurs elsewhere.
- It is exactly reproducible: same corpus hashes in, same classes out, no seed, no temperature, no provider. Re-running the hosted arm can return a different set.

## Reading this comparison honestly

This is not a quality ranking. The hosted arm surfaced institutional terms in prose the rules arm's head-noun list does not cover (803 of them), and some are good. The claim being made is narrower: the release artifact needs a provenance that a reader can re-derive from the corpus alone, and only the deterministic arm provides one. The hosted arm is reported, kept on disk, and excluded from the artifact.

### Terms only the hosted arm found (first 30)

- `ABG test` (L2, App1-3.00)
- `ability to adjust to other work` (L2, 404.1563)
- `Abnormality of a major joint` (L2, App1-1.01)
- `absence seizures` (L2, App1-111.00)
- `abuse of discretion` (L5, 404.918)
- `Acceptable medical source` (L2, 404.1502)
- `acceptable reason` (L2, 404.1530)
- `Acquiescence Ruling` (L5, 404.903)
- `action in Federal district court` (L7, 404.981)
- `acute congestive heart failure` (L2, App1-4.01)
- `acute extended physician intervention` (L2, App1-4.01)
- `adjudicatory unit` (L5, 404.915)
- `adjustment period` (L1, 404.1670)
- `administrative determinations or decisions` (L5, 404.985)
- `administrative law judge hearing decision` (L4, 404.1512)
- `administrative law judge's decision` (L5, 404.925)
- `administrative medical findings` (L4, 404.1513a)
- `administrative review process` (L4, 404.1512)
- `adopted child` (L0, 404.733)
- `adopting parent` (L0, 404.733)
- `advanced age` (L2, 404.1562)
- `advances in medical therapy` (L2, 404.1579)
- `adverse determination or decision` (L4, 404.703)
- `age categories` (L2, 404.1563)
- `agency or department of the United States` (L4, 404.721)
- `agreement` (L5, 404.926)
- `Allowance of a period of disability` (L4, 404.1519o)
- `amended notice of hearing` (L5, 404.938)
- `American College of Rheumatology` (L2, App1-114.00)
- `Amputation` (L2, App1-1.01)

### Terms only the rules arm found (first 30)

- `ABG report` (L2, App1-105.00)
- `a complete medical history` (L7, 404.1589)
- `a continuing disability review` (L7, 404.1590)
- `a digestive disorders listing` (L2, App1-105.00)
- `a fully favorable decision` (L5, 404.942)
- `a fully favorable determination` (L2, App1-3.00)
- `A longitudinal clinical record` (L2, App1-104.00)
- `a longitudinal medical record` (L2, App1-1.00)
- `a prior favorable determination` (L5, 404.1579)
- `a respiratory disorders listing` (L2, App1-103.00)
- `a straight-leg raising test` (L2, App1-1.00)
- `a written reconsidered determination` (L5, 404.916)
- `abnormal findings` (L2, App1-100.00)
- `abnormal laboratory findings` (L2, App1-112.00)
- `abnormally elevated calcium levels` (L2, App1-109.00)
- `abnormally high level` (L2, App1-109.00)
- `abnormally low level` (L2, App1-109.00)
- `absence evidence` (L4, 404.1579)
- `Absence of longitudinal evidence` (L2, App1-112.00)
- `accept a persuasive report` (L2, App1-114.00)
- `accept report` (L2, App1-1.00)
- `accept statement` (L2, App1-106.00)
- `acceptable automated static threshold` (L2, App1-102.00)
- `acceptable documentation` (L2, App1-114.00)
- `acceptable test` (L2, App1-3.00)
- `Acceptable tests` (L2, App1-102.00)
- `accordance definitions` (L2, App2-all)
- `accordance rules` (L2, 404.1579)
- `accordance standards` (L4, 404.1519s)
- `according rules` (L4, 404.1513a)
