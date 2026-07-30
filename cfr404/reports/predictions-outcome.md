# Pre-registered predictions — outcome

Date: 2026-07-30

`predictions/PREREGISTERED.md` was committed before any detector was run (commit `c3df8718`, which contains phases 1-4 and the predictions and no detector output). Each prediction is scored below against the artifact as built.

## P1 — Stratum D is non-empty, precision above 0.5

**HELD** — K-D3 raised 2 candidates. Every Stratum D candidate was hand-adjudicated (a census, not a sample) and 2 of 2 survived as defects of the regulation, precision 1.00. The point estimate clears 0.5; the 95% Wilson lower bound is 0.34, because a census of two cannot be precise. The prediction holds on its stated terms and the interval is reported rather than hidden.

## P2 — K-D1 fires at the norm-to-effect edge, L5 to L6

**UNDECIDED** — Undecidable from this build. K-D1 is **not computed**: SSA published ALJ disposition statistics could not be acquired from this host (https://www.ssa.gov/appeals/DataSets/03_ALJ_Disposition_Data.html -> 403; https://www.ssa.gov/appeals/DataSets/ -> 403; https://www.ssa.gov/open/data/ -> 403; https://catalog.data.gov/api/3/action/package_search?q=ALJ+disposition+hearings -> 404). K-D1 is a claim about the world, not about the text, and the spec is explicit that asserting the gap without the measured variance would be an assertion rather than a detection. No K-D1 flag was raised, and none should have been. The prediction is neither confirmed nor refuted; it is untested.

## P3 — K-D3 fires at L7, in reopening / res judicata / medical improvement

**HELD** — Both K-D3 candidates are at L7 and both trace to 404.957, the res judicata dismissal provision, interacting with the reopening conditions of 404.988. That is the site named in advance. The medical improvement review standard (404.1594) is modelled and participates in the repair-path graph but did not itself yield a candidate.

## P4 — K-B2 fires at L1 to L3 via 404.1546

**HELD** — K-B2 raised 1 candidate, on the L1->L3 edge, and it survived adjudication. 404.1546 assigns one residual functional capacity assessment to seven assessor roles whose authority traces to three distinct grants. This is the prediction that most depended on a modelling choice made before the run: had authority and assessor been left merged as the baseline had them, the comparison would have had no terms and K-B2 could not have fired at all.

## P5 — K-D2 is rare or absent

**HELD** — K-D2 raised no candidates. Reported as a null. Both shapes the detector looks for — an act that is a repair path for itself, and an act whose precondition is also its own exclusion — are absent from the modelled corpus.

## P6 — Strata A, B and C all fire

**DID NOT HOLD** — Partly, and not in the sense predicted. All three strata raised candidates (A, B, C), but under hand audit only stratum B contains a defect belonging to the regulation. A and C fire on translation artifacts exclusively: unsatisfiable classes created by misapplied relations, classes with no differentia, mereological cycles the text never asserts. The corpus does **not** occupy the full four-stratum row of Table 8 in the strong sense; it occupies B and D.

## P7 — straddles remain at or near zero

**HELD** — Zero continuant/occurrent straddles in the merged artifact, held across every phase including the merge. As pre-registered, this is the weakest prediction in the set: Phase 1 starts from a baseline that already reports zero, so confirming it is close to true by construction. What would have been informative is a regression, and there was nearly one — an early merge introduced 4 straddles by grounding 'finding' as an act against the baseline's grounding of it as evidence. That was caught, fixed at the category level, and a merge-time guard added.

## Summary

| Prediction | Outcome |
|---|---|
| P1 Stratum D non-empty, precision > 0.5 | held |
| P2 K-D1 at L5->L6 | untested (data unavailable) |
| P3 K-D3 at L7, reopening/res judicata | held |
| P4 K-B2 at L1->L3 via 404.1546 | held |
| P5 K-D2 rare or absent | held (null) |
| P6 Strata A, B, C all fire | did not hold as stated |
| P7 straddles at or near zero | held (weakly, as flagged in advance) |

Four held, one held as a null, one is untested for want of external data, and one did not hold. P6 failing is the more interesting result: it says the kernel's lower strata, applied to this corpus, measure the formalizer rather than the regulation.

