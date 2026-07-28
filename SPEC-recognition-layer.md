# SPEC — Integrating the Recognition-Layer Typology into Social-Ontology Building

Source: `Recognition Layer.txt` (§§5–7, §6.4, §10, §13).
Status: **implemented** (P1–P9). See §7 for what landed and §8 for the validation run.

## 0. The claim we are implementing

The paper's operative equation is

    typology  =  kernel  ×  chain

- **Kernel** — twelve domain-neutral contradiction primitives in four strata:
  - A (model-theoretic): K-A1 Inconsistency, K-A2 Term Incoherence, K-A3 Indeterminacy
  - B (definitional): K-B1 Circularity, K-B2 Equivocation, K-B3 Residual Definition
  - C (meta-ontological): K-C1 Category Violation, K-C2 Level Confusion, K-C3 Dependence Violation
  - D (pragmatic): K-D1 Falsification, K-D2 Performative Self-Defeat, K-D3 Modal Clash (de jure / de facto)
- **Chain** — seven loci present in every recognition-constituted institution: *authority → criteria →
  assessor-in-role → presenting facts → recognition act → effects → remedy* (Table 1, twelve domains).
- **Stratum profile** (§6.4) — which primitives can fire at all, fixed by chain thickness. Scientific
  reference ontology: A, B, C only. Recognition-only institution (ICD-11, DSM, technical standards):
  A, B, C + thin D. Act-thick/repair-thick institution (legal order, licensure, refugee status): all twelve.
- **Detectability partition** (§6.3) — a DL reasoner sees A1/A2, C1 (given upper-level disjointness),
  B1 for cyclic subsumption, C3 partially. Structural axiom analysis sees A3, B2, B3, C2. World-facing
  data sees D1. Process modelling sees D2/D3. *A reasoner is necessary and never sufficient.*
- **CD** (§7) — contradiction debt: `CD(x) = Σ w(c)` over unresolved defects in which `x` participates;
  warrant-neutral (measures coherence, not clinical/legal validity), monotone under repair.
- **Discipline** (§10) — every finding must be attributed to *the source* or to *our translation of it*.

## 1. What the app already has (verified)

| Component | File | Status |
|---|---|---|
| Stratum A/B/C detectors: K-A1, K-A3b, K-B1, K-B3, K-C1, K-C2, K-C3 | `app/kernel_audit.py` | Works; called **only** from `scripts/icd_repropose.py`. Not in the build loop. |
| 8-node Minimal Legal Chain (authority → norm → actor-in-role → triggering facts → legal act → target → effect → remedy) | `app/sool_extension.py` | SOoL-gated, imported only by `app/mlc.py` and tests. **Not wired in.** |
| 13-type legal contradiction typology (FR-5) over MLC node pairs | `app/mlc.py` | **Dead code** — imported nowhere. |
| Gate tiers CONSTRUCTION → LINT → REASONER; K-A1/K-A2 in effect | `app/coherence_gate.py`, `app/gate_structural.py` | Live, in-loop. |
| Construction lint PC-1..PC-8 (BFO anchoring) | `app/construction_linter.py` | Live, pre-reasoner. |
| Findings ledger with `attribution` (source vs translation) | `app/incoherence_ledger.py` | Live — this is the §10 hook, already half-built. |
| Per-ontology manifest incl. `fidelity` (faithful/curated) | `app/registry.py` | Live — the natural home for the recognition profile. |
| Proposer rules 1–16, BFO anchoring, anchor-not-regenerate | `app/llm_proposer.py` | Live. No chain awareness. |
| Realizable-misuse detect/repair (roles/dispositions realized without process) | `app/realizable_misuse.py` | Live — reusable substrate for K-D3. |

**Gap.** The app builds social ontologies with a *BFO-only* kernel. The recognition chain exists only
as a legal-domain prototype that nothing calls, the kernel audit runs only offline on ICD-11, and
Stratum D — the stratum that distinguishes an institutional ontology from a scientific one — is
entirely uninstrumented. Nothing types findings by chain locus, and nothing declares which primitives
*could* fire, so a clean report is indistinguishable from an uninstrumented one (§7.5, §13).

## 2. Target architecture

Three orthogonal registrations, none of which mints new classes into `working.owl`:

