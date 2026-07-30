# cfr404 — a Stratum D control built from 20 CFR Part 404

An act-thick, repair-thick legal ontology built to test whether Stratum D of the
contradiction kernel is non-empty in a legal corpus. *The Recognition Layer* §13 names
that run as the single experiment on which the general claim of §5 stands or falls.

Built to `spec/SPEC-cfr404-stratum-d.md`.

**License:** CC-BY 4.0, consistent with `icd11bfo.owl` and `dsm_icd_crosswalk.owl`.
The underlying regulation is public domain under the edicts-of-government doctrine.

---

## What this is not

**This is a research-grade formalization of part of 20 CFR Part 404.
It is not the regulation.** It is not legal advice. It must not be used to determine anyone's
entitlement to benefits, and it should not be relied on by anyone making or contesting
a disability determination. Where the artifact and the CFR disagree, the CFR is right
and the artifact is wrong.

It also does not cover Part 404. Four of twenty-two subparts were acquired, chosen to
cover the recognition chain. Every density reported here is a density over that
selection.

---

## Headline results

| | |
|---|---|
| Named classes | 2,494 |
| Continuant/occurrent straddles | **0** |
| Classes with no BFO ancestor | **0** |
| HermiT verdict | **consistent**, 2 unsatisfiable classes, ~9 s |
| Kernel primitives implemented | **12 of 12** |
| Kernel primitives computed | 11 of 12 (K-D1 needs external data) |
| Stratum D | **non-empty** — K-D3 fires at L7 |
| Stratum D precision (census, n=2) | **1.00**, 95% Wilson [0.34, 1.00] |
| Whole-sample precision (n=60) | 0.05, 95% Wilson [0.02, 0.14] |

The two results the experiment was built to test:

- **K-B2 fires at the L1→L3 edge.** 404.1546 assigns one and the same residual
  functional capacity assessment to seven assessor roles whose authority traces to three
  distinct grants — a State agency acting under delegation, SSA officers, and the Appeals
  Council acting under its own review authority. Same function, unequal grants.
- **K-D3 fires at L7.** 404.957(c)(1) directs dismissal where a prior determination on
  the same facts has become final; 404.988 makes that same finality the precondition for
  reopening it. One provision treats finality as the reason to refuse review, the other
  as the occasion for it.

Both survived hand adjudication as defects of the regulation rather than of the
translation. See `reports/predictions-outcome.md` for all seven pre-registered
predictions scored, including the one that did not hold.

### The result that did not go as predicted

P6 predicted that strata A, B and C would all fire, putting the corpus in the full
four-stratum row of Table 8. They all raised candidates, but under hand audit **only
B and D contain a defect belonging to the regulation**. Strata A and C fire almost
entirely on translation artifacts: unsatisfiable classes created by misapplied
relations, classes with no differentia, mereological cycles the text never asserts.

That is worth more than a clean sweep would have been. It says the kernel's lower
strata, applied to this corpus, largely measure the formalizer, and it is the pattern
§10.1 predicts — translation-artifact load running inversely to recognition load.

---

## Layout

```
corpus/       eCFR XML as downloaded, section-level chunks, manifest with SHA-256
ontology/     cfr404-base.owl (repaired baseline) + cfr404-chain.owl (hand-authored
              scaffold) + cfr404-adjudication.owl (extracted) -> cfr404.owl (release)
kernel/       kernel-vocab.owl, detectors/ (one module per primitive), flags.json ledger
data/         world-facing data for K-D1
audit/        sample.csv, precision-report.md
reports/      table8, table13, reasoner verdict, known defects, predictions outcome,
              extraction arm comparison
predictions/  PREREGISTERED.md, committed before the first kernel run
scripts/      the pipeline
```

`ontology/cfr404.owl` is the release artifact. Kernel flags are deliberately **not** in
it: a candidate is a finding *about* the artifact, not content *of* it, and it must not
enter the class census.

`corpus/raw/*.xml` is gitignored. It is reproducible from `corpus/manifest.json`, which
pins the exact bytes by SHA-256.

---

## Reproducing

