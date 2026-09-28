# Spec: Output Quality and Reasoner Visibility

Status: DRAFT, scoped 2026-09-28, not yet implemented.
Companions: `fol-gate-spec.md` (FG-0), `fidelity-mode-spec.md` (FM-*), `SPEC-bfo-agent.md` (coherence gate).
Deliverable tool already written: `scripts/ontology_audit.py` (ships with this spec; move it into `scripts/`).

## 0. Assumptions and how to adapt

Line numbers and function names below were read from `main` on 2026-09-28. Before editing, re-read each cited function; if it has moved or already partially implements a requirement, extend it rather than duplicating it. In particular, check `app/sool_extension.py`, `app/realizable_misuse.py`, `scripts/detect_canonicalization.py` and the coverage-kernel work before adding anything under sections D and F, since they may already cover part of it.

Every requirement has an ID (`QS-xx`). Reference IDs in commit messages and test names.

## 1. Motivation and evidence

Two artifacts were audited with `scripts/ontology_audit.py`:

| Metric | `sool.owl` (981-class run) | `ontology/library/SOoL_v1/working.owl` (Zenodo artifact copy) |
|---|---|---|
| Local classes / individuals | 981 / 415 | 6,881 / 1,678 |
| Visibility ratio (reasoner-visible / intended axioms) | 0.753 | 0.320 |
| Reasoner-invisible axioms | 1,070 | 19,737 |
| Mangled standard predicates (`working#rdfs:subClassOf`, `working#rdf:type`, `owl:subClassOf`) | 0 | 13,102 |
| Punned relation triples (object property asserted between classes, or class and individual) | 1,070 (873 class to class) | 6,635 |
| Malformed local IRIs | 8 | 250 (188 CURIE in fragment, 51 sentences or restriction text, 11 upper-ontology IDs) |
| Local namespace | http working# | `file:///home/drkoepsell/...` plus http working# |
| Declared object properties | 11, all unused | 0 |
| Undeclared properties used | 10 | 77 |
| Restrictions (empty / with domain-level filler) | 399 (5 / 0) | 0 |
| Definition coverage | 0% | 0% |
| Redundant asserted parents | 235 | 16 |
| Max direct fan-out | 207 (Legal Process) | 2,173 (BFO process) |
| Local disjointness axioms | 0 | 1 |
| Near-synonym clusters (classes covered) | 105 (248) | 596 (1,389) |
| Meta-annotated labels (`example...`, `(reuse)`, `(instance)`) | 238 | 0 |

Consequences:

1. The HermiT certificate on the published artifact is true of what the reasoner saw, which is about a third of what was extracted. README claims about consistency need to be restated after migration (QS-C5).
2. Even in the newer run, one quarter of extracted content is invisible, the reasoner-visible restrictions carry no domain content, and there is no disjointness below BFO, so the consistency gate has very little to catch.

Root causes were confirmed against current `main`:

```
_resolve_iri("RO_0000052")              -> working#RO_0000052        iri_is_malformed: False
_resolve_iri("IAO_0000115")             -> working#IAO_0000115       iri_is_malformed: False
_resolve_iri("_:r1")                    -> working#_:r1              iri_is_malformed: False
_resolve_iri("working:rdfs:subClassOf") -> working#rdfs:subClassOf   iri_is_malformed: False
_resolve_iri("ro:RO_0000052")           -> working#ro:RO_0000052     iri_is_malformed: False
```

`ontology_manager._add_relation` writes `(class, objectProperty, class)` as a raw rdflib triple, which OWL 2 DL treats as punning and the reasoner ignores. `coherence_gate.scaffolding_directives` emits restrictions whose fillers are always top-level BFO classes (`INDEPENDENT_CONTINUANT`, `PROCESS`).

## 2. Governing principle: construction tier versus content tier

FG-0 and FM-3 apply unchanged: the system represents the text, including the text's own errors, and never corrects the text's claims. Every requirement below is classified:

