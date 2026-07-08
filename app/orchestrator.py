"""Flask orchestrator.

Endpoints:
  POST /propose       utterance -> structured proposal + reasoner verdict
  POST /commit        confirmed proposal -> write to working ontology + git
  GET  /graph         current ontology stats + recent commits
  GET  /graph/classes list working classes with parents
  GET  /graph/individuals  list named individuals with types
  POST /query         grounded Q&A against the working ontology
  GET  /session/<id>  replay the event log
  GET  /health        liveness
"""
from __future__ import annotations

import atexit
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)

from flask import Flask, Response, jsonify, request
from flask_cors import CORS
from pydantic import ValidationError

from . import config
from . import coherence_gate as gate_mod
from . import gate_client
from . import incoherence_ledger as ledger_mod
from . import kext as kext_mod
from . import job_runner
from . import job_transfer
from . import jobs as jobs_store
from . import timing
from .coherence_gate import GateOutcome, GatePolicy
from .extractor import ClaimExtractor, chunk_text
from .llm_proposer import LLMProposer
from .ontology_manager import CommitCoherenceError, OntologyManager
from .registry import FinalizeVerificationError, OntologyRegistry
from .schema import (
    CommitRequest,
    Proposal,
    ProposeRequest,
    QueryRequest,
    QueryResponse,
)
from .storage import (
    gate_log_path,
    git_commit_library_change,
    git_commit_working_ontology,
    load_session,
    log_event,
    log_gate_events,
    recent_commits,
    session_path,
)


# Global single-instance state. The orchestrator is not designed for
# concurrent writers; a single lock serializes proposal/commit operations.
_lock = threading.Lock()
_registry: OntologyRegistry | None = None
_proposer: LLMProposer | None = None
_extractor: ClaimExtractor | None = None
# Small in-memory store of the latest proposal per id so /commit can round-trip
_proposal_cache: dict[str, Proposal] = {}


def _get_registry() -> OntologyRegistry:
    global _registry
    if _registry is None:
        active = config.active_ontology_name()
        if active is None:
            raise RuntimeError(
                "No active ontology name in config. Did phase 1 run?"
            )
        _registry = OntologyRegistry(
            bfo_path=config.BFO_PATH,
            library_root=config.LIBRARY_ROOT,
            active_name=active,
        )
    return _registry


def _get_manager() -> OntologyManager:
    """Backward-compatible shortcut to the active manager.

    Preserves the method name used throughout this module so the phase 2
    diff stays minimal. Phase 3 will add a dispatcher that honors an
    optional ontology-name parameter.
    """
    return _get_registry().active_manager()


def _get_proposer() -> LLMProposer:
    global _proposer
    if _proposer is None:
        _proposer = LLMProposer()
    return _proposer


def _get_extractor() -> ClaimExtractor:
    global _extractor
    if _extractor is None:
        _extractor = ClaimExtractor()
    return _extractor


def _gate_policy(faithful: bool = False) -> GatePolicy:
    # fidelity-mode-spec.md FM-4: faithful extraction forces annotate --
    # lint/reasoner clashes are flagged and committed as-asserted, never
    # repaired or content-resampled, regardless of GATE_POLICY.
    if faithful:
        return GatePolicy.ANNOTATE
    try:
        return GatePolicy(config.GATE_POLICY)
    except ValueError:
        return GatePolicy.REJECT_RESAMPLE


def _active_fidelity() -> str:
    """Fidelity mode of the active ontology (FM-1/FM-2); curated on any error."""
    try:
        return _get_registry().fidelity()
    except Exception:
        return "curated"


def _run_coherence_gate(proposal, mgr, proposer, ctx, session_id, context=None,
                        faithful=False, exclude_axioms=None):
    """Run the coherence gate under the configured policy and log the events.

    Returns the GateRun. The caller decides what to do with GateRun.outcome and
    GateRun.proposal (which may be a repaired/resampled rewrite of the input).
    """
    utterance = proposal.utterance

    def resample_fn(prev_proposal, gate_result, neighborhood):
        scope = (
            "Regenerate the local neighborhood of axioms for this claim"
            if neighborhood
            else "Re-propose this claim"
        )
        if getattr(gate_result, "violations", None):
            # Construction tier: feed the structured rule/rewrite guidance back
            # so the proposer self-corrects the prohibited constructions.
            fixes = "\n".join(
                f"- [{v['rule']}] '{v['offending_term']}': {v['suggested_rewrite']}"
                for v in gate_result.violations
            )
            note = (
                f"\n\nCONSTRAINT: a previous attempt used prohibited "
                f"constructions. {scope}, applying each fix below. Prefer "
                f"individuals + BFO object-property assertions + class "
                f"expressions over minting new classes; never name a class for "
                f"an absence or a relation.\n{fixes}"
            )
        else:
            note = (
                f"\n\nCONSTRAINT: a previous attempt violated BFO coherence. "
                f"{gate_result.reason} {scope} so that no class is placed under "
                f"two disjoint BFO categories. Choose a single coherent BFO genus."
            )
        try:
            return proposer.propose(
                utterance=utterance + note,
                session_id=session_id,
                working_classes=ctx["working_classes"],
                known_individuals=ctx["known_individuals"],
                relevant_classes=ctx.get("relevant_classes"),
            )
        except Exception:
            return None

    run = gate_mod.run_with_policy(
        proposal,
        mgr,
        policy=_gate_policy(faithful),
        resample_fn=resample_fn,
        run_reasoner=config.GATE_RUN_REASONER,
        max_attempts=config.GATE_MAX_ATTEMPTS,
        run_construction=config.ENABLE_CONSTRUCTION_LINTER,
        strict_closed_vocab=config.STRICT_CLOSED_VOCAB,
        exclude_axioms=exclude_axioms,
    )

    if run.result.degraded:
        # FM-10: reasoner tier was skipped because the coherent view itself
        # is incoherent (ledger reconstruction incomplete). Log loudly.
        log.warning(
            "Coherence gate DEGRADED for session %s: %s",
            session_id, run.result.reason,
        )

    # Stamp the (possibly rewritten) proposal with the gate verdict.
    final = run.proposal
    final.gate_outcome = run.outcome.value
    final.gate_tier = run.result.tier.value
    final.gate_reason = run.result.reason or None
    if run.events:
        final.gate_policy_action = run.events[-1].get("policy_action")

    log_gate_events(session_id, run.events, context=context)
    return run


def _budget_and_kext_notes(proposal, mgr) -> list[str]:
    """Soft class-count budget (FR-7) + kernel-extension paper trail (spec §8).

    Never blocks a commit; surfaces warnings so proliferation and
    kernel-extension requests are visible rather than silently accepted.
    """
    notes: list[str] = []
    new_classes = [
        e for e in proposal.entities
        if getattr(e, "kind", None) == "class"
        and getattr(e, "is_new", False)
        and not getattr(e, "existing_iri", None)
    ]
    n = len(new_classes)
    if config.CLASS_BUDGET_PER_PROPOSAL and n > config.CLASS_BUDGET_PER_PROPOSAL:
        names = ", ".join(
            (e.iri_suggestion or e.label) for e in new_classes
        )
        notes.append(
            f"class-budget: this proposal mints {n} new classes "
            f"(soft cap {config.CLASS_BUDGET_PER_PROPOSAL}); prefer individuals "
            f"and BFO property assertions over new classes. New: {names}"
        )
    if config.CLASS_BUDGET_WARN_TOTAL:
        try:
            total = mgr.stats().get("num_classes", 0)
            if total > config.CLASS_BUDGET_WARN_TOTAL:
                notes.append(
                    f"class-proliferation: the working ontology now has {total} "
                    f"classes (> {config.CLASS_BUDGET_WARN_TOTAL})."
                )
        except Exception:
            pass
    # §8: structured kernel-extension-requests. Persisted as *.kext.json for
    # human review (best-effort) and surfaced as notes. Never blocks the commit.
    requests = kext_mod.collect(proposal, config.KERNEL_VERSION_IRI)
    for req in requests:
        try:
            kext_dir = Path(mgr.working_path).resolve().parent / "kext"
            kext_mod.persist(req, kext_dir)
        except Exception:
            pass  # the paper trail is advisory; never fail a commit over it
        detail = req.term + (f" -- {req.justification}" if req.justification else "")
        notes.append(f"kernel-extension-request [{req.id}]: {detail}")
    return notes


def _apply_scaffolding(proposal, mgr, session_id) -> list[dict]:
    """Add the BFO constraint a dependent-continuant class requires, then verify.

    Runs after a class is committed. The scaffolding is validated by a coherence
    dry-run; if it somehow makes the ontology incoherent it is rolled back, so
    the scaffolding can never introduce a clash (SPEC Task 4).
    """
    if not config.ENABLE_SCAFFOLDING:
        return []
    directives = gate_mod.scaffolding_directives(proposal, mgr)
    if not directives:
        return []

    # Amortized save (change 7): the backup/rollback below works on disk
    # bytes and _load()s on rollback, so the file must reflect the in-memory
    # world (including this claim's just-applied commit) before scaffolding
    # touches it — else a rollback would silently drop the unsaved window.
    if not config.SAVE_EVERY_COMMIT and getattr(mgr, "unsaved_commits", 0) > 0:
        mgr.save()

    backup = mgr.working_path.read_bytes() if mgr.working_path.exists() else None
    applied = gate_mod.apply_scaffolding(directives, mgr)
    if not applied:
        return []
    mgr.save()

    coherent, _unsat, _detail = mgr.check_coherence_dry_run(
        Proposal(session_id=session_id, utterance="")
    )
    if not coherent:
        if backup is not None:
            mgr.working_path.write_bytes(backup)
        mgr._load()
        log_event(session_id, "scaffold_rollback",
                  {"directives": directives})
        return []

    log_event(session_id, "scaffold", {"applied": applied})
    return applied


