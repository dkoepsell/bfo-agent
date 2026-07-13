# NFIP Coverage Demonstrator - Build Spec (Claude Code)

Status: IMPLEMENTED 2026-07-12 (not for deployment). Local build only. No Hetzner/DGX push.
Built under `nfip/`; page at `client/coverage.html`; routes `/coverage` + `/coverage/findings`
in `app/orchestrator.py`. Run: `python -m nfip.run_analysis` then start the server.
Scope delivered: Dwelling Form only, no court cross-check (both deferred per user).
Source specs: `coverage-kernel-spec.md`, `nfip-extraction-spec.md`.
Source text: `44CFR61NFIP.txt` (258 KB / 3,773 lines; SFIP forms from 44 CFR Part 61 App A).
Target: full NFIP demonstrator (kernel + Dwelling Form extraction + two proof shapes + a
demonstrator page served from a Flask route in `run.py`).

This spec maps the two design specs onto the actual repo pipeline so a Claude Code agent can
execute it without re-deriving where things live.

---

## 0. Pipeline facts (verified, do not re-discover)

- Web server: `run.py` (Flask). UI shell: `client/index.html`. Add the demonstrator route here.
- Extraction: `app/orchestrator.py` (orchestrate) + `app/extractor.py` + `app/llm_proposer.py`,
  driven as a feed job via `app/job_runner.py`. Output lands in `ontology/library/<Corpus>/`
  with `jobs/job_*.json` manifests and `sessions/*.jsonl`.
- Gates (all evidence-only, never auto-correct): `app/construction_linter.py` (PC-1..PC-9),
  `app/gate_structural.py`, `app/coherence_gate.py` (HermiT), `app/kernel_audit.py`,
  `app/fol_gate.py` (Prover9/Mace4, `FOL_GATE_ENABLED` off by default),
  `app/incoherence_ledger.py` (FLAG outcomes, faithful/curated fidelity).
- Reasoner: HermiT via owlready2 (`sync_reasoner`, `inconsistent_classes()`).
- Kernel: `bfo.owl` (BFO 2020) is the dominant kernel. Coverage kernel imports it.
- Stable IRIs: `app/stable_iri.py`.

## 1. Deliverables (files this build creates)

1. `ontology/kernels/coverage-kernel.owl` - the reusable coverage kernel (proprietary asset).
2. `ontology/kernels/coverage-kernel-fixtures.ttl` - self-test fixtures (a known-bad carveback,
   a known collision) proving both proof shapes fire on toy input before real extraction.
3. `nfip/segment_sfip.py` - deterministic segmenter: `44CFR61NFIP.txt` -> clause records.
4. `nfip/sfip_clauses.json` - segmented Dwelling Form clauses (typed: grant/exclusion/
   carveback/condition/definition, with verbatim text + CFR citation).
5. `ontology/library/NFIP_SFIP_v1/` - the extracted + hand-repaired ontology (kernel-anchored).
6. `nfip/proofs/` - the two proof-shape ABox/TBox test individuals (see Section 5).
7. `nfip/run_analysis.py` - runs reasoner + justification extraction -> `nfip/findings.json`.
8. `nfip/findings.json` - the product surface: findings with quoted clause text (E-5 allows it).
9. Flask route + template for the demonstrator page (Section 7).

Nothing under `nfip/findings.json` or the page exposes the kernel axioms or the typology.
Findings are the product; the kernel is the asset (see Section 8).

---

## 2. Phase A - Coverage kernel (`coverage-kernel.owl`)

Implement `coverage-kernel-spec.md` literally. Import `bfo.owl`. Key content:

Clause ICE types (all subclasses of `iao:InformationContentEntity` / BFO generically dependent
continuant): `Grant`, `Exclusion`, `Carveback`, `Condition`, `Definition`.

Entities: `Policy` (a document act), `IndemnityObligation` (BFO_0000023 realizable entity),
`Insurer`, `Insured` (roles), `Loss` (occurrent), `DefinedTerm`.

Relations (object properties): `grants` (Grant->IndemnityObligation), `excludes`
(Exclusion->LossClass), `restores` + `modifies` (Carveback->LossClass / Carveback->Exclusion),
`conditions` (Condition->IndemnityObligation), `defines` (Definition->DefinedTerm),
`uses` (Clause->DefinedTerm), `followsForm` (Layer->Primary).

The pivot axiom (this is the whole design):
```
CoveredLoss  and  UncoveredLoss  SubClassOf  owl:Nothing      # disjoint - bivalent domain
```
Bearer discipline (the DSM lesson, 379 unsat classes): `IndemnityObligation` inheres in the
`Insurer`, NOT in the `Policy` or the `Loss`. Add and unit-test this before anything else.

Acceptance: `coverage-kernel.owl` loads consistent under HermiT with no unsatisfiable classes;
`coverage-kernel-fixtures.ttl` (deliberately broken) produces exactly one unsat class (Shape 1)
and, in a second fixture, one ABox inconsistency with a 2-clause justification (Shape 2).

---

## 3. Phase B - Segment the SFIP text (`segment_sfip.py`)

