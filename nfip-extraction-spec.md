# NFIP Public Demonstrator: Extraction Spec

**Objective:** produce a non-toy, fully public-domain demonstration of the coverage coherence layer, by running the Standard Flood Insurance Policy through bfo-agent against the coverage kernel, and reporting the structural failures a reasoner finds.
**Companion:** `coverage-kernel-spec.md`
**Author:** SEAL Lab, Texas A&M · SOoL (Koepsell)

---

## 1. Why this source

The NFIP Standard Flood Insurance Policy (SFIP) is the right corpus, and close to the only good one.

- **Genuinely public domain.** The SFIP forms are published as appendices to 44 CFR Part 61 and incorporated by reference into the policy. Federal regulations are government edicts and carry no copyright. (Contrast ISO forms, which are Verisk-copyrighted and must not be used.)
- **Non-toy.** A complete policy: Definitions; Coverage A (Building Property), B (Personal Property), C (Other Coverages), D (Increased Cost of Compliance); Property Not Insured; Exclusions; Deductibles; General Conditions; Nullification and Cancellation.
- **It already contains the structures the kernel is built for.** Coverage D pays certain costs "subject to Coverage D Exclusion 5.g below": a grant conditioned on an exclusion by explicit cross-reference.
- **Follow-form exists in the public domain.** The Group Flood Insurance Policy is defined as the Dwelling Form *except that* named articles do not apply. That is a follow-form structure with deletions, in a public source.
- **It is versioned.** FEMA has proposed a new Homeowner Flood Form (Appendix A(4)) to replace the Dwelling Form for one-to-four family residences. Old form versus new form supports the cross-edition drift analysis.
- **The regulator has already conceded the same-term problem.** FEMA proposed to clarify that the Part 59 definitions apply to Part 61 including appendices, but that where an appendix defines a term differently, the appendix definition controls. A tiebreaker rule is an admission that the conflict exists. The CT pattern is not hypothetical here.
- **Independent validation is available.** NFIP coverage disputes are litigated in federal court and those opinions are public domain, so a machine-found contradiction can be checked against a case where a court wrestled with the same ambiguity.

---

## 2. Sources (retrieve, do not paraphrase from memory)

- 44 CFR Part 61, Appendix A(1): Dwelling Form.
- 44 CFR Part 61, Appendix A(2): General Property Form.
- 44 CFR Part 61, Appendix A(3): Residential Condominium Building Association Policy.
- 44 CFR 61.17: Group Flood Insurance Policy (the follow-form case).
- 44 CFR 61.13: incorporation of forms.
- 44 CFR Part 59: definitions applicable to Part 61 (the cross-scope definition source).
- The proposed Homeowner Flood Form (Appendix A(4)) for the drift comparison.

Prefer the eCFR as the authoritative text. Record the retrieval date and CFR edition in the ontology metadata.

---

## 3. Extraction targets

For each form, extract into the kernel's vocabulary:

1. **Clauses**, typed as `Grant`, `Exclusion`, `Carveback`, `Condition`, or `Definition`, each with a stable identifier tied to its citation (for example `SFIP-DW-III-A-1`, `SFIP-DW-V-2`). The identifier is the provenance: it is what a justification cites.
2. **Defined terms**, each with `defines`, and critically with `inScope`. Terms defined in Part 59 and again in an appendix are the same-term candidates. Do not silently merge them.
3. **Cross-references.** Every "subject to", "except as provided in", "notwithstanding", and explicit citation becomes a `modifies`, `gates`, or `followsForm` relation. These are the load-bearing edges; a missed cross-reference is a missed contradiction.
4. **Loss classes**, as the intersection of conditions a loss must meet to fall under a grant or an exclusion (peril, property type, location, timing, insured conduct).
5. **The Group Flood Insurance Policy** as a `followsForm` policy over the Dwelling Form, with the named articles deleted.

---

## 4. Extraction rules (in addition to the standing agent rules)

- **E-1** Every clause carries a source citation annotation (CFR part, appendix, article, paragraph). Findings without a citation are worthless.
- **E-2** A carveback MUST carry `modifies` pointing at the exclusion it modifies. If the agent cannot determine which exclusion, it flags the clause rather than guessing.
- **E-3** Definitions MUST carry `inScope`. A definition without a scope defeats the same-term analysis, which is the point of using this corpus.
- **E-4** Recursive definition closure: follow defined terms through their definitions until closure or unavailability, recording the depth. Report any term whose closure depth exceeds three, since that is where definitional gutting hides.
- **E-5** The regulatory text is public domain, so the clause text MAY be stored in annotations. This is the opposite of the ICD-11 constraint and should be used: quoting the source clause in a justification is what makes the finding checkable.
- **E-6** No proposition-as-class, no boolean-in-IRI, no re-minted BFO IRIs. The gate applies unchanged.

---

## 5. Analysis passes

Run in this order, in the verified reasoner stack (ROBOT with HermiT; sanity-gate the reasoner first).

1. **Baseline:** kernel plus one form. Report consistency and the unsatisfiable-class list. Any unsatisfiable coverage class is a Shape 1 finding: a coverage that cannot pay.
2. **Carveback audit:** for every `Carveback C` with `modifies(C, E)`, test whether `excludesLossClass(E) ⊓ restoresLossClass(C)` is satisfiable. Empty means illusory.
3. **Definitional gutting:** for every grant, test whether the class of losses its own text advertises remains satisfiable after definition closure.
4. **Same-term:** for every defined term with more than one `inScope` definition, construct a loss individual satisfying one sense and not the other, and test for inconsistency (Shape 2).
5. **Follow-form:** load the Group Flood Insurance Policy over the Dwelling Form and test whether any deleted article contradicts a retained one (Shape 2).
6. **Drift:** repeat 1 to 5 on the proposed Homeowner Flood Form and report the delta. Did the revision add or remove structural incoherence?
7. **Validation:** for each finding, search the public federal case law for a dispute turning on the same clause or term. A machine-found contradiction that matches a litigated ambiguity is the strongest possible evidence.

---

## 6. Deliverables

- `sfip-dwelling.owl`, `sfip-general.owl`, `sfip-rcbap.owl`, `gfip.owl` : the extracted forms against the kernel.
- `sfip-findings.md` : every finding with its verdict, justification, cited clauses, and plain-language consequence; Shape 1 and Shape 2 findings reported separately.
- `sfip-drift.md` : the Dwelling Form versus Homeowner Flood Form structural comparison.
- `sfip-validation.md` : findings cross-checked against public case law.
- A public artifact page in the style of the existing coverage demonstrator, showing the findings on a real, public, non-cherry-picked policy.

---

## 7. Why this matters commercially

The demonstrator is built on a policy that is public, real, complete, and impossible to accuse of being cherry-picked. It proves the kernel before any client engagement, at no cost to the client, and it means a bounded review for a carrier is an *application of a validated instrument* rather than an experiment. It also gives the method a citable, reproducible public result, which is what an academic publication needs and what a commercial prospect finds most persuasive.
