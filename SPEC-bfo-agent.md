# Build Spec: bfo-agent Coherence Gate

**Target repo:** `github.com/dkoepsell/bfo-agent`
**Role of this service:** generation. It produces BFO-aligned OWL via local LLM inference (Ollama). The artifact `aero_base.owl` analyzed by the tester is its output, and the unsatisfiable `Force` / straddled reference-frame classes are generation defects, not audit defects.
**Goal:** move the BFO conformance check **upstream into the generation loop** so disjoint-parent straddles are prevented or repaired at emission time, instead of surviving into a published file. This is the architectural-confabulation thesis made operational: the model emits locally plausible single axioms (`Force` is a quality; `Force` is a disposition) that are jointly incoherent, and nothing currently holds the global constraint.

**Note on sections 7-8:** several loop-mechanics and efficiency improvements are adapted from OntoCast (`github.com/growgraph/ontocast`), a mature agentic extraction framework. They are translated, not copied: OntoCast builds instance graphs from text and validates data shapes with SHACL, which cannot see logical incoherence. The borrowings are about loop efficiency and critic structure, not its validation stack. One of them (parallelism) is actively unsafe if copied directly; section 7 says why.

---

## 0. Assumptions and how to adapt

I do not have the repo's exact internals in front of me. Before writing code, inspect and record:

- The generation loop entry point and how proposed axioms are represented between the LLM call and serialization (raw text, an intermediate axiom list, direct owlready2 calls?).
- The Ollama invocation seam (model name, prompt assembly, where completions are parsed into axioms).
- The current win-condition / scoring code (the discriminating-entailment metric and consistency sampling that were recently rearchitected). The gate must compose with this scorer, not bypass it.

State assumptions in comments. Do not use em-dashes in any code, prompt template, or log copy in this project.

---

## 1. Patch-based axiom emission (the representation everything else needs)

**Problem and decision.** The gate in section 2 needs a discrete unit to intercept. If the loop emits whole-ontology OWL text in one shot, per-axiom gating is awkward and every check re-reads the entire file. Adopt OntoCast's **GraphUpdate** idea: the LLM emits **patches** (ordered `insert` / `delete` axiom ops), not regenerated graphs.

**Task.**
1. Define an `AxiomPatch` type: a list of typed ops, each an `insert(axiom)` or `delete(axiom)`, where `axiom` is a structured record (e.g. `SubClassOf(sub, super)`, `SubClassOf(C, Restriction(prop, filler))`), not a text blob. Parse LLM output into this type; reject unparseable output at the boundary.
2. Apply patches against the working ontology incrementally. A commit is the application of one gated patch.
3. This is the token-efficiency win on its own: the model writes deltas, not the growing whole, so prompt and completion size stay roughly flat as the ontology grows.

If the current loop has no such representation, introducing it **is** this task and is a precondition for sections 2 through 5. Flag the size of that change before proceeding; everything downstream assumes patches exist.

**Acceptance.** The loop round-trips a generation step as an `AxiomPatch`; applying a patch mutates the working ontology by exactly its ops; a malformed patch is rejected without mutating state.

---

## 2. Shared BFO bundle (consume, do not reinvent)

Depend on the **same** `bfo/` bundle defined in the tester spec: vendored BFO 2020 (ISO/IEC 21838-2), the disjointness closure, and the relation signatures. Import it as a package or git submodule so the agent and the tester can never disagree about what BFO asserts. If the tester work has not landed yet, build the bundle here and have the tester depend on it; one source of truth either way.

Expose to the agent: `bfo_catalog` (label/IRI/parent maps), `disjointness_closure()` (predicate `clash(cat_a, cat_b) -> bool`), `relation_signatures()` (domain/range/characteristics).

---

## 3. In-loop coherence gate

The core change. Between "LLM proposes a patch" and "commit," insert a gate.

**Task.** Implement `gate(patch, current_onto, bfo) -> GateResult` where `GateResult` is `ACCEPT`, `REPAIR(new_patch)`, or `REJECT(reason, justification)`.

Two tiers, cheap first:

1. **Lint tier (no reasoner).** Reuse the partition-straddle walk from the tester. For each `insert(SubClassOf(C, P))` in the patch, compute `C`'s resulting BFO parent set **including already-committed parents**, and test all pairs with `clash()`. A patch putting `Force` under Disposition when it already sits under Quality fails here, instantly, with a localized reason. Catches the entire `Force` / `Drag` / `*Load` family. Runs per patch, in-process, cheap.
2. **Reasoner tier (Pellet).** The lint cannot see contradictions mediated by restrictions or relation signatures. Cadence matters: do **not** re-reason on every patch. Run `sync_reasoner_pellet()` on an **accumulated batch** of lint-passed patches (every N commits, or on a watermark), so the expensive full classification runs rarely while the cheap lint screens the stream. Any class that becomes `owl:Nothing` triggers `REJECT` or `REPAIR` with Pellet's justification, and the offending patch is traced back within the batch.

**Acceptance.** Feeding the loop `insert SubClassOf(Force, Quality)` then `insert SubClassOf(Force, Disposition)`: the gate ACCEPTs the first and REJECTs/REPAIRs the second, reason naming the quality/disposition clash, at the lint tier, no reasoner call. A restriction-mediated clash is caught at the reasoner tier on batch flush and attributed to the right patch.

---

## 4. Tiered repair: deterministic autofix before LLM escalation

**Problem and borrow.** OntoCast runs SHACL autofix **without** an extra LLM pass, escalating to the model only when machine repair cannot resolve. Apply the same cheapest-first discipline to coherence repair. Most straddles have a deterministic nearest-coherent form; spending an LLM call on them is waste.

**Task.** Make the repair policy a ladder, tried in order, stopping at the first that yields a coherent patch:

1. **Deterministic autofix (no LLM).** A fixed rule table over clash types:
   - quality + disposition straddle: keep the quality; re-home the realizable claim as a separate `Disposition` (or `Function`) class with `bearer_of` / `realized_in`.
   - continuant + process straddle: split into the continuant and the process that has it as participant.
   - role + disposition straddle: separate the role (`realized_in` a process in which the bearer participates) from the disposition.
2. **`reject_resample` (one LLM call).** Discard the clashing patch, return the clash reason to the model as context, resample. For clashes the rule table does not cover.
3. **`reground` (local regeneration).** Feed the reasoner's justification back and regenerate the **local neighborhood** of axioms, not just the one, so the model can pick a different genus from the start. For clashes where the right fix is upstream of the offending axiom.

Keep all three selectable by config (`REPAIR_POLICY`) so you can also force a single policy for clean experimental comparison. The ladder is the production default; the forced-single modes are the ablation.

**Acceptance.** A quality+disposition straddle is resolved by tier 1 with zero LLM calls. A clash outside the rule table escalates to tier 2. The experiment report (section 6) records, per policy, how many clashes each tier resolved. The deterministic-resolution fraction is itself a finding: it measures how mechanical the confabulations are.

---

## 5. Relation-aware scaffolding at emission

**Problem.** Bare `SubClassOf` under a BFO category is what produces straddle-prone is-a trees. When a class lands under a dependent category, the agent should emit the **constraint that category requires**, so the ontology constrains rather than merely classifies.

**Task.** When an accepted class lands under:
- a **quality** (SDC): emit `SubClassOf(C, inheres_in some IndependentContinuant)` and prompt for the specific bearer.
- a **role**: emit the `realized_in some Process` + bearer pattern.
- a **function/disposition**: emit `realized_in some Process` and prompt whether the bearer was **engineered/selected** for it (function) or merely has it (disposition). Exactly the discrimination the current file gets wrong, where engineered behaviors like `ThrustGeneration` sit beside misfiled `*Disposition` and `*Quality` classes.

These scaffolding axioms are themselves patches and run back through the gate (section 3) so the scaffolding cannot introduce a clash.

**Acceptance.** Generating a new quality class yields a committed `inheres_in` restriction with a concrete bearer, coherent under Pellet with relation signatures active.

---

## 6. Tie into the win condition, and the regression corpus

**Scorer.** The rearchitected scorer rewards discriminating entailments. An ontology can score on entailments while harboring unsatisfiable classes, which silently poison entailment (everything follows from `owl:Nothing`).

1. Make **coherence a hard precondition**: any unsatisfiable class scores zero, regardless of entailment metrics. Verify the consistency-sampling fix distinguishes consistency from coherence and is not passing incoherent-but-consistent ontologies.
2. Add a **coherence-preservation reward**: credit runs that reach a target axiom count with zero reasoner-tier rejections, separating "never proposed a clash" from "proposed and recovered." That gap is a clean measure of the architectural-confabulation claim.