Deterministic (no LLM) segmentation of `44CFR61NFIP.txt`, scoped to the **Dwelling Form** first
(the spec's primary target; Group Flood + Homeowner drift are Phase F stretch).

Emit `sfip_clauses.json`, one record per clause:
`{ id, cfr_cite, sfip_section, clause_type_hint, verbatim_text, defined_terms_used }`.

Priority clauses to guarantee are captured (named in `nfip-extraction-spec.md`):
- Coverage A/B/C/D grants.
- Coverage D "subject to Coverage D Exclusion 5.g" - the grant-conditioned-on-exclusion
  cross-reference (Shape 1 candidate).
- EXCLUSIONS list.
- The Part 59 vs appendix `Claim`/definition tiebreaker text (same-term-two-meanings, Shape 2).

`clause_type_hint` is a hint only; typing is finalized during extraction, not asserted as truth.

---

## 4. Phase C - Extract + anchor (bfo-agent pipeline)

Run the standard pipeline against `sfip_clauses.json`, anchored to `coverage-kernel.owl`
(which itself anchors to `bfo.owl`). Reuse `app/orchestrator.py` + gates; do NOT bypass the
construction linter or coherence gate.

- Fidelity = **faithful** (extract the text's structure as written; the demonstrator's whole
  point is to surface the text's own contradictions, per the extraction-fidelity principle).
- IRI hygiene via `app/stable_iri.py`.
- Output to `ontology/library/NFIP_SFIP_v1/`. Expect hand-repair after the automated pass
  (the spec budgets manual repair; log every manual edit in the job manifest).

Acceptance: extracted TBox is coherent EXCEPT where the text is genuinely broken - those
survive as ledger FLAGs, not silent fixes.

---

## 5. Phase D - Encode the two proof shapes (`nfip/proofs/`)

Shape 1 (coherence failure, TBox): a coverage class is unsatisfiable = "this coverage cannot
pay anything." Target: the Coverage D / Exclusion 5.g carveback structure. Assert the restored-
loss class; if the exclusion never removed those losses, `CarvebackRestoredLoss SubClassOf
owl:Nothing`. Also test definitional gutting (`AdvertisedLoss and CoveredLoss = Nothing`).

Shape 2 (inconsistency, ABox): one concrete `Loss` individual entailed into BOTH `CoveredLoss`
(via a grant) and `UncoveredLoss` (via a condition that reads `Claim` under the other
definition). Ontology goes inconsistent; the justification names the colliding clauses. This is
where the Part 59/appendix `Claim` tiebreaker pays off - the regulator legislated the conflict,
so it is not hypothetical.

Each proof is a small, checked-in TTL fixture layered on `NFIP_SFIP_v1`, plus an expected-result
assertion (which class is unsat / which individuals collide).

## 6. Phase E - Run + justify (`run_analysis.py` -> `findings.json`)

For each proof: load kernel + `NFIP_SFIP_v1` + the proof fixture; run HermiT via the existing
coherence-gate path; on unsat/inconsistency, extract the justification (responsible axioms ->
back-map to clause IDs via the manifest). Optionally corroborate with `fol_gate.py`
(Prover9/Mace4) for a second independent witness. Record everything through
`app/incoherence_ledger.py`.

`findings.json` schema (per finding): `{ shape, verdict, coverage_or_loss, responsible_clauses:
[{cfr_cite, sfip_section, verbatim_text}], plain_english, court_crosscheck? }`.
E-5: verbatim SFIP clause text MAY be quoted (public domain) - do it, so each finding is
independently checkable. Optional court cross-check: cite a public NFIP opinion that wrestled
with the same ambiguity (turns "our tool found something" into "our tool found the thing the
court spent 30 pages on").

## 7. Phase F - Demonstrator page (Flask route in `run.py`)

Add a route (e.g. `/coverage` or `/nfip`) to `run.py` that reads `nfip/findings.json` and
renders a page styled to match `case_report.html` (same font stack / token palette; map, do not
restyle). Reuse a verdict component if one already exists rather than introducing a second design
language.

Page tells the scroll story: real public policy -> here are provable coherence failures in its
own wording -> each finding shows the responsible clauses verbatim -> (optional) a court fought
over this same clause.

Demo discipline (HARD constraint from the integration spec): reveal capability, never mechanism.
- No "how it works", no "view the rules", no axiom dump, no debug/kernel view, no reasoner
  internals. The kernel and typology stay server-side.
- The page ships findings only. If a reader wants the method, that is the paid review.

Stretch (Phase F+, only if time): Group Flood Policy as `followsForm` of the Dwelling Form
(public-domain follow-form test); Homeowner Flood Form (App A(4)) as versioned drift.

---

## 8. Proprietary boundary (enforce in code, not just docs)

- `coverage-kernel.owl`, the clause typology, and the two proof-shape encodings are the ASSET.
  They live server-side and are never emitted to the client or written into `findings.json`.
- `findings.json` and the page carry FINDINGS ONLY (verdict + verbatim clause + plain English).
- No route serves the kernel OWL, the fixtures, or justification axiom sets to the browser.

## 9. Out of scope / will NOT do

- No deployment (Hetzner/DGX). Local only.
- No free-text policy intake / live underwriter workflow (that is the fundable Track-2 module,
  not this demonstrator).
- No changes to the legal Case Reader (`case_server.py`, `case_report.html`) or the ICD/DSM/SOoL
  feeds.
- No auto-correction of the SFIP text; contradictions are reported, never "fixed."

## 10. Open questions for the user

1. Route name + whether it sits behind the existing Caddy basicauth or stays purely local.
2. Court cross-check: include one public NFIP opinion now, or leave `court_crosscheck` as a
   later enrichment?
3. Scope of first pass: Dwelling Form only (recommended), or all three SFIP forms up front?
