# SPEC-cfr404-stratum-d

Repair and extension of `CFR-DisabilityRegs.owl` into a Stratum D positive control for
*The Recognition Layer* §13.

Owner: David R. Koepsell
Target machine: DGX Station (`david-koepsell-dgx-station`, user `drkoepsell`), development on KoeppyBox with rsync to DGX
Status: specification, not yet implemented

---

## 0. Why this exists

*The Recognition Layer* §6.4 predicts that Stratum D of the contradiction kernel fires only in
systems containing acts, and §9.4 reports Stratum D empty in ICD-11 as predicted. §13 concedes
that this is informative only if Stratum D is **non-empty** in a legal corpus run through the same
pipeline, and names that run as the single experiment on which the general claim of §5 stands or
falls.

The existing artifact cannot serve that purpose. It extracted the criteria layer of 20 CFR Part 404
and almost none of the adjudicative machinery, so an empty Stratum D would be an artifact of corpus
selection rather than a fact about the regulation. That is exactly the artifact-versus-source
conflation §10 exists to prevent.

**Goal:** produce `cfr404.owl`, an act-thick, repair-thick legal ontology in which all four strata
can fire, with per-locus flag densities reportable in the format of Table 13 and a stratum profile
reportable in the format of Table 8.

---

## 1. Baseline audit of the current artifact

Measured on the uploaded `CFR-DisabilityRegs.owl`. Any rebuild must preserve or improve every line
of this table.

| Property | Value |
|---|---|
| Named classes (local namespace) | 1,009 |
| RDF triples | 8,180 |
| Named individuals | 22 |
| Object properties | 11 |
| Annotation properties | **0** |
| Datatype properties | **0** |
| BFO version | 2020, imported from `http://purl.obolibrary.org/obo/bfo.owl` |
| Continuant/occurrent straddles (K-C1) | **0** |
| Classes with no BFO ancestor | 1 (`Exertional Limitation`) |
| Disjointness axioms | 69 |
| Complement axioms | 41 |

Direct BFO groundings: process 284, disposition 190, material entity 126, quality 25, site 10,
generically dependent continuant 8, temporal region 2, **role 3**.

Restriction predicates: `realized in` 500, `has participant` 165, `has part` 127, `inheres in` 93,
`part of` 77, `occurs in` 51, `bearer of` 30, `realizes` 12, `participates in` 11, `concretizes` 4,
`has role` 2, `has history` 2, `is concretized by` 1.

Recognition chain coverage in the baseline:

| Link | Status | Classes present |
|---|---|---|
| Authority | thin | `Government Agency`, `State Agency` (both material entity) |
| Criteria | good | `Diagnostic Criterion`, `Duration Requirement`, per-system evaluation norms and rules |
| Assessor in role | **absent** | `Determining Agency Role` only, which is an agency-level role, not a person in role |
| Recognition act | minimal | `Disability Determination`, `Blindness Determination` under `Legal Process` |
| Effect | minimal | `Disability Status`, `Blindness Status` under `Legal Status` |
| Remedy | **absent** | none |
| Evidence (supporting) | thin | `Evidence`, `Objective Medical Evidence`, `Source of Evidence`, `Medical Source`, `Nonmedical Source`, `Acceptable Medical Source` |

The three-role count is the diagnostic finding. A system with an assessor link should have many
roles; this one has three.

---

## 2. Deliverables

```
cfr404/
  README.md
  spec/SPEC-cfr404-stratum-d.md          # this file
  corpus/
    raw/                                  # eCFR XML as downloaded, unmodified
    chunks/                               # section-level chunks with stable ids
    manifest.json                         # section id -> file, hash, retrieval date
  ontology/
    cfr404-base.owl                       # repaired baseline, criteria layer
    cfr404-chain.owl                      # chain-locus scaffold (hand-authored)
    cfr404-adjudication.owl               # extracted act/remedy layer
    cfr404.owl                            # merged release artifact
  kernel/
    kernel-vocab.owl                      # K-* primitives, locus vocabulary, flag properties
    detectors/                            # one module per primitive
  data/
    ssa-outcomes/                         # world-facing data for K-D1
  audit/
    sample.csv                            # hand-adjudicated flag sample
    precision-report.md
  reports/
    table8-stratum-profile.md
    table13-locus-density.md
    reasoner-verdict.md
  scripts/
    validate.py
    build.py
    report.py
  predictions/PREREGISTERED.md
```

