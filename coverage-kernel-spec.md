# The Coverage Kernel: a BFO-Grounded Ontology of Policy Wording

**Purpose:** the reusable formal model that turns insurance policy wording into a reasoning-capable structure, so that structural failures (illusory coverage, definitional gutting, same-term-two-meanings, follow-form contradiction) become reasoner-provable rather than merely scored.
**Companions:** `bfo-agent-improvements-spec.md`, `owltesterservice-spec.md`, `nfip-extraction-spec.md`
**Author:** SEAL Lab, Texas A&M · SOoL (Koepsell)

---

## 1. The central design decision

Model the **fact pattern**, not the document.

Existing coverage analytics treat a policy as text to be classified and scored. The kernel treats a policy as a **conditional obligation** and asks what the wording does to a hypothetical loss. Coverage is not a property of the document; it is the outcome of running a loss through the document.

This single decision is what makes contradictions provable. A scoring function can only return a number. A reasoner over this kernel can return **unsatisfiable** (this coverage cannot pay anything) or **inconsistent** (this loss is both covered and not covered), and can name the clauses responsible.

---

## 2. BFO grounding

| Kernel entity | BFO category | Note |
|---|---|---|
| `Policy` | generically dependent continuant (IAO information artifact) | A document act in Smith's sense: executing it creates obligations. |
| `Clause` | information content entity, part of a `Policy` | Five subtypes, Section 3. |
| `DefinedTerm` | information content entity | Denotes a class of entities or events. |
| `Insurer`, `Insured` | role (BFO_0000023), borne by a person or organization | Never conflate the role with its bearer. |
| `IndemnityObligation` | specifically dependent continuant (deontic), inheres in the `Insurer` role bearer | Realized in a `PaymentProcess`. |
| `Loss` | occurrent (BFO_0000015 process) | The fact pattern. Bears a cause, a mechanism, a time, a location, and satisfied or unsatisfied conditions. |
| `PaymentProcess` | occurrent | Realization of the obligation. |

The obligation is a dependent continuant sustained by recognition (the executed policy) and realized or defeated by the facts. This is the SOoL apparatus applied to a private legal instrument rather than to a public one.

---

## 3. Clause taxonomy

```
Clause
├── Grant           creates the indemnity obligation for a class of losses
├── Exclusion       removes a class of losses from the obligation
├── Carveback       restores a class of losses that an Exclusion removed
├── Condition       gates the obligation on facts the insured must satisfy
└── Definition      fixes the denotation of a DefinedTerm
```

A `Carveback` is not a second `Grant`. It is defined **relative to** an `Exclusion`, and that relativity is the whole point: a carveback that does not intersect the losses its exclusion removed restores nothing. Encode the relation explicitly, never implicitly.

---

## 4. Relations

| Relation | Domain | Range | Meaning |
|---|---|---|---|
| `grants` | Grant | IndemnityObligation | The grant creates the obligation. |
| `coversLossClass` | Grant | LossClass | The losses the grant reaches. |
| `excludesLossClass` | Exclusion | LossClass | The losses the exclusion removes. |
| `modifies` | Carveback | Exclusion | **Required.** A carveback always modifies a named exclusion. |
| `restoresLossClass` | Carveback | LossClass | The losses the carveback purports to restore. |
| `gates` | Condition | IndemnityObligation | The obligation holds only if the condition is satisfied. |
| `defines` | Definition | DefinedTerm | |
| `uses` | Clause | DefinedTerm | Supports recursive definition closure and same-term detection. |
| `inScope` | Definition | Clause or Section | **Required for same-term detection.** A definition has a scope. |
| `followsForm` | Policy (excess layer) | Policy (underlying) | The layer incorporates the underlying wording. |
| `attaches` | Endorsement | Policy | For the pre-bind check. |

---

## 5. The pivot: bivalent coverage

Declare, once, in the kernel:

```
CoveredLoss  ⊓  UncoveredLoss  ⊑  ⊥
```

Coverage is genuinely bivalent: a loss either triggers the indemnity obligation or it does not. This disjointness is **natural to the domain**, not imposed by the analyst. (Contrast the DSM work, where the analyst had to supply the disorder-disjointness by hand. Here the domain gives it for free, which is why proofs come more easily in this setting.)

Coverage is then a **defined** class, not a primitive one. This is essential: without sufficient conditions, nothing is ever entailed to be covered, and no contradiction can fire.

```
CoveredLoss  ≡  Loss
                ⊓ (∃ satisfiesGrant . Grant)
                ⊓ ¬(∃ fallsUnder . Exclusion)          [subject to carveback, below]
                ⊓ (∀ gatedBy . SatisfiedCondition)

UncoveredLoss ≡ Loss ⊓ ¬CoveredLoss  (or asserted directly by an exclusion path)
```