- **[CONSTRUCTION]**: a defect in the tool's rendering (malformed IRI, wrong predicate, punned triple, missing declaration). It is fixed or refused in both modes, because it is the proposer's or serializer's error, never the author's. Migrating legacy artifacts to repair construction defects (section C) is permitted under FG-0 because it restores the claim the text made rather than altering it.
- **[CONTENT]**: a judgment about the ontology's substance (disjointness, Hohfeldian placement, fan-out). In `faithful` mode it is report-only: findings go to the incoherence ledger and the job report, never a reject, repair or rollback. In `curated` mode it may gate.
- **[ENTAILMENT-PRESERVING]**: a change that alters no entailment (for example, dropping an asserted parent already entailed by another asserted parent). It is allowed in both modes and logged.

## 3. Workstream A: write-path correctness (P0, all [CONSTRUCTION])

**QS-A1. Resolver covers OBO IDs and known prefixes.** In `ontology_manager._resolve_iri`:
- Bare `RO_\d+`, `IAO_\d+`, `BFO_\d+`, `OBI_\d+` resolve to `http://purl.obolibrary.org/obo/<ID>`.
- `ro:`, `iao:`, `obo:`, `bfo:` prefixes resolve to the OBO base.
- Any other string containing `:` that is not an absolute IRI and not a known prefix raises `ValueError` (read paths may return a sentinel instead of raising; write paths must refuse).
- `working:` followed by a string that itself contains `:` (e.g. `working:rdfs:subClassOf`) is refused, not concatenated.

**QS-A2. `owl_checks.iri_is_malformed` catches what A1 cannot.** Return `True` when a local-namespace fragment:
- contains `:`, which covers CURIEs pasted into fragments, nested `file://` IRIs and `_:` blank-node labels;
- matches `^(BFO|RO|IAO)_\d+$`, since upper-ontology IDs must never be minted locally. The existing `_UPPER_ID` regex should be reused here;
- starts with `_:`.

Keep the existing whitespace and construct-name checks. The same function in `sool_owl_checks.py` is a duplicate; make it import from `app/owl_checks.py` so they cannot drift.

**QS-A3. No punned object-property assertions.** In `_add_relation`, after resolution, classify subject and object as class, individual, or unknown using the live world:

| Subject | Object | Action |
|---|---|---|
| individual | individual | write the property assertion (unchanged) |
| class | class | route to `add_existential_restriction(s, p, o)`: `s ⊑ p some o` |
| class | individual | write `s ⊑ p value o` (`owl:hasValue`) |
| individual | class | refuse; re-prompt for an individual of that class or a class-level restriction |
| unknown on either side | any | refuse |

When the proposer did not state a quantifier, annotate the restriction with `bfoagent:quantifierDefaulted true` so the `some` reading is auditable. The resulting axiom goes through the normal gate: in faithful mode a clash goes to the ledger, not the bin.

The proposer schema (`app/schema.py`) should gain an optional `quantifier` field (`some` / `only` / `value`) on relations, and the system prompt in `app/llm_proposer.py` should ask for it on class-level relations.

**QS-A4. Predicate allowlist at write time.** A relation predicate must be one of: `rdf:type`, `rdfs:subClassOf`, `owl:disjointWith`, `owl:equivalentClass`, a declared `owl:ObjectProperty`, or a declared `owl:AnnotationProperty`. Anything else is refused with a message naming the offending predicate. This is the check that would have stopped `working#rdfs:subClassOf` and `owl:subClassOf`.

**QS-A5. One local namespace.** `WORKING_IRI` is the only local base. On load and on save, any entity under a `file://` base is an error in new jobs (QS-C1 handles legacy files). `ontology_manager._load` should set the ontology IRI explicitly so owlready2 never serializes with a filesystem base.

**QS-A6. Sentences never become IRIs.** Free text in an entity or relation slot is refused (already partly covered by the whitespace check). Where the proposer is clearly emitting a gloss or thesis statement, the construction linter should suggest `rdfs:comment` on the subject instead. Add a PC rule if none exists.

## 4. Workstream B: relation vocabulary (P0)

