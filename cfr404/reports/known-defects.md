# Known-defect inventory

Date: 2026-07-30

The §10.2 analogue for this artifact. Every construction decision, blanket flag, coverage gap and known error, written down so that a reader does not have to reverse-engineer them from the OWL.

## 1. Corpus coverage

- Source: eCFR versioner API, Title 20 Part 404, currency date **2026-07-28**.
- Subparts acquired: H, J, P, Q plus appendices 1 and 2. **The other 17 subparts of Part 404 are not in the corpus.**
- 305 section-level chunks.
- Consequence: any claim about Part 404 as a whole is out of scope. The corpus was selected to cover the recognition chain, and a locus density computed here is a density over the adjudicative and criteria machinery, not over the Part.

## 2. Provenance coverage

- 902 of 1008 baseline classes trace to a section (89.5%).
- Only 590 (58.5%) trace on the **full class label**. 312 trace only through a sub-phrase, and 106 have no verbatim basis in the corpus at all.
- The `UNATTRIBUTED` sentinel is not a section. Any count grouped by section must exclude it.
- Consequence: roughly a tenth of the baseline vocabulary is the earlier extractor's coinage rather than the regulation's wording. Those classes carry `approved false`.

## 3. Extraction method

- Backend: **rules**, ruleset `cfr404-rules-1.0`, deterministic and seedless.
- **Deviation from the spec:** §3 and §15 require extraction to run on the DGX via Ollama. The DGX was unreachable from the build host and no local Ollama was available, so a deterministic rule-based extractor was written instead. It satisfies the underlying requirement — reproducibility from local components — more strongly than any model would, since it has no seed, no temperature and no provider. The Ollama backend is implemented and can be run with `--backend ollama`.
- A hosted-API arm was run at the owner's explicit request as a labelled comparison (`reports/extraction-arm-comparison.md`). Its classes are **not** in this artifact.
- Coverage cap: term-of-art test: kept if the phrase heads a section, occurs >= 2 times, recurs across >= 2 sections, or is a multi-word phrase of >= 12 characters; max phrase length 4 words. 44 candidate phrases were dropped by that test and are listed in the manifest. Coverage is bounded, not exhaustive.
- Deontic guard suppressed 967 candidate phrases containing a modal or a bare deontic noun.

## 4. Branch and locus assignment

- `cfr:chainLocus` comes from the Phase 3 section-to-link mapping, which fixes the set of links a section serves; the head noun selects within that set.
- **Where a term's kind is not one the section serves, the term's kind wins.** Forcing it into the section's first link would place it at a locus its BFO grounding contradicts, manufacturing K-B3 straddles out of the pipeline. Early builds did exactly that and produced 270 spurious K-B3 candidates.
- Locus precedence on merge: chain > adjudication > base.

## 5. Repairs applied to the baseline

- **namespace rewrite**.
- **delete SimpleMessage**: 11 triples removed; host axioms dropped from ['Reading', 'Writing'].
- **ground Exertional Limitation**.
- **resolve UCTD anonymous parent**.
- **correct mis-declared relation domains and ranges**: [{'property': 'BFO_0000196', 'axiom': 'range', 'was': ['BFO_0000016'], 'now': 'BFO_0000020', 'why': 'a bearer bears any specifically dependent continuant, not only dispositions'}]
  - BFO_0000196 and BFO_0000197 are BFO 2.0 IRIs and are not declared by BFO 2020, whose corresponding relations are RO_0000053 (bearer of) and RO_0000052 (inheres in). They are retained here, declared locally with BFO-consistent domains and ranges, because renaming 31 restrictions would diverge from the baseline this phase is meant to repair rather than rewrite. The vintage mismatch is carried in the known-defect inventory.
- **orphan restriction sweep**: 7 restriction bodies were already dangling in the input, i.e. axioms whose subject the original extraction lost. They are reported, not silently collected.

## 6. Merge-time repairs

- 2 class grounding collisions across BFO's disjoint top-level categories were repaired by dropping the less authoritative edge.
- 1 individual type collisions were repaired the same way. These are the ones that matter: a class in two disjoint categories is merely unsatisfiable, but an *individual* in two makes the whole ontology inconsistent.

  - `TwelveMonthDurationRequirement`: kept generically dependent continuant via ['DurationRequirement'], dropped `BFO_0000016` (specifically dependent continuant).

## 7. Residual defects, not repaired

- **2 unsatisfiable class(es)** remain: `CardiacLaboratoryFinding`, `CardiacStructuralAbnormality`.
  These are genuine defects of the baseline formalization — `is concretized by` applied to a filler that relation does not accept — and they are **left in place deliberately**. They are what K-A2 detects. Repairing them would delete the finding.
- The baseline uses the BFO 2.0 relation IRIs `BFO_0000196` (bearer of) and `BFO_0000197` (inheres in). BFO 2020 declares neither; its counterparts are `RO_0000053` and `RO_0000052`. The artifact declares both locally with BFO-consistent domains and ranges, so it is self-contained, but the vintage mismatch is real and is not silently rewritten.
- Baseline audit table correction: the spec's §1 table lists `concretizes` 4 / `is concretized by` 1 and `has history` 2. The measured artifact has those first two transposed, and the third is `exists at` (BFO_0000108), not `has history` (BFO_0000185).

## 8. Blanket flags

- **K-A3**: 246 of 330 candidates are blanket. Table 13 reports densities with and without them.

## 9. Audit independence

The artifact-versus-source adjudication was performed by the agent that built the artifact, not by an independent reader. Precision figures are provisional. The sample, the fixed question and the adjudication rules are on disk so a human can redo it and see which candidates move.

## 10. What this artifact is not

It is a research-grade formalization of part of 20 CFR Part 404. It is not the regulation, it is not legal advice, and it must not be used to determine anyone's entitlement to benefits. Where the artifact and the CFR disagree, the CFR is correct and the artifact is wrong.