Release artifact licensed CC-BY 4.0, consistent with `icd11bfo.owl` and `dsm_icd_crosswalk.owl`.

---

## 3. Environment

- Python 3.12 venv at `~/research` on the DGX, `uv` managed.
- `rdflib`, `owlready2`, `pandas`, `requests`, `lxml`.
- Java 17+ for HermiT. Verify before Phase 7; the ICD-11 run exceeded the compute budget and the
  spec must not repeat that silently.
- Ollama local models on the DGX for extraction. Do **not** call hosted APIs for corpus extraction:
  the artifact must be reproducible from local components.
- Do not collide with existing services. Nothing in this project binds a port. If a review UI is
  added later, pick a port outside 80, 81, 8081, 8083, 8181, 9091.

---

## 4. Phase 1: repair the baseline

Input `CFR-DisabilityRegs.owl`, output `ontology/cfr404-base.owl`.

1. **Change the namespace.** Baseline uses `http://davidkoepsell.com/bfo-agent/working#`, which is a
   scratch IRI shared with other projects. Move to `http://davidkoepsell.com/cfr404#` with an
   `owl:versionIRI` of `http://davidkoepsell.com/cfr404/2026-07-30`. Rewrite all subject and object
   IRIs. Keep BFO and RO IRIs untouched.

2. **Delete `SimpleMessage`.** Grounded as generically dependent continuant, currently a sibling of
   `Evidence`. It is contamination from another project. Remove the class and every axiom mentioning
   it. Log the deletion in the README with a one-line justification.

3. **Ground `Exertional Limitation`.** It has no BFO ancestor. It should be a `disposition`
   (BFO_0000016), consistent with how the build already types functional limitations. Confirm
   against 404.1567 before asserting; if the source treats it as a range of a quality rather than a
   dispositional limit, ground as `quality` (BFO_0000019) and note the decision.

4. **Resolve the anonymous parent on `UCTD`.** It carries a blank-node type alongside
   `Connective Tissue Disorder` and `Autoimmune Disorder`. Determine whether this is an intended
   restriction or extraction noise, and either name it or drop it.

5. **Re-run the straddle check.** Must remain zero. This is a headline number for §10 and must not
   regress at any later phase.

Acceptance: `validate.py` reports 1,008 named classes, zero straddles, zero classes without a BFO
ancestor, zero blank-node types, and a clean parse under `rdflib` and `owlready2`.

---

## 5. Phase 2: provenance layer

The baseline carries no annotation properties at all. The ICD-11 artifact carries source quote,
claim text, chunk index, extraction confidence, approval flag, and note per claim. §13 already
concedes that the ICD-11 gradient test failed a powered analysis for want of chapter-level
provenance stamping. **Do not repeat that omission here.** The entire Stratum D claim is a claim
about *locus*, so every assertion must be traceable to a CFR section.

Define in `kernel-vocab.owl`:

| Property | Type | Range | Required on |
|---|---|---|---|
| `cfr:sourceSection` | annotation | string, e.g. `404.1546` | every class and axiom |
| `cfr:sourceQuote` | annotation | string, verbatim, under 40 words | every extracted class |
| `cfr:chunkId` | annotation | string | every extracted class |
| `cfr:extractionConfidence` | annotation | `high` \| `medium` \| `low` | every extracted class |
| `cfr:approved` | annotation | boolean | every extracted class |
| `cfr:chainLocus` | annotation | one of the seven link ids | every institutional class |
| `cfr:reviewNote` | annotation | string | optional |