def _ledger_faithful_commit(mgr, proposal, gate_run, session_id,
                            provenance) -> str | None:
    """Record evidence after a faithful-mode commit (FM-7/FM-8).

    Two sources, mutually exclusive by construction (a FLAGged commit skips
    the post-commit verify): a gate FLAG carrying the pre-commit diagnosis,
    or a post-commit verify that found NEW incoherence (retroactive ABox
    clash). Returns the ledger entry id, or None when the commit was coherent.
    Never raises -- evidence recording must not fail a commit that fidelity
    says stands.
    """
    try:
        if gate_run is not None and gate_run.outcome == GateOutcome.FLAG:
            result = gate_run.result
        elif getattr(mgr, "last_commit_incoherence", None):
            detail = mgr.last_commit_incoherence.get("detail", "")
            unsat = []
            if "unsatisfiable classes:" in detail:
                unsat = [
                    s.strip() for s in
                    detail.split("unsatisfiable classes:", 1)[1].split("\n")[0].split(",")
                    if s.strip()
                ]
            result = gate_mod.GateResult(
                outcome=gate_mod.GateOutcome.FLAG,
                tier=gate_mod.GateTier.REASONER,
                reason=f"post-commit incoherence: {detail}",
                unsat_classes=unsat,
            )
        else:
            return None

        subjects = [result.subject] if result.subject else []
        subjects += [iri for iri in result.unsat_classes if iri not in subjects]
        exclude = gate_mod.clash_exclusion_triples(proposal, result)
        entry_id = ledger_mod.record_flag(
            mgr, proposal, result.to_dict(), exclude, subjects,
            provenance=provenance,
        )
        log_event(session_id, "incoherence_flagged", {
            "ledger_id": entry_id,
            "subjects": subjects,
            "reason": result.reason,
            "exclude_axioms": exclude,
            **{k: v for k, v in (provenance or {}).items()
               if k in ("job_id", "claim_id")},
        })
        return entry_id
    except Exception:
        log.exception("Failed to record incoherence ledger entry")
        return None


def _fol_audit_on_complete(job_id: str, state: dict) -> str | None:
    """End-of-job FOL audit hook (fol-gate-spec.md FG-2.2).

    Evidence-only and best-effort: runs once after a feed job completes,
    writes the R-1 record under the ontology's sessions/ directory, and
    returns a one-line summary for the completion notification. Never blocks
    or fails the job; disabled unless FOL_GATE_ENABLED and the prover9/mace4
    binaries are present.
    """
    if not config.FOL_GATE_ENABLED:
        return None
    try:
        from . import fol_gate

        if not fol_gate.binaries_available():
            return None
        mgr = _get_manager()
        record = fol_gate.audit(mgr.working_path, mode="B", probes=True)
        return fol_gate.summarize(record)
    except Exception as e:
        log.exception("FOL audit failed for job %s", job_id)
        return f"FOL audit failed: {e}"


# ---------------------------------------------------------------------------
# Phase 3: per-request ontology resolution
# ---------------------------------------------------------------------------

def _resolve_ontology_for_read(name: str | None):
    """Return (manager, resolved_name) for a read endpoint.

    If `name` is None, returns the active manager. If `name` is given
    but unknown, raises KeyError which the caller should map to 404.
    """
    reg = _get_registry()
    if name is None:
        return reg.active_manager(), reg.active_name()
    mgr = reg.get(name)  # raises OntologyNotFoundError (a KeyError) if absent
    return mgr, name


def _ontology_query_arg():
    """Read the ?ontology=NAME query string, or None."""
    return request.args.get("ontology", None)


def _not_found_response(name: str):
    return jsonify({
        "status": "error",
        "error": f"ontology not found: {name!r}",
    }), 404


def _active_is_finalized() -> bool:
    """True iff the active ontology's manifest declares status=='finalized'.

    Used to guard write endpoints. Phase 4 is what actually sets that
    status; for now this returns False in normal operation.
    """
    try:
        reg = _get_registry()
        manifest = reg.manifest(reg.active_name())
        return manifest.get("status") == "finalized"
    except Exception:
        return False


def _finalized_guard_response():
    reg = _get_registry()
    return jsonify({
        "status": "error",
        "error": (
            f"active ontology {reg.active_name()!r} is finalized; "
            f"writes are disabled. Create a new active ontology first."
        ),
    }), 409


def _count_pending(job_id: str) -> int:
    job = jobs_store.load_job(job_id)
    return sum(1 for c in job["claims"]
               if c["status"] == "pending" and c.get("approved"))


def _emit_claim_timing(session_id: str, job_id: str, claim_id,
                       proposal_id, verdict, committed: bool,
                       gate_attempts, mgr, t_claim: float) -> None:
    """Emit one "claim_timing" event (SPEC-bfo-agent-speed.md Step 0).

    ``ms`` carries the per-phase timings accumulated in :mod:`app.timing`
    plus a wall-clock ``total`` measured from claim selection. Pure
    instrumentation: guarded by TIMING_INSTRUMENTATION and wrapped so a
    telemetry failure can never break the feed.
    """
    if not config.TIMING_INSTRUMENTATION:
        return
    try:
        ms = timing.snapshot()
        ms["total"] = round((time.perf_counter() - t_claim) * 1000, 1)
        st = mgr.stats()
        log_event(session_id, "claim_timing", {
            "job_id": job_id,
            "claim_id": claim_id,
            "proposal_id": proposal_id,
            "verdict": verdict,
            "committed": committed,
            "gate_attempts": gate_attempts,
            "ms": ms,
            "stats": {"classes": st.get("num_classes"),
                      "individuals": st.get("num_individuals")},
        })
    except Exception as e:  # noqa: BLE001 — never break the feed for telemetry
        log.warning("claim_timing emission failed: %s", e)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ledger_checkpoint_incoherence(mgr, report: dict, job_id: str,
                                   suspect_from, suspect_to,
                                   kind: str = "checkpoint") -> str | None:
    """Faithful mode: a failed full-graph certificate is evidence, not a
    defect to fix (extraction-fidelity principle). One incoherence-ledger
    entry records the finding with checkpoint provenance; feeding continues.
    Never raises -- evidence recording must not stop a faithful run."""
    try:
        return ledger_mod.append(mgr.working_path, {
            "mode": "faithful",
            "subjects": report.get("unsat_classes") or [],
            "tier": kind,
            "gate_result": {"reason": report.get("detail")},
            "exclude_axioms": [],
            "provenance": {
                "checkpoint": True,
                "kind": kind,
                "job_id": job_id,
                "claim_window": [suspect_from, suspect_to],
            },
        })
    except Exception:
        log.exception("Failed to ledger %s-verify incoherence", kind)
        return None


def _flush_unsaved_locked(mgr, job_id: str | None, session_id: str | None,
                          watermark_claim_id: int | None,
                          reason: str) -> bool:
    """Persist unsaved in-memory commits (SPEC-bfo-agent-speed.md change 7).

    Caller must hold the module _lock. Saves the working file, git-commits
    it, and advances the job's last_saved_claim_id watermark so crash
    recovery never resets claims that are actually on disk. No-op when
    nothing is unsaved.
    """
    if getattr(mgr, "unsaved_commits", 0) <= 0:
        return False
    unsaved = mgr.unsaved_commits
    mgr.save()
    git_commit_working_ontology(
        session_id or "amortized-save", f"flush-{reason}",
        f"amortized save flush ({reason}, {unsaved} commit(s))",
    )
    if job_id is not None:
        if watermark_claim_id is None:
            job = jobs_store.load_job(job_id)
            committed = [c["id"] for c in job.get("claims", [])
                         if c.get("status") == "committed"]
            watermark_claim_id = max(committed) if committed else 0
        jobs_store.bump_feed_state(
            job_id, last_saved_claim_id=watermark_claim_id
        )
    if session_id:
        log_event(session_id, "amortized_save", {
            "reason": reason,
            "unsaved_commits": unsaved,
            "job_id": job_id,
            "last_saved_claim_id": watermark_claim_id,
        })
    return True