with the carveback interaction expressed as:

```
EffectivelyExcludedLoss ≡ Loss
                          ⊓ (∃ fallsUnder . Exclusion)
                          ⊓ ¬(∃ restoredBy . Carveback)
```

---

## 6. The two proof shapes

Every one of the four named failure patterns reduces to one of these. This is the core result of the kernel.

### Shape 1. Coherence failure: an empty coverage class (TBox)

A coverage class is **unsatisfiable**. The reasoner returns `SomeCoverageClass ⊑ ⊥`, meaning: *this coverage cannot pay anything.*

- **Illusory carveback.** The exclusion removes losses where `¬P`. The carveback restores losses where `P`. The class of losses the carveback actually restores from that exclusion is:
  `RestoredFromExclusion ≡ excludesLossClass(E) ⊓ restoresLossClass(C)` where `modifies(C, E)`.
  If that class is unsatisfiable, the carveback restores nothing. It reads as a concession, prices as a concession, and grants nothing.
- **Definitional gutting.** The grant advertises a class of losses `A`. A definition several levels down narrows a term such that `A ⊓ CoveredLoss ⊑ ⊥`. The coverage cannot reach the scenarios its own insuring clause names.

### Shape 2. Inconsistency: a fact pattern that is both (ABox)

An individual `loss_1` is entailed into both `CoveredLoss` and `UncoveredLoss`, which are disjoint, so the ontology is **inconsistent**. The reasoner returns the justification: the minimal set of clauses that collide.

- **Same term, two meanings.** `Definition_1 inScope Grant` and `Definition_2 inScope NoticeCondition` define the same `DefinedTerm` incompatibly. A loss satisfying the grant's sense but not the condition's sense is entailed covered and uncovered at once.
- **Follow-form contradiction.** The excess layer `followsForm` the primary, incorporating Exclusion X, while its own endorsement deletes Exclusion X. Two governing provisions, opposite outcomes, one loss.

**Report the shapes differently.** Shape 1 is a defect in the *wording* (a coverage that cannot operate). Shape 2 is a defect that *surfaces on a fact pattern* (a claim whose outcome the policy does not determine). Both are findings; they are not the same finding.

---

## 7. What the reasoner must return

Not a score. For each finding:

1. **Verdict:** unsatisfiable class, or inconsistent ontology.
2. **Justification:** the minimal set of clauses responsible, cited to source (clause identifier, page, document).
3. **Plain-language consequence:** what this means for a claim.

The justification is the product. It is evidence a carrier can act on and, if necessary, defend. A confidence weight is not.

---

## 8. Anti-patterns (inherit from the existing gate)

- No boolean expressions encoded as IRIs (PC-7). Carvebacks and exclusions are anonymous class expressions built with the emitters in `sool_owl_checks.py`.
- No proposition-as-class (PC-9). A clause's *content* becomes axioms; the clause itself is an information content entity with an identifier, not a sentence reified as a class name.
- No re-minted BFO IRIs; reference `obo:` directly.
- **No relation-category mismatch.** The DSM run failed because disorders were typed as dispositions while also bearing `has disposition`, whose BFO domain is independent continuant. Here: the `IndemnityObligation` is borne by the *insurer*, not by the policy and not by the loss. Get the bearer right.
- Every class and property IRI is a valid NCName; human strings live in `rdfs:label`.

---

## 9. Acceptance criteria

1. The kernel loads with BFO merged and is consistent and coherent on its own (no domain content).
2. `CoveredLoss` and `UncoveredLoss` are disjoint and **defined** (sufficient conditions present), so membership is entailed rather than merely necessary.
3. A synthetic illusory-carveback fixture yields an **unsatisfiable** restored-loss class (Shape 1).
4. A synthetic same-term fixture yields an **inconsistent** ontology on one loss individual, with a justification naming both definitions (Shape 2).
5. A synthetic follow-form fixture yields Shape 2 across two policy documents.
6. Every finding carries a justification with clause-level source citation.
7. The reasoner is sanity-gated: it must flag a trivial `A ⊑ ¬B, x:A, x:B` case before any verdict is trusted.

---

## 10. Deliverables

- `coverage-kernel.owl` : the kernel, domain-content-free, BFO-grounded, with the bivalence axiom and the defined coverage classes.
- `kernel-fixtures/` : four synthetic fixtures, one per failure pattern, each with the expected reasoner verdict, serving as the regression suite.
- `run_coverage_coherence.py` : sanity gate, then load kernel plus policy, report unsatisfiable classes and inconsistency with justifications.