**QS-B1. One canonical relation vocabulary. [CONSTRUCTION]** Current artifacts mix RO IRIs (declared, unused) with BFO 2020 IRIs (used, undeclared). Pick one, declared in the seed, with an alias table that maps the other at resolve time.

Default recommendation (OPEN DECISION D-1, confirm with David): BFO 2020 relation IRIs (`BFO_0000197` inheres in, `BFO_0000196` bearer of, `BFO_0000054` has realization, `BFO_0000055` realizes, `BFO_0000056` participates in, `BFO_0000057` has participant, `BFO_0000058` is concretized by, `BFO_0000059` concretizes, `BFO_0000066` occurs in, `BFO_0000108` exists at). This is because the import is `bfo.owl` and the proposer already emits these IRIs. Map `RO_0000052 → BFO_0000197`, `RO_0000053 → BFO_0000196`, `RO_0000056 → BFO_0000056`, `RO_0000057 → BFO_0000057`, `RO_0000058/59 → BFO_0000058/59`.

Keep RO for relations BFO 2020 does not provide (`RO_0000087` has role, `RO_0001025` located in) and declare them.

**QS-B2. Declarations with semantics. [CONSTRUCTION]** The relations seed (`scripts/patch_add_relations_seed.py` or its successor) declares every canonical property with label, inverse, domain, range, and characteristics (transitive for part-of). Remove declarations that nothing can use.

**QS-B3. Declared-but-unused and used-but-undeclared are job-report findings.** Both appear in the audit (QS-G1) and must be zero before finalization.

**QS-B4. Annotation properties.** Declare a `bfoagent:` annotation namespace (`http://davidkoepsell.com/bfo-agent/meta#`) with at least `quantifierDefaulted`, `scaffolded`, `sourceSpan`, `definitionStatus`, `migratedFrom`, `distinctFrom`.

## 5. Workstream C: legacy migration and re-certification (P0)

**QS-C1. `scripts/migrate_invisible_axioms.py`. [CONSTRUCTION]** The script is read-only on its input and writes a new file plus a JSON migration report. It applies these steps in order, counting each:

1. **Namespace unification.** Rewrite `file:///...working.owl#X` to `WORKING_IRI#X`. Nested cases (`working#file:///...#X`) resolve to `WORKING_IRI#X`.
2. **Mangled predicates.** `working#rdfs:subClassOf` becomes `rdfs:subClassOf`, `working#rdf:type` becomes `rdf:type`, `owl:subClassOf` becomes `rdfs:subClassOf`. Also rewrite `working#rdfs:comment`, `working#rdfs:seeAlso` and `working#owl:disjointWith`.
3. **Locally minted upper IDs.** `working#BFO_0000040` becomes `obo:BFO_0000040`; `working#RO_0000052` goes through the QS-B1 alias table.
4. **Restriction text inside IRIs.** For objects such as `RO_0000052 some working:LegalInstitution` or `_:x [owl:onProperty ...; owl:someValuesFrom ...]`, parse with `owl_checks.parse_class_expression` (extend the parser for the bracketed forms seen in the artifact) and emit a real restriction on the subject. Unparseable text becomes an `rdfs:comment` on the subject plus a ledger entry.
5. **Sentence IRIs.** An IRI whose fragment contains whitespace becomes an `rdfs:comment` literal on the subject, with `bfoagent:migratedFrom` recording the original predicate.
6. **Punned triples.** Apply the QS-A3 table.
7. **Blank-node label IRIs (`working#_:r1`).** Resolve these to the restriction they name if one exists on the same subject; otherwise drop them and add a ledger entry.

Each step's output is gated in faithful mode: an axiom that restores the text's claim but clashes is kept out of the coherent view and ledgered (FM behavior), never silently dropped.

**QS-C2. Idempotence.** Running the migration twice produces a byte-identical second output. Add a test.

**QS-C3. Full verification.** After migration, run `verify_full` and the FOL gate audit. Record consistency, the unsatisfiable-class list, and ledger size in the report.

