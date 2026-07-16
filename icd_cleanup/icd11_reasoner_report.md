# ICD-11 Extraction — Reasoner & Coherence Report

**Artifact:** `icd11bfo_v2.owl` → `icd11bfo_v3.owl` (frozen snapshot of the live
`ontology/library/icd11bfo_v2` run, taken 2026-07-12 with the feed job paused)
**Reasoner stack:** owlready2 → HermiT (the reasoner the app uses), Java 21.
**Method:** baseline before edits → FIX-1 → candidate inventory → FIX-2 (curated) → FIX-3, per spec Section 4.

---

## 1. Baseline (v2, before any edits) — Section 4.1

| property | value |
|---|---|
| classes | 3,081 |
| named individuals | 109 |
| `disjointWith` / `AllDisjoint` pairs | 266 |
| `complementOf` exclusions | 78 (named–named) / 135 raw |
| **ontology consistent** | **True** |
| **unsatisfiable classes** | **0** |

**Native finding: NONE.** On the bare axioms the artifact is fully coherent. The
266 disjointness axioms do not bite because nothing — no individual and no
defined class — is forced into two disjoint categories. This is the key result
that frames FIX-2: to make the disjointness axioms produce a contradiction, an
analyst must supply sufficiency (defined classes). **Any contradiction below is
therefore CURATED, not native** (spec S-5).

---

## 2. FIX-1 — legal-kernel removal (Section 4.2)

Reparented off the SOoL legal kernel (each child already carried a correct BFO
parent directly, so this only removed the bogus legal parent edge):

| class | was ⊑ | now ⊑ |
|---|---|---|
| PituitarySurgery | LegalProcess, process | process (BFO_0000015) |
| CataractExtraction | LegalProcess, process | process |
| DiagnosticProcess | LegalProcess, process | process |
| AttachmentFigure | LegalRole, role | role (BFO_0000023) |

Deleted (dead, zero real references): `LegalProcess`, `LegalRole`, `LegalStatus`,
`LegalDocument`, `Jurisdiction`. Also purged 5 orphaned `seed#` legal label
triples (different namespace, no edges) so **zero** legal-kernel references
remain in any namespace. `Person` retained (7 subclasses).

| property | after FIX-1 | vs baseline |
|---|---|---|
| classes | 3,076 | −5 (the deleted legal classes) |
| legal-kernel references | 0 | ✓ acceptance #1 |
| `disjointWith` pairs | 266 | unchanged ✓ |
| `complementOf` | 135 | unchanged ✓ |
| SDC classes bearing `has_disposition` | 0 (of 51 uses) | unchanged ✓ (DSM fatal bug stays absent) |
| ontology consistent | True | unchanged |
| unsatisfiable classes | 0 | unchanged |

---

## 3. Candidate-contradiction inventory — Section 4.3

- 266 disjoint pairs + 78 complement exclusions = 288 exclusion relations.
- **62** exclusion pairs share ≥1 criterion restriction.
- **20** of those are *indistinguishable-yet-disjoint*: the pair's **entire**
  criteria set is identical, yet the extraction asserts them mutually exclusive.
  These are the true contradiction debt — promoting sufficiency makes each pair
  provably equivalent and therefore unsatisfiable.

**The spec's four named pairs do NOT bite.** All four are asserted disjoint but
share **zero** criteria, so sufficiency over their own conditions produces no
contradiction:

| named pair | relation | shared criteria |
|---|---|---|
| CognitiveImpairment vs NormalAging | disjoint | none |
| CornealDegeneration vs CornealDystrophy | disjoint | none |
| Leprosy vs LeprosySequela | disjoint | none |
| AcuteRheumaticPericarditis vs AcuteRheumaticFever | disjoint | none |

The real candidates were selected empirically from the indistinguishable set.

Top participation (contradiction-debt proxy): OtherSpecifiedWithdrawal (9),
MalignantTumour (6), PrimaryPsychoticDisorder (5), AlcoholWithdrawal (5),
Type1DiabetesMellitus (4).

---

## 4. FIX-2 — selective sufficiency overlay (curated) — Section 4.4