```bash
python scripts/fetch_corpus.py          # eCFR, endpoints discovered at run time
python scripts/repair_baseline.py       # Phase 1
python scripts/build_kernel_vocab.py    # Phase 2 vocabulary
python scripts/backfill_provenance.py   # Phase 2 backfill
python scripts/build_chain.py           # Phase 4 scaffold (anchors verified)
python scripts/extract.py               # Phase 5
python scripts/build.py                 # merge -> cfr404.owl
python scripts/run_reasoner.py          # Phase 7 HermiT
python scripts/run_kernel.py            # Phase 6 detectors
python scripts/make_audit_sample.py && python scripts/adjudicate.py
python scripts/audit_report.py
python scripts/report.py                # Phase 8, all tables
python scripts/validate.py ontology/cfr404.owl --strict
```

`scripts/validate.py --strict` exits non-zero on any straddle, any class without a BFO
ancestor, any bare blank-node type, or a file owlready2 cannot parse.

---

## Design decisions that shaped the result

**Authority is separated from assessor.** The baseline collapsed both into
`DeterminingAgencyRole`. `cfr:AuthorityRole` (L1) and `cfr:AssessorRole` (L3) are
asserted disjoint, and the merged term is deprecated. Without this split K-B2 is
undetectable *by construction* — an occupant could never be assigned a function
exceeding its grant, because grant and function would be the same entity.

**The chain scaffold is hand-authored, and authored before extraction.** Letting an
extractor invent the loci would make locus attribution circular. Every one of the 54
scaffold terms is anchored to a verbatim phrase in a named section, and the build
*refuses to emit* a term whose anchor cannot be found in the corpus.

**Locus comes from the section-to-link mapping, not from branch position.** §13's
concession about the ICD-11 gradient traces directly to inferring strata from where the
extractor put a class. Here the Phase 3 mapping fixes which links a section serves and
the head noun selects within that set. Where a term's kind is not one the section
serves, the term's kind wins — forcing it into the section's first link put terms at
loci their BFO grounding contradicted and manufactured 270 spurious K-B3 candidates in
an early build.

**No class is minted from a modal.** Legal text is saturated with "shall", "must" and
"may". An extractor that mints an obligation class per modal manufactures institutional
structure out of grammar. The guard suppressed 967 candidate phrases.

**Provenance is two-tier and reported honestly.** 46% of baseline classes trace to a
section on their full label; 43% only through a sub-phrase; 10% have no verbatim basis
in the corpus at all. That last figure is a measurement, not a matcher failure — terms
like `Ascites Realization Process` are scaffolding the earlier extractor minted, and
20 CFR Part 404 does not contain them.

---

## Deviations from the spec

Both are recorded in full in `reports/known-defects.md`.

1. **Extraction backend.** §3 and §15 require extraction on the DGX via Ollama. The DGX
   was unreachable from the build host and no local Ollama was available. A
   deterministic, seedless rule-based extractor was written instead; it satisfies the
   underlying requirement — reproducibility from local components — more strongly than a
   model would, having no seed, no temperature and no provider. The Ollama backend is
   implemented and runs with `--backend ollama`.

2. **A hosted-API comparison arm was run** at the owner's explicit request
   (`reports/extraction-arm-comparison.md`). It is labelled, kept separate, and its
   classes are **not** in the release artifact, because §3 forbids hosted APIs for corpus
   extraction. The comparison is itself informative: the two arms agree on very little
   (Jaccard 0.055), and the hosted arm proposed 46 terms whose wording does not appear in
   the section it was given — an error a rule-based extractor cannot make.

**K-D1 is not computed.** It is a claim about the world, not about the text, and needs
SSA's published ALJ disposition statistics. `ssa.gov` returned HTTP 403 to every request
from the build host. `scripts/fetch_ssa_outcomes.py` records each attempt, and the
detector reports NOT COMPUTED rather than asserting a structure-to-practice gap without
the measured variance. That is a third thing, distinct from both a finding and a null.

---

## Known defects

`reports/known-defects.md` is the §10.2 analogue: corpus coverage, provenance coverage,
the coverage cap the term-of-art test imposes, every repair applied to the baseline,
every merge-time collision repaired, the residual unsatisfiable classes left in place
deliberately because they are what K-A2 detects, and the blanket-flag accounting.

Two things worth pulling out here:

- **The audit is not independent.** Adjudication was performed by the agent that built
  the artifact. Precision figures are provisional. The sample, the fixed question and the
  adjudication rules are on disk so a human can redo it and see which candidates move.
- **The baseline uses BFO 2.0 relation IRIs** (`BFO_0000196`, `BFO_0000197`) that BFO
  2020 does not declare. They are declared locally with BFO-consistent domains and ranges
  so the artifact is self-contained, but the vintage mismatch is real and is not silently
  rewritten.
