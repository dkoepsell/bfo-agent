# Build Spec: Better Drafts from Legal Sources

**Target repo:** `bfo-agent` (dkoepsell)
**Goal:** when bfo-agent drafts an ontology from legislation or model rules, the draft is faithful to the text, honest about quantifiers, scoped to the act each definition comes from, anchored to BFO and the LOF design patterns, and close to release-ready.
**Companions:** `fidelity-mode-spec.md` (faithful vs curated), `SPEC-bfo-agent.md` (coherence gate), `coverage-kernel-spec.md`; OwlTesterService `SPEC-audit.md` and `SPEC-release.md`.
**Status:** partly implemented (2 November 2026). Built: L1 plain-text reader with locators and act detection (`app/legal_source.py`; Formex, XHTML and Akoma Ntoso readers not yet), L2 `legislation` / `model_rules` / `policy` extraction profiles with the verbatim-quote validator (`app/extractor.py`), 3.3 verbatim provenance (`meta:sourceSpan` = quote, plus `sourceLocator`, `sourceAct`, `claimText`), L3 rules Q1 to Q5 and the five checks (`app/legal_drafting.py`), with tests for D1, D2 and D5 (`tests/test_legal_drafting.py`). Genre is chosen explicitly in the Extract panel (default `philosophy`, unchanged); open decision 1 is therefore "explicit, not inferred" for now. Curated mode repairs bearer-universal (flipped to the dependent side), modal-universal (kept as `meta:possibleRelation`) and multi-bearer (one bearer kept, the rest raised as an open question; union fillers are not yet supported by the write path), and sends unverified quotes and unscoped definitions to `needs_review`. Faithful mode commits as asserted and records every finding in `legal_findings.jsonl` beside the ontology. Section 4.3 needed no change: scaffolding already adds only dependent-side constraints and marks them `scaffolded`. Not yet built: 2.2 act individuals (needs the LOF kernel), L4 scoped definitions, L5 LOF kernel, L6 identity checks and language tags, L7 draft report, L8 finishing, section 8 fixture and benchmark.

Do not introduce em-dashes into any user-facing copy, docstrings, or report templates in this project.

---

## 0. Baseline and assumptions

**The baseline draft** is `EU-Personal_data.owl` (1 November 2026): 27 classes, 4 individuals, 280 triples, drafted from data protection definitions (GDPR Art. 4, Regulation 2018/1725 Art. 3). OwlTester: consistent, 78 inferred subsumptions, every class under BFO, OWL 2 DL with BFO merged. Its defects, each a regression case in section 8:

| ID | Defect in the draft |
| --- | --- |
| D1 | `NaturalPerson ⊑ bearer_of some DataProtectionOfficer`, `... some Expertise`, `... some Experience`: every person is a DPO with expertise and experience. The source said persons *can* have them |
| D2 | `Independence` (a quality) must inhere in some `NationalSupervisoryAuthority` and in some `NaturalPerson`: a quality has one bearer |
| D3 | Two entities labelled "European Data Protection Supervisor": a role class and a `Body` individual |
| D4 | `Controller` defined only as in 2018/1725 ("a Union institution, body, …"); the GDPR definition (natural or legal person, public authority, agency) is lost, and nothing records which act a definition came from |
| D5 | `sourceSpan` values are paraphrases ("Persons can have experience."), not the legal text |
| D6 | `DataProcessing` has no definition and no links; "determines the purposes and means *of the processing of personal data*" lost its object |
| D7 | No legal act, legal content or document (LOF-ODP-001), no rights or duties (ODP-003), no chain to a remedy (ODP-004) |
| D8 | Labels typed `xsd:string` while definitions are language-tagged; no license or version; working namespace; BFO imported over the network |

**Root causes in this repository** (public `main`, commit `7bfc462`, 15 July 2026):

