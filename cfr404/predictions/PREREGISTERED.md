# Pre-registered predictions — cfr404 Stratum D control

**Committed before the first kernel run.** Nothing in `kernel/detectors/` had been
executed against `cfr404.owl` when this file was committed; the git history is the
evidence. Phases 1 through 4 (repair, provenance, corpus, chain scaffold) were
complete, so the loci these predictions name were already fixed and could not be
retrofitted to whatever the detectors happened to find.

Author: David R. Koepsell
Date: 2026-07-30
Artifact under test: `ontology/cfr404.owl` (20 CFR Part 404, subparts H, J, P, Q,
appendices 1 and 2, eCFR currency date 2026-07-28)

---

## Why these are stated at this granularity

A control that predicts only "Stratum D is non-empty" is weak: it is satisfied by any
flag anywhere, including flags that are artefacts of the translation. Each prediction
below names the **locus** and, where possible, the **sections**, so that a flag in the
wrong place counts as a failure rather than a success.

---

## Predictions

**P1. Stratum D is non-empty.**
At least one of K-D1, K-D2, K-D3 fires with hand-audited precision above 0.5.

**P2. K-D1 fires at the norm-to-effect edge, L5 → L6.**
The structural grant of assessment capacity is uniform across occupants of L3 roles;
observed dispositions at L5 vary by occupant beyond what the L2 criteria explain.

**P3. K-D3 fires at L7**, concentrated in the interaction of reopening
(404.987–404.996), res judicata (404.957(c)(1)), and the medical improvement review
standard (404.1594).

**P4. K-B2 fires at L1 → L3** if 404.1546 assigns the same RFC-assessment function to
occupants holding different grants of authority.

**P5. K-D2 is rare or absent.**
Performative self-defeat is not expected in this corpus. A null here is reported as a
null; two of three Stratum D primitives firing is a stronger result than three firing
under loose criteria.

**P6. Strata A, B, and C all fire**, so the corpus occupies the full four-stratum row
of Table 8.

**P7. Continuant/occurrent straddles remain at or near zero**, supporting §10.1's claim
that translation-artifact load is inversely related to recognition load.

---

## Predictions that would falsify the design rather than the hypothesis

Stated so they cannot be reinterpreted after the fact:

- If **K-D3 fires only outside Subpart J**, P3 is wrong even if Stratum D is non-empty.
- If **K-B2 fires at L1 → L3 but every flag traces to a section other than 404.1546,
  404.1503, 404.1503a, or 404.1615/1616**, P4 is wrong.
- If **Stratum D is non-empty only because of blanket flags** applied to whole class
  families, P1 is not satisfied. Blanket flags are reported and removed separately, and
  the post-removal count is the one that counts.
- If **hand-audited precision on the Stratum D sample is at or below 0.5**, P1 fails
  regardless of raw flag counts.

---

## Standing caveat on P7

P7 is the weakest prediction in the set and is flagged as such in advance: the repaired
baseline already reports zero straddles, so P7 is close to being true by construction of
Phase 1 rather than by discovery. It is retained because a *regression* would be
informative, not because confirming it is.

---

## Audit protocol fixed in advance

- Sample size: at least 60 Stratum D flags, stratified across primitives and loci.
- Adjudication question, fixed wording: *does this defect belong to the regulation or to
  the translation?*
- Precision reported with a Wilson score interval at 95%.
- The precision figure is reported whatever it is.