**Regression corpus.** Freeze the current `aero_base.owl` under `tests/corpus/`. Assert that regenerating its domain under the gate produces **zero** unsatisfiable classes, against the frozen baseline's known set (`Force`, `Weight`, `Drag`, `Lift`, `Toughness`, `NoiseNuisance`, `InducedDrag`, the `*Load` family, the reference frames). This before/after is the natural figure for the paper.

**Acceptance.** A deliberately incoherent ontology scores zero. The scorer output includes a coherence field and proposed-vs-recovered clash counts. The corpus test fails on the frozen baseline and passes on gated regeneration.

---

## 7. Loop mechanics and efficiencies (adapted from OntoCast)

Translated borrows. Each states what OntoCast does and what changes for a TBox engine.

### 7.1 Bounded critic budget
OntoCast caps retries with `MAX_VISITS_PER_NODE` and a separate critic-pass budget. The repair ladder in section 4 **must** be bounded the same way, or `reject_resample` / `reground` can loop on a clash the model keeps reproducing. Add `MAX_REPAIR_ATTEMPTS` per patch; on exhaustion, drop the patch, log it as an unrepaired confabulation, and continue. Unrepaired-drop count is a scorer input, not a crash.

### 7.2 Parallelism, with the commit path serialized (the trap)
OntoCast runs `PARALLEL_WORKERS` concurrent per-chunk workers because **chunks are independent**. Axioms in one ontology are **not** independent: two patches each coherent in isolation can clash once both are committed, and global coherence is the whole point here. So do not parallelize the commit path.

Safe pattern: fan out the **LLM generation and the lint-tier screen** (embarrassingly parallel over candidate patches), then funnel survivors through a **single serialized committer** that applies them in order and runs the batched reasoner. Parallel proposal, serial commit. This keeps the throughput win without letting concurrent commits manufacture the exact straddles the gate exists to stop.

### 7.3 LLM caching and in-flight limits
OntoCast disk-caches completions, caps in-flight calls, and can pre-warm. Adopt all three. Repair prompts (section 4) are highly repetitive across a run, so a disk cache keyed on the full prompt cuts real work even against local Ollama. In-flight caps keep the DGX from thrashing when section 7.2's fan-out is wide.

### 7.4 Context retrieval instead of whole-ontology prompts
OntoCast's `ONTOLOGY_CONTEXT_MODE` selects each unit's schema context by catalog pick or vector retrieval rather than dumping everything. For generation, retrieve the **relevant committed neighborhood** (candidate genus, sibling classes under it, the BFO categories and relation signatures in play) and put that in the prompt, instead of the growing whole ontology. This is both a token efficiency and a quality lever: better-grounded genus selection means fewer straddles proposed in the first place, which is cheaper than catching them. Pairs naturally with patch emission (section 1), since both assume the model works on a local delta, not the entire graph.

### 7.5 Pipeline knobs as config
OntoCast exposes the decisions that change what a run does as a small env surface. Consolidate bfo-agent's into one config block: `REPAIR_POLICY`, `MAX_REPAIR_ATTEMPTS`, reasoner cadence `N`, `PARALLEL_PROPOSERS`, cache on/off, context-retrieval mode. The experiment matrix in section 6 is then a sweep over this block, not a code edit per run.

---

## 8. Suggested order and positioning

**Order.** 1 → 2 → 3 → 6 → 4 → 5 → 7. Patches (1) and the bundle (2) are preconditions. Get the gate's lint tier (3) wired to the scorer (6) next, because that alone stops the published-incoherence bleed. The repair ladder (4) and scaffolding (5) are the research signal. Efficiencies (7) last, once the loop is correct and worth speeding up, except 7.1's repair bound, which lands with 4 because an unbounded ladder is a hang, not a slowdown.

**Positioning for the paper.** OntoCast demonstrates that an agentic ontology loop can be made robust, parallel, and token-efficient at the **extraction** layer, while leaving coherence to SHACL shape validation, which cannot detect subsumption contradictions. Run `aero_base.owl`'s straddles through its entire validation stack and they pass clean. That is the clean one-sentence frame: prior agentic frameworks validate graph **shape**; none validate logical **coherence** against an upper ontology, and incoherence silently poisons entailment. bfo-agent closes that gap, and reuses OntoCast's loop mechanics to do it affordably.

---

## How this relates to the tester spec

Same BFO knowledge, two consumption points. The tester runs the lint and reasoner **after the fact, to judge**. The agent runs the same checks **inside the loop, to constrain**. If both land, the tester should have almost nothing to catch on the agent's output, and any divergence between the agent's in-loop Pellet and the tester's Pellet is itself a bug worth a loud test.