Backfill `cfr:sourceSection` onto the existing 1,008 baseline classes. Where a class cannot be
traced to a section, mark `cfr:extractionConfidence` low and `cfr:approved` false rather than
guessing. Report the backfill coverage rate; it is a reportable number.

Keep the verbatim quote under 40 words per class. These are federal regulations and therefore public
domain under the edicts-of-government doctrine, so there is no copyright constraint, but short
quotes keep the artifact reviewable.

---

## 6. Phase 3: corpus acquisition

Fetch from eCFR or govinfo as structured XML, not scraped HTML. **Verify the current bulk endpoints
before writing the loader**; these have moved before, and a hardcoded URL from training data will
fail. Store raw XML unmodified in `corpus/raw/` with retrieval date and SHA-256 in `manifest.json`.

Sections to add. The baseline already covers Appendix 1 and the medical evidence provisions; keep
them and add the adjudicative machinery.

| Sections | Chain link served | Why |
|---|---|---|
| 404.900 – 404.999a | remedy, act | Full administrative review process: initial determination, reconsideration, ALJ hearing, Appeals Council review, federal court review, reopening, revision, res judicata |
| 404.1546 | assessor in role | Specifies who makes the RFC assessment at each level. Single densest assessor locus in the corpus |
| 404.1503, 404.1503a | authority, assessor | Who makes determinations; the State agency's role and its relation to the Commissioner |
| 404.1520, 404.1520b, 404.1520c | criteria to act | Sequential evaluation; how medical opinions are weighed via supportability and consistency |
| 404.1512 – 404.1513a | evidence | Evidence responsibilities and categories of evidence |
| 404.1588 – 404.1599 | remedy, effect | Continuing disability review, cessation, termination |
| 404.1594 | remedy | Medical improvement review standard. Highest-probability K-D3 site in the corpus |
| 404.1567 | criteria | Exertional levels, needed for the Phase 1 grounding decision |

Chunk at the section level, never across sections. Section number is the stable chunk id and becomes
the locus key. Target roughly 250 to 400 chunks, comparable to the ICD-11 build's 364.

**Hard constraint:** no Westlaw, Lexis, or any commercial database text enters the pipeline. The
artifact is released CC-BY and must be redistributable.

---

## 7. Phase 4: chain-locus scaffold

Hand-author `ontology/cfr404-chain.owl` before running any extraction. This is the index set over
which the kernel is instantiated, and letting an extractor invent it would make locus attribution
circular.

Seven link classes, each with a stable id used as the value of `cfr:chainLocus`:

| Id | Link | BFO grounding | SSA instantiation |
|---|---|---|---|
| `L1` | source of authority | material entity, bearer of an authority role | Congress, Commissioner of Social Security |
| `L2` | criteria | generically dependent continuant | Listings, duration requirement, sequential evaluation steps |
| `L3` | assessor in role | **role** (BFO_0000023), borne by a person | disability examiner, State agency medical consultant, ALJ, Appeals Council member |
| `L4` | presenting facts | generically dependent continuant | evidence of record, medical and nonmedical |
| `L5` | recognition act | process (BFO_0000015) | initial determination, reconsideration determination, ALJ decision |
| `L6` | effect | role or disposition | disability status, entitlement to benefits, Medicare eligibility |
| `L7` | remedy | process (BFO_0000015) | reconsideration, hearing, Appeals Council review, judicial review, reopening |

### Critical modelling rule

**Separate the authority from the assessor.** The baseline collapses them into
`Determining Agency Role`. Keep them apart:

- The Commissioner is an entity at L1 bearing an authority role.
- The granting of an assessor role is itself a recognition act whose effect is the existence of an
  L3 role.
- The ALJ is a **person** bearing an L3 role that is *realized in* an L5 determination process.

Collapsing these makes **K-B2, authority inflation, undetectable by construction**, and K-B2 is
among the primitives most likely to actually fire in this corpus. If the merged model is retained,
the experiment cannot falsify anything at the authority-to-role locus, which is precisely where
Lindahl and Reidhav's treatment predicts trouble.

