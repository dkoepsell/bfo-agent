# NFIP -> Prod Coverage-Coherence Integration - Build Spec (Claude Code)

Status: IMPLEMENTED 2026-07-12 (local prod codebase; NOT deployed). All routes/functions
verified over HTTP. The live pure-feed breadth extraction is intentionally deferred so it cannot
compete with the remote ICD-11 feed for the shared LLM account; NFIP is seeded with the proven
deterministic core (queryable now) and the coverage-aware feed machinery is ready for breadth
once ICD-11 finishes. Deploy (Hetzner) is a separate, ICD-gated step.

Extends the local demonstrator (`nfip/`, see `nfip-demonstrator-build-spec.md`)
into the production bfo-agent so a user can query an extracted NFIP ontology, get reasoner
coherence scoring, test well-formed claim language, AND paste new policy wording for on-the-fly
coherence analysis - all from the main bfo-agent UI, without disturbing the feed run or any
existing feature.

Decisions locked by the user:
- Extraction = **pure feed** (run the whole Dwelling Form through the existing LLM feed with
  coverage-aware anchoring rules).
- Scope = **also accept new policy wording** (the pre-bind / Track-2 module).
- Surface = **main bfo-agent query UI** (`client/index.html`), NFIP as one selectable ontology.

Design principle: everything below is ADDITIVE. NFIP is just another registered ontology; the
coverage capability is new routes + optional response fields + opt-in coverage mode. No existing
route changes behavior; no existing feed is touched.

---

## 0. Prod seams (verified - reuse, do not rebuild)

- `app/registry.py OntologyRegistry` auto-discovers `ontology/library/<name>/` (seed `.ttl` +
  manifest, bootstrapped on BFO). Register NFIP here -> `/query`, feed, propose/commit work
  per-ontology unchanged.
- Reasoner scoring already exists: `OntologyManager.check_coherence_dry_run()` ->
  `(coherent, unsat_classes, detail)`, distinguishing unsatisfiable classes (Shape 1) from
  outright inconsistency (Shape 2). This is the scoring primitive.
- Claim-language validation already exists: `/propose` -> `construction_check` -> `lint_check`
  (PC-1..PC-9, `app/construction_linter.py`) -> `reasoner_check` (`app/coherence_gate.py`).
  A non-committing dry-run of this chain IS the claim tester.
- Proposer anchoring rules live in `app/llm_proposer.py PROMPT_TEMPLATE` (numbered rules; same
  place the ICD-11 rules 14/15 were added). Respect the prompt-cache breakpoints when appending.
- Feed run: `app/job_runner.py` (durable thread, /progress, stall backstop) + `app/orchestrator.py`
  `_feed_one_core`. Bounded-LLM + stall monitor already in place - reuse for the analyzer.
- Multi-ontology `/query` already resolves `body.ontology` via `_resolve_ontology_for_read` and
  returns `{answer, referenced_iris, grounded}` (LLM-grounded NL Q&A; no reasoner today).

## 1. Deliverables

1. `ontology/library/NFIP_SFIP_v1/seed/coverage-kernel.ttl` - coverage kernel serialized to
   Turtle from `nfip/kernel.py` (the registry seed). Manifest flag `kernel_profile: "coverage"`.
2. Coverage-aware anchoring rules appended to `app/llm_proposer.PROMPT_TEMPLATE`, gated on
   `kernel_profile == "coverage"` so no other feed sees them.
3. A coverage extraction job over the full Dwelling Form (`44CFR61NFIP.txt`) -> populates
   `NFIP_SFIP_v1` through the normal feed pipeline + gates.
4. `app/coverage_reason.py` - reasoner scoring + claim/fact-pattern -> ABox -> verdict service
   (wraps `check_coherence_dry_run`; produces the Contradiction-Debt-style score).
5. New routes in `app/orchestrator.py`:
   - `POST /coverage/score`   - coherence score for a named ontology (+ optional claim ABox).
   - `POST /coverage/check-claim` - well-formed claim / fact pattern -> reasoned verdict.
   - `POST /coverage/analyze` - paste NEW policy wording -> ephemeral extract + check (Track-2).
