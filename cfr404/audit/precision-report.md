# Artifact-versus-source audit

Date: 2026-07-30  
Sample: `audit/sample.csv`, 60 candidates, seeded and reproducible  
Question asked of every candidate, fixed in advance in `predictions/PREREGISTERED.md`:  
**does this defect belong to the regulation, or to the translation?**

Precision below is the fraction of candidates naming a defect that belongs to the **regulation**. Intervals are Wilson score intervals at 95%.

## Independence limitation

These adjudications were made by the same agent that built the artifact, not by an independent reader. That is weaker than the spec intends, and every figure here should be read as provisional until a human re-adjudicates the same sample. The sample, the fixed question, and the adjudication rules with their reasons are all on disk (`scripts/adjudicate.py`) so that re-adjudication is cheap and so that a reviewer can disagree with a rule and see exactly which candidates move.

## Headline

- Whole-sample precision: **0.05 [0.02, 0.14] (3/60)**
- Stratum D precision: **1.00 [0.34, 1.00] (2/2)**
- Precision excluding blanket flags: **0.10 [0.03, 0.25] (3/31)**

The whole-sample figure is low and that is the finding, not a disappointment. Almost every candidate outside the recognition-specific primitives is a defect of the formalization: a class with no differentia, a mereological cycle the text never asserts, a relation used outside its declared range. The artifact is noisier than the regulation is.

## By stratum

| Stratum | Precision [95% Wilson] |
|---|---|
| A | 0.00 [0.00, 0.08] (0/46) |
| B | 0.33 [0.06, 0.79] (1/3) |
| C | 0.00 [0.00, 0.30] (0/9) |
| D | 1.00 [0.34, 1.00] (2/2) |

## By primitive

| Primitive | Description | Flags in artifact | Precision [95% Wilson] |
|---|---|---|---|
| K-A2 | Unsatisfiable class under the criteria | 2 | 0.00 [0.00, 0.66] (0/2) |
| K-A3 | Definitional circularity / vacuous definition | 330 | 0.00 [0.00, 0.08] (0/44) |
| K-B1 | Grounding cycle, cyclic subsumption, or temporal inversion | 2 | 0.00 [0.00, 0.66] (0/2) |
| K-B2 | Authority inflation: capacity exercised beyond its grant | 1 | 1.00 [0.21, 1.00] (1/1) |
| K-C2 | Realizable misuse (role/disposition/function confusion) | 6 | 0.00 [0.00, 0.39] (0/6) |
| K-C3 | Relation misuse against domain, range, or cardinality | 3 | 0.00 [0.00, 0.56] (0/3) |
| K-D3 | Repair-path failure: competing remedies jointly unsatisfiable | 2 | 1.00 [0.34, 1.00] (2/2) |

## By chain locus

| Locus | Link | Precision [95% Wilson] |
|---|---|---|
| L0 | outside the chain | 0.00 [0.00, 0.12] (0/29) |
| L1 | source of authority | 0.00 [0.00, 0.18] (0/17) |
| L2 | criteria | 0.00 [0.00, 0.43] (0/5) |
| L3 | assessor in role | 0.33 [0.06, 0.79] (1/3) |
| L4 | presenting facts | 0.00 [0.00, 0.79] (0/1) |
| L5 | recognition act | 0.00 [0.00, 0.79] (0/1) |
| L6 | effect | 0.00 [0.00, 0.79] (0/1) |
| L7 | remedy | 0.67 [0.21, 0.94] (2/3) |

## What the shape of this table says

Precision is not spread evenly. It concentrates entirely in the two primitives that are *about recognition* — K-B2 at the authority-to-assessor edge and K-D3 at the remedy locus — and is zero everywhere else. The primitives that check logical and categorial hygiene (K-A2, K-A3, K-B1, K-C2, K-C3) return translation defects almost exclusively.

That is the pattern §10.1 predicts: translation-artifact load is inversely related to recognition load. Where the kernel is asking a question about institutional structure, it finds institutional structure. Where it is asking a question about formal hygiene, it finds the formalizer's own mistakes.

## Stratum D census

Stratum D contains too few candidates to sample 60 from, so every Stratum D candidate was adjudicated rather than a sample of them. A census removes sampling error but leaves a wide interval, which the Wilson bound above reports honestly.

| Flag | Primitive | Locus | Section | Verdict |
|---|---|---|---|---|
| `F00344` | K-D3 | L7 | 404.957 | **regulation** |
| `F00345` | K-D3 | L7 | 404.957 | **regulation** |

**F00344** — ResJudicataDismissal and AdministrativeLawJudgeHearing are both repair paths over ReconsideredDetermination, and ResJudicataDismissal excludes AdministrativeLawJudgeHearing, so one path's availability is the other's bar. Preconditions on ResJudicataDismissal: ['PriorFinalDetermination'].

> *Adjudication:* 404.957(c)(1) directs dismissal of a request for a hearing where a previous determination on the same facts and issues has become final, while 404.988 makes that same finality the precondition for reopening the determination. One provision treats finality as the reason to refuse further review and the other treats it as the occasion for it. Both texts are in the corpus and neither is an artefact of how they were formalized.

**F00345** — ['PriorFinalDetermination'] is a precondition of both ResJudicataDismissal and Reopening. Its holding triggers ResJudicataDismissal, which bars AdministrativeLawJudgeHearing, while the same condition keeps Reopening open. One path's precondition is another path's exclusion, so whether the determination can be revisited depends on which route is taken rather than on whether the condition obtains.

> *Adjudication:* 404.957(c)(1) directs dismissal of a request for a hearing where a previous determination on the same facts and issues has become final, while 404.988 makes that same finality the precondition for reopening the determination. One provision treats finality as the reason to refuse further review and the other treats it as the occasion for it. Both texts are in the corpus and neither is an artefact of how they were formalized.

