# Spec: Faithful-Extraction Mode ("annotate, don't repair")

Status: DRAFT — scoped 2026-07-06, not yet implemented.
Companion to `fol-gate-spec.md` (FG-0); this spec applies the same principle
to the *in-loop* pipeline.

## 1. Motivation

Principle (user-mandated, 2026-07-06): an ontology extracted from a text must
be true to that text, *including the text's own logical and ontological
errors*. The system's job is to surface evidence of those errors to the user,
never to correct them.

The in-loop pipeline currently violates this in four places:

1. **Gate REJECT** (`coherence_gate.gate`): a lint- or reasoner-tier clash
   discards the text's claim outright.
2. **REPAIR / resample policies** (`run_with_policy`, `GATE_POLICY` env →
   `orchestrator.py:103`): repair rewrites the proposal (drops parent edges,
   mints `*Realizable` classes); reject-resample/reground re-prompt the LLM,
   pressuring it to distort the text's claim until it passes the gate.
3. **Commit backstop** (`ontology_manager.commit_proposal`, verify=True →
   `CommitCoherenceError` rollback at `ontology_manager.py:952`): a claim the
   text genuinely makes can be silently rolled back.
4. **Scaffolding** (`apply_scaffolding`): adds existential restrictions the
   text never asserted.

All four are correct behavior for *authoring* a coherent ontology; they are
wrong for *representing* a text. Both uses matter, so this is a mode, not a
replacement.

## 2. Mode definition and selection

