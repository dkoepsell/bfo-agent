# Spec: Prover9/Mace4 FOL Gate (post-HermiT first-order audit)

Status: DRAFT — scoped 2026-07-06, not yet implemented.

## 1. Motivation

Every deductive check in the pipeline today is OWL-DL: HermiT consistency at
commit time (`ontology_manager.commit_proposal`), the coherence gate
(`app/coherence_gate.py`), and the structural PC/G linters. But BFO 2020's
normative axiomatization is first-order (ISO/IEC 21838-2, Common Logic), and
the OWL rendering the agent reasons over drops most of it: temporalized
`instance_of`/`exists_at`, continuant/occurrent mereology, specific and
generic dependence, participation. A module can therefore pass HermiT and the
coherence gate while contradicting the real BFO axioms.

Two tools close the gap, with complementary verdicts:

- **Mace4** (finite model finder): a found model is *positive* evidence of
  consistency — strictly stronger than HermiT's "no clash found", and it is
  independent tooling cross-checking the whole owlready2/HermiT path.
- **Prover9** (FOL theorem prover): a derivation of `$F` is a *definite*
  inconsistency with an auditable, citable proof object — useful both as a
  diagnostic and as artifact evidence for publications.

The BFO-2020 repository ships the FOL axioms in Prover9 syntax already
(`21838-2/prover9/*.prover9`), modularized by sub-theory, so no CLIF→LADR
translation is needed on the BFO side. Only the OWL module needs translating.

## 2. Placement and non-goals

**FG-0. Governing principle: evidence, never correction.** When the ontology
is extracted from a text, it must stay true to that text — *including the
text's own errors*. The FOL gate therefore has no write path of any kind:
it opens the ontology read-only, never mutates, repairs, filters, or
re-samples anything, never invokes the coherence gate's repair/resample
policies, and never writes annotations into the `.owl` file itself. Its sole
output is sidecar evidence (JSON records, proof/model artifacts, report
sections) telling the user *that* and *where* the extracted ontology is
logically inconsistent or BFO-incoherent. An inconsistent verdict is a
finding about the source text, not a defect to fix in the ontology. Every
requirement below is subordinate to this.

**FG-1.** The FOL gate is an *out-of-loop audit tier*, not part of the
per-proposal gate loop. Prover9 searches are semi-decidable and Mace4 domain
sweeps are exponential; neither belongs on the claim-commit path. The in-loop
gates (linter → HermiT → coherence) are unchanged.

**FG-2.** Trigger points, in implementation order:
  1. CLI, on demand, against any OWL file (working ontology, `sool.owl`,
     `case_module.owl`, …).
  2. `app/job_runner.py`: one FOL audit at end-of-job over the job's final
     working ontology, result attached to the job record.
  3. (optional, env-gated) every N commits during a job.

**FG-3.** Report-only, permanently (per FG-0). A definite Prover9
inconsistency is surfaced as a WARN in the job report and an ntfy
notification; it never rolls back commits, fails the job, or gates any
downstream step. There is no blocking mode: blocking would let the audit
shape what gets extracted, which violates fidelity to the source text.

**Non-goals:** no TPTP/other-prover backends; no CLIF parsing; no per-proposal
in-loop use; DGX fork migration is out of scope (no LLM involvement anyway,
but binaries/vendoring would need porting — decide later in DGX_MIGRATION.md).

## 3. Translation: OWL module → LADR clauses (`app/fol_gate.py`)

**TR-1.** New module `app/fol_gate.py` containing the translator and the two
runner wrappers. No new dependencies beyond the two binaries (§7).

**TR-2. Construct whitelist.** Translate exactly the constructs the pipeline
emits (see `construction_linter.py` / `owl_checks.py`): `SubClassOf` with
named classes, `ObjectComplementOf`, `ObjectIntersectionOf`,
`ObjectUnionOf`, `ObjectSomeValuesFrom`, `ObjectAllValuesFrom`,
`DisjointClasses`, `EquivalentClasses`, class assertions, object-property
assertions, property domain/range, `SubObjectPropertyOf`. Standard DL→FOL
translation (e.g. `A ⊑ B` → `all x (a(x) -> b(x))`;
`A ⊑ ∃r.B` → `all x (a(x) -> exists y (r(x,y) & b(y)))`).

**TR-3. No silent drops.** Any axiom outside the whitelist is emitted to the
run report as a SKIPPED entry (axiom rendering + reason). The gate result
records `skipped_axiom_count`; a nonzero count downgrades "consistent" to
"consistent (partial translation)".