1. **`app/recognition.py` (new)** — domain-neutral vocabulary:
   - `Locus` enum: `AUTHORITY, CRITERIA, ASSESSOR, FACTS, ACT, EFFECT, REMEDY`
   - `KERNEL`: the twelve primitives, each with stratum, definition, formal signature, and the
     *instrument* required (§6.3) — reasoner / structural / world-data / process-model.
   - `DOMAIN_PROFILES`: Table 1's twelve rows (legal order, clinical nosology, currency, academic
     credential, citizenship, corporate personhood, patent grant, professional licensure, refugee
     status, technical certification, electoral office, scholarly record) with default chain thickness
     and active strata.
   - `CT_TYPES`: CT-1..CT-7 as `(kernel, locus)` products, so the classification-specific types are
     *derived*, not a second hand-maintained list.
   - `MLC_ALIASES`: the existing 8 SOoL nodes and 13 FR-5 codes expressed as `(kernel, locus)` pairs.
     `app/mlc.py` and `app/sool_extension.py` become thin domain profiles over `recognition.py`;
     no FR-4/FR-5 semantics are lost, and the legal chain stops being a parallel universe.
   - BFO anchors per locus, fixed once and reused by proposer/linter: authority → organization +
     role-bearing entity; criteria → generically dependent continuant (ICE); assessor → role;
     presenting facts → quality/process in the world; recognition act → process; effect → deontic
     realizable (role/disposition) inhering in the status-bearer; remedy → process.

2. **Ontology-level recognition profile** — a `recognition` block in the registry manifest:
   ```json
   {"domain": "clinical_nosology", "authority": "WHO",
    "chain_thickness": {"act": "thin", "repair": "external"},
    "active_strata": ["A","B","C","D-thin"],
    "loci_present": ["criteria","act","effect"],
    "instrumented": ["K-A1","K-A2","K-B1","K-B3","K-C1","K-C2","K-C3"],
    "silent_by_principle": ["K-D1"]}
   ```
   Set at ontology creation (default for a non-institutional source: `domain: "scientific_reference",
   chain: none, active_strata: [A,B,C]`). This is the §6.4 fingerprint, and it is what makes a zero
   count *mean* something.

3. **Locus tagging on proposals** — `recognition_locus` as an optional field on entity proposals
   (`app/schema.py`), populated by the proposer, checked by the linter, carried into the ledger.

## 3. Work packages

### P1 — Vocabulary module (`app/recognition.py`)
Pure data + helpers, no I/O. Refactor `mlc.py`/`sool_extension.py` to consume it. Tests: every FR-5
code maps to exactly one `(kernel, locus)` pair; every CT-1..CT-7 resolves; twelve domains load.

### P2 — Manifest profile (`app/registry.py`, `app/orchestrator.py`)
Read/write the `recognition` block; default it from the source-kind at ontology creation; expose in
the ontology list API. Backfill existing ontologies with a migration that defaults to
`scientific_reference` — never guess an institutional profile silently.

### P3 — Proposer: chain-aware anchoring (`app/llm_proposer.py`, `app/schema.py`)
Add rule 17: in a feed whose profile has a chain, every proposed term declares its locus or `none`,
and must anchor per the locus table in P1. This is the anchoring discipline extended to social kinds
and it directly targets the recurring straddle (a conferred status modelled as a quality of a person,
a norm modelled as a process). Rule 18: never mint a class for the chain itself — the loci are
annotations, not new universals (anchor-not-regenerate).

### P4 — Linter: chain-aware construction rules (`app/construction_linter.py`), pre-reasoner and cheap
- **PC-9 / K-C3 (CT-6)** — a conferred status without a link to a conferring act, or a deontic
  realizable without a bearer: dependence violation.
- **PC-10 / K-B3 (CT-5)** — residual definition: "other specified", "unspecified", "NOS",
  complement-only definitions with no positive differentia. (Logic exists in `kernel_audit`; move it
  forward so it is caught at proposal time, not only in a post-hoc audit.)
- **PC-11 / K-B2 (CT-4)** — one criterion bound to both inclusion and exclusion roles for the same
  category.
- **PC-12 / K-C2** — level confusion: criteria asserted as individuals, an act asserted as a class.
- **PC-13 / K-A1-latent (CT-1)** — sibling categories with jointly satisfiable criteria where the
  source asserts exclusivity: unasserted disjointness. Flag, do not repair, in faithful mode.

### P5 — Kernel audit in the loop (`app/coherence_gate.py`, `app/incoherence_ledger.py`)
Run `kernel_audit` at checkpoint (not per proposal — reasoner cost; serialize behind the existing
`_REASONER_LOCK`). Every finding gains three fields: `kernel_code`, `locus`, `attribution`
(`source | translation | undetermined`, default **undetermined** — §10 forbids defaulting to
"source"). Ledger entries become `(kernel × locus × attribution)` triples.

