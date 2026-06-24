# Build Spec: bfo-agent Coherence Gate

**Target repo:** `github.com/dkoepsell/bfo-agent`
**Role of this service:** generation. It produces BFO-aligned OWL via local LLM inference (Ollama). The artifact `aero_base.owl` analyzed by the tester is its output, and the unsatisfiable `Force` / straddled reference-frame classes are generation defects, not audit defects.
**Goal:** move the BFO conformance check **upstream into the generation loop** so disjoint-parent straddles are prevented or repaired at emission time, instead of surviving into a published file. This is the architectural-confabulation thesis made operational: the model emits locally plausible single axioms (`Force` is a quality; `Force` is a disposition) that are jointly incoherent, and nothing currently holds the global constraint.

---

## 0. Assumptions and how to adapt

I do not have the repo's exact internals in front of me. Before writing code, inspect and record:

- The generation loop entry point and how proposed axioms are represented between the LLM call and serialization (raw text, an intermediate axiom list, direct owlready2 calls?).
- The Ollama invocation seam (model name, prompt assembly, where completions are parsed into axioms).
- The current win-condition / scoring code (the discriminating-entailment metric and consistency sampling that were recently rearchitected). The gate must compose with this scorer, not bypass it.

State assumptions in comments. If the loop has no discrete "proposed axiom" representation and emits OWL text in one shot, **Task 1 includes introducing that representation**; flag this as a larger change before proceeding.

Do not use em-dashes in any code, prompt template, or log copy in this project.

---

## 1. Shared BFO bundle (consume, do not reinvent)

Depend on the **same** `bfo/` bundle defined in the tester spec: vendored BFO 2020 (ISO/IEC 21838-2), the disjointness closure, and the relation signatures. Import it as a package or git submodule so the agent and the tester can never disagree about what BFO asserts. If the tester work has not landed yet, build the bundle here and have the tester depend on it; one source of truth either way.

Expose to the agent:
- `bfo_catalog`: label/IRI/parent maps.
- `disjointness_closure()`: a predicate `clash(cat_a, cat_b) -> bool`.
- `relation_signatures()`: domain/range/characteristics for the BFO relations.

---

## 2. In-loop coherence gate

This is the core change. Between "LLM proposes axiom(s)" and "commit to the working ontology," insert a gate.

**Task.** Implement `gate(proposed_axioms, current_onto, bfo) -> GateResult` where `GateResult` is one of `ACCEPT`, `REPAIR(new_axioms)`, `REJECT(reason, justification)`.

Two tiers, cheap first:

1. **Lint tier (no reasoner).** Reuse the partition-straddle walk from the tester (Task 3 there). For each newly proposed `SubClassOf(C, P)`, compute `C`'s resulting BFO parent set **including already-committed parents**, and test all pairs with `clash()`. A proposal that puts `Force` under Disposition when it already sits under Quality fails here, instantly, with a localized reason. This catches the entire `Force` / `Drag` / `*Load` family.
2. **Reasoner tier (Pellet, periodic or on-demand).** The lint cannot see contradictions mediated by restrictions or relation signatures. Run `sync_reasoner_pellet()` on the candidate ontology (current + proposed) either every N accepted axioms or whenever the lint passes but you want a full check. Any class that becomes `owl:Nothing` triggers `REJECT` or `REPAIR` carrying Pellet's justification.

**Acceptance.** A unit test feeds the loop the two axioms `SubClassOf(Force, Quality)` then `SubClassOf(Force, Disposition)`; the gate ACCEPTs the first and REJECTs (or REPAIRs) the second with a reason naming the quality/disposition clash. Reasoner-tier test: a relation-signature-mediated clash is caught at the Pellet tier and not the lint tier.

---

## 3. Gate policy: reject-resample vs repair vs reground

The interesting design question, and the one with measurable consequences for the resulting ontologies and for the paper. Implement all three behind a config switch so they can be compared, not just one:

