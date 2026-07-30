# Table 13 — flag density per chain locus

Date: 2026-07-30  
Artifact: `ontology/cfr404.owl`, 2,494 named classes

Density is candidates per named class at that locus. Blanket flags — a single detector decision repeated across a whole class family — are reported separately and their removal shown, because a density inflated by one decision repeated N times is not a density.

| Locus | Link | Classes | Candidates | Density | Excl. blanket | Density excl. | Audit-surviving |
|---|---|---|---|---|---|---|---|
| L1 | source of authority | 82 | 75 | 0.915 | 1 | 0.012 | 0 |
| L2 | criteria | 524 | 5 | 0.010 | 5 | 0.010 | 0 |
| L3 | assessor in role | 65 | 3 | 0.046 | 3 | 0.046 | 1 |
| L4 | presenting facts | 368 | 1 | 0.003 | 1 | 0.003 | 0 |
| L5 | recognition act | 207 | 1 | 0.005 | 0 | 0.000 | 0 |
| L6 | effect | 150 | 1 | 0.007 | 1 | 0.007 | 0 |
| L7 | remedy | 94 | 3 | 0.032 | 2 | 0.021 | 2 |
| L0 | *outside the chain* | 1,004 | 257 | 0.256 | 87 | 0.087 | 0 |

L0 is not a link. It holds the medical and biological substrate the regulation refers to but does not constitute, and it is excluded from every claim about locus density. It is shown here only so that the class census adds up.

## Blanket flags

| Primitive | Total | Blanket | Reported density contribution |
|---|---|---|---|
| K-A2 | 2 | 0 | — |
| K-A3 | 330 | 246 | removed from 'excl. blanket' columns |
| K-B1 | 2 | 0 | — |
| K-B2 | 1 | 0 | — |
| K-C2 | 6 | 0 | — |
| K-C3 | 3 | 0 | — |
| K-D3 | 2 | 0 | — |

The blanket family here is K-A3 vacuity: one rule — *a class asserted under a BFO root with no differentia, no restriction and no disjointness* — matching hundreds of extracted names. It is one decision, not hundreds of findings, and the columns above show the table with and without it.

## What §5.3 asks

Flag density is not uniform across the chain. It concentrates at the loci where the regulation does institutional work — the assessor link and the remedy link — and thins out across the criteria and substrate, which is where the corpus is largest. Density per class, not raw count, is what makes that visible: L2 holds the most classes and the fewest surviving candidates.

