# bfo-agent — Specification

**Component:** SOoL ontology generation agent
**Consumes:** `sool-kernel.ttl` (frozen), source/case text
**Produces:** case-level OWL/TTL fragment that `owl:imports` the kernel
**Gate:** every output MUST pass `owltesterservice` before it is written to a durable location
**Author:** SEAL Lab, Texas A&M · SOoL (Koepsell, Palgrave forthcoming)

**Handoff package (build against these):**
- `bfo-agent-spec.md` — this file
- `owltesterservice-spec.md` — the validation/repair gate this agent submits to
- `sool_owl_checks.py` — reference impl: OWL class-expression **emitters** (§6.1) and the anti-pattern/expression-IRI/re-mint **validators** the agent self-lints with (§6)
- `bfo_agent_cached.py` — reference impl: the prompt-caching Anthropic client the agent makes all model calls through (§9)
- Runtime target: Hetzner Linux host; key in `SOOL_ANTHROPIC_KEY`

---

## 1. Purpose & scope

bfo-agent expresses legal scenarios as OWL **assertions over a fixed kernel**. It does not invent ontology. The kernel — the 8 MLC node types, the 4 clusters, the 13-type contradiction typology, the closed object-property set, and the BFO anchors — is hand-authored, versioned, and read-only. bfo-agent's only job is to map a source scenario onto that vocabulary and emit individuals + axioms.

**Non-goals (hard):** regenerating BFO categories; minting new primitive classes for the kernel; modeling absence/failure as classes; repairing inconsistency (that belongs to `owltesterservice`).

This spec exists because a prior run produced `SOOL_autofixed.owl`: 6,881 flat class declarations, 0 `subClassOf`, 0 object properties, 0 individuals, 0 BFO grounding. The rules below make that output structurally impossible to emit.

---

## 2. Core principle — anchor, don't regenerate

> The agent reasons in natural language about a scenario, then **selects from the kernel's declared signature**. It never creates a new named class to stand for a phrase. Anything the kernel cannot already say must be said as a *class expression or axiom over kernel terms*, or routed to the contradiction typology — never as a new primitive.

If the agent believes a genuinely new primitive is required, it does not emit it. It emits a `kernel-extension-request` (see §8) for human review and proceeds without it.

---

## 3. Inputs

| Input | Form | Notes |
|---|---|---|
| `--kernel` | path to `sool-kernel.ttl` | Read-only. Source of truth for the allowed class set `K_C` and property set `K_P`. Carries an explicit `owl:versionIRI`. |
| `--source` | text / path | Scenario, case, or statute fragment to model. |
| `--mode` | `case` \| `scenario` \| `batch` | `batch` reads the 149-row offense CSV or a JSON design and emits one fragment per row. |
| `--out` | path | Target fragment. Written only on gate pass. |

The agent MUST parse the kernel at startup and load `K_C` and `K_P` from it. The closed-vocabulary check (§6) is run against the actual kernel signature, never a hardcoded list.

---

## 4. Outputs

A single OWL fragment (Turtle preferred) that:

1. declares `owl:imports <kernel versionIRI>`;
2. contains only (a) `owl:NamedIndividual`s typed to classes in `K_C`, (b) object-property assertions using properties in `K_P`, and (c) class expressions / SubClassOf / EquivalentClasses / DisjointClasses axioms built solely from `K_C` and `K_P`;
3. declares **zero** new named classes and **zero** new named properties (default mode);
4. carries provenance: `dct:source`, `prov:wasGeneratedBy bfo-agent`, kernel versionIRI, timestamp.

---

## 5. Functional requirements

- **FR-1 — Import.** Output imports the frozen kernel by versionIRI. No copy-in of kernel axioms.
- **FR-2 — Closed vocabulary.** Every class IRI used is in `K_C`; every property IRI is in `K_P`. (Linter PC-4.)
- **FR-3 — Full typing.** Every emitted individual has at least one `rdf:type` resolving (through the kernel) to a BFO category. Untyped individuals are rejected. (Linter PC-3.)
- **FR-4 — MLC well-formedness.** When the scenario asserts a legal effect, the agent emits the node occupants it can support (1 Source of Authority → 2 Norm → 3 Actor-in-Role → 4 Triggering Facts → 5 Legal Act/Omission → 6 Target → 7 Legal Effect → 8 Remedy), each typed to the BFO kind the kernel requires for that node (e.g., Target = relational dependent continuant; Legal Effect = realizable dependent continuant; Actor-in-Role = role inhering in a material entity; events = occurrents). Missing nodes are left **absent**, not stubbed with placeholder classes.
- **FR-5 — Failure ⇒ typology, not class.** Any structural defect the agent detects is asserted as an instance of the relevant contradiction type from the kernel's 13-type typology, indexed to its MLC link (e.g., a recognition gap at nodes 6/7 → `Recognition Failure`; will displacing norm at 1→2 → `Authority Inflation`). It is never a new `AbsenceOf*`/`Lack*`/`Failure*` class.
- **FR-6 — Determinism of vocabulary.** Two runs over the same source against the same kernel version produce the same set of class/property IRIs (individual IRIs may differ but must be stable-hashed from source for diffability).
- **FR-7 — Class-count budget.** Default mode emits 0 new classes. A single case fragment SHOULD stay under a configurable individual budget (default 200); exceeding it raises a warning, not silent acceptance.