### P6 — Stratum D (the new capability)
- **K-D3 Modal Clash** — computable over the chain graph: for each conferred status, is there a path
  authority → criteria → assessor → act → effect? A capacity asserted in structure with no assessor
  or no realizing act in the same artifact is `◇-in-structure ∧ ¬◇-in-practice`. Reuse
  `realizable_misuse` traversal; report only where the profile says the locus should exist.
- **K-D2 Performative Self-Defeat** — criteria whose satisfaction entails failure of the act's success
  conditions (e.g. a criterion requiring the absence of the very assessment that establishes it).
  Detectable structurally in the narrow case; report as candidate, analyst-adjudicated.
- **K-D1 Falsification** — *not* detectable from the artifact. Emit `not_instrumented`, never `0`.

### P7 — CD and honest reporting (`app/interim_report.py`, Audits tab)
`CD(x)` and `CD(O)` with typed weights in config, marked **uncalibrated** by default. Every report
that shows a CD number must render the stratum-completeness table alongside it: for each of the
twelve primitives — instrumented / silent-by-principle / not-applicable-to-this-profile. A CD over a
subset of primitives is printed as a **lower bound**, per §13. Non-negotiable invariant: no aggregate
score without its completeness table.

### P8 — API/UI (`app/orchestrator.py` Flask app; Audits tab)
- `GET/PUT /api/recognition_profile/<ontology>`
- `POST /api/kernel_audit/<ontology>` → typed findings, grouped by locus and stratum
- Audits tab: seven-locus chain strip with per-locus finding counts, stratum-profile card, CD panel
  with the completeness table. Remember the Caddy split — client calls must use the `/api/*` prefix.

### P9 — Validation against artifacts already in the repo
Falsifiable predictions from §6.4, run as tests:
- `sool.owl` / legal case ontologies (act-thick, repair-thick) → all twelve primitives *can* fire; D
  findings permitted and expected.
- `icd11bfo.owl` (recognition-only, act-thin) → A/B/C findings, thin D; K-D3 findings must be
  attributable to a missing assessor/remedy locus, not invented.
- NFIP SFIP extraction (technical certification/contract) → recognition-only profile; the existing
  mudflow / earth-movement finding should re-type cleanly as CT-1 or K-C1 at the criteria locus.
- A scientific reference ontology as control → **D must be empty**. A D finding on the control is a
  bug in the implementation, not a discovery.

## 4. Cross-cutting invariants

1. **Anchor, don't regenerate.** The recognition layer adds annotations, gate checks and report
   structure. It does not mint chain classes into extracted ontologies. (Optional exception: one
   curated `recognition_chain.ttl` seed in the SOoL namespace, imported, never generated.)
2. **Fidelity mode holds.** In faithful mode every kernel finding is evidence-only — flag, never
   silently repair. Extracted ontologies keep the source's errors.
3. **Artifact vs source.** `attribution: undetermined` until adjudicated. Reports must not describe a
   translation defect as a defect of the source — that is the exact conflation §10.3 records.
4. **Warrant-neutrality.** CD says nothing about whether a classification is clinically or legally
   *correct*; the UI copy must not imply otherwise.
5. **Reasoner is necessary, never sufficient.** Any surface that reports only reasoner output is
   silently scoring half the kernel as zero and must carry the completeness table.

## 5. Sequencing and cost

P1–P2 are self-contained and unblock everything (≈ a day). P3–P4 are the highest value per unit
effort: they move recognition-layer checks pre-reasoner, where the feed loop actually spends its
budget, and they attack the churn the ICD-11 feed already exhibits. P5 is small once P1 lands. P6 is
the genuinely new research capability and should follow P9's control test being green. P7–P8 are
presentation but P7's invariant is the paper's central methodological point and should not be
deferred past first public output.

## 7. What landed