**QS-C4. Versioning.** Migrated artifacts get a new library version and a new Zenodo version (OPEN DECISION D-4: new version of the same concept DOI, recommended, versus a new record). Put a changelog in `manifest.json` under `migration` with step counts.

**QS-C5. README claim hygiene.** Wherever the README states a consistency result, it must also state the visibility ratio, the number of local disjointness axioms, and the fidelity mode. Replace the current "HermiT-consistent under 9 BFO disjointness axioms" line after re-certification.

## 6. Workstream D: proposal-time content quality

**QS-D1. Definitions required. [CONSTRUCTION]** Add `definition: str` and `source_span: str` to new-class entities in the proposal schema. Write the definition as `IAO_0000115` and the span as `bfoagent:sourceSpan`.

The definition should follow genus-differentia form, with the genus being the asserted parent's label; the linter checks that the parent label appears in the definition. In faithful mode the definition must be drawn from the source span. If the text gives none, set `bfoagent:definitionStatus "absent-in-source"` rather than inventing one. A new construction-linter rule (PC-9) rejects a new class with neither a definition nor that status.

**QS-D2. Scaffolding carries content or does nothing. [CONTENT]** In `coherence_gate.scaffolding_directives`:
- stop emitting directives whose filler is a top-level BFO class (`INDEPENDENT_CONTINUANT`, `PROCESS`) by default (`SCAFFOLD_UPPER_FILLERS=0`);
- emit a directive only when the proposal or graph supplies a domain-level filler;
- annotate every scaffolded restriction `bfoagent:scaffolded true` so it can be filtered or removed later.

Faithful mode already disables scaffolding per the FM spec; this change affects curated mode.