---

## 6. Prohibited constructions (agent-side linter — all are hard rejects)

The agent runs these on its own draft before handing to the gate. A violation aborts emission with a structured error. PC-1/PC-2/PC-7/PC-8 are implemented by the validators in `sool_owl_checks.py` (`check_antipatterns_all`, `check_expression_iris`) — the agent calls the **same** functions the gate uses, so self-lint and gate never diverge.

- **PC-1 — Privation primitives.** Reject any new class whose label matches `^(AbsenceOf|Absence|Lack|Loss|Missing|No|Non|Un|Failure|Broken|Invalid|Degraded|Collapsed|Residual|Partial)`. Absence is modeled as a contradiction-type individual (FR-5) or, where genuinely needed, as `owl:complementOf` / a `cardinality 0` restriction over a kernel property — never a primitive. *(This pattern alone accounted for >130 invented classes in the bad artifact.)*
- **PC-2 — Compound CamelCase.** Reject any new class name that fuses ≥2 concepts or bakes a relation into the string (`NormAlignmentWithRuleOfRecognition`, `NormDependencyRelation`, `AbsenceOfRecognitionGrounding`). The intended meaning is a class expression: e.g. `Norm and (alignedWith some RuleOfRecognition)`. Heuristic: >1 internal capitalized token that is not a known kernel term, or a contained relational keyword (`With`, `Of`, `For`, `Dependency`, `Alignment`, `Relation`).
- **PC-3 — Untyped entity.** Reject any individual or class with no path to a BFO category.
- **PC-4 — Off-vocabulary IRI.** Reject any class/property IRI not in `K_C`/`K_P` (default mode).
- **PC-5 — String-baked relation.** Reject class names encoding what a property assertion should carry; emit the property triple instead.
- **PC-6 — Continuant/Occurrent conflation.** Reject typing one entity as both a continuant and an occurrent.
- **PC-7 — Class expression baked into an IRI.** Reject any IRI (in `rdf:about` or `rdf:resource`) whose fragment contains a logical operator or bracket: `[`, `]`, ` and `, ` or `, ` not `, ` some `, ` only `, ` value `, ` min `, ` max `, ` exactly `. A boolean or restriction is an *anonymous class construct*, never a named IRI. *(Observed in `AristotleCategories.owl`: four IRIs of the form `#[ Quantity and not ( BFO_0000196 some Contrary ) ]` — the intended union/complement axioms serialized as opaque named classes, inert to any reasoner. The file contained 0 `owl:unionOf`/`owl:complementOf`/`owl:intersectionOf` despite clearly intending them.)*
- **PC-8 — Anti-pattern term anywhere, not just in declarations.** PC-1/PC-2 apply to **every** IRI fragment and every referenced term — inside class expressions, restriction fillers, and `rdf:resource` targets — not only to `owl:Class` declarations. *(Observed: `NonQuantity` appeared only inside a PC-7 malformed IRI, never declared, so it was simultaneously a dangling reference and a `Non*` privation term that a declaration-only scan misses. The correct rewrite reuses `Contrary`: `Quantity and not (hasQuality some Contrary)`.)*

Each rejection returns `{rule, offending_term, suggested_rewrite}` so the agent can self-correct and retry (bounded retries, default 3) before failing the item.

### 6.1 Required serialization of class expressions

When the agent needs a boolean or restriction, it MUST emit proper OWL constructs, never an IRI string:

| Intended logic | Required serialization | Never |
|---|---|---|
| `A or B` | anonymous `owl:Class` with `owl:unionOf` (RDF list) | `#[ A or B ]` |
| `A and B` | anonymous `owl:Class` with `owl:intersectionOf` | `#[ A and B ]` |
| `not A` | anonymous `owl:Class` with `owl:complementOf` | `#[ not A ]` |
| `p some C` | `owl:Restriction` + `owl:onProperty p` + `owl:someValuesFrom C` | `#[ p some C ]` |
| `p only C` | `owl:Restriction` + `owl:allValuesFrom C` | `#[ p only C ]` |
| `p exactly n C` | `owl:Restriction` + qualified cardinality | string |

The agent MUST build these via the shared emitter helpers in `sool_owl_checks.py` (`union_of`, `intersection_of`, `complement_of`, `some_values_from`, `all_values_from`) rather than hand-templating RDF/XML, which is where the malformed IRIs originate. Those helpers return proper anonymous-class / `owl:Restriction` blocks (verified to round-trip and re-parse cleanly). `owl:Restriction` serialization already works in current output (58 emitted correctly in the Aristotle artifact); only the boolean combinators are broken, so the fix is localized to swapping in these helpers.

---

## 7. Required BFO typing (must mirror the kernel, not re-decide it)