6. `client/index.html` additions: coherence-score chip on query, a claim-test panel, and a
   "analyze new policy wording" panel (findings rendered inline, like the `/coverage` demo).
7. Coverage-aware regression: `nfip/fixtures.py` + a findings verifier asserting the extracted
   NFIP still yields the two proven findings (extraction quality gate).

Nothing new exposes the kernel axioms or the typology to the browser (Section 7 boundary).

---

## 2. Phase A - NFIP as a registered ontology (pure-feed extraction)

1. Serialize the coverage kernel to `NFIP_SFIP_v1/seed/coverage-kernel.ttl`. Add a manifest
   with `fidelity: "faithful"` and `kernel_profile: "coverage"`. The registry now bootstraps
   BFO + coverage kernel for this corpus; it is selectable everywhere immediately.
2. Coverage-aware anchoring rules (append to `PROMPT_TEMPLATE`, coverage-profile only):
   - Type every clause as exactly one of Grant / Exclusion / Carveback / Condition / Definition.
   - A Carveback MUST `modifies` a named Exclusion. A Grant may be `subjectTo` a Condition/Exclusion.
   - Model perils as loss-classes DEFINED by features (`exhibits.some(Feature)`); wire
     `Mudflow -> GrantedLoss`, `EarthMovement -> ExcludedLoss`, etc.
   - Preserve the policy's own disjointness claims as `owl:AllDisjoint` (e.g. II.C.20 mudflow vs
     earth movement). These definitional axioms are what make findings provable.
   - Bearer discipline: `IndemnityObligation inheres_in Insurer` (never the policy/loss).
   - Bivalence stays in the kernel seed; the feed never re-mints it.
3. Run the whole Dwelling Form as a normal feed job into `NFIP_SFIP_v1`. Existing
   construction_linter + coherence_gate re-validate every proposal. Feed run untouched.
4. Extraction quality gate (Section 6): after the feed, assert the extracted ontology still
   yields the two proven findings. If the LLM did not reliably emit the critical definitional
   disjointness (pure-feed variance is the known risk), the mitigation is a MINIMAL curated seed
   supplement carrying only those few disjointness axioms - kept in the seed, not hand-editing
   the fed TBox. Flag any such supplement explicitly in the manifest.

## 3. Phase B - reasoner scoring in the query flow (`coverage_reason.py`)

`score(ontology, claim_abox=None) -> {coherent, verdict, unsat_class_count, unsat_classes,
inconsistent, colliding_clauses, contradiction_debt}`:
- Loads the ontology's working world, optionally adds a claim ABox, calls
  `check_coherence_dry_run`.
- verdict maps: consistent+no-unsat = "coherent"; consistent+unsat coverage class = Shape 1;
  inconsistent = Shape 2.
- `contradiction_debt` = a compact weighted count (unsat coverage classes + entailed
  covered/uncovered collisions), reusing `app/incoherence_ledger.py` weighting where present.
- `colliding_clauses` = back-map responsible IRIs -> clause records (verbatim, for E-5 quoting;
  SFIP is public domain).
- Cache the base-ontology score per ontology version; only re-run when the ontology changes or a
  claim ABox is supplied (keeps query latency low - reasoner does not run on every keystroke).

Wire into `/query`: add an optional `score` block to the response when the selected ontology has
`kernel_profile == "coverage"`. NL Q&A behavior is otherwise unchanged (recaps current use).

## 4. Phase C - claim-language testing (`POST /coverage/check-claim`)

Input: a well-formed claim / fact pattern in the claim language (the existing `schema.py Claim`
model extended with the coverage loss/feature vocabulary). Flow, non-committing:
1. `construction_check` + `lint_check` -> is the claim WELL-FORMED? (returns lint verdict).
2. Encode the claim as ABox individuals (loss + exhibited features) against the selected
   coverage ontology.
3. `coverage_reason.score(ontology, claim_abox)` -> coherence verdict + colliding clauses + score.
Return `{well_formed, verdict, score, colliding_clauses}`. This is the demo's Shape-2 path made
interactive, and it reuses the entire existing propose/lint/reason chain minus the commit.