**QS-D3. Reuse before minting. [CONSTRUCTION]** Before a new class is minted, retrieve the top-k existing classes by normalized-label key (suffix-stripped as in the audit's synonym clustering) and by embedding similarity. The proposer receives the candidates and must either reuse one or supply `bfoagent:distinctFrom <IRI>` with a source span showing the text distinguishes them.

A new linter rule (PC-10) rejects a mint whose normalized key collides with an existing class and that has no `distinctFrom` justification. This moves canonicalization from post hoc (`detect_canonicalization.py`) to commit time.

**QS-D4. Throttle category triads. [CONSTRUCTION]** The proposer currently mints disposition, process and quality variants of one noun (for example Recognition, Recognition Disposition, Recognition Process, Recognition Quality). A realizable/realization pair may be minted only when the source span names or describes the realization. Otherwise, mint the one category the text supports. Enforce this through a prompt instruction plus a lint warning (PC-11) that fires when a proposal mints two classes sharing a normalized key in different BFO categories without source support for both.

**QS-D5. Transitive reduction of asserted parents. [ENTAILMENT-PRESERVING]** On commit, drop an asserted named parent `P` of `C` when another asserted named parent `Q` of `C` already has `P` as an ancestor. Log each removal in the delta so rollback restores it.

**QS-D6. Fan-out findings. [CONTENT]** The job report lists classes with more than `FANOUT_LIMIT` (default 40) direct named subclasses. In curated mode the proposer summary for a hub includes its current children, so the model can place new classes under intermediate classes. There is no forced restructuring in either mode.

**QS-D7. Label hygiene. [CONSTRUCTION]** Labels must not carry meta-annotations: parenthetical `(reuse)`, `(instance)`, `(class)`, `(example ...)`, or an `example` prefix. Add a linter rule (PC-12). Reuse is expressed by reusing the IRI, not by relabeling.

**QS-D8. ABox separation.** Individuals created as illustrations rather than as referents in the text (currently marked by `example` labels) are written to a separate module `examples.owl` that imports the working ontology. The proposer schema gets an `illustrative: bool` flag on individuals.

## 7. Workstream E: give the reasoner teeth

**QS-E1. Domain profiles. [CONTENT]** Add `ontology/profiles/<name>.owl`, selected by a `profiles` list in `manifest.json`. A profile contains disjointness and category constraints over canonical classes (section F), not over extracted IRIs. First profile: `legal-hohfeld.owl`:
- Pairwise disjointness among the eight Hohfeldian positions.
- All eight positions under one BFO category, with the category fixed by OPEN DECISION D-2 (role versus disposition). The uploaded run split them: Claim ⊑ role, Right and Power ⊑ disposition.
- Disjointness between legal-act classes (process) and legal-document classes (GDC). This targets the conflations seen in the audit, e.g. Judgment and Contract placed only under Legal Document.

In faithful mode, profile violations are ledger findings (for example "Permission ⊑ Obligation violates legal-hohfeld: Privilege ⊓ Duty = ⊥"). In curated mode they reject at the reasoner tier.

**QS-E2. Gate-recall benchmark.** Add `evaluation/gate_recall.py` with a fixture set of known-bad proposals, each labelled with the tier that should catch it:

| Fixture | Expected catcher |
|---|---|
| quality ⊑ process | BFO disjointness |
| `working#rdfs:subClassOf` predicate | QS-A4 |
| `RO_0000052` bare | QS-A1 |
| `_:r1` as object | QS-A2 |
| class-to-class `realizes` triple | QS-A3 (converted, not stored raw) |
| restriction on a kernel class | existing guard |
| Permission ⊑ Obligation | QS-E1 profile |
| sentence as object IRI | QS-A6 |

Run each against a scratch copy of a real artifact, never the artifact itself. Report recall per tier with and without profiles. This becomes a paper-grade evaluation complementing the refusal probe.

**QS-E3. Certification reporting.** The finalization certificate records: consistency, unsatisfiable classes, visibility ratio, local disjointness count, profiles active, and fidelity mode.

## 8. Workstream F: canonical anchoring (SOoL first, generic mechanism)

**QS-F1. Canonical seed.** `manifest.json` gains `canonical_seed: <path>`. For SOoL this points to `SOoLRev2.owl`, which is not in the repo; ask David for the path or have him add it. It holds the canonical contradiction-type IRIs (K-A1 through K-D3), the MLC nodes (Source of Authority, Norm, Actor in Role, Triggering Facts, Legal Act or Omission, Target, Legal Effect, Remedy), the IAT components, and Contradiction Debt.

Seed classes are loaded read-only, like the BFO kernel, and are never mutated (reuse the kernel-class guard in `_add_relation`).

**QS-F2. Canonical-first retrieval. [CONSTRUCTION]** `summary_for_proposer` and the QS-D3 candidate retrieval rank canonical classes first. A mint whose normalized label matches a canonical label must reuse the canonical IRI (part of PC-10).

**QS-F3. Coverage report. [CONTENT]** At end of job, list each canonical class with its count of anchored subclasses, instances and restrictions, and flag zero-coverage terms. In the 981-class run, Remedy, Triggering Facts, the 13 contradiction types, IAT, Contradiction Debt as a quantity, and Legal Person were absent. Coverage gaps are findings, never prompts to invent content (FG-0).

**QS-F4. Design decisions as profile data, not model judgment.** Category decisions the text or the author settles are recorded in the profile and enforced by E1, so the LLM does not decide them case by case. The chief example is norm content as GDC versus normative position as SDC, against the canonical "legal entities are SDCs" (OPEN DECISION D-3).

## 9. Workstream G: metrics, audit, CI

**QS-G1. Audit tool.** Commit `scripts/ontology_audit.py` as delivered. It is read-only and outputs JSON plus a summary. Metrics: `visibility_ratio`, `malformed_iris`, `mangled_standard_predicates`, `punned_triples`, `undeclared_properties_used`, `declared_properties_unused`, `empty_restrictions`, `domain_filler_share`, `definition_coverage`, `redundant_parent_assertions`, `max_fanout`, `hubs_over_limit`, `local_disjointness_axioms`, `synonym_clusters`, `meta_labels`, `file_scheme_namespaces`.

**QS-G2. Wire into the pipeline.** `job_runner` runs the audit at job completion and attaches the JSON to the job record. `interim_report.py` includes the summary, and so does the ntfy completion message.

**QS-G3. Finalization gates.** These are construction-tier only, so they are consistent with FG-0 in both modes. An artifact cannot be finalized unless:

```
malformed_iris == 0
mangled_standard_predicates == 0
punned_triples == 0
empty_restrictions == 0
undeclared_properties_used == 0
file_scheme_namespaces == 0
visibility_ratio >= 0.99
```

Curated mode additionally requires `definition_coverage >= 0.95`. Faithful mode reports it: `definitionStatus "absent-in-source"` counts as covered.

**QS-G4. CI.** Add a CI job that runs the audit with the QS-G3 gates on a small fixture ontology built by the test suite, plus the gate-recall benchmark at a fixed seed.

## 10. Test plan

New and extended tests, named `test_qs_<id>_*`:

- `tests/test_resolver.py`: the five strings from section 1 each resolve correctly or raise (QS-A1, QS-A2).
- `tests/test_restriction_axioms.py` (extend): each row of the QS-A3 table, including `hasValue` and the refusal cases, plus the `quantifierDefaulted` annotation.
- `tests/test_commit_guard.py` (extend): predicate allowlist (QS-A4); `file://` base refused (QS-A5).
- `tests/test_migration.py`: a fixture OWL containing one instance of each legacy defect (mangled predicate, nested file IRI, restriction text in IRI, sentence IRI, `_:r1`, locally minted BFO ID, punned triple). Assert per-step counts, idempotence (QS-C2), and that the audit on the output passes QS-G3.
- `tests/test_construction_linter.py` (extend): PC-9 through PC-12.
- `tests/test_scaffolding.py` (extend): no upper-level fillers by default; `scaffolded` annotation present.
- `tests/test_transitive_reduction.py`: redundant parent removed, rollback restores it, entailments unchanged.
- `tests/test_ontology_audit.py`: metrics on a hand-built fixture with known counts.
- Faithful-mode regression: a profile violation produces a ledger entry and no rejection; a construction violation is still rejected.

## 11. Implementation order

1. QS-G1, then the resolver and IRI guard tests (QS-A1, A2). This makes the defects measurable and stops new ones.
2. QS-A3 to A6 and QS-B1 to B4. After this, new jobs produce reasoner-visible output.
3. QS-C1 to C3 on `SOoL_v1`, `SpinozaCorpus`, `LeibnitzPhilCorpus`, `GeometryofTheGood` and `NFIP_SFIP_v1`. Audit each before and after, then do QS-C4 and C5. This step should happen before the three-ontology paper is submitted.
4. QS-D1, D3, D5, D7 (definitions, reuse, reduction, labels), then D2, D4, D6, D8.
5. QS-F1 to F3, then QS-E1 and E2 (profiles depend on canonical classes).
6. QS-G2 to G4 and QS-E3.

Re-run the 20-probe refusal evaluation after step 3, because the graph it queries will change materially.

## 12. Open decisions (ask David before implementing the dependent items)

- **D-1 (QS-B1):** BFO 2020 relation IRIs as canonical, with RO mapped in (recommended), or the reverse.
- **D-2 (QS-E1):** BFO category for Hohfeldian positions: role (externally grounded, fits the recognition thesis) or disposition (fits "obligation as disposition" in the current extraction).
- **D-3 (QS-F4):** norm content as GDC with normative positions as SDC, or all legal entities as SDC per the SOoL canonical statement.
- **D-4 (QS-C4):** new Zenodo version under the existing concept DOI, or a new record.
- **D-5 (QS-G3):** whether `domain_filler_share` should become a curated-mode gate once scaffolding is fixed. It is report-only here.

## 13. Non-goals

- No change to FG-0 or FM semantics. Nothing here lets a content-tier check alter, drop or roll back a claim the text makes in faithful mode.
- No automatic restructuring of hubs or merging of existing synonym clusters in published artifacts. Clusters are reported, and merges are an explicit curated-mode operation for a later spec.
- No replacement of HermiT or changes to the FOL gate.