| Package | Files | Notes |
|---|---|---|
| P1 vocabulary | `app/recognition.py` (new) | 12 primitives × 7 loci × 13 domain profiles (Table 1 + control), CT-1..CT-7 and the 13 FR-5 legal codes derived as `(kernel, locus)` pairs, MLC node→locus map, detectability partition, CD. |
| P2 manifest profile | `app/registry.py` | `recognition_profile()` / `set_recognition_profile()`; `create(recognition_domain=…)`. Absent or unknown domain → scientific-reference control, never an inferred institution. |
| P3 proposer | `app/llm_proposer.py`, `app/schema.py`, `app/orchestrator.py` | `recognition_rules()` (rules 17–20) appended only for institutional feeds — returns `""` otherwise, so existing feeds' prompts stay byte-identical and the prompt cache is preserved. `Entity.recognition_locus` added. |
| P4 lint | `app/construction_linter.py` | PC-9 (K-C1 at a locus) and PC-12 (K-C2) reject; PC-10 (K-B3/CT-5), PC-11 (K-B2/CT-4), PC-13 (K-A1 latent/CT-1) **record** as `Finding`s. New `LintReport.findings`. |
| P5 gate + ledger | `app/coherence_gate.py`, `app/gate_client.py`, `app/incoherence_ledger.py`, `app/orchestrator.py` | `chain_active` / `findings_out` threaded through gate, policy runner and client; findings recorded from the accepted (or flagged/degraded) attempt only. `record_kernel_findings()` writes `(kernel_code, locus, attribution)` triples. |
| P6 Stratum D | `app/recognition_audit.py` (new) | K-D2 = unsatisfiable class at the **act** locus; K-D3 = conferred capacity with no realizing act; K-D1 never returned — reported silent-by-principle. A D2 retyping *replaces* the generic K-A2 rather than adding to the debt. |
| P7 reporting | `app/recognition_audit.py`, `client/index.html` | `render_text()` and the API both always emit the 12-row completeness table; CD is flagged uncalibrated and lower-bound; sampled types report how many of the counted total are shown. |
| P8 API/UI | `app/orchestrator.py`, `client/index.html` | `GET /recognition/domains`, `GET|PUT /ontologies/<n>/recognition-profile`, `POST /ontologies/<n>/kernel-audit`, `GET /ontologies/<n>/kernel-findings`; Audits-tab panel with the 7-locus chain strip and the completeness table. |
| P9 tests | `tests/test_recognition_layer.py` (new) | 31 tests, including the control prediction. |

Deliberate deviations from the plan as drafted:

- **PC-9 is K-C1, not K-C3.** A locus/anchor mismatch is a category violation at a
  chain locus; that is what the check actually detects. The K-C3 dependence cases
  are covered by `kernel_audit` at audit time.
- **Loci are derived from BFO anchors at audit time, not persisted as annotations.**
  Writing chain annotations into extracted ontologies would violate the
  anchor-don't-regenerate invariant and change every artifact on disk. The
  proposal-time `recognition_locus` drives the lint and the ledger; the file-level
  audit infers locus from the BFO anchor (realizable→effect, process→act,
  role→assessor) and says so.
- **Findings default to `attribution: "undetermined"`, not `"source"`**, including
  the residual-definition finding. §10 forbids defaulting to source: what the
  linter sees is our proposal, not the source text.

## 8. Validation run (P9)

`.venv/bin/python -m app.recognition_audit <working.owl> --domain <d> --bfo bfo.owl`

| Ontology | Declared domain | Active strata | CD (lower bound) | Stratum D |
|---|---|---|---|---|
| `SOoL_v1` | legal_order (act-thick, repair-thick) | A B C D | 6974 | **K-D3** at the effect locus: conferred capacities (`AIAgentRole`, `AILegalAgentRole`, …) with no realization link to any act in the artifact |
| `NFIP_SFIP_v1` | technical_certification (recognition-only) | A B C, thin D | 140 | **K-D2** at the act locus: `RestoredFromEarthMovement` |
| `GeometryofTheGood` | scientific_reference (control) | A B C | 2685 | **empty**, as predicted |

The control prediction of §6.4 holds: the scientific-reference ontology produces no
Stratum D finding, and its completeness table marks all three D primitives
not-applicable rather than zero. A D finding on the control would have been a bug in
the implementation, not a discovery; `test_stratum_d_is_empty_for_the_scientific_reference_control`
pins it.

One plan expectation did **not** hold as written: the NFIP mudflow / earth-movement
finding was predicted to re-type as CT-1 or K-C1 at the criteria locus. It re-types as
**K-D2 at the act locus** — `RestoredFromEarthMovement` is an unsatisfiable *process*
class, i.e. an act defined so that performing it is impossible. That is the product
construction working exactly as §6.2 says it should: the same empty class is K-A2 read
at the criteria locus and K-D2 read at the act locus, and the more specific typing wins.

## 9. Open decisions

1. **Profile authorship** — does the operator declare the domain/thickness at feed creation, or does
   the proposer infer it from the source text and the operator confirms? (Recommend: operator
   declares from the twelve-row list; inference is one more place to smuggle in unwarranted structure.)
2. **CD weights** — leave uncalibrated and report per-type counts only, or ship provisional weights
   marked as such? (Recommend: per-type counts first; §13 says an uncalibrated aggregate presented as
   a measurement is the failure mode.)
3. **Retire or keep `mlc.py`'s 13 codes** — recommend keeping them as aliases over `(kernel, locus)`
   so existing SOoL work and the legal reasoner keep their vocabulary.