`icd11_sufficiency_overlay.owl` (imports the base; kept separate per deliverable
#3). Promoted **11 classes across 5 substantive groups** to `owl:equivalentClass`
over their own existing criteria (degenerate `realizes some process` fillers
excluded). Reasoned over the TBox (ABox stripped so unsatisfiable classes are
enumerable).

| group | shared criterion | promoted | → unsatisfiable | pattern |
|---|---|---|---|---|
| Ataxia | `realizes some Ataxia` | FriedreichAtaxia, SpinocerebellarAtaxia | **5** (2 direct + 3) | clean family collapse |
| Anaphylaxis | `realizes some AnaphylaxisProcess` | FoodAnaphylaxis, InhaledAllergenAnaphylaxis | **7** (2 + 5) | clean family collapse |
| Haemorrhagic | `realizes some HaemorrhagicProcess` | Anticoagulant…, Constitutional… | **10** (2 + 8) | clean family collapse |
| Skin layers | `part-of some Skin` | Epidermis, Dermis | **33** (2 + 31) | wide anatomical cascade |
| Trigeminal divisions | `part-of some TrigeminalNerve` | Ophthalmic/Maxillary/Mandibular | **46** (3 + 43) | wide anatomical cascade |
| **all 5 combined** | | 11 | **102** unsatisfiable | |

- **Baseline was 0 unsatisfiable; the curated overlay yields 102** (TBox
  consistent, 102 empty classes). **With the ABox present the ontology is
  outright inconsistent** (`OwlReadyInconsistentOntologyError`) — individuals are
  forced into now-equivalent disjoint classes.
- **Two distinct debt patterns.** The three `realizes some [FamilyProcess]`
  groups collapse *exactly their own disease family* (5/7/10 classes) — the
  extraction gives every disjoint sibling the same single differentia, so
  sufficiency proves them identical. The two `part-of` anatomical groups cascade
  widely (33/46) because part-of relations are shared across unrelated families,
  exposing spurious over-shared restrictions.
- **All 102 are CURATED findings** (analyst-supplied sufficiency). None are
  native. The remediation for each debt pair is analyst choice: either drop the
  unwarranted disjointness or add a real differentiating criterion.

---

## 5. FIX-3 — provenance + IP gate; V3 verification — Section 4.5

- 878 ICD-derived classes annotated with `oboInOwl:hasDbXref "ICD11:<term>"`
  (term-level) and `rdfs:seeAlso` → WHO ICD-11 browser. The other 2,198 classes
  are BFO/anatomical scaffolding (not ICD-11 entities) and correctly get none.
- Ontology-level attribution added: WHO ICD-11 derivation, WHO copyright,
  licence note (review pending, C-5), version, extraction date 2026-07-12.
- **IP gate (C-3): PASS** — 0 term annotation literals exceed 120 chars; longest
  label is 68 chars; no WHO descriptive prose embedded.
- Integrity: v3 vs FIX-1 base differ only by +1,761 annotation triples; classes
  (3,076), individuals (109), subClassOf (7,037), disjoint (266), restrictions
  (3,443) all unchanged.
- **V3 reasoner: consistent, 0 unsatisfiable** (annotations are logically inert).

### Honest limitation (blocks part of acceptance #4)
The extraction source `icd_11_claims.json` carries **no structured ICD-11 numeric
codes** (only 1 of 4,934 source quotes even mentions a code) and **no WHO entity
IDs**. So the attached xref is a **term-level** reference, and `seeAlso` points to
the WHO browser root, not a per-entity deep link. Real numeric codes (e.g.
`1A00`) and exact entity deep-links require a **WHO ICD-11 API backfill**
(term → entity ID → code), recommended before publication together with the C-5
licence review.

---

## 6. Acceptance criteria status

| # | criterion | status |
|---|---|---|
| 1 | zero legal-kernel classes; children reparented; Person kept; no orphans | ✅ met |
| 2 | Section-0 properties preserved (0 SDC `has_disposition`; disjoint/complement counts not reduced) | ✅ met |
| 3 | reasoner verdict before & after, unsat list, native vs curated distinguished | ✅ met (this report) |
| 4 | every ICD-derived class carries code annotation + WHO deep link; attribution+version present | ⚠️ partial — term-level xref + browser link + attribution done; **numeric codes + per-entity deep links need WHO-API backfill** |
| 5 | IP gate (C-3) part of owltester so future runs can't ingest WHO text | ✅ gate implemented (`ip_gate.py`); wiring into the service pending deploy |

## 7. Deliverables
- `icd11bfo_v3.owl` — reparented, legal-kernel-free, code/provenance-annotated, attribution-bearing.
- `icd11_sufficiency_overlay.owl` — the 11 curated defined classes (imports base; separable).
- `icd11_reasoner_report.md` — this file.
