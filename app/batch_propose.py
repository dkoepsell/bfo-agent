"""Batch propose pass (SPEC-bfo-agent-speed.md change 5).

Decouples propose from commit: submits every pending+approved claim of a job
to the Anthropic Message Batches API (~50% cheaper than live calls) against
ONE context snapshot, then persists the parsed proposals into the job file
(``claim["proposal"]``). The feeder (orchestrator._feed_one_core) consumes a
stored proposal when present -- consume-once -- instead of calling the API
inline. Every precomputed proposal still passes the full gate at feed time,
and gate resamples always re-propose live. Batch failures simply leave the
claim without a stored proposal, so it falls back to live propose.

Because all batch entries are built from one snapshot, later claims cannot
see classes minted by claims committed just before them; commit-time IRI
reservation (config.IRI_RESERVATION_ENABLED, OntologyManager._reserve_iris)
closes that gap at apply time.

This module is Anthropic/Hetzner-only and is NEVER merged to the DGX fork
(local LLM, no Batches API). The ``anthropic`` SDK is imported lazily inside
functions so the module itself imports cleanly without it.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from . import config
from . import jobs
from . import llm_proposer
from .cached_client import Usage
from .ontology_manager import _rank_by_overlap

log = logging.getLogger(__name__)

PROPOSAL_SOURCE = "batch"


def _make_client(api_key: str | None = None):
    """Build an Anthropic client (lazy SDK import; owner key by default)."""
    import anthropic  # imported lazily so this module needs no SDK to import

    if api_key is None:
        config.require_api_key()
        api_key = config.ANTHROPIC_API_KEY
    return anthropic.Anthropic(api_key=api_key)


def _active_manager():
    """The registry's active manager -- same resolution as _feed_one_core."""
    from .orchestrator import _get_registry

    return _get_registry().active_manager()


def snapshot_context(mgr, max_items: int = 40) -> dict:
    """One context snapshot for the whole batch.

    Mirrors OntologyManager.summary_for_proposer (head-of-list
    working_classes/known_individuals) but keeps the FULL class list so
    per-claim REUSE CANDIDATES can be ranked per entry (``_relevant_for``)
    exactly like the live path does per call.
    """
    all_cls = mgr.list_working_classes()
    return {
        "working_classes": all_cls[:max_items],
        "known_individuals": mgr.list_individuals()[:max_items],
        "all_classes": all_cls,
    }


def _relevant_for(claim: dict, ctx: dict) -> list[dict]:
    """Per-claim REUSE CANDIDATES from the snapshot: the same ranking
    summary_for_proposer applies (rank the non-head classes by lexical
    overlap with the utterance)."""
    all_cls = ctx.get("all_classes")
    if all_cls is None:
        return ctx.get("relevant_classes") or []
    head = {c["iri"] for c in ctx["working_classes"]}
    return _rank_by_overlap(
        [c for c in all_cls if c["iri"] not in head],
        claim.get("claim") or "",
    )


def build_request(claim: dict, ctx: dict, model: str, ttl: str,
                  job_id: str) -> dict:
    """One Message Batches ``Request`` entry for a claim.

    The system/messages shape comes from llm_proposer.build_prompt_blocks --
    the SAME helper the live propose path uses -- so batch entries are
    byte-identical to live calls and share the prompt-cache prefix
    (best-effort within the batch).
    """
    system, user_message = llm_proposer.build_prompt_blocks(
        utterance=claim["claim"],
        working_classes=ctx["working_classes"],
        known_individuals=ctx["known_individuals"],
        relevant_classes=_relevant_for(claim, ctx),
        ttl=ttl,
    )
    return {
        "custom_id": f"{job_id}:{claim['id']}",
        "params": {
            "model": model,
            "max_tokens": 4000,
            "system": system,
            "messages": [{"role": "user", "content": user_message}],
        },
    }


def _claims_to_prepare(job: dict) -> list[dict]:
    """Claims the feeder would consume (mirrors jobs.next_pending: status
    pending + approved) that do not already carry a stored proposal."""
    return [
        c for c in job.get("claims", [])
        if c.get("status") == "pending" and c.get("approved")
        and not c.get("proposal")
    ]


