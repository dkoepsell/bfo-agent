# Table 8 — stratum profile

Date: 2026-07-30  
Artifact: `ontology/cfr404.owl`, 2,494 named classes  
Row: **act-thick, repair-thick legal system**

Filled empirically. A stratum counts as firing when a detector raised at least one candidate that survived hand adjudication as a defect of the regulation; a stratum that raised candidates but survived none is reported as firing on translation artifacts only, which is a different claim.

## Which primitives fire

| Primitive | Stratum | Instrument | Candidates | Blanket | Status |
|---|---|---|---|---|---|
| K-A1 | A | description-logic reasoner | 0 | 0 | no candidates (reported null) |
| K-A2 | A | description-logic reasoner | 2 | 0 | candidates, none survived audit |
| K-A3 | A | structural analysis of axioms | 330 | 246 | candidates, none survived audit |
| K-B1 | B | structural analysis of axioms | 2 | 0 | candidates, none survived audit |
| K-B2 | B | structural analysis of axioms | 1 | 0 | **fires on the regulation** |
| K-B3 | B | structural analysis of axioms | 0 | 0 | no candidates (reported null) |
| K-C1 | C | description-logic reasoner | 0 | 0 | no candidates (reported null) |
| K-C2 | C | structural analysis of axioms | 6 | 0 | candidates, none survived audit |
| K-C3 | C | description-logic reasoner | 3 | 0 | candidates, none survived audit |
| K-D1 | D | world-facing data | — | — | **NOT COMPUTED** |
| K-D2 | D | process-level modelling | 0 | 0 | no candidates (reported null) |
| K-D3 | D | process-level modelling | 2 | 0 | **fires on the regulation** |

## Stratum summary

| Stratum | Candidates | Primitives firing on the regulation | Occupied |
|---|---|---|---|
| A | 332 | — | artifact-only |
| B | 3 | K-B2 | yes |
| C | 9 | — | artifact-only |
| D | 2 | K-D3 | yes |

## Not computed, and why

- **K-D1** — SSA published ALJ disposition statistics could not be acquired from this host (https://www.ssa.gov/appeals/DataSets/03_ALJ_Disposition_Data.html -> 403; https://www.ssa.gov/appeals/DataSets/ -> 403; https://www.ssa.gov/open/data/ -> 403; https://catalog.data.gov/api/3/action/package_search?q=ALJ+disposition+hearings -> 404). K-D1 is a claim about the world, not about the text, and the spec is explicit that asserting the gap without the measured variance would be an assertion rather than a detection.

## Reasoner

HermiT: **consistent**, coherent=False, 2 unsatisfiable class(es), 5.0s.

## How to read the empty cells

K-D2 raised no candidates. That is reported as a null, not massaged into a finding: performative self-defeat is not something 20 CFR Part 404 appears to contain, and the spec is explicit that a Stratum D firing on two of three primitives is a stronger result than one firing on all three by loose criteria.

K-D1 is **not computed**, which is a third thing again — neither a finding nor a null. The regulation cannot answer it; only SSA's published adjudicator outcome data can, and that data could not be retrieved from the build host.