**TR-4. Two signature modes.**
  - **Mode A (untemporalized, self-contained):** unary predicates per class,
    binary per property, module axioms only (no BFO FOL axioms; the module's
    imported BFO *OWL* skeleton — hierarchy + disjointness from
    `bfo_catalog.py` — is translated in). This is a HermiT cross-check with
    stronger positive verdicts and proof objects.
  - **Mode B (BFO-2020 conformance):** the module is bridged into the exact
    signature declared by the vendored axiom files
    (`universal-declaration.prover9`, `existence-instantiation.prover9`):
    class membership becomes `instance_of(x, <class-constant>, t)`, using the
    axiom files' constant names for BFO classes and freshly declared
    constants (following the universal-declaration pattern) for
    working-namespace classes.

**TR-5. Temporal bridge (Mode B), stated approximation.** OWL assertions are
atemporal; the bridge adopts *rigidity*: a class assertion `A(a)` becomes
`all t (exists_at(a,t) -> instance_of(a, A, t))` for continuant classes, and
the axiom files' occurrent instantiation form for occurrent classes. Object
property assertions map to the temporalized relations of
`temporalized-relations.p9` where a mapping exists, with the same
all-times-of-existence reading; unmapped properties stay untemporalized
binary predicates (recorded as UNBRIDGED in the report, analogous to TR-3).
This direction is chosen so that a Mode-B inconsistency can be caused by the
rigidity assumption only when the module genuinely asserts something
time-sensitive — which OWL cannot express — i.e. false positives are
bounded to a class of cases the report can flag.

**TR-6. IRI→symbol mapping.** BFO IRIs map to axiom-file constants via a
static table generated once from `bfo_catalog.py` + the vendored
`universal-declaration.prover9`; an unmapped *BFO* IRI is a hard error (the
table is stale), an unmapped working IRI just mints a fresh symbol
(`w_<sanitized-fragment>`). Symbol minting must be deterministic and
collision-checked.

## 4. Mace4 runner (consistency evidence)

**M-1.** `mace4` runs on the merged clause set with a domain-size sweep
(2..`FOL_MACE4_MAX_DOMAIN`, default 8) and per-run CPU limit. Outcomes:
`MODEL_FOUND(n)` (consistent; model saved as artifact),
`EXHAUSTED` / `TIMEOUT` (inconclusive — reported as such, never as
"consistent").

**M-2.** Mode A is the primary Mace4 target. Mode B with the full axiom set
is expected to be hard for a model finder; run it with the reduced default
profile (§6) and accept `TIMEOUT` as the common outcome. (Stretch, not
required: seed from the repo's shipped consistent model in
`21838-2/prover9/model/`.)

## 5. Prover9 runner (inconsistency detection + probes)

**P-1. Refutation check.** Prover9 with goal `$F` over the merged set, CPU
limit `FOL_TIMEOUT_SECS` (default 60). `PROOF` ⇒ definite inconsistency:
save the proof object as an artifact, extract the input clauses used in the
proof, and map them back to source axioms/IRIs for the report (the proof is
the diagnosis — this is the FOL analogue of the coherence gate's culprit
listing). Where extraction provenance exists (axiom → session record →
source passage), the report follows it through, so the user sees *which
claims of the text* jointly contradict — the evidence FG-0 exists to
deliver.

**P-2. Incoherence probes.** A configurable list of conjectures run as
individual Prover9 goals, each answering "does the module + axioms entail
this pathology?" Initial probe set:
  - category straddle: `exists x exists t (instance_of(x, C1, t) &
    instance_of(x, C2, t))` for each top-level disjoint category pair — the
    FOL mirror of the coherence gate's straddle check;
  - per-class unsatisfiability: for each working class `W`, goal
    `all x all t -(instance_of(x, W, t))` — PROOF means `W` can have no
    instance (FOL-unsatisfiable class), the Mode-B analogue of HermiT's
    `inconsistent_classes()`.
Probes are budgeted (`FOL_PROBE_TIMEOUT_SECS`, default 10 each) and the
per-class probe set is capped with the cap logged (no silent truncation).

## 6. BFO-2020 axiom vendoring

**B-1.** Vendor the Prover9 axiom files from
`github.com/BFO-ontology/BFO-2020` (`21838-2/prover9/`: the 14
`*.prover9`/`*.p9` sub-theory files) into `ontology/bfo-2020-fol/`, pinned to
a named commit recorded in `ontology/bfo-2020-fol/PROVENANCE.md` together
with the upstream license. `FOL_AXIOMS_DIR` overrides the location.

**B-2. Profiles.** Sub-theories are selectable. Default Mode-B profile:
`universal-declaration`, `existence-instantiation`, `temporalized-relations`,
`continuant-mereology`, `specific-dependency`, `generic-dependence`,
`participation`. Full set (adds `occurrent-mereology`, `temporal-region`,
`spatial`, `spatiotemporal`, `material-entity`, `history`, `order`) behind
`FOL_AXIOM_PROFILE=full`. The active profile is recorded in every result.