def _claim_id_of(custom_id: str) -> Optional[int]:
    try:
        return int(str(custom_id).rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def prepare_job_proposals(
    job_id: str,
    poll_secs: float | None = None,
    progress_cb: Optional[Callable] = None,
    client=None,
) -> dict:
    """Submit a job's pending claims as one message batch, poll it to the
    end, and persist the parsed proposals into the job file.

    Progress is recorded under ``job["meta"]["batch_propose"]``. Errored or
    refused entries are counted and left without a stored proposal (the feed
    falls back to live propose for them). Returns
    ``{"submitted", "succeeded", "errored", "batch_id", "usage"}``.
    """
    poll = config.BATCH_PROPOSE_POLL_SECS if poll_secs is None else poll_secs
    job = jobs.load_job(job_id)
    session_id = job["session_id"]
    claims = _claims_to_prepare(job)
    if not claims:
        return {"submitted": 0, "succeeded": 0, "errored": 0,
                "batch_id": None, "usage": {}}

    mgr = _active_manager()
    ctx = snapshot_context(mgr)
    model = config.ANTHROPIC_MODEL
    ttl = config.CACHE_TTL
    requests = [build_request(c, ctx, model, ttl, job_id) for c in claims]

    if client is None:
        client = _make_client()

    started_at = jobs._now()
    batch = client.messages.batches.create(requests=requests)
    log.info("batch propose: job %s submitted %d claims as %s",
             job_id, len(requests), batch.id)
    jobs.set_job_meta(job_id, "batch_propose", {
        "batch_id": batch.id,
        "status": "submitted",
        "submitted": len(requests),
        "succeeded": 0,
        "errored": 0,
        "started_at": started_at,
        "ended_at": None,
    })

    try:
        while True:
            b = client.messages.batches.retrieve(batch.id)
            if progress_cb is not None:
                progress_cb(getattr(b, "request_counts", None))
            if getattr(b, "processing_status", None) == "ended":
                break
            time.sleep(poll)

        # Results arrive in any order: key by custom_id, never position.
        by_id = {c["id"]: c for c in claims}
        proposals: dict[int, dict] = {}
        succeeded = errored = 0
        usage = Usage()
        for entry in client.messages.batches.results(batch.id):
            claim_id = _claim_id_of(entry.custom_id)
            claim = by_id.get(claim_id) if claim_id is not None else None
            result = entry.result
            if claim is None or getattr(result, "type", None) != "succeeded":
                errored += 1
                continue
            msg = result.message
            text = "".join(
                blk.text for blk in msg.content if getattr(blk, "text", None)
            )
            try:
                proposal = llm_proposer.parse_proposal_response(
                    text, session_id, claim["claim"]
                )
            except Exception as e:
                log.warning("batch propose: claim %s unparseable: %s",
                            claim_id, e)
                errored += 1
                continue
            if getattr(msg, "usage", None) is not None:
                usage.add(msg.usage)
            proposals[claim_id] = {
                "proposal": proposal.model_dump(),
                "proposal_source": PROPOSAL_SOURCE,
            }
            succeeded += 1

        # ONE atomic write attaches everything that succeeded.
        if proposals:
            jobs.set_claim_proposals(job_id, proposals)
        jobs.set_job_meta(job_id, "batch_propose", {
            "batch_id": batch.id,
            "status": "ended",
            "submitted": len(requests),
            "succeeded": succeeded,
            "errored": errored,
            "started_at": started_at,
            "ended_at": jobs._now(),
        })
    except Exception:
        # Leave a terminal marker so the route's already-running (409) guard
        # cannot wedge on a crashed run; the claims stay unset, so the feed
        # simply proposes live for all of them.
        try:
            jobs.set_job_meta(job_id, "batch_propose", {
                "batch_id": batch.id,
                "status": "error",
                "submitted": len(requests),
                "succeeded": 0,
                "errored": 0,
                "started_at": started_at,
                "ended_at": jobs._now(),
            })
        except Exception:
            pass
        raise

    log.info("batch propose: job %s batch %s ended: %d succeeded, %d errored",
             job_id, batch.id, succeeded, errored)
    return {
        "submitted": len(requests),
        "succeeded": succeeded,
        "errored": errored,
        "batch_id": batch.id,
        "usage": {
            "calls": usage.calls,
            "input": usage.input,
            "cache_read": usage.cache_read,
            "cache_write": usage.cache_write,
            "output": usage.output,
        },
    }
