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
from .registry import OntologyRegistry
from .schema import (
    CommitRequest,
    Proposal,
    ProposeRequest,
    QueryRequest,
    QueryResponse,
)
from .storage import (
    git_commit_library_change,
    git_commit_working_ontology,
    load_session,
    log_event,
    recent_commits,
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
            return jsonify({
                "status": "ok",
                "ontology": resolved,
                "stats": mgr.stats(),
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
        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
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