## 5. Phase D - analyze NEW policy wording (`POST /coverage/analyze`, Track-2)

Input: arbitrary pasted policy wording. Flow:
1. Create an EPHEMERAL world seeded on the coverage kernel (never the persisted corpus).
2. Coverage-aware extraction (reuse extractor + proposer in coverage profile), BOUNDED: chunk
   cap, per-call LLM timeout, and the `job_runner` stall backstop. Small wording = synchronous;
   larger = the existing job/progress mechanism with a returned job id.
3. `coverage_reason.score` + findings enumeration on the ephemeral ontology.
4. Return verdict + findings (quoting the USER's own submitted wording). Discard the ephemeral
   world by default; offer "save as ontology" to persist into a new library corpus.
Isolation, bounding, and error handling are the whole risk surface here - reuse the durable
job_runner primitives (bounded LLM + stall monitor, commits 41a64e87) rather than new machinery.

## 6. Phase E - extraction quality gate + regression

- `nfip/fixtures.py` (sanity gate + 3 synthetic fixtures) must be green before any verdict.
- Findings verifier: assert the freshly extracted `NFIP_SFIP_v1` still produces NFIP-F1
  (mudflow/earth-movement inconsistency) and NFIP-F2 (illusory mudflow carveback). This is the
  gate that catches pure-feed drift. Wire it into the feed's post-complete hook.
- Log (do not silently drop) any clause the coverage-aware extractor could not type - coverage
  gaps must be visible, not implied-complete.

## 7. Phase F - UI (`client/index.html`, additive)

- Ontology selector already multi-ontology: NFIP appears alongside SOoL/ICD/DSM.
- On query of a coverage-profile ontology: show a compact coherence-score chip (coherent /
  N unsat coverage classes / inconsistent) with a drill-down to colliding clauses (verbatim).
- New "Test a claim / fact pattern" panel -> `/coverage/check-claim`; shows well-formed? + verdict.
- New "Analyze policy wording" panel -> `/coverage/analyze`; renders findings inline in the
  `/coverage` demo style (reuse that renderer).
- All panels are opt-in sections; the propose/commit/feed builder UI is unchanged.

## 8. Preservation guarantees (must hold)

- Feed run and SOoL/ICD/DSM feeds untouched; coverage mode is opt-in via `kernel_profile`.
- `/query`, `/propose`, `/commit`, `/jobs`, `/progress` keep current behavior; only additive,
  guarded response fields and new `/coverage/*` routes.
- Prompt-cache breakpoints preserved when appending anchoring rules (see speed/caching work).
- The demo (`/coverage`, `nfip/run_analysis.py`) keeps working standalone.

## 9. Proprietary boundary (unchanged from the demo, enforced in code)

- Coverage kernel, clause typology, and proof encodings stay server-side. `/coverage/*`
  responses carry verdicts + clause references + score only - never kernel axioms.
- For `/coverage/analyze`, findings quote the user's OWN submitted wording; the kernel encoding
  is not returned. SFIP clause text may be quoted (public domain, E-5).

## 10. Risks / mitigations

1. Pure-feed variance vs stable findings (PRIMARY). Mitigation: strong coverage-aware anchoring
   rules + findings verifier gate + minimal curated seed supplement for the few non-negotiable
   definitional disjointness axioms (in the seed, flagged in the manifest).
2. Reasoner cost per query. Mitigation: cache base-ontology score per version; reason live only
   on claim submission / analyze.
3. New-wording extraction reliability + latency. Mitigation: ephemeral isolation, chunk/timeout
   bounds, reuse the durable job_runner + stall backstop; sync only for small inputs.
4. Anchoring-rule bloat degrading other feeds. Mitigation: rules gated on coverage profile only.

## 11. Out of scope

- No deployment (local first; deploy is a separate decision).
- No changes to the legal Case Reader.
- No auto-correction of policy wording; coherence is reported, never "fixed".
- Multi-document follow-form / cross-form comparison (Group Flood, Homeowner drift) remain the
  later enrichment from the demonstrator spec.
