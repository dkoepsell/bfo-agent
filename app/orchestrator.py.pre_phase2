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

import threading
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request
from flask_cors import CORS
from pydantic import ValidationError

from . import config
from . import jobs as jobs_store
from .extractor import ClaimExtractor, chunk_text
from .llm_proposer import LLMProposer
from .ontology_manager import OntologyManager
from .schema import (
    CommitRequest,
    Proposal,
    ProposeRequest,
    QueryRequest,
    QueryResponse,
)
from .storage import (
    git_commit_working_ontology,
    load_session,
    log_event,
    recent_commits,
)


# Global single-instance state. The orchestrator is not designed for
# concurrent writers; a single lock serializes proposal/commit operations.
_lock = threading.Lock()
_manager: OntologyManager | None = None
_proposer: LLMProposer | None = None
_extractor: ClaimExtractor | None = None
# Small in-memory store of the latest proposal per id so /commit can round-trip
_proposal_cache: dict[str, Proposal] = {}


def _get_manager() -> OntologyManager:
    global _manager
    if _manager is None:
        _manager = OntologyManager(
            bfo_path=config.BFO_PATH,
            working_path=config.WORKING_PATH,
            seed_path=config.SEED_PATH,
        )
    return _manager


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


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)

    @app.get("/health")
    def health():
        try:
            mgr = _get_manager()
            return jsonify({"status": "ok", "stats": mgr.stats()})
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/propose")
    def propose():
        try:
            body = ProposeRequest(**request.get_json(force=True))
        except ValidationError as e:
            return jsonify({"error": e.errors()}), 400

        session_id = body.session_id or f"sess_{uuid4().hex[:12]}"

        with _lock:
            mgr = _get_manager()
            proposer = _get_proposer()
            ctx = mgr.summary_for_proposer()

            try:
                proposal = proposer.propose(
                    utterance=body.utterance,
                    session_id=session_id,
                    working_classes=ctx["working_classes"],
                    known_individuals=ctx["known_individuals"],
                )
            except Exception as e:
                log_event(
                    session_id,
                    "propose_error",
                    {"utterance": body.utterance, "error": str(e)},
                )
                return jsonify({"error": f"Proposer error: {e}"}), 500

            # Reasoner dry-run
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

            try:
                warnings = mgr.commit_proposal(body.proposal)
            except Exception as e:
                log_event(
                    body.session_id,
                    "commit_error",
                    {"proposal_id": body.proposal_id, "error": str(e)},
                )
                return jsonify({"error": f"Commit error: {e}"}), 500

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
        mgr = _get_manager()
        return jsonify(
            {
                "stats": mgr.stats(),
                "recent_commits": recent_commits(10),
            }
        )

    @app.get("/graph/classes")
    def graph_classes():
        return jsonify(_get_manager().list_working_classes())

    @app.get("/graph/individuals")
    def graph_individuals():
        return jsonify(_get_manager().list_individuals())

    @app.get("/graph/bfo")
    def graph_bfo():
        return jsonify(_get_manager().list_bfo_classes())

    @app.post("/query")
    def query():
        try:
            body = QueryRequest(**request.get_json(force=True))
        except ValidationError as e:
            return jsonify({"error": e.errors()}), 400

        session_id = body.session_id or f"sess_{uuid4().hex[:12]}"

        with _lock:
            mgr = _get_manager()
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

    @app.post("/jobs/<job_id>/append_claims")
    def jobs_append_claims(job_id):
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
        """Feed the next pending+approved claim in the job.

        Runs /propose internally (not the HTTP route) so the full proposal
        and reasoner verdict are produced. If auto_accept is true and the
        verdict is 'consistent', it also commits. Either way, the claim's
        status is persisted to the job file before returning.

        Body: {auto_accept?: bool}
        Returns: {claim, proposal, committed: bool, remaining: int}
                 or {done: true} if no pending+approved claims remain.
        """
        body = request.get_json(force=True) or {}
        auto_accept = bool(body.get("auto_accept", True))

        try:
            job = jobs_store.load_job(job_id)
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404

        # Respect paused state: caller should not ask us to feed a paused job
        if job.get("status") == "paused":
            return jsonify({"error": "job is paused; call /resume first"}), 409

        pending = jobs_store.next_pending(job_id, limit=1)
        if not pending:
            jobs_store.set_job_status(job_id, "completed")
            return jsonify({"done": True, "remaining": 0})

        claim = pending[0]
        session_id = job["session_id"]

        with _lock:
            mgr = _get_manager()
            proposer = _get_proposer()
            ctx = mgr.summary_for_proposer()

            try:
                proposal = proposer.propose(
                    utterance=claim["claim"],
                    session_id=session_id,
                    working_classes=ctx["working_classes"],
                    known_individuals=ctx["known_individuals"],
                )
            except Exception as e:
                jobs_store.update_claim_status(
                    job_id, claim["id"], "error", verdict="error"
                )
                log_event(session_id, "propose_error",
                          {"utterance": claim["claim"], "error": str(e),
                           "job_id": job_id, "claim_id": claim["id"]})
                remaining = _count_pending(job_id)
                return jsonify({
                    "claim": claim,
                    "proposal": None,
                    "error": str(e),
                    "committed": False,
                    "remaining": remaining,
                })

            # Dry-run reasoner check
            try:
                ok, detail = mgr.check_consistency_dry_run(proposal)
                proposal.reasoner_verdict = "consistent" if ok else "inconsistent"
                proposal.reasoner_detail = detail
            except Exception as e:
                proposal.reasoner_verdict = "error"
                proposal.reasoner_detail = str(e)

            _proposal_cache[proposal.proposal_id] = proposal
            log_event(session_id, "propose", {
                "proposal": proposal.model_dump(),
                "job_id": job_id,
                "claim_id": claim["id"],
            })

            verdict = proposal.reasoner_verdict
            committed = False
            warnings: list[str] = []

            if verdict == "inconsistent":
                new_status = "inconsistent"
            elif auto_accept and verdict == "consistent":
                try:
                    warnings = mgr.commit_proposal(proposal)
                except Exception as e:
                    log_event(session_id, "commit_error", {
                        "proposal_id": proposal.proposal_id,
                        "error": str(e),
                        "job_id": job_id,
                        "claim_id": claim["id"],
                    })
                    new_status = "error"
                else:
                    git_commit_working_ontology(
                        session_id, proposal.proposal_id,
                        claim["claim"][:80]
                    )
                    log_event(session_id, "commit", {
                        "proposal_id": proposal.proposal_id,
                        "decision": "accept",
                        "warnings": warnings,
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
            )

        remaining = _count_pending(job_id)
        return jsonify({
            "claim": {**claim, "status": new_status,
                      "proposal_id": proposal.proposal_id,
                      "verdict": verdict},
            "proposal": proposal.model_dump(),
            "committed": committed,
            "warnings": warnings,
            "remaining": remaining,
        })

    @app.post("/jobs/<job_id>/pause")
    def jobs_pause(job_id):
        try:
            job = jobs_store.set_job_status(job_id, "paused")
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(jobs_store._job_summary(job))

    @app.post("/jobs/<job_id>/resume")
    def jobs_resume(job_id):
        try:
            job = jobs_store.set_job_status(job_id, "feeding")
        except FileNotFoundError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify(jobs_store._job_summary(job))

    def _count_pending(job_id: str) -> int:
        job = jobs_store.load_job(job_id)
        return sum(1 for c in job["claims"]
                   if c["status"] == "pending" and c.get("approved"))

    @app.get("/session/<session_id>")
    def session_log(session_id):
        return jsonify(load_session(session_id))

    return app