The agent reads the per-node BFO type from the kernel and applies it. For reference, the kernel fixes: legal norms, roles, obligations, statuses, legal effects = **specifically dependent continuants** (`BFO:0000020`); roles = realizable dependent continuants inhering in bearers; agents = **material entities** (`BFO:0000040`); legal events/processes = **occurrents**; legal facts = information artifacts. The agent MUST NOT assign BFO types from its own judgment when the kernel already fixes them for a node.

---

## 8. Kernel-extension request (escape hatch, human-gated)

When modeling truly requires a term absent from the kernel, the agent emits a separate `*.kext.json`:

```json
{
  "requested_term": "string",
  "proposed_bfo_parent": "BFO_xxxxxxx",
  "justification": "why no kernel expression covers this",
  "blocking": false
}
```

It then continues without the term. Kernel extensions are reviewed and merged by a human into `sool-kernel.ttl`; the agent never self-grants them. This prevents vocabulary drift while keeping a paper trail.

---

## 9. Model-call layer — prompt caching (`bfo_agent_cached.py`)

bfo-agent uses the Anthropic API to do the natural-language → kernel-vocabulary mapping. The cost profile is fixed: a large **stable** prefix (system instructions + the `sool-kernel.ttl` closed vocabulary loaded at startup per §3 + few-shot examples + the PC-1…PC-8 rules) is re-sent on every case, while only the `--source` text varies. All model calls MUST therefore route through `bfo_agent_cached.CachedAnthropic`, which places one cache breakpoint at the end of that prefix so it is written once and read at ~10% of base input cost thereafter.

- **MC-1 — One client per run.** Instantiate a single `CachedAnthropic` for the whole `--mode batch` corpus run, not one per case. The 5-minute cache TTL refreshes on each hit, so back-to-back cases keep the prefix warm for the entire run.
- **MC-2 — The kernel is the cached block.** The kernel text loaded for the closed-vocabulary check (§3) is the *same* string passed as `kernel_text` to the cached client — one source of truth, no duplication. Instructions + kernel are cached together (tools → system order).
- **MC-3 — Caching never changes outputs.** It is a cost optimisation only; FR-6 (vocabulary determinism) and all of §6 still hold byte-for-byte. The gate (§ acceptance) is unaffected.
- **MC-4 — Model tiering.** `--model` selects the tier: Haiku for first-pass/bulk classification, Sonnet (default) for generation, Opus for hard cases. The model string also selects the rate table used in the savings report; pass the matching string so the report stays accurate.
- **MC-5 — TTL policy.** Default `ttl="5m"`. Use `ttl="1h"` only when cases are spaced >5 minutes apart (it costs 2× base input on the write vs 1.25× for 5m).
- **MC-6 — Key handling.** Read `SOOL_ANTHROPIC_KEY` from the environment (Hetzner: systemd `Environment=` or an `.env`). The key is never logged, never written into a fragment, never serialized into provenance.
- **MC-7 — Per-run metrics.** Each run writes the cache report (`calls`, `cache_hit_ratio`, token breakdown, `saved_usd`, `saved_pct`) to the run log via `client.report()`. CI asserts `cache_read_input_tokens > 0` from the second case onward — the proof that caching is live; a run that never reads cache is a misconfiguration and should warn.

Self-test before deploy: `python3 bfo_agent_cached.py` runs the mock metrics check (no key/network); `python3 bfo_agent_cached.py --smoke` makes two real calls and prints the report (the second shows populated `cache_read`).

---

## 10. Acceptance criteria

1. Output imports the kernel by versionIRI; contains 0 new classes and 0 new properties in default mode.
2. Every individual is typed to a BFO category through the kernel.
3. PC-1…PC-8 produce zero violations. In particular, the output contains **no IRI fragment with a logical operator** (PC-7), and every intended boolean is a real `owl:unionOf`/`owl:complementOf`/`owl:intersectionOf` construct.
4. Output passes `owltesterservice` (all stages) — this is the binding gate; bfo-agent does not write `--out` otherwise.
5. **Regression (autofixed):** re-running the generator over the original SOoL sources must not reproduce any of the `AbsenceOf*` / compound-CamelCase classes present in `SOOL_autofixed.owl`. A diff against that file's class list is a CI assertion: overlap with the prohibited set must be 0.
6. **Regression (Aristotle):** the `AristotleCategories.owl` defect set must not recur — 0 expression-IRIs, 0 smuggled `Non*`/privation terms, 0 locally re-minted `BFO_\d+`/`RO_\d+` IRIs.

---

## 11. CLI contract

```
bfo-agent \
  --kernel sool-kernel.ttl \
  --source case.txt \
  --mode case \
  --out fragments/case-0001.ttl \
  --gate http://owltester.local:8080 \  # gate URL or local binary
  --model claude-sonnet-4-6 \           # haiku=first-pass, sonnet=default, opus=hard
  --cache-ttl 5m \                      # 1h only if cases are >5 min apart
  --report run.log                      # cache-hit % + $ saved appended here
# exit 0 only if generation AND gate pass; non-zero otherwise.
# emits case-0001.ttl, optional case-0001.kext.json, and a run log.
# all model calls go through bfo_agent_cached.CachedAnthropic (§9); SOOL_ANTHROPIC_KEY from env.
```