| Cause | Where | Produces |
| --- | --- | --- |
| The extractor is written for philosophy: "claims the AUTHOR IS ASSERTING IN HIS OWN VOICE", each rephrased as one short sentence; hedged wording is excluded | `app/extractor.py` `EXTRACTION_PROMPT` | D5; drops permissions ("may", "is entitled to"), which are hedges in philosophy but norms in law |
| The proposer and provenance see only the paraphrase: `utterance=claim["claim"]`, `"source_text": claim["claim"]` | `app/orchestrator.py` (around lines 1116, 1147, 1280) | D5, D1 (the modal "can" is lost or blurred before the axiom is written) |
| The claim schema has no quantifier or modality | extractor and proposer schemas | D1 |
| "EMIT RELATIONS … mandatory" plus scaffolding of dependent continuants | `app/llm_proposer.py` rule 4, rule 10; `coherence_gate.scaffolding_directives` | D1, D2 (relations added to satisfy the rule, not the text) |
| Bearer-side restrictions are not checked against the text's quantifier; no check on one bearer per specifically dependent continuant | proposer rule 7, gate | D1, D2 |
| No document structure: claims carry a `section`, not an act and article | extractor chunking | D4, D7 |
| No legal kernel: reuse candidates come only from the working ontology | `summary_for_proposer`, `ontology/kernels/` holds only the coverage kernel | D7, D6 |

**Assumption to resolve first.** The baseline draft carries `meta:sourceSpan`, `meta:definitionStatus`, `meta:quantifierDefaulted`, `meta:scaffolded`, `meta:migratedFrom` and `meta:distinctFrom` annotations that do not exist in the public repository. The running copy (DGX or Hetzner) is ahead of GitHub. **Before implementing, push or merge the running code into `main`** and re-read this spec against it; where the running code already does something below (for example a `quantifierDefaulted` flag), extend it rather than adding a second mechanism.

---

## 1. Principles

1. **The text is the evidence.** Every axiom cites a verbatim span with a locator (act, article, paragraph, point, character offsets). Paraphrase is allowed for prompting, never as provenance.
2. **No universal without a universal.** A class-level axiom `C ⊑ p some D` requires text that says every C has such a D. "Can", "may", "where", "in some cases" never produce one.
3. **Every definition belongs to its act.** A legal definition is scoped to the instrument that makes it ("For the purposes of this Regulation…"). Two acts' definitions of one term are two scoped senses until a person decides otherwise.
4. **Anchor before minting.** BFO and the LOF patterns (ODP-001 to 004) are the first reuse candidates. A legal role, a legal act, a duty, a remedy are already modelled; the draft specializes them.
5. **Relations the text supports, not relations the rules demand.** An isolated class is a reported gap, not a reason to invent an axiom.
6. **Faithful and curated modes both benefit.** In `faithful` mode (`fidelity-mode-spec.md`) the text's own errors stand and are annotated; in `curated` mode the gate may still reject. These rules govern what is *proposed*, in both modes.

---

## 2. Source-aware ingestion (L1)

### 2.1 A legal document model

New module `app/legal_source.py`, used when a job's source is legislation, model rules or a code:

```python
@dataclass
class SourceUnit:
    act_id: str          # ELI or CELEX where known, else a stable local id ("DCFR", "BGB")
    act_title: str
    locator: str         # "art_4/par_1/pnt_7", "II.-6:101", "§ 164(1)"
    kind: str            # definition | scope | establishment | conferral | norm | condition | remedy | other
    text: str            # verbatim, normalized whitespace only
    offsets: tuple       # character span in the source file
    language: str        # "en"
```