Model role occupancy with `bearer of` (BFO_0000196) from person to role, and role realization with
`realized in` (BFO_0000054) from role to process. The baseline already uses `realized in` 500 times,
so this is consistent with existing practice.

---

## 8. Phase 5: extraction

Run the same pipeline used for `icd11bfo.owl`, unmodified where possible. Any pipeline change
weakens the comparison, so log every deviation.

For each chunk, extract claims and mint classes with full Phase 2 provenance. Assign
`cfr:chainLocus` at extraction time from the section-to-link mapping in Phase 3, not by inference
from branch position. §13's concession about the ICD-11 gradient traces directly to inferring strata
from the extractor's branch assignment; do not repeat it.

Size target: 2,000 to 2,500 named classes after merge. The ICD-11 artifact has 5,667, so densities
must be reported per class and per locus rather than as raw counts.

Merge order: `cfr404-base.owl` + `cfr404-chain.owl` + `cfr404-adjudication.owl` -> `cfr404.owl`.
Re-run the straddle check after merge. Still zero.

---

## 9. Phase 6: kernel audit layer

Operationalize all twelve primitives, or state explicitly which are not implemented and why. The
ICD-11 run implemented four of twelve, and §13 concedes that absence of a flag is not evidence of
absence of the failure. Doing better here is cheap and strengthens the paper.

Flags are asserted with `cfr:hasContradictionCandidate`, carrying the primitive id, the locus id, the
detecting instrument, and the source section. Follow the ICD-11 convention: these are **candidates**,
the detector's suspicions, not reasoner-confirmed contradictions, and the vocabulary must say so.

Detector assignment follows Table 7:

| Instrument | Primitives |
|---|---|
| Description-logic reasoner | K-A1, K-A2, K-C1; K-B1 for cyclic subsumption; K-C3 partially via domain, range, cardinality |
| Structural analysis of axioms | K-A3, K-B2, K-B3, K-C2; K-B1 for grounding cycles and temporal inversions |
| World-facing data | K-D1 |
| Process-level modelling | K-D2, K-D3 |

### K-D1, the one that needs external data

K-D1 is the gap between a capacity granted in structure and one available in practice. It cannot be
computed from the regulation text alone. Acquire SSA published ALJ disposition statistics, which
report allowance rates by adjudicator. Verify the current publication location before writing the
loader. The measured inter-adjudicator variance is the evidence; a claim that a gap exists without
that data is an assertion, not a detection.

Model it as: L3 role grants a uniform capacity in structure; observed dispositions at L5 vary by
occupant beyond what the criteria at L2 explain. Flag the norm-to-effect edge.

### K-D3, the likely find

Model the interaction of reopening and revision (404.987 through 404.996), res judicata
(404.957(c)(1)), and the medical improvement review standard (404.1594) as competing repair paths
over the same determination. If the conditions under which a determination can be revisited are
jointly unsatisfiable, or if one path's precondition is another path's exclusion, that is repair
failure at L7.

### K-D2

Performative self-defeat. Expect rare or absent. Report the null honestly if so; a Stratum D that
fires on two of three primitives is a stronger result than one that fires on all three by loose
criteria.

---

## 10. Phase 7: reasoner and audit

1. **Run HermiT** on the merged artifact with BFO imported. Report consistency and coherence, and
   the unsatisfiable class list if any. If the run exceeds the compute budget, say so explicitly in
   `reasoner-verdict.md` and report **no** verdict rather than citing a predecessor build. The
   tension between the two ICD-11 assessments described in §10.2 is a cautionary example.

2. **Artifact-versus-source audit.** Draw a random sample of at least 60 Stratum D flags, stratified
   across primitives and loci. Hand-adjudicate each against the CFR section text: does the defect
   belong to the regulation or to the translation? Report precision with a Wilson confidence
   interval. Report it whatever it is.

3. **Known-defect inventory.** Write the §10.2 analogue for this artifact: every construction
   decision, every blanket flag, every branch misassignment, every coverage gap. Any blanket flag
   applied to a whole class family must be reported separately and its removal shown, exactly as
   §9.3 does for the 154 traditional-medicine patterns.