**FM-1.** Two modes: `curated` (today's behavior, unchanged, the default) and
`faithful` (this spec). Selected per ontology via a `"fidelity"` field in the
library `manifest.json` (`ontology/library/<Name>/manifest.json`); absent
field means `curated`. Env `FIDELITY_DEFAULT` may change the default for new
ontologies only — never reinterprets an existing manifest.

**FM-2.** The active mode is resolved once per job by the job runner /
orchestrator and threaded explicitly (parameter, not global), and is stamped
into every session record, ledger entry (§5), and report so a reader always
knows which regime produced the artifact.

## 3. Gate behavior in faithful mode

**FM-3. Construction tier stays fully active.** PC-1..PC-8 police the *LLM's
rendering* (malformed names, untyped entities, invented predicates), not the
text's claims — a construction violation is the proposer's error, never the
author's. Rejection and resampling on construction violations remain, but the
resample prompt in faithful mode must instruct: re-render the *same textual
claim* in well-formed terms; do not weaken or alter its content.

**FM-4. Lint and reasoner tiers become evidential.** New
`GateOutcome.FLAG` and `GatePolicy.ANNOTATE` in `coherence_gate.py`. In
faithful mode the policy is forced to ANNOTATE: a lint straddle or reasoner
incoherence produces a `GateResult(outcome=FLAG, ...)` carrying the same
diagnostic payload (clash_pair, unsat_classes, subject, justification) as
today's REJECT — but the proposal proceeds to commit *unmodified*.
`repair_proposal` and content resampling are never invoked. `run_with_policy`
returns after at most one gate pass (no retry loop for FLAG).

**FM-5. Scaffolding becomes advisory.** `scaffolding_directives` still runs
(the questions are valuable), but in faithful mode `apply_scaffolding` is not
called; directives are appended to the proposal's open questions and the
report instead. No axiom the text did not assert is committed.

## 4. Commit path

**FM-6.** `commit_proposal` gains a fidelity parameter. In faithful mode the
verify backstop does not roll back: the axioms are committed as-asserted, and
the post-commit HermiT verdict (inconsistent ontology and/or unsatisfiable
classes) is recorded as a ledger entry (§5) instead of raising
`CommitCoherenceError`. Rollback still applies to *mechanical* failures
(serialization errors, malformed IRIs — the G-1..G-4 class of defects), which
are pipeline bugs, not text content.

## 5. Evidence: annotations + incoherence ledger

**FM-7. Ledger.** Append-only JSONL per ontology under its `sessions/`
directory (`incoherence_ledger.jsonl`). One entry per FLAG or post-commit
incoherence: mode, timestamp, subject IRI(s), gate tier, full `GateResult`
payload, the committed axioms involved (as structured triples, exactly as
committed), and the extraction provenance (session id, source passage) so
evidence points back to *what the text said*. The ledger is the machine-
readable interface consumed by reports and by the FOL gate's provenance
mapping (`fol-gate-spec.md` P-1).

**FM-8. In-file annotations, additive only.** Each flagged class is annotated
with a `working:incoherenceEvidence` annotation property (value: short human
summary + ledger entry id). Annotations are OWL-DL semantics-free, so the
ontology's logical content — the text's content — is untouched; this is the
one permitted category of write. No axiom is added, removed, or modified.
(Class-level annotation + ledger detail is chosen over reified owl:Axiom
annotations, which owlready2 handles poorly.)

## 6. Keeping the gate alive after a flag

**FM-9. Coherent-view dry-runs.** Once a genuinely incoherent claim is
committed, naive HermiT dry-runs for *subsequent* proposals become useless
(an inconsistent ontology entails everything). In faithful mode,
`check_coherence_dry_run` evaluates candidates against a *coherent view*: the
working ontology minus the ledgered clash axioms (reconstructed from FM-7's
structured triples on a scratch copy — the persisted file is never touched).
Thus claim N+1 is still gated on its own merits, and each new incoherence is
attributed to its own claims rather than drowned in prior flags.

**FM-10.** If the coherent view itself is inconsistent (ledger
reconstruction missed something), degrade to construction+lint tiers only,
log loudly, and record the degradation in the session — never block the feed
and never widen the exclusion set silently.

## 7. Reporting

**FM-11.** Job reports and the report HTML gain an "Incoherence findings"
section rendering the ledger: per finding, the source passage(s), the
committed axioms, and the diagnosis ("the text asserts Force is both a
quality and a disposition; these BFO categories are disjoint") — framed as
findings about the text, per FG-0. The end-of-job FOL audit
(`fol-gate-spec.md`) supplies the deeper first-order evidence over the same
ontology and cross-links ledger entries where its proofs involve flagged
axioms.

## 8. Acceptance criteria

- **FA-1.** Faithful mode, text asserting a straddle ("X is a quality";
  "X is a disposition"): both parents committed as-asserted, class annotated
  (FM-8), one ledger entry with both source passages, no repair/resample
  event in the session log.
- **FA-2.** After FA-1, an unrelated coherent proposal is still gated
  normally and an unrelated *incoherent* proposal is still flagged with its
  own diagnosis (coherent-view dry-run, FM-9).
- **FA-3.** Faithful mode never invokes `repair_proposal`,
  `apply_scaffolding`, or content resampling (asserted via session events);
  construction-tier resample still fires on a PC violation.
- **FA-4.** Removing the ledgered clash axioms from a copy of the persisted
  file yields a HermiT-consistent ontology (ledger completeness check).
- **FA-5.** Curated mode regression: with no `fidelity` field or
  `"curated"`, byte-for-byte identical behavior to today on the existing
  test corpus (gate outcomes, commits, reports).
- **FA-6.** Mode is visible in every session record, ledger entry, and
  report header (FM-2).

## 9. Implementation order

1. **FIX-A:** mode plumbing (manifest field, config, threading; FM-1/FM-2)
   + `GateOutcome.FLAG`/`GatePolicy.ANNOTATE` + commit-path flag-and-commit
   (FM-4/FM-6). Tests FA-1 (minus annotation), FA-5.
2. **FIX-B:** ledger + annotations + report section (FM-7/FM-8/FM-11).
   Tests FA-1 (full), FA-4, FA-6.
3. **FIX-C:** coherent-view dry-run + degradation path (FM-9/FM-10),
   scaffolding-advisory + construction-resample prompt wording (FM-3/FM-5).
   Tests FA-2, FA-3.