**Readers** (in order of preference): Formex 4 XML (the Official Journal's format: definitions are numbered list items inside the definitions article), EUR-Lex XHTML (article and paragraph ids), Akoma Ntoso, the Japanese Law Translation XML, then plain text with heading detection ("Article 4", "§ 164", "II.–6:101"). A reader that cannot find structure falls back to today's chunking and marks every claim `locator: unknown`.

### 2.2 The act itself enters the graph (ODP-001)

Each source act becomes, at ingestion and before any claim:

- an individual of **legal content** (ODP001_0001001) for the act's content, labelled with its title and ELI;
- an individual of **enactment** (ODP001_0001003) that *establishes* it, with the adoption date where the source gives it;
- an individual of **legal document** (ODP001_0001002) for the published text (the Official Journal issue), *carrier of* the content.

Every definition and norm extracted later is *part of* that legal content. This alone fixes the absence of ODP-001 (D7) and gives every axiom a home.

---

## 3. Extraction for legal text (L2)

### 3.1 A genre profile

`EXTRACTION_PROMPT` becomes one of several **genre profiles**, selected per job (`genre: philosophy | legislation | model_rules | policy`), default `philosophy` (today's behaviour, unchanged). The `legislation` and `model_rules` profiles replace "the author's own voice" with **clause types**, and stop treating modal verbs as hedges:

| Clause type | Typical wording | Extract as |
| --- | --- | --- |
| Definition | "'X' means …", "X shall be understood as …" | A scoped definition (section 5) |
| Scope | "This Regulation applies to …", "shall not apply to …" | A scope rule on the act's content |
| Establishment / status | "is hereby established", "shall have legal personality" | An individual bearing a role (the EDPB pattern in the baseline, which was right) |
| Conferral | "shall designate", "shall be appointed", "shall have the power to" | ODP-002: a role conferred by legal content, borne by the designated party |
| Norm | "shall", "shall not", "may", "is entitled to", "has the right to" | ODP-003 positions: duty, prohibition, liberty, claim, with the correlative party where the text names one |
| Condition / trigger | "where", "in the case of", "after having become aware" | ODP-004 triggering occurrence |
| Time limit | "within 72 hours", "without undue delay" | A temporal constraint on the norm |
| Remedy / sanction | "shall have the right to lodge a complaint", "shall be liable", "administrative fines" | ODP-004 remedy |

### 3.2 The claim schema

```json
{
  "claim": "Short restatement, for prompting only",
  "source_quote": "Verbatim text, unchanged",
  "locator": "art_4/par_1/pnt_7",
  "act_id": "32016R0679",
  "clause_type": "definition",
  "subject": "controller",
  "quantifier": "every | some | can | at_most_one | none | definitional",
  "modality": "obligation | prohibition | permission | right | power | none",
  "confidence": "high | medium | low",
  "note": "..."
}
```

- `quantifier` records what the text says about how many, which is the field D1 needed. `definitional` is for "X means Y" (necessary and sufficient, section 5).
- `modality` records the deontic force of a norm; it is never inferred from a paraphrase.
- The extractor must copy `source_quote` exactly. A validator rejects any claim whose `source_quote` does not occur in the source unit (whitespace-normalized match).

### 3.3 The proposer sees the text

`orchestrator._feed_one_core` and `batch_propose` pass the proposer **both** the claim and the verbatim `source_quote` with its locator, and the claim's `quantifier`, `modality` and `clause_type`. Provenance (`source_text`, `meta:sourceSpan`) records the verbatim quote and the locator; the paraphrase is kept separately as `meta:claimText`. This is the single largest fix (D5) and a precondition for section 4.

---

## 4. Quantifier and direction discipline (L3)

### 4.1 Proposer rules

Replace rule 4's "This is mandatory, not optional" with "Emit the relations the source states; when a kind has no stated relation, say so in `open_questions`." Add:

- **Q1.** A `subClassOf` restriction `C ⊑ p some D` only when the claim's quantifier is `every` or `definitional`. For `can`, emit nothing at class level; record the possibility as an annotation (`meta:possibleRelation`) or, if the text names a particular, as an individual-level assertion.
- **Q2.** For roles, dispositions, functions and qualities, put the restriction on the **dependent** side (`DataProtectionOfficer ⊑ inheres_in some NaturalPerson`), never on the bearer class (`NaturalPerson ⊑ bearer_of some DataProtectionOfficer`) unless the text says every such bearer has it.
- **Q3.** A specifically dependent continuant class gets **at most one** `inheres_in` restriction. If the text names alternative bearers, use one restriction over their union.
- **Q4.** A definition's relative clauses must be carried as relations ("determines the purposes and means **of the processing of personal data**"); a dangling object is an `open_question`, not a dropped phrase (D6).
- **Q5.** Generically dependent continuants do not participate in processes directly. When the text says data is processed, link the process to the bearer of a concretization (the record, the storage medium) or use `is concretized by`, per BFO.

### 4.2 Gate checks (both modes)

New checks in `app/construction_linter.py`, run before the reasoner:

| Check | Fires when | In `curated` | In `faithful` |
| --- | --- | --- | --- |
| `bearer-universal` | An independent-continuant class is given `bearer_of some R` (R a role, disposition or function) without a claim of quantifier `every` | Reject; propose the dependent-side form | Commit; ledger entry |
| `multi-bearer` | A specifically dependent continuant class has more than one `inheres_in` restriction | Reject; propose the union form | Commit; ledger entry |
| `modal-universal` | A class-level axiom derives from a claim with quantifier `can`, modality `permission`, or a conditional | Reject | Commit; ledger entry |
| `unscoped-definition` | A definition axiom has no `act_id` | Reject | Ledger entry |
| `paraphrase-provenance` | `source_quote` is not found verbatim in the source unit | Reject the claim | Reject the claim (fidelity requires the real text) |

### 4.3 Scaffolding

`scaffolding_directives` proposes, and the job **records** (`meta:scaffolded true`, already in the running code), but scaffolding no longer *adds* bearer-side or multi-bearer restrictions; it adds only the dependent-side `inheres_in` / `realized_in` to the BFO default filler, and only when no text-derived restriction exists. Scaffolded axioms are listed in the draft report (section 7) for review.

---

## 5. Scoped definitions (L4)

1. Each legal definition becomes:
   - an individual of a definition class (LOF reuse candidate: legal content that is a definition; LOMO's `lomo:Definition` as an external alignment), *part of* the act's content;
   - `IAO_0000115` on the defined class holding the **verbatim definiens**, and `IAO_0000119` (definition source) holding the act's ELI and locator;
   - optionally a genus-differentia rendering marked `meta:definitionStatus derived`.
2. **One term, several acts.** If a term is already defined from another act and the definiens differs, create a scoped sense per act (`Controller_GDPR`, `Controller_EUI`, each labelled "controller (Regulation (EU) 2016/679)" and so on) under a shared general class "controller", and report the difference. Never overwrite the earlier definition (D4). If the definiens is the same, reuse the class and add the second source.
3. "For the purposes of this Regulation" and similar scope phrases are kept on the definition individual (`meta:scope`), so a reasoner-free reader can see the limit.

---

## 6. Kernel anchoring (L5) and identity (L6)

### 6.1 The LOF kernel

- Vendor the four LOF base releases (`lof-odp-00N-base.owl`, CC BY 4.0) and their BFO extract into `ontology/kernels/lof/`, with a catalog, pinned to their release tags.
- A job may name kernels in its manifest (`"kernels": ["lof"]`). Kernel classes and properties are added to the proposer's reuse candidates and to `BFO_PRIMER`'s companion list, with their definitions.
- Mapping guidance in the `legislation` profile prompt: designated or appointed parties' functions are subclasses of **legal role** (ODP-002); duties, prohibitions, liberties and claims are ODP-003 positions with their correlatives; triggering facts, generating acts and remedies use ODP-004. The baseline's `Controller`, `DataProtectionOfficer`, `LegalPerson` and `Supervisor` become specializations of legal role.
- The draft report includes an **MLC coverage table** per extracted norm: which of the eight chain nodes the text supplied (node, evidence span) and which are missing. This is reported, never filled in by the agent.

### 6.2 Identity and labels

- One label per referent: before committing a new class or individual, check for an existing entity with the same normalized label; a class and an individual with one label (D3) need a distinguishing label and `meta:distinctFrom`, or the proposer must reuse.
- Named institutions (EDPB, EDPS, the Commission) are individuals, created once, reused across claims and acts.
- All `rdfs:label` and `IAO_0000115` values are `@en` (or the source language) language-tagged strings (D8).

---

## 7. Draft quality report and finishing (L7, L8)

At job completion, before finalizing, write `draft-report.md` and `draft-report.json` beside the ontology:

| Section | Content |
| --- | --- |
| Sources | Acts, their ELI, articles covered, units read, units with no claims |
| Provenance | Share of axioms with a verbatim span (target 100%); claims rejected for paraphrase |
| Quantifiers | Class-level axioms by quantifier; any from `can`/permission (target 0 in curated) |
| Scaffolded axioms | Each, with its class, for review |
| Definitions | Terms defined in several acts, with the differences |
| Isolated terms | Classes with no relation other than their BFO parent (`DataProcessing` in the baseline) |
| `absent-in-source` | Count and share of classes with no definition in the source |
| MLC coverage | Per norm, nodes present and missing (section 6.1) |
| Ledger | Faithful-mode entries, with their checks |
| Audit | The OwlTester audit summary (below) |

**OwlTester as the final check.** When `OWLTESTER_URL` (or the local `owltester` CLI) is configured, run `owltester audit --profile bfo --fail-on major` on the finished draft and include its summary. Its lints should include the two this draft exposed (bearer-side universal roles, multi-bearer qualities); a companion change in OwlTesterService adds them to R2b, so the two tools agree.

**Finishing the artifact** (D8): ontology header with title, description, `dcterms:source` per act (ELI), license (default CC BY 4.0 for the ontology, with a note that the source texts' own reuse terms apply), `owl:versionInfo`, `owl:versionIRI`, creator, `prov:wasGeneratedBy` naming bfo-agent and its version; BFO imported through a catalog to the vendored file, not the network; a project namespace from the job manifest instead of the working namespace. This makes the draft a valid input to the OwlTester release builder (`SPEC-release.md` Part B).

---

## 8. Evaluation

### 8.1 Regression: the baseline draft

`tests/fixtures/legal/eu_personal_data_baseline.owl` and its source units. Re-running the same source with this spec in place must show:

| Defect | Expected |
| --- | --- |
| D1 | No `bearer_of some` restriction on `NaturalPerson`; the three "can" claims recorded as possibilities |
| D2 | `Independence` with one bearer restriction |
| D3 | One entity labelled "European Data Protection Supervisor" per referent, distinguished |
| D4 | Two scoped senses of controller (GDPR, 2018/1725) under one general class, with sources |
| D5 | Every `sourceSpan` found verbatim in the source |
| D6 | Controller related to processing of personal data; processing defined from Art. 4(2) |
| D7 | The acts as legal content established by enactments, carried by documents; roles under legal role |
| D8 | Language-tagged labels, header complete, BFO via catalog |

### 8.2 Benchmark corpus and metrics

A small gold standard, built by hand in LOF style (owner: David Koepsell; reviewers as he chooses):

1. GDPR Art. 4 (definitions), Arts. 33 and 34 (breach notification), Art. 58 (powers), Arts. 77 to 83 (remedies and fines).
2. Regulation 2018/1725 Art. 3 and Directive 2016/680 Art. 3 (the same terms, defined again).
3. Directive 93/13/EEC Arts. 2 to 6 (unfair terms: contract formation and a constitutive legal effect).
4. Later: DCFR Annex and Books II and III; BGB §§ 104 to 185, 194, 241, 280 (English translation).

Metrics per run, reported in `evaluation/legal/`:

| Metric | Target |
| --- | --- |
| Verbatim provenance rate | 100% |
| Modal-derived universal axioms (curated) | 0 |
| Bearer-universal and multi-bearer axioms (curated) | 0 |
| Definitions with source act | 100% |
| Class and axiom precision / recall against gold | Report; improve run on run |
| Kernel anchoring: extracted roles and positions under ODP classes | 90% or more |
| MLC node recall per norm against gold | Report |
| OwlTester verdict (`bfo` profile) | No critical; majors explained |
| Run-to-run variance (3 runs, as in `evaluation/variance/`) | Report |

---

## 9. Open decisions

1. Default genre for new jobs: keep `philosophy`, or infer from the source (Formex or XHTML input implies `legislation`)? (This spec: infer, overridable.)
2. Should `can` claims produce nothing at class level (this spec) or a weaker OWL form (for example a `meta:possibleRelation` annotation axiom on the class)? (This spec: the annotation.)
3. Scoped senses: separate classes per act (this spec), or one class with per-act definition individuals and no class split until a contradiction appears?
4. Should the LOF kernel be vendored into bfo-agent (this spec) or imported from the w3id release IRIs through a catalog at job start?
5. Gold standard: who builds and reviews it, and is it published with LOF?

---

## Suggested order

1. **Reconcile** the running code with `main` (section 0).
2. **L1 + 3.3:** source units with locators; the proposer and provenance see the verbatim text. Biggest effect, smallest change.
3. **L3:** quantifier and modality in the schema; proposer rules Q1 to Q5; the five gate checks; scaffolding limits.
4. **L2:** the `legislation` genre profile.
5. **L4, L5, L6:** scoped definitions, the LOF kernel, identity and labels.
6. **L7, L8:** the draft report, OwlTester check, artifact finishing.
7. **Evaluation:** the regression fixture first, then the benchmark corpus.