---

## 11. Phase 8: reports

`table8-stratum-profile.md`: the row for act-thick, repair-thick systems, filled empirically rather
than by construction. Report which of the twelve primitives fire, with counts.

`table13-locus-density.md`: flag density per named class, broken down by chain locus L1 through L7,
with and without any blanket flags. This is the table that answers §5.3.

Both must be reproducible by `python scripts/report.py` from the artifact alone.

---

## 12. Pre-registered predictions

Write `predictions/PREREGISTERED.md` and commit it **before** the first kernel run. A control that
predicts only "Stratum D is non-empty" is weak. Name the loci.

1. Stratum D is non-empty. At least one of K-D1, K-D2, K-D3 fires with hand-audited precision above
   0.5.
2. K-D1 fires at the norm-to-effect edge, L5 to L6.
3. K-D3 fires at L7, concentrated in the reopening, res judicata, and medical improvement review
   interaction.
4. K-B2 fires at L1 to L3 if 404.1546 assigns the same RFC-assessment function to occupants holding
   different grants of authority.
5. K-D2 is rare or absent.
6. Strata A, B, C all fire, so the corpus occupies the full four-stratum row of Table 8.
7. Continuant/occurrent straddles remain at or near zero, supporting §10.1's claim that
   translation-artifact load is inversely related to recognition load.

Prediction 7 is worth flagging: the baseline already supports it, and it is the cleanest extraction
in the programme so far.

---

## 13. Acceptance criteria

The project is done when all of the following hold.

- [ ] `cfr404.owl` parses cleanly, imports BFO 2020, and carries a version IRI.
- [ ] Zero continuant/occurrent straddles. Zero classes without a BFO ancestor.
- [ ] Every class carries `cfr:sourceSection`, `cfr:chainLocus`, `cfr:extractionConfidence`, and
      `cfr:approved`. Coverage is reported, not assumed.
- [ ] All seven chain links are populated, with L3 and L7 non-empty. **L3 must contain at least four
      distinct person-borne roles** and must be modelled separately from L1.
- [ ] All twelve kernel primitives are either operationalized or explicitly listed as not
      implemented with a stated reason.
- [ ] Stratum D flags exist and have survived hand audit at reported precision.
- [ ] A reasoner verdict is reported, or its absence is reported with the reason.
- [ ] `table8-stratum-profile.md` and `table13-locus-density.md` regenerate from the artifact.
- [ ] `PREREGISTERED.md` was committed before the first kernel run, and the report says which
      predictions held and which did not.
- [ ] Artifact released CC-BY 4.0 with a README stating what it is not: a research-grade
      formalization of the regulation, not the regulation.

---

## 14. Optional Phase 9: the third row

Table 8 has three rows. ICD-11 fills the act-thin row, this artifact fills the act-thick row, and
the scientific reference ontology row is currently filled by construction rather than by
measurement. Running GO or ChEBI through the same pipeline and showing an empty Stratum D **and** an
empty or near-empty chain would complete the table empirically.

Three rows, one pipeline, three distinct stratum profiles is substantially harder to dismiss than
two. Do this only after Phases 1 through 8 are accepted.

---

## 15. Constraints and gotchas

- **Deontic inflation.** Legal text is saturated with modals. An extractor will happily mint
  obligation and permission classes and light up Stratum D on contact. That would be a translation
  artifact, not a finding. The baseline shows no deontic classes at all, so the risk is real but not
  yet realized. Guard against it: a Stratum D flag requires an *act*, not merely a modal verb.
- **Do not tune the pipeline to produce the predicted result.** Every deviation from the ICD-11
  pipeline is logged and reported, and a deviation introduced after seeing a null result is
  reported as such.
- **Provenance is not optional.** If a phase ships without section stamping, the phase is not done.
- **No commercial legal database text.** Ever.
- **Reproducibility.** Extraction runs locally on the DGX via Ollama. Record model name, version,
  quantization, seed, and prompt hash in `manifest.json`.