def flush_working_ontology(job_id: str | None = None,
                           reason: str = "flush") -> bool:
    """Public flush point for amortized save: pause/runner exit and graceful
    shutdown. With no job_id, every job currently marked feeding gets its
    watermark advanced (single-writer in practice). No-op unless
    SAVE_EVERY_COMMIT is off and unsaved commits exist.
    """
    if config.SAVE_EVERY_COMMIT:
        return False
    with _lock:
        try:
            mgr = _get_manager()
        except Exception:
            return False  # no active ontology yet; nothing to flush
        if getattr(mgr, "unsaved_commits", 0) <= 0:
            return False
        if job_id is not None:
            job_ids = [job_id]
        else:
            job_ids = [j["job_id"] for j in jobs_store.list_jobs()
                       if j.get("status") == "feeding"]
        session_id = None
        if job_ids:
            try:
                session_id = jobs_store.load_job(job_ids[0]).get("session_id")
            except Exception:  # noqa: BLE001 — flush must not raise
                pass
        flushed = _flush_unsaved_locked(
            mgr, job_ids[0] if job_ids else None, session_id, None, reason
        )
        # Multi-job edge: advance the remaining feeding jobs' watermarks too.
        for extra in job_ids[1:]:
            try:
                job = jobs_store.load_job(extra)
                committed = [c["id"] for c in job.get("claims", [])
                             if c.get("status") == "committed"]
                jobs_store.bump_feed_state(
                    extra,
                    last_saved_claim_id=max(committed) if committed else 0,
                )
            except Exception:  # noqa: BLE001
                pass
        return flushed


def _flush_at_exit() -> None:
    """Graceful-shutdown backstop (atexit): a clean SIGTERM/exit persists
    whatever the amortized-save mode is still holding in memory."""
    if _registry is None:
        return  # nothing was ever loaded, so nothing can be unsaved
    try:
        flush_working_ontology(reason="shutdown")
    except Exception:  # noqa: BLE001 — never raise during interpreter exit
        pass


atexit.register(_flush_at_exit)


def _prepare_amortized_resume(job_id: str) -> list[int]:
    """Crash recovery for amortized save (SPEC-bfo-agent-speed.md change 7),
    run before a job's feed loop (re)starts.

    A crash between saves loses the in-memory commits, so claims recorded
    committed after the last on-disk save watermark are reset to pending and
    re-feed through the full path (bounded to <= FULL_VERIFY_EVERY_K
    re-proposals; stable IRIs plus the reservation map converge on the same
    entities). When the live process still holds the unsaved commits
    (plain pause/resume, no restart), nothing was lost and nothing is reset.
    A None watermark means this is the job's first run under amortized save:
    memory equals disk right now, so the current committed set becomes the
    saved baseline. Returns the reset claim ids.
    """
    if config.SAVE_EVERY_COMMIT:
        return []
    with _lock:
        try:
            mgr = _get_manager()
        except Exception:
            return []
        if getattr(mgr, "unsaved_commits", 0) > 0:
            return []  # live memory still holds them; flush will persist
        job = jobs_store.load_job(job_id)
        fs = jobs_store.get_feed_state(job)
        last_saved = fs.get("last_saved_claim_id")
        committed_ids = [c["id"] for c in job.get("claims", [])
                         if c.get("status") == "committed"]
        if last_saved is None:
            jobs_store.bump_feed_state(
                job_id,
                last_saved_claim_id=max(committed_ids) if committed_ids
                else 0,
            )
            return []
        stale = [cid for cid in committed_ids if cid > last_saved]
        if not stale:
            return []
        reset = jobs_store.reset_claims_pending(job_id, stale)
        log_event(job.get("session_id"), "resume_replay_reset", {
            "job_id": job_id,
            "claim_ids": reset,
            "last_saved_claim_id": last_saved,
            "reason": "committed after last on-disk save; memory lost",
        })
        return reset


def _maybe_checkpoint_verify(job_id: str, claim: dict, session_id: str,
                             mgr, faithful: bool,
                             exclusions) -> dict | None:
    """Bump the persisted commit counter and, every FULL_VERIFY_EVERY_K
    commits, run the full-graph HermiT certificate over the saved artifact
    (SPEC-bfo-agent-speed.md change 6). Called from _feed_one_core inside the
    module _lock, only when VERIFY_EVERY_COMMIT is off.

    Returns None to keep feeding, or a fatal-shaped dict when a curated
    checkpoint failed: the job is paused here and job_runner._run reads the
    "fatal" key as stop-the-loop-and-notify.
    """
    # With per-commit save still on, the just-committed claim is on disk, so
    # keep the save watermark fresh (protects against a later flip to
    # amortized save wrongly resetting these claims on crash recovery).
    extra = (
        {"last_saved_claim_id": claim["id"]}
        if config.SAVE_EVERY_COMMIT else {}
    )
    fs = jobs_store.bump_feed_state(
        job_id, increment={"commits_since_checkpoint": 1}, **extra
    )
    if fs["commits_since_checkpoint"] < config.FULL_VERIFY_EVERY_K:
        return None

    # Amortized save (change 7): the certificate judges the saved artifact,
    # so persist the unsaved window first. The checkpoint is the natural
    # save boundary.
    if not config.SAVE_EVERY_COMMIT:
        _flush_unsaved_locked(
            mgr, job_id, session_id, claim["id"], "checkpoint"
        )

    suspect_from = fs.get("last_verified_claim_id")
    report = mgr.verify_full(exclude_axioms=exclusions if faithful else None)
    log_event(session_id, "checkpoint_verify", {
        **report,
        "job_id": job_id,
        "suspect_from": suspect_from,
        "suspect_to": claim["id"],
    })

    if report["ok"] or faithful:
        if not report["ok"]:
            _ledger_checkpoint_incoherence(
                mgr, report, job_id, suspect_from, claim["id"]
            )
        jobs_store.bump_feed_state(
            job_id, commits_since_checkpoint=0,
            last_checkpoint_at=_utcnow_iso(),
            last_checkpoint_ok=report["ok"],
            last_verified_claim_id=claim["id"],
        )
        return None

    # Curated failure: pause with a <=K-claim suspect window. The counter and
    # last_verified_claim_id are left untouched so a resume re-runs the
    # certificate with the same bisection bounds. No auto-rollback:
    # evidence-first, the per-commit git history of working.owl makes
    # bisection tractable.
    jobs_store.bump_feed_state(
        job_id, last_checkpoint_at=_utcnow_iso(), last_checkpoint_ok=False
    )
    flipped: list[int] = []
    if config.CHECKPOINT_FAIL_MARK_REVIEW:
        flipped = jobs_store.mark_window_needs_review(
            job_id, suspect_from, claim["id"]
        )
    jobs_store.set_job_status(job_id, "paused")
    return {
        "fatal": "checkpoint_verify_failed",
        "error": (
            f"full-graph checkpoint failed after claim {claim['id']} "
            f"(suspect window {suspect_from}..{claim['id']}"
            + (f"; {len(flipped)} claim(s) flipped to needs_review"
               if flipped else "")
            + f"): {report['detail']}"
        ),
    }


def _final_verify_before_complete(job_id: str, session_id: str) -> dict | None:
    """Mandatory full-graph certificate before a job is marked completed
    (SPEC-bfo-agent-speed.md change 6). Called from _feed_one_core's
    no-pending branch inside the module _lock, so both the server-side
    runner and manual stepping get it.

    Returns None when completion may proceed, or a fatal-shaped dict when a
    curated final pass failed (the job is paused instead of completed).
    """
    if not config.FINALIZE_REQUIRES_FULL_VERIFY:
        return None
    job = jobs_store.load_job(job_id)
    fs = jobs_store.get_feed_state(job)
    mgr = _get_manager()
    if (
        config.VERIFY_EVERY_COMMIT
        and fs.get("commits_since_checkpoint", 0) <= 0
        and getattr(mgr, "commits_since_full_verify", 0) <= 0
    ):
        return None  # every commit already carried the full certificate

    faithful = _active_fidelity() == "faithful"
    exclusions = (
        ledger_mod.exclusion_triples(mgr.working_path) if faithful else None
    )

    committed_ids = [c["id"] for c in job.get("claims", [])
                     if c.get("status") == "committed"]
    last_claim_id = max(committed_ids) if committed_ids else None

    # Amortized save (change 7): persist the tail window before the
    # certificate and advance the watermark — completion is a save boundary.
    if not config.SAVE_EVERY_COMMIT:
        _flush_unsaved_locked(
            mgr, job_id, session_id, last_claim_id or 0, "final"
        )

    report = mgr.verify_full(exclude_axioms=exclusions)
    log_event(session_id, "final_verify", {**report, "job_id": job_id})

    if report["ok"] or faithful:
        if not report["ok"]:
            _ledger_checkpoint_incoherence(
                mgr, report, job_id,
                fs.get("last_verified_claim_id"), last_claim_id,
                kind="final",
            )
        jobs_store.bump_feed_state(
            job_id, commits_since_checkpoint=0,
            last_checkpoint_at=_utcnow_iso(),
            last_checkpoint_ok=report["ok"],
            last_verified_claim_id=last_claim_id,
        )
        return None

    # Curated failure: pause instead of completing. job_runner._fail
    # notifies; the job stays resumable once the operator bisects the
    # offending commit out of working.owl's git history.
    jobs_store.bump_feed_state(
        job_id, last_checkpoint_at=_utcnow_iso(), last_checkpoint_ok=False
    )
    jobs_store.set_job_status(job_id, "paused")
    return {
        "fatal": "final_verify_failed",
        "error": f"final full verification failed: {report['detail']}",
    }