- **`reject_resample`**: discard the clashing axiom, return the clash reason to the model as additional context, and resample. Cleanest to evaluate; measures whether the model can self-correct given the constraint.
- **`repair`**: programmatically rewrite the offending axiom toward the nearest coherent form using a fixed rule table (e.g. quality+disposition straddle -> keep the quality, re-home the realizable claim as a separate `Disposition`/`Function` class with `bearer_of`/`realized_in`; continuant+process straddle -> split into the continuant and the process that has it as participant). Deterministic, but injects the tool's judgment.
- **`reground`**: feed the reasoner's justification back as context and regenerate the **local neighborhood** of axioms, not just the one, so the model can choose a different genus from the start.

**Task.** A `GatePolicy` enum and a dispatcher. Log every gate event: proposal, tier that fired, policy action, outcome. These logs are the experimental record.

**Acceptance.** The same generation seed run under each policy produces three ontologies; a summary report tabulates, per policy: axioms proposed, clashes caught at lint tier, clashes caught at reasoner tier, resamples/repairs, and final unsatisfiable-class count (target zero).

---

## 4. Relation-aware scaffolding at emission

**Problem.** Bare `SubClassOf` under a BFO category is what produces straddle-prone is-a trees. When the model places a class under a dependent category, the agent should propose the **constraint that category requires**, so the ontology constrains rather than merely classifies.

**Task.** When an accepted class lands under:
- a **quality** (SDC): propose `SubClassOf(C, inheres_in some IndependentContinuant)` and prompt for the specific bearer.
- a **role**: propose the `realized_in some Process` + bearer pattern.
- a **function/disposition**: propose `realized_in some Process` and prompt whether the bearer was **engineered/selected** for it (function) or merely has it (disposition). This is exactly the discrimination the current file gets wrong, where engineered behaviors like `ThrustGeneration` sit beside misfiled `*Disposition` and `*Quality` classes.

Run these proposals back through the gate (Task 2) so the scaffolding itself cannot introduce a clash.

**Acceptance.** Generating a new quality class yields a committed `inheres_in` restriction with a concrete bearer, and the result reasons coherently under Pellet with relation signatures active.

---

## 5. Tie into the win condition

**Problem.** The recently rearchitected scorer rewards discriminating entailments. An ontology can score on entailments while harboring unsatisfiable classes, which silently poison entailment (everything follows from `owl:Nothing`).

**Task.**
1. Make **coherence a hard precondition** of the win condition: an ontology with any unsatisfiable class scores zero (or is disqualified) regardless of entailment metrics, because its entailments are vacuous. Verify the consistency-sampling fix accounts for this distinction (consistency vs coherence) and is not passing incoherent-but-consistent ontologies.
2. Add a **coherence-preservation reward**: credit runs that reach a target axiom count with zero gate rejections at the reasoner tier, separating "never proposed a clash" from "proposed and recovered." The gap between those two numbers is a clean measure of the architectural-confabulation claim.

**Acceptance.** A deliberately incoherent ontology scores zero on the win condition. The scorer's output includes a coherence field and the proposed-vs-recovered clash counts.

---

## 6. Regression corpus

**Task.** Freeze the current `aero_base.owl` as a regression fixture under `tests/corpus/`. Write a test asserting that regenerating its domain under the gate produces **zero** unsatisfiable classes, in contrast to the frozen baseline's known set (`Force`, `Weight`, `Drag`, `Lift`, `Toughness`, `NoiseNuisance`, `InducedDrag`, the `*Load` family, the reference frames). This is the before/after that demonstrates the gate works and is the natural figure for the paper.

---

## Suggested order

1 → 2 → 5 → 3 → 4 → 6. Get the shared bundle and the gate's lint tier working and wired to the scorer first, because that alone stops the published-incoherence bleed. Policy comparison (3) and scaffolding (4) are where the research signal is; do them once the gate is load-bearing. Freeze the regression corpus (6) last, once you have a clean run to compare against.

---

## How this relates to the tester spec

Same BFO knowledge, two consumption points. The tester runs the lint and reasoner **after the fact, to judge**. The agent runs the same checks **inside the loop, to constrain**. If both land, the tester should have almost nothing to catch on the agent's output, and any divergence between what the agent's in-loop Pellet says and what the tester's Pellet says is itself a bug worth a loud test.