## 7. Operational constraints

**O-1. Binaries.** `FOL_PROVER9_BIN` / `FOL_MACE4_BIN` (defaults `prover9` /
`mace4` on PATH; Debian package `prover9`, or LADR-2009-11A built from
source). If either binary is absent the gate soft-disables: one startup log
line, all trigger points no-op, pipeline unaffected (A-5).

**O-2. Serialization + rlimits.** Same discipline as the 2026-07-02 HermiT
incident fix: one process-wide `_FOL_LOCK`, and FOL runs additionally acquire
`_REASONER_LOCK` (`ontology_manager.py:43`) so a prover never runs
concurrently with a HermiT java process on the 4GB host. Subprocesses get
`RLIMIT_CPU` (from the timeouts above) and `RLIMIT_AS` 1 GB, plus a
wall-clock kill at 2× the CPU limit.

**O-3. Config.** New in `app/config.py`: `FOL_GATE_ENABLED` (default off),
`FOL_TIMEOUT_SECS`,
`FOL_PROBE_TIMEOUT_SECS`, `FOL_MACE4_MAX_DOMAIN`, `FOL_AXIOMS_DIR`,
`FOL_AXIOM_PROFILE`, `FOL_PROVER9_BIN`, `FOL_MACE4_BIN`.

## 8. Reporting

**R-1.** Each run produces a JSON result record: ontology ref + content hash,
mode, axiom profile, mace4 outcome, prover9 outcome, probe results, skipped/
unbridged axiom counts, elapsed, artifact paths (model file, proof files).
Persisted under the active ontology's `sessions/` directory alongside the
feed session logs.

**R-2.** Job reports (and the report HTML) gain a "FOL audit" section
rendering R-1, framed as *findings about the extracted content* ("the
ontology faithfully represents the text; the following claims of the text
are jointly inconsistent under BFO"), not as pipeline errors; definite
inconsistencies notify via the existing ntfy topic.

## 9. CLI

**C-1.** `python -m app.fol_gate <file.owl> [--mode A|B] [--profile default|full]
[--probes] [--out result.json]` — runs offline against any OWL file, prints a
human summary, writes the R-1 record. This is deliverable #1 so the
translator can be exercised against `sool.owl` / DSM output before any
pipeline wiring.

## 10. Acceptance criteria

- **A-1.** Toy consistent module (Mode A) → Mace4 `MODEL_FOUND`, Prover9 no
  proof within budget.
- **A-2.** Module with `A ⊑ B`, `A ⊑ ¬B`, `A(a)` (Mode A) → Prover9 `PROOF`
  of `$F`; report maps the proof back to the three source axioms; HermiT
  agrees (cross-check consistency).
- **A-3.** A module that is HermiT-*consistent* but Mode-B Prover9-`PROOF`
  inconsistent. Validated case: a straddle class with no instance — OWL
  reports a mere satisfiability defect (unsatisfiable class, ontology
  consistent), while the ISO axioms prove `$F` outright because
  every-universal-is-instantiated-at-some-time forces an impossible
  instance; the proof object names the module axioms and the ISO axioms
  that jointly clash. (Purely OWL-invisible violations — e.g. mereological
  antisymmetry cycles — are also expressible but their proofs may exceed
  the default budget; raise `FOL_TIMEOUT_SECS` for deep-mereology audits.)
- **A-4.** Module containing a whitelisted-out construct → SKIPPED entry in
  the report, verdict downgraded per TR-3, no crash.
- **A-5.** Binaries absent → gate soft-disabled, CLI exits with a clear
  message, job runner unaffected.
- **A-6.** With `FOL_GATE_ENABLED=1`, an end-to-end feed job completes with
  an FOL-audit section in its report; total added wall-clock ≤ the
  configured budgets (audit runs once, after the job).
- **A-7 (fidelity, FG-0).** Byte-identical ontology before and after any
  audit run — verified by content hash in the test — including runs that
  find definite inconsistencies. The audit process opens the file read-only;
  no code path in `app/fol_gate.py` imports commit/repair machinery.

## 11. Implementation order

1. **FIX-A:** vendoring (B-1/B-2) + Mode-A translator + Mace4/Prover9
   runners + CLI (C-1). Tests A-1, A-2, A-4, A-5.
2. **FIX-B:** Mode-B bridge (TR-4/TR-5/TR-6) + probes (P-2). Test A-3.
3. **FIX-C:** job-runner wiring, report section, ntfy, config flags
   (FG-2.2, R-2, O-3). Tests A-6, A-7.
4. **FIX-D (optional):** every-N-commits trigger, model seeding (M-2
   stretch).