def _resume_verify(job_id: str) -> bool:
    """Boot-resume certificate (SPEC-bfo-agent-speed.md change 6): a job
    interrupted with unverified commits gets one verify_full before its feed
    loop restarts. Passed to job_runner.resume_incomplete; runs inside the
    runner thread so boot is never blocked.

    Returns True to resume feeding. False (curated failure) makes the runner
    pause the job and notify instead of resuming. Fails open on mechanical
    errors -- the mandatory completion pass remains the backstop.
    """
    try:
        # Amortized-save crash recovery (change 7) runs first: claims
        # committed after the last on-disk save are reset to pending before
        # the certificate judges (and before feeding trusts) the disk state.
        _prepare_amortized_resume(job_id)
        job = jobs_store.load_job(job_id)
        fs = jobs_store.get_feed_state(job)
        if fs.get("commits_since_checkpoint", 0) <= 0:
            return True
        session_id = job.get("session_id")
        with _lock:
            mgr = _get_manager()
            faithful = _active_fidelity() == "faithful"
            exclusions = (
                ledger_mod.exclusion_triples(mgr.working_path)
                if faithful else None
            )
            report = mgr.verify_full(exclude_axioms=exclusions)
        log_event(session_id, "resume_verify", {**report, "job_id": job_id})
        committed_ids = [c["id"] for c in job.get("claims", [])
                         if c.get("status") == "committed"]
        last_claim_id = max(committed_ids) if committed_ids else None
        if report["ok"] or faithful:
            if not report["ok"]:
                _ledger_checkpoint_incoherence(
                    mgr, report, job_id,
                    fs.get("last_verified_claim_id"), last_claim_id,
                    kind="resume",
                )
            jobs_store.bump_feed_state(
                job_id, commits_since_checkpoint=0,
                last_checkpoint_at=_utcnow_iso(),
                last_checkpoint_ok=report["ok"],
                last_verified_claim_id=last_claim_id,
            )
            return True
        jobs_store.bump_feed_state(
            job_id, last_checkpoint_at=_utcnow_iso(),
            last_checkpoint_ok=False,
        )
        return False
    except Exception as e:  # noqa: BLE001 — never brick boot-resume
        log.warning("resume_verify failed open (%s); resuming feed", e)
        return True


def _feed_one_core(job_id: str, auto_accept: bool = True) -> dict:
    """Feed the next pending+approved claim in the job.

    Runs /propose internally (not the HTTP route) so the full proposal
    and reasoner verdict are produced. If auto_accept is true and the
    verdict is 'consistent', it also commits. Either way, the claim's
    status is persisted to the job file before returning.

    Shared by the /feed_one route and the server-side job runner, so it
    returns plain dicts:
      {done: true} when no pending+approved claims remain,
      {fatal: ..., error: ...} when feeding must stop for good,
      {claim, proposal, committed, warnings, remaining} otherwise.

    Raises FileNotFoundError if the job does not exist.
    """
    if _active_is_finalized():
        reg = _get_registry()
        return {
            "fatal": "finalized",
            "error": (
                f"active ontology {reg.active_name()!r} is finalized; "
                f"writes are disabled."
            ),
        }

    job = jobs_store.load_job(job_id)
    session_id = job["session_id"]

    with _lock:
        # Claim selection happens under the lock so a concurrent caller
        # (e.g. a stale browser tab still looping /feed_one alongside the
        # server-side runner) cannot grab the same claim.
        pending = jobs_store.next_pending(job_id, limit=1)
        if not pending:
            # Mandatory final pass (SPEC-bfo-agent-speed.md change 6): any
            # commit that skipped the per-claim full verify gets certified
            # here, before the job may be marked completed.
            final_fatal = _final_verify_before_complete(job_id, session_id)
            if final_fatal is not None:
                return final_fatal
            jobs_store.set_job_status(job_id, "completed")
            return {"done": True, "remaining": 0}
        claim = pending[0]

        # Per-claim phase timers (SPEC-bfo-agent-speed.md Step 0).
        timing.start_claim()
        t_claim = time.perf_counter()

        mgr = _get_manager()
        proposer = _get_proposer()
        ctx = mgr.summary_for_proposer(utterance=claim["claim"])

        # Batch propose (SPEC-bfo-agent-speed.md change 5): a prepare pass
        # (app/batch_propose.py) may have already run this claim through the
        # LLM against a context snapshot. Consume the stored proposal instead
        # of calling the API inline -- keeping the stored proposal_id for
        # determinism -- and clear it (consume-once) in the same write that
        # records the claim's outcome below. The gate + commit path is
        # untouched, and any gate resample still re-proposes live.
        proposal = None
        used_stored_proposal = False
        pre = claim.get("proposal") if config.BATCH_PROPOSE_ENABLED else None
        if pre:
            try:
                with timing.phase("propose"):  # near-zero; keeps the key
                    data = dict(pre)
                    data["session_id"] = session_id
                    data.setdefault("utterance", claim["claim"])
                    proposal = Proposal.model_validate(data)
                used_stored_proposal = True
            except Exception as e:
                # Malformed stored proposal: fall back to live propose.
                log_event(session_id, "batch_proposal_invalid",
                          {"error": str(e), "job_id": job_id,
                           "claim_id": claim["id"]})
                proposal = None

        if proposal is None:
            try:
                with timing.phase("propose"):
                    proposal = proposer.propose(
                        utterance=claim["claim"],
                        session_id=session_id,
                        working_classes=ctx["working_classes"],
                        known_individuals=ctx["known_individuals"],
                        relevant_classes=ctx.get("relevant_classes"),
                    )
            except Exception as e:
                jobs_store.update_claim_status(
                    job_id, claim["id"], "error", verdict="error"
                )
                log_event(session_id, "propose_error",
                          {"utterance": claim["claim"], "error": str(e),
                           "job_id": job_id, "claim_id": claim["id"]})
                _emit_claim_timing(session_id, job_id, claim["id"], None,
                                   "error", False, None, mgr, t_claim)
                remaining = _count_pending(job_id)
                return {
                    "claim": claim,
                    "proposal": None,
                    "error": str(e),
                    "committed": False,
                    "remaining": remaining,
                }

        # Extraction fidelity (fidelity-mode-spec.md): in faithful mode the
        # ontology must stay true to the text including its errors -- clashes
        # are flagged and committed as-asserted, with the ledgered clash
        # axioms excluded from dry-runs so each claim is judged on its own
        # merits (FM-9).
        faithful = _active_fidelity() == "faithful"
        exclusions = (
            ledger_mod.exclusion_triples(mgr.working_path) if faithful else None
        )

        # Coherence gate under the configured policy. This is the
        # load-bearing check: it may rewrite the proposal (repair/resample)
        # before it becomes eligible to commit, and it catches the
        # consistent-but-incoherent straddles the legacy check missed.
        gate_run = None
        if config.ENABLE_COHERENCE_GATE:
            with timing.phase("gate_total"):
                run = _run_coherence_gate(
                    proposal, mgr, proposer, ctx, session_id,
                    context={"job_id": job_id, "claim_id": claim["id"],
                             "proposal_id": proposal.proposal_id},
                    faithful=faithful, exclude_axioms=exclusions,
                )
            gate_run = run
            proposal = run.proposal
            gate_accepted = run.outcome in (GateOutcome.ACCEPT,
                                            GateOutcome.FLAG)
            if run.outcome == GateOutcome.ACCEPT:
                verdict = "consistent"
            elif run.outcome == GateOutcome.FLAG:
                verdict = "flagged"
            else:
                verdict = "inconsistent"
            proposal.reasoner_verdict = verdict
            proposal.reasoner_detail = run.result.reason or run.result.justification
        else:
            try:
                with timing.phase("gate_total"):
                    ok, detail = mgr.check_consistency_dry_run(proposal)
                proposal.reasoner_verdict = "consistent" if ok else "inconsistent"
                proposal.reasoner_detail = detail
            except Exception as e:
                proposal.reasoner_verdict = "error"
                proposal.reasoner_detail = str(e)
            gate_accepted = proposal.reasoner_verdict == "consistent"
            verdict = proposal.reasoner_verdict

        _proposal_cache[proposal.proposal_id] = proposal
        propose_payload = {
            "proposal": proposal.model_dump(),
            "job_id": job_id,
            "claim_id": claim["id"],
        }
        if used_stored_proposal:
            propose_payload["proposal_source"] = "batch"
        log_event(session_id, "propose", propose_payload)

        committed = False
        warnings: list[str] = []

        if not gate_accepted:
            new_status = "inconsistent"
        elif auto_accept:
            try:
                # Faithful mode (FM-6): a gate FLAG already carries the
                # incoherence verdict, so skip the redundant post-commit
                # reasoner pass; an ACCEPTed claim is verified against the
                # coherent view so only NEW incoherence is reported.
                flagged = gate_run is not None and \
                    gate_run.outcome == GateOutcome.FLAG
                # SPEC-bfo-agent-speed.md change 6: with VERIFY_EVERY_COMMIT
                # off, the per-claim commit never runs the full-graph verify;
                # the checkpoint/final-pass certificate covers it instead.
                # Default (on) keeps today's behavior exactly.
                with timing.phase("commit_total"):
                    warnings = mgr.commit_proposal(
                        proposal,
                        verify=(not flagged) if config.VERIFY_EVERY_COMMIT
                        else False,
                        faithful=faithful,
                        exclude_axioms=exclusions,
                    )
            except CommitCoherenceError as e:
                # commit-time backstop rolled the ontology back: the
                # proposal would have made the base incoherent. Treat it
                # exactly like a reasoner rejection, not an error.
                log_event(session_id, "commit_rolled_back", {
                    "proposal_id": proposal.proposal_id,
                    "reason": str(e),
                    "job_id": job_id,
                    "claim_id": claim["id"],
                })
                new_status = "inconsistent"
                verdict = "inconsistent"
            except Exception as e:
                log_event(session_id, "commit_error", {
                    "proposal_id": proposal.proposal_id,
                    "error": str(e),
                    "job_id": job_id,
                    "claim_id": claim["id"],
                })
                new_status = "error"
            else:
                if faithful:
                    entry_id = _ledger_faithful_commit(
                        mgr, proposal, gate_run, session_id,
                        {"session_id": session_id, "job_id": job_id,
                         "claim_id": claim["id"],
                         "source_text": claim["claim"]},
                    )
                    if entry_id:
                        verdict = "flagged"
                        warnings = (warnings or []) + [
                            f"incoherence-flagged [{entry_id}]: committed "
                            f"as-asserted; see incoherence ledger"
                        ]
                warnings = (warnings or []) + _budget_and_kext_notes(
                    proposal, mgr
                )
                # FM-5: in faithful mode scaffolding is advisory -- the BFO
                # constraint a category requires becomes an open question,
                # never a committed axiom the text did not assert.
                if faithful:
                    scaffolded = []
                    directives = gate_mod.scaffolding_directives(proposal, mgr)
                    if directives:
                        for d in directives:
                            if d.get("question"):
                                proposal.open_questions.append(d["question"])
                        log_event(session_id, "scaffold_advisory", {
                            "directives": directives,
                            "job_id": job_id,
                            "claim_id": claim["id"],
                        })
                else:
                    with timing.phase("scaffolding"):
                        scaffolded = _apply_scaffolding(
                            proposal, mgr, session_id
                        )
                # Amortized save (change 7): nothing new reached disk on
                # this claim, so there is nothing for git to record; the
                # flush points commit the batched window instead.
                if config.SAVE_EVERY_COMMIT:
                    with timing.phase("git"):
                        git_commit_working_ontology(
                            session_id, proposal.proposal_id,
                            claim["claim"][:80]
                        )
                log_event(session_id, "commit", {
                    "proposal_id": proposal.proposal_id,
                    "decision": "accept",
                    "fidelity": "faithful" if faithful else "curated",
                    "warnings": warnings,
                    "scaffolded": scaffolded,
                    "proposal": proposal.model_dump(),
                    "job_id": job_id,
                    "claim_id": claim["id"],
                })
                _proposal_cache.pop(proposal.proposal_id, None)
                new_status = "committed"
                committed = True
        else:
            new_status = "needs_review"

        jobs_store.update_claim_status(
            job_id, claim["id"], new_status,
            proposal_id=proposal.proposal_id,
            verdict=verdict,
            clear_proposal=used_stored_proposal,
        )

        # Checkpointed full verification (SPEC-bfo-agent-speed.md change 6):
        # only when the per-claim full verify is off. Runs after the claim's
        # status is persisted so a failed checkpoint's suspect window is
        # bounded by claims actually recorded as committed.
        checkpoint_fatal = None
        if committed and not config.VERIFY_EVERY_COMMIT:
            checkpoint_fatal = _maybe_checkpoint_verify(
                job_id, claim, session_id, mgr, faithful, exclusions
            )

        _emit_claim_timing(
            session_id, job_id, claim["id"], proposal.proposal_id,
            verdict, committed,
            gate_run.attempts if gate_run is not None else None,
            mgr, t_claim,
        )

    if checkpoint_fatal is not None:
        # Curated checkpoint failure: the job was paused inside the lock;
        # the fatal shape stops job_runner._run and fires its notification.
        return checkpoint_fatal

    remaining = _count_pending(job_id)
    return {
        "claim": {**claim, "status": new_status,
                  "proposal_id": proposal.proposal_id,
                  "verdict": verdict},
        "proposal": proposal.model_dump(),
        "committed": committed,
        "warnings": warnings,
        "remaining": remaining,
    }


def create_app() -> Flask:
    app = Flask(
        __name__,
        static_folder=str(Path(__file__).resolve().parent.parent / "client"),
        static_url_path="/static",
    )
    CORS(app, origins=["*", "null"])

    @app.after_request
    def _cors_null_origin(response):
        # Browsers send Origin: null for file:// requests; * doesn't cover null
        if request.headers.get("Origin") == "null":
            response.headers["Access-Control-Allow-Origin"] = "null"
        return response

    @app.get("/")
    def serve_client():
        from flask import send_from_directory
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/health")
    def health():
        try:
            name = _ontology_query_arg()
            try:
                mgr, resolved = _resolve_ontology_for_read(name)
            except KeyError:
                return _not_found_response(name)
            cache_stats = _proposer.stats() if _proposer is not None else None
            return jsonify({
                "status": "ok",
                "ontology": resolved,
                "stats": mgr.stats(),
                "prompt_cache": cache_stats,
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.get("/ontologies")
    def list_ontologies():
        """Phase 2 preview: read-only listing of the ontology library.

        Added in phase 2 so that post-migration we can inspect the registry
        state via curl. UI integration arrives in phase 5.
        """
        try:
            reg = _get_registry()
            return jsonify({
                "active": reg.active_name(),
                "ontologies": reg.list_ontologies(),
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    # ------------------------------------------------------------------
    # Phase 4: lifecycle endpoints (create, activate, finalize, delete)
    # ------------------------------------------------------------------

    @app.post("/ontologies")
    def create_ontology():
        """Create a new ontology in the library.

        Body: {
          "name": "Peirce_categorial",         # required
          "description": "...",                 # required
          "source_text": "...",                 # optional
          "author": "...",                      # optional
          "clone_seeds_from": "SOoL_v1"         # optional; default active
        }

        Does NOT activate. Use /ontologies/<name>/activate separately.
        """
        try:
            body = request.get_json(force=True) or {}
            name = (body.get("name") or "").strip()
            description = (body.get("description") or "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            if not description:
                return jsonify({"error": "description required"}), 400

            with _lock:
                reg = _get_registry()
                try:
                    manifest = reg.create(
                        name=name,
                        description=description,
                        source_text=body.get("source_text"),
                        author=body.get("author"),
                        clone_seeds_from=body.get("clone_seeds_from"),
                    )
                except ValueError as e:
                    return jsonify({"error": str(e)}), 400
                except KeyError as e:
                    return jsonify({"error": f"not found: {e}"}), 404

                lib_entry = config.LIBRARY_ROOT / name
                git_commit_library_change(
                    [lib_entry],
                    f"Create ontology {name}",
                )

            return jsonify({
                "name": name,
                "manifest": manifest,
                "status": "created",
            }), 201
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/ontologies/<name>/activate")
    def activate_ontology(name):
        """Make <name> the active writable ontology."""
        try:
            with _lock:
                reg = _get_registry()
                try:
                    new_active, previous = reg.activate(name)
                except KeyError:
                    return _not_found_response(name)

                if new_active != previous:
                    active_file = config.LIBRARY_ROOT.parent / "active.txt"
                    git_commit_library_change(
                        [active_file],
                        f"Activate ontology {new_active} (was {previous})",
                    )

            return jsonify({
                "active": new_active,
                "previous_active": previous,
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/ontologies/<name>/finalize")
    def finalize_ontology(name):
        """Mark <name> as finalized (read-only). Idempotent."""
        try:
            with _lock:
                reg = _get_registry()
                try:
                    manifest = reg.finalize(name)
                except KeyError:
                    return _not_found_response(name)
                except FinalizeVerificationError as e:
                    # SPEC-bfo-agent-speed.md change 6: unverified commits
                    # failed the fresh full-graph certificate; refuse to
                    # freeze an uncertified artifact.
                    return jsonify({
                        "status": "error",
                        "error": str(e),
                        "report": e.report,
                    }), 409

                manifest_path = config.LIBRARY_ROOT / name / "manifest.json"
                git_commit_library_change(
                    [manifest_path],
                    f"Finalize ontology {name}",
                )

            return jsonify({
                "name": name,
                "manifest": manifest,
                "status": "finalized",
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.get("/ontologies/<name>/download")
    def download_ontology(name):
        """Download an ontology's OWL file as an attachment.

        Works for any library ontology, finalized or not; the finalized flow
        is finalize -> download. Bytes are read under the write lock so a
        concurrent commit can't hand back a half-written file.
        """
        try:
            reg = _get_registry()
            try:
                mgr = reg.get(name)
            except KeyError:
                return _not_found_response(name)
            if not mgr.working_path.exists():
                return jsonify({"error": f"no OWL file on disk for '{name}'"}), 404
            with _lock:
                data = mgr.working_path.read_bytes()
            resp = app.response_class(data, mimetype="application/rdf+xml")
            resp.headers["Content-Disposition"] = (
                f'attachment; filename="{name}.owl"'
            )
            return resp
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.get("/ontologies/<name>/incoherence")
    def ontology_incoherence(name):
        """Incoherence-findings report (fidelity-mode-spec.md FM-11).

        Renders the ledger: findings about the extracted TEXT (the ontology
        faithfully represents it; these claims are jointly incoherent under
        BFO), each with provenance back to the source passage. Empty for
        curated ontologies.
        """
        try:
            reg = _get_registry()
            try:
                mgr = reg.get(name)
            except KeyError:
                return _not_found_response(name)
            entries = ledger_mod.read_all(mgr.working_path)
            return jsonify({
                "ontology": name,
                "fidelity": reg.fidelity(name),
                "framing": (
                    "The ontology faithfully represents its source text; "
                    "each finding below is evidence that claims OF THE TEXT "
                    "are jointly inconsistent or incoherent under BFO."
                ),
                "count": len(entries),
                "entries": entries,
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.get("/ontologies/<name>/fol_audit")
    def ontology_fol_audit(name):
        """Latest FOL audit record for an ontology (fol-gate-spec.md R-1/R-2).

        Read-only: reports the most recent Prover9/Mace4 audit; POST-free by
        design -- audits run at end-of-job or via the CLI, never from here.
        """
        try:
            reg = _get_registry()
            try:
                mgr = reg.get(name)
            except KeyError:
                return _not_found_response(name)
            from . import fol_gate

            record = fol_gate.latest_record(mgr.working_path)
            if record is None:
                return jsonify({
                    "ontology": name,
                    "status": "no_audit",
                    "detail": "no FOL audit has been run for this ontology",
                    "enabled": config.FOL_GATE_ENABLED,
                    "binaries_available": fol_gate.binaries_available(),
                }), 404
            return jsonify({"ontology": name, "record": record})
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/ontologies/preview-import")
    def preview_import_ontology():
        """Inspect an uploaded OWL file. Does NOT commit."""
        try:
            if "file" not in request.files:
                return jsonify({"error": "missing file upload"}), 400
            uploaded = request.files["file"]
            if not uploaded.filename:
                return jsonify({"error": "empty filename"}), 400

            import tempfile
            from pathlib import Path as _Path
            with tempfile.NamedTemporaryFile(
                suffix=_Path(uploaded.filename).suffix or ".owl",
                delete=False,
            ) as tmp:
                uploaded.save(tmp.name)
                tmp_path = _Path(tmp.name)

            try:
                reg = _get_registry()
                report = reg.preview_owl(tmp_path)
                return jsonify(report)
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/ontologies/import")
    def import_ontology():
        """Import an OWL file as a new library entry."""
        try:
            if "file" not in request.files:
                return jsonify({"error": "missing file upload"}), 400
            uploaded = request.files["file"]
            if not uploaded.filename:
                return jsonify({"error": "empty filename"}), 400

            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            if not description:
                return jsonify({"error": "description required"}), 400

            import tempfile
            from pathlib import Path as _Path
            with tempfile.NamedTemporaryFile(
                suffix=_Path(uploaded.filename).suffix or ".owl",
                delete=False,
            ) as tmp:
                uploaded.save(tmp.name)
                tmp_path = _Path(tmp.name)

            try:
                with _lock:
                    reg = _get_registry()
                    try:
                        manifest = reg.import_from_file(
                            file_path=tmp_path,
                            name=name,
                            description=description,
                            source_text=request.form.get("source_text") or None,
                            author=request.form.get("author") or None,
                            clone_seeds_from=request.form.get("clone_seeds_from") or None,
                        )
                    except ValueError as e:
                        return jsonify({"error": str(e)}), 400
                    except KeyError as e:
                        return jsonify({"error": f"not found: {e}"}), 404

                    lib_entry = config.LIBRARY_ROOT / name
                    git_commit_library_change(
                        [lib_entry],
                        f"Import ontology {name} (from {uploaded.filename})",
                    )

                return jsonify({
                    "name": name,
                    "manifest": manifest,
                    "status": "imported",
                }), 201
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500


    @app.delete("/ontologies/<name>")
    def delete_ontology(name):
        """Delete a non-active, non-finalized ontology."""
        try:
            with _lock:
                reg = _get_registry()
                try:
                    reg.delete(name)
                except KeyError:
                    return _not_found_response(name)
                except ValueError as e:
                    return jsonify({"error": str(e)}), 409

                git_commit_library_change(
                    [config.LIBRARY_ROOT],
                    f"Delete ontology {name}",
                )

            return ("", 204)
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    # All endpoints below are copied verbatim from pre-phase-2. They
    # reference _get_manager(), which now routes through the registry.

    @app.post("/propose")
    def propose():
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        try:
            body = ProposeRequest(**request.get_json(force=True))
        except ValidationError as e:
            return jsonify({"error": e.errors()}), 400

        session_id = body.session_id or f"sess_{uuid4().hex[:12]}"

        with _lock:
            mgr = _get_manager()
            proposer = _get_proposer()
            ctx = mgr.summary_for_proposer(utterance=body.utterance)

            try:
                proposal = proposer.propose(
                    utterance=body.utterance,
                    session_id=session_id,
                    working_classes=ctx["working_classes"],
                    known_individuals=ctx["known_individuals"],
                    relevant_classes=ctx.get("relevant_classes"),
                )
            except Exception as e:
                log_event(
                    session_id,
                    "propose_error",
                    {"utterance": body.utterance, "error": str(e)},
                )
                return jsonify({"error": f"Proposer error: {e}"}), 500

            # Coherence gate (lint + reasoner). Reports the verdict for human
            # review; it does not auto-resample on the interactive path.
            if config.ENABLE_COHERENCE_GATE:
                try:
                    result = gate_client.evaluate(
                        proposal, mgr,
                        run_reasoner=config.GATE_RUN_REASONER,
                        run_construction=config.ENABLE_CONSTRUCTION_LINTER,
                        strict_closed_vocab=config.STRICT_CLOSED_VOCAB,
                    )
                    proposal.gate_outcome = result.outcome.value
                    proposal.gate_tier = result.tier.value
                    proposal.gate_reason = result.reason or None
                    proposal.reasoner_verdict = (
                        "consistent" if result.accepted else "inconsistent"
                    )
                    proposal.reasoner_detail = (
                        result.reason or result.justification or "coherent"
                    )
                    log_gate_events(
                        session_id, [result.to_dict()],
                        context={"endpoint": "propose",
                                 "proposal_id": proposal.proposal_id},
                    )
                except Exception as e:
                    proposal.reasoner_verdict = "error"
                    proposal.reasoner_detail = str(e)
            else:
                try:
                    ok, detail = mgr.check_consistency_dry_run(proposal)
                    proposal.reasoner_verdict = "consistent" if ok else "inconsistent"
                    proposal.reasoner_detail = detail
                except Exception as e:
                    proposal.reasoner_verdict = "error"
                    proposal.reasoner_detail = str(e)

            _proposal_cache[proposal.proposal_id] = proposal
            log_event(
                session_id,
                "propose",
                {"proposal": proposal.model_dump()},
            )

        return jsonify(proposal.model_dump())

    @app.post("/commit")
    def commit():
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        try:
            body = CommitRequest(**request.get_json(force=True))
        except ValidationError as e:
            return jsonify({"error": e.errors()}), 400

        with _lock:
            mgr = _get_manager()

            if body.user_decision == "reject":
                log_event(
                    body.session_id,
                    "reject",
                    {
                        "proposal_id": body.proposal_id,
                        "user_notes": body.user_notes,
                        "proposal": body.proposal.model_dump(),
                    },
                )
                _proposal_cache.pop(body.proposal_id, None)
                return jsonify({"status": "rejected"})

            faithful = _active_fidelity() == "faithful"
            exclusions = (
                ledger_mod.exclusion_triples(mgr.working_path)
                if faithful else None
            )
            flag_result = None

            # Defensive gate: a user-edited proposal must not bypass coherence.
            if config.ENABLE_COHERENCE_GATE:
                try:
                    result = gate_client.evaluate(
                        body.proposal, mgr,
                        run_reasoner=config.GATE_RUN_REASONER,
                        run_construction=config.ENABLE_CONSTRUCTION_LINTER,
                        strict_closed_vocab=config.STRICT_CLOSED_VOCAB,
                        exclude_axioms=exclusions,
                    )
                except Exception as e:
                    result = None
                    log_event(body.session_id, "gate_error",
                              {"proposal_id": body.proposal_id, "error": str(e)})
                if result is not None:
                    log_gate_events(
                        body.session_id, [result.to_dict()],
                        context={"endpoint": "commit",
                                 "proposal_id": body.proposal_id},
                    )
                    if not result.accepted:
                        # FM-4: in faithful mode a lint/reasoner clash is
                        # evidence about the text, not grounds to refuse the
                        # commit. Construction violations still reject -- a
                        # malformed rendering is the proposer's error.
                        if faithful and result.tier in (
                            gate_mod.GateTier.LINT, gate_mod.GateTier.REASONER
                        ):
                            flag_result = result
                        else:
                            log_event(body.session_id, "gate_reject", {
                                "proposal_id": body.proposal_id,
                                "reason": result.reason,
                                "tier": result.tier.value,
                            })
                            return jsonify({
                                "status": "rejected_by_gate",
                                "tier": result.tier.value,
                                "reason": result.reason,
                                "justification": result.justification,
                                "unsat_classes": result.unsat_classes,
                            }), 409

            try:
                warnings = mgr.commit_proposal(
                    body.proposal,
                    verify=flag_result is None,
                    faithful=faithful,
                    exclude_axioms=exclusions,
                )
            except CommitCoherenceError as e:
                # backstop rolled the ontology back; the base is intact, so
                # report a rejection (like the gate does), not a server error
                log_event(
                    body.session_id,
                    "commit_rolled_back",
                    {"proposal_id": body.proposal_id, "reason": str(e)},
                )
                return jsonify({
                    "status": "rejected_by_commit_guard",
                    "reason": str(e),
                }), 409
            except Exception as e:
                log_event(
                    body.session_id,
                    "commit_error",
                    {"proposal_id": body.proposal_id, "error": str(e)},
                )
                return jsonify({"error": f"Commit error: {e}"}), 500

            if faithful:
                shim = None
                if flag_result is not None:
                    from types import SimpleNamespace
                    shim = SimpleNamespace(outcome=GateOutcome.FLAG,
                                           result=flag_result)
                entry_id = _ledger_faithful_commit(
                    mgr, body.proposal, shim, body.session_id,
                    {"session_id": body.session_id, "endpoint": "commit",
                     "source_text": body.proposal.utterance},
                )
                if entry_id:
                    warnings = (warnings or []) + [
                        f"incoherence-flagged [{entry_id}]: committed "
                        f"as-asserted; see incoherence ledger"
                    ]
            warnings = (warnings or []) + _budget_and_kext_notes(
                body.proposal, mgr
            )
            git_commit_working_ontology(
                body.session_id,
                body.proposal_id,
                body.proposal.utterance[:80],
            )
            log_event(
                body.session_id,
                "commit",
                {
                    "proposal_id": body.proposal_id,
                    "decision": body.user_decision,
                    "user_notes": body.user_notes,
                    "warnings": warnings,
                    "proposal": body.proposal.model_dump(),
                },
            )
            _proposal_cache.pop(body.proposal_id, None)

        return jsonify({"status": "committed", "warnings": warnings})

    @app.get("/graph")
    def graph():
        name = _ontology_query_arg()
        try:
            mgr, resolved = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(
            {
                "ontology": resolved,
                "stats": mgr.stats(),
                # recent_commits is still global across all ontologies;
                # scoping per-ontology is deferred to phase 6.
                "recent_commits": recent_commits(10),
            }
        )

    @app.get("/graph/classes")
    def graph_classes():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(mgr.list_working_classes())

    @app.get("/graph/individuals")
    def graph_individuals():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(mgr.list_individuals())

    @app.get("/graph/triples")
    def graph_triples():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        iri = request.args.get("iri", "")
        if not iri:
            return jsonify({"error": "iri parameter required"}), 400
        return jsonify(mgr.get_class_triples(iri))

    @app.get("/graph/argmap")
    def graph_argmap():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        expand = request.args.get("expand")
        if expand:
            return jsonify(mgr.expand_argmap_node(expand))
        return jsonify(mgr.build_argmap_spine())

    @app.get("/graph/bfo")
    def graph_bfo():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(mgr.list_bfo_classes())

    @app.post("/query")
    def query():
        try:
            body = QueryRequest(**request.get_json(force=True))
        except ValidationError as e:
            return jsonify({"error": e.errors()}), 400

        session_id = body.session_id or f"sess_{uuid4().hex[:12]}"

        # Phase 3: /query can target a specific ontology via body.ontology.
        # Defaults to active if not given. Unknown name returns 404.
        try:
            mgr, resolved_ontology = _resolve_ontology_for_read(body.ontology)
        except KeyError:
            return _not_found_response(body.ontology)

        with _lock:
            proposer = _get_proposer()
            # For MVP we pass the full class/individual list as text context.
            # This will need a retrieval step when the graph grows.
            ctx_parts = []
            ctx_parts.append("WORKING CLASSES:")
            for c in mgr.list_working_classes():
                ctx_parts.append(
                    f"- {c['iri']} (label: {c['label']}, parents: {c['parents']})"
                )
            ctx_parts.append("\nINDIVIDUALS:")
            for i in mgr.list_individuals():
                ctx_parts.append(
                    f"- {i['iri']} (label: {i['label']}, types: {i['types']})"
                )
            graph_ctx = "\n".join(ctx_parts)

            try:
                raw = proposer.answer_grounded(body.question, graph_ctx)
            except Exception as e:
                # Degrade gracefully: return an ungrounded response rather
                # than a 500 so eval harnesses can continue past bad LLM
                # output (e.g. malformed or truncated JSON).
                log_event(session_id, "query_error",
                          {"question": body.question, "error": str(e)})
                resp = QueryResponse(
                    question=body.question,
                    answer=f"[query error] {e}",
                    grounded=False,
                    referenced_iris=[],
                    missing_iris=[],
                )
                return jsonify(resp.model_dump())

            referenced = raw.get("referenced_iris", [])
            missing = [iri for iri in referenced if not mgr.iri_exists(iri)]
            # Grounded iff the answer cites at least one IRI AND every
            # cited IRI resolves in the graph. Empty referenced means the
            # answer is narrative-only (typically a refusal), which is
            # correctly scored as not-grounded regardless of Claude's
            # self-reported `grounded` flag (which is unreliable).
            grounded = bool(referenced) and len(missing) == 0

            resp = QueryResponse(
                question=body.question,
                answer=raw.get("answer", ""),
                grounded=grounded,
                referenced_iris=referenced,
                missing_iris=missing,
            )
            log_event(session_id, "query", resp.model_dump())

        return jsonify(resp.model_dump())

    @app.post("/extract/prepare")
    def extract_prepare():
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        """Chunk raw text server-side. No LLM call.

        Body: {text, chunk_chars?, overlap?, section?}
        Returns: {chunks: [str], n_chunks, section, total_chars}
        """
        body = request.get_json(force=True) or {}
        text = body.get("text", "")
        if not text or not text.strip():
            return jsonify({"error": "empty text"}), 400
        chunk_chars = int(body.get("chunk_chars", 8000))
        overlap = int(body.get("overlap", 400))
        section = body.get("section", "")

        chunks = chunk_text(text, chunk_chars, overlap)
        return jsonify(
            {
                "chunks": chunks,
                "n_chunks": len(chunks),
                "section": section,
                "total_chars": len(text),
            }
        )

    @app.post("/extract/chunk")
    def extract_chunk():
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        """Run extraction on a single chunk.

        Body: {chunk, section?, chunk_index?}
        Returns: {claims: [...], chunk_index}
        """
        body = request.get_json(force=True) or {}
        chunk = body.get("chunk", "")
        if not chunk:
            return jsonify({"error": "empty chunk"}), 400
        section = body.get("section", "")
        chunk_index = int(body.get("chunk_index", 0))

        try:
            extractor = _get_extractor()
            claims = extractor.extract_chunk(chunk, section=section)
            for c in claims:
                c["chunk_index"] = chunk_index
        except Exception as e:
            return jsonify({"error": f"extraction failed: {e}"}), 500

        return jsonify({"claims": claims, "chunk_index": chunk_index})

    # =================================================================
    # JOB endpoints: persistent, resumable extract + feed pipelines
    # =================================================================

    @app.get("/jobs")
    def jobs_list():
        return jsonify(jobs_store.list_jobs())

    @app.post("/jobs")
    def jobs_create():
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        body = request.get_json(force=True) or {}
        name = (body.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        meta = body.get("meta") or {}
        job = jobs_store.create_job(name, meta=meta)
        return jsonify(job)

    @app.get("/jobs/<job_id>")
    def jobs_get(job_id):
        try:
            return jsonify(jobs_store.load_job(job_id))
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404

    @app.delete("/jobs/<job_id>")
    def jobs_delete(job_id):
        ok = jobs_store.delete_job(job_id)
        return jsonify({"deleted": ok})

    @app.get("/jobs/<job_id>/export")
    def jobs_export(job_id):
        """Download a job's extracted claims as a portable JSON envelope, so
        another deployment can import and feed them without re-extracting."""
        try:
            job = jobs_store.load_job(job_id)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        envelope = job_transfer.build_export_envelope(job)
        data = json.dumps(envelope, indent=2, ensure_ascii=False).encode("utf-8")
        resp = app.response_class(data, mimetype="application/json")
        resp.headers["Content-Disposition"] = (
            f'attachment; filename="{job_transfer.export_filename(job)}"'
        )
        return resp

    @app.post("/jobs/import")
    def jobs_import():
        """Create a new, feedable job from an exported claims envelope. Claims
        arrive reset to pending (append_claims re-inits feed state), so the
        expensive feed runs here while extraction happened elsewhere."""
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        payload = request.get_json(force=True, silent=True)
        if payload is None:
            return jsonify({"error": "invalid or missing JSON body"}), 400
        try:
            name, meta, claims = job_transfer.parse_import_envelope(payload)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        job = jobs_store.create_job(name, meta=meta)
        job = jobs_store.append_claims(job["job_id"], claims)
        return jsonify(jobs_store._job_summary(job)), 201

    @app.post("/jobs/<job_id>/append_claims")
    def jobs_append_claims(job_id):
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        """Append already-extracted claims to a job. Used after the client
        has run extract/prepare + extract/chunk on a chapter; can be called
        repeatedly to add more chapters to the same job."""
        body = request.get_json(force=True) or {}
        claims = body.get("claims") or []
        if not isinstance(claims, list):
            return jsonify({"error": "claims must be a list"}), 400
        try:
            job = jobs_store.append_claims(job_id, claims)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(jobs_store._job_summary(job))

    @app.post("/jobs/<job_id>/approve")
    def jobs_approve(job_id):
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        """Bulk-set approval flags. Body: {approvals: {claim_id: bool, ...}}"""
        body = request.get_json(force=True) or {}
        approvals_raw = body.get("approvals") or {}
        try:
            approvals = {int(k): bool(v) for k, v in approvals_raw.items()}
        except (ValueError, TypeError):
            return jsonify({"error": "approvals keys must be integer claim ids"}), 400
        try:
            job = jobs_store.set_approval(job_id, approvals)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(jobs_store._job_summary(job))

    @app.post("/jobs/<job_id>/feed_one")
    def jobs_feed_one(job_id):
        """Feed the next pending+approved claim in the job (single step).

        The browser no longer loops over this endpoint for long runs --
        POST /jobs/<id>/resume starts a server-side runner instead (see
        job_runner.py). This stays for one-off/manual stepping and for
        API compatibility.

        Body: {auto_accept?: bool}
        Returns: {claim, proposal, committed: bool, remaining: int}
                 or {done: true} if no pending+approved claims remain.
        """
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()

        body = request.get_json(force=True) or {}
        auto_accept = bool(body.get("auto_accept", True))

        try:
            job = jobs_store.load_job(job_id)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404

        # Respect paused state: caller should not ask us to feed a paused job
        if job.get("status") == "paused":
            return jsonify({"error": "job is paused; call /resume first"}), 409

        return jsonify(_feed_one_core(job_id, auto_accept))

    @app.post("/jobs/<job_id>/prepare_proposals")
    def jobs_prepare_proposals(job_id):
        """Kick a batch prepare pass (SPEC-bfo-agent-speed.md change 5):
        submit every pending+approved claim without a stored proposal to the
        Anthropic Message Batches API and persist the parsed proposals into
        the job file, so the feed consumes them instead of calling the API
        inline. Runs on a daemon thread; poll GET /jobs/<id> and read
        meta.batch_propose for progress. 409 while a batch is running."""
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
        try:
            job = jobs_store.load_job(job_id)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        bp = (job.get("meta") or {}).get("batch_propose") or {}
        if bp.get("status") in ("submitted", "processing"):
            return jsonify({
                "error": "a proposal batch is already running for this job",
                "batch_propose": bp,
            }), 409

        # Anthropic-only module; imported lazily so the DGX fork (local LLM,
        # no Batches API) never loads it.
        from . import batch_propose

        def _run():
            try:
                batch_propose.prepare_job_proposals(job_id)
            except Exception:
                log.exception("prepare_proposals failed for job %s", job_id)

        threading.Thread(target=_run, daemon=True,
                         name=f"prepare-proposals-{job_id}").start()
        return jsonify({"started": True, "job_id": job_id})

    @app.post("/jobs/<job_id>/pause")
    def jobs_pause(job_id):
        """Pause feeding. The server-side runner notices between claims
        and stops; a claim already mid-proposal finishes first."""
        try:
            job = jobs_store.set_job_status(job_id, "paused")
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        out = jobs_store._job_summary(job)
        out["runner"] = job_runner.status(job_id)
        return jsonify(out)

    @app.post("/jobs/<job_id>/resume")
    def jobs_resume(job_id):
        """Mark the job feeding AND start the server-side runner, so the
        run keeps going after the browser tab closes. Idempotent while a
        runner is alive. Body: {auto_accept?: bool}"""
        if _active_is_finalized():
            return _finalized_guard_response()
        body = request.get_json(silent=True) or {}
        auto_accept = bool(body.get("auto_accept", True))
        try:
            job = jobs_store.set_job_status(job_id, "feeding")
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        try:
            # Amortized-save crash recovery (change 7): a manual resume
            # after a process death must not trust claim statuses beyond
            # the last on-disk save.
            _prepare_amortized_resume(job_id)
        except Exception:  # noqa: BLE001 — never brick resume
            log.exception("amortized-save resume preparation failed")
        runner = job_runner.start(job_id, _feed_one_core, auto_accept,
                                  on_complete=_fol_audit_on_complete,
                                  flush_fn=flush_working_ontology)
        out = jobs_store._job_summary(job)
        out["runner"] = runner
        return jsonify(out)

    @app.get("/jobs/<job_id>/progress")
    def jobs_progress(job_id):
        """Cheap progress snapshot for polling (UI or curl): job counts
        from disk plus the in-memory runner state and an ETA."""
        try:
            job = jobs_store.load_job(job_id)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        out = jobs_store._job_summary(job)
        remaining = sum(1 for c in job["claims"]
                        if c["status"] == "pending" and c.get("approved"))
        out["remaining_approved"] = remaining
        runner = job_runner.status(job_id)
        out["runner"] = runner
        if runner and runner.get("avg_seconds_per_claim") and remaining:
            out["eta_seconds"] = int(
                remaining * runner["avg_seconds_per_claim"])
        return jsonify(out)

    @app.get("/session/<session_id>")
    def session_log(session_id):
        return jsonify(load_session(session_id))

    @app.get("/jobs/<job_id>/stream")
    def jobs_stream(job_id):
        """Read-only Server-Sent Events tail of a job's live feed.

        Streams the per-claim trace by tailing the two on-disk logs the feed
        already writes -- the main session log and the coherence-gate log --
        so a browser panel can watch propose -> gate -> commit as it happens.
        Pure log tailing: never touches the feed/gate/runner hot path.
        """
        try:
            job = jobs_store.load_job(job_id)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        session_id = job["session_id"]
        main_path = session_path(session_id)
        gate_path = gate_log_path(session_id)

        backlog_lines = 150
        max_tail_bytes = 1_000_000  # cap initial backlog read at ~1MB/file
        heartbeat_after = 15.0      # seconds of silence before a ping comment
        poll_interval = 0.5

        def _sse(obj) -> str:
            return f"data: {json.dumps(obj)}\n\n"

        def _tail_lines(path: Path) -> list[str]:
            """Last ~backlog_lines complete lines, reading at most the final
            ~max_tail_bytes so a huge log never loads whole into memory."""
            try:
                size = path.stat().st_size
            except OSError:
                return []
            start = max(0, size - max_tail_bytes)
            try:
                with path.open("rb") as f:
                    f.seek(start)
                    data = f.read()
            except OSError:
                return []
            if start > 0:
                # Drop the partial first line produced by the mid-file seek.
                nl = data.find(b"\n")
                data = data[nl + 1:] if nl != -1 else b""
            text = data.decode("utf-8", errors="replace")
            lines = [ln for ln in text.split("\n") if ln.strip()]
            return lines[-backlog_lines:]

        def _emit_line(src: str, line: str) -> str | None:
            try:
                record = json.loads(line)
            except (ValueError, TypeError):
                return None
            return _sse({"src": src, "event": record})

        def gen():
            try:
                yield f"event: hello\ndata: {json.dumps({'session_id': session_id, 'job_id': job_id})}\n\n"

                # Backlog: merge the tails of both files (best-effort file
                # order), so a freshly-opened panel shows recent context.
                for src, path in (("main", main_path), ("gate", gate_path)):
                    if path.exists():
                        for line in _tail_lines(path):
                            chunk = _emit_line(src, line)
                            if chunk:
                                yield chunk

                # Tail loop: track byte offsets and stream new complete lines.
                offsets: dict[str, int] = {}
                buffers: dict[str, bytes] = {"main": b"", "gate": b""}
                for src, path in (("main", main_path), ("gate", gate_path)):
                    try:
                        offsets[src] = path.stat().st_size
                    except OSError:
                        offsets[src] = 0

                last_data = time.monotonic()
                while True:
                    sent = False
                    for src, path in (("main", main_path), ("gate", gate_path)):
                        try:
                            size = path.stat().st_size
                        except OSError:
                            continue  # file not created yet -- keep polling
                        if size < offsets[src]:
                            # Truncated/rotated: restart from the top.
                            offsets[src] = 0
                            buffers[src] = b""
                        if size <= offsets[src]:
                            continue
                        try:
                            with path.open("rb") as f:
                                f.seek(offsets[src])
                                chunk = f.read()
                        except OSError:
                            continue
                        offsets[src] = size
                        buffers[src] += chunk
                        *complete, buffers[src] = buffers[src].split(b"\n")
                        for raw in complete:
                            line = raw.decode("utf-8", errors="replace")
                            if not line.strip():
                                continue
                            out = _emit_line(src, line)
                            if out:
                                yield out
                                sent = True

                    now = time.monotonic()
                    if sent:
                        last_data = now
                    elif now - last_data >= heartbeat_after:
                        yield ": ping\n\n"
                        last_data = now
                    time.sleep(poll_interval)
            except GeneratorExit:
                # Client disconnected -- stop cleanly, no spin, no raise.
                return
            except Exception as e:  # noqa: BLE001 -- a stream must not 500 mid-flight
                log.warning("jobs_stream generator error: %s", e)
                return

        return Response(
            gen(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # A restart (deploy, crash, OOM) interrupts any server-side feed run.
    # Per-claim state is on disk, so pick those jobs up where they left off.
    if config.AUTORESUME_JOBS:
        try:
            resumed = job_runner.resume_incomplete(
                _feed_one_core, on_complete=_fol_audit_on_complete,
                resume_verify_fn=_resume_verify,
                flush_fn=flush_working_ontology)
            if resumed:
                print(f"[job_runner] auto-resumed feeding jobs: "
                      f"{', '.join(resumed)}")
        except Exception as e:
            print(f"[job_runner] auto-resume failed: {e}")

    return app