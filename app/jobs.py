"""Job persistence for extraction + feeding pipelines.

A "job" bundles a named project (e.g. a book) with its extracted claims,
their approval flags, and per-claim feed status. Jobs live as JSON files
in `jobs/<job_id>.json` so they survive browser reloads, server restarts,
and mid-run stops. The same job can be paused, resumed, and appended to.

Shape on disk:
{
  "job_id": "job_abc123",
  "name": "SOoL Ch.4",
  "created_at": "...",
  "updated_at": "...",
  "session_id": "feed_SOoL_ch4_abc123",
  "status": "draft" | "extracting" | "ready" | "feeding" | "paused" | "completed",
  "meta": { "source_file": "...", "model": "...", "chunk_chars": 8000, ... },
  "claims": [
    {
      "id": 0,
      "claim": "...",
      "source_quote": "...",
      "confidence": "high" | "medium" | "low",
      "note": "...",
      "section": "...",
      "chunk_index": 2,
      "approved": true,
      "status": "pending" | "committed" | "inconsistent" | "needs_review" | "error" | "rejected",
      "proposal_id": "prop_..." | null,
      "verdict": "consistent" | ... | null,
      "updated_at": "...",
    },
    ...
  ]
}

Status transitions (claim-level):
  pending -> committed | inconsistent | needs_review | error | rejected

The job-level `status` tracks the overall pipeline stage; the per-claim
`status` tracks each individual outcome. Either can be resumed: the
feeder picks up any claim whose status is `pending` and whose approved
flag is true.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import ROOT


JOBS_DIR = ROOT / "jobs"
JOBS_DIR.mkdir(parents=True, exist_ok=True)

# Per-process lock for file I/O; keeps atomic writes safe on the single
# orchestrator process. Not intended to coordinate multiple servers.
_file_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_job_id() -> str:
    return f"job_{uuid.uuid4().hex[:12]}"


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via a tempfile + rename so a crash mid-write
    cannot corrupt the job record."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


# Persisted checkpoint bookkeeping (SPEC-bfo-agent-speed.md change 6). Jobs
# written before this field existed read as these defaults (get_feed_state).
_FEED_STATE_DEFAULTS = {
    "commits_since_checkpoint": 0,
    "last_checkpoint_at": None,
    "last_checkpoint_ok": None,
    "last_verified_claim_id": None,
    "last_saved_claim_id": None,
}


def get_feed_state(job: dict) -> dict:
    """The job's feed_state, with defaults filled in for pre-existing job
    files that lack the field (or lack newer keys)."""
    fs = dict(_FEED_STATE_DEFAULTS)
    fs.update(job.get("feed_state") or {})
    return fs


def bump_feed_state(job_id: str, increment: Optional[dict] = None,
                    **updates) -> dict:
    """Read-modify-write the job's persisted feed_state.

    ``increment`` adds to numeric counters (e.g.
    ``increment={"commits_since_checkpoint": 1}``); keyword arguments set
    absolute values. Returns the updated feed_state. Follows the module's
    load/modify/save pattern; the atomic write in save_job keeps a crash
    mid-update from corrupting the record.
    """
    job = load_job(job_id)
    fs = get_feed_state(job)
    for key, delta in (increment or {}).items():
        fs[key] = (fs.get(key) or 0) + delta
    fs.update(updates)
    job["feed_state"] = fs
    save_job(job)
    return fs


def mark_window_needs_review(job_id: str, from_id: Optional[int],
                             to_id: int) -> list[int]:
    """Flip committed claims in the suspect window ``(from_id, to_id]`` to
    needs_review after a failed full-graph checkpoint
    (CHECKPOINT_FAIL_MARK_REVIEW). ``from_id`` None means from the start.
    Returns the flipped claim ids."""
    job = load_job(job_id)
    flipped = []
    for c in job["claims"]:
        if c.get("status") != "committed":
            continue
        if from_id is not None and c["id"] <= from_id:
            continue
        if c["id"] > to_id:
            continue
        c["status"] = "needs_review"
        c["updated_at"] = _now()
        flipped.append(c["id"])
    if flipped:
        save_job(job)
    return flipped


# -------------------------------------------------------------- CRUD
def create_job(name: str, meta: Optional[dict] = None) -> dict:
    """Create a new empty job."""
    with _file_lock:
        job_id = _new_job_id()
        job = {
            "job_id": job_id,
            "name": name,
            "created_at": _now(),
            "updated_at": _now(),
            "session_id": f"feed_{_safe_slug(name)}_{job_id[4:10]}",
            "status": "draft",
            "meta": meta or {},
            "claims": [],
            "feed_state": dict(_FEED_STATE_DEFAULTS),
        }
        _atomic_write(_job_path(job_id), job)
    return job


def load_job(job_id: str) -> dict:
    path = _job_path(job_id)
    if not path.exists():
        raise FileNotFoundError(f"No job at {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_job(job: dict) -> dict:
    with _file_lock:
        job["updated_at"] = _now()
        _atomic_write(_job_path(job["job_id"]), job)
    return job


def delete_job(job_id: str) -> bool:
    path = _job_path(job_id)
    if path.exists():
        path.unlink()
        return True
    return False


def list_jobs() -> list[dict]:
    """Return a summary list of all jobs, most recently updated first."""
    out = []
    for p in JOBS_DIR.glob("job_*.json"):
        try:
            with p.open("r", encoding="utf-8") as f:
                job = json.load(f)
        except Exception:
            continue
        out.append(_job_summary(job))
    out.sort(key=lambda j: j.get("updated_at", ""), reverse=True)
    return out


def _job_summary(job: dict) -> dict:
    claims = job.get("claims", [])
    counts = {
        "total": len(claims),
        "approved": sum(1 for c in claims if c.get("approved")),
        "pending": sum(1 for c in claims if c.get("status") == "pending"),
        "committed": sum(1 for c in claims if c.get("status") == "committed"),
        "inconsistent": sum(1 for c in claims if c.get("status") == "inconsistent"),
        "needs_review": sum(1 for c in claims if c.get("status") == "needs_review"),
        "error": sum(1 for c in claims if c.get("status") == "error"),
        "rejected": sum(1 for c in claims if c.get("status") == "rejected"),
    }
    return {
        "job_id": job["job_id"],
        "name": job["name"],
        "status": job.get("status", "draft"),
        "session_id": job.get("session_id"),
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "counts": counts,
    }


# ----------------------------------------------------------- mutations
def append_claims(job_id: str, new_claims: Iterable[dict]) -> dict:
    """Append extracted claims to a job, assigning stable ids and default
    state. Safe to call repeatedly to grow a job from multiple extraction
    rounds (e.g. one chapter at a time)."""
    job = load_job(job_id)
    existing = job.get("claims", [])
    next_id = (max((c["id"] for c in existing), default=-1)) + 1

    for c in new_claims:
        conf = (c.get("confidence") or "low").lower()
        # Default approve = True for high, False otherwise. Caller can
        # still override via set_approval before feeding.
        default_approved = conf == "high"
        existing.append(
            {
                "id": next_id,
                "claim": (c.get("claim") or "").strip(),
                "source_quote": (c.get("source_quote") or "").strip(),
                "confidence": conf,
                "note": (c.get("note") or "").strip(),
                "section": c.get("section", ""),
                "chunk_index": c.get("chunk_index", 0),
                "approved": bool(c.get("approved", default_approved)),
                "status": "pending",
                "proposal_id": None,
                "verdict": None,
                "updated_at": _now(),
            }
        )
        next_id += 1

    job["claims"] = existing
    if job.get("status") in (None, "draft", "extracting"):
        job["status"] = "ready" if any(c.get("approved") for c in existing) else "draft"
    return save_job(job)


def set_approval(job_id: str, approvals: dict) -> dict:
    """Bulk update approval flags. `approvals` is {claim_id: bool}.
    Only applies to claims that are still pending."""
    job = load_job(job_id)
    for c in job["claims"]:
        if c["status"] != "pending":
            continue
        if c["id"] in approvals:
            c["approved"] = bool(approvals[c["id"]])
            c["updated_at"] = _now()
    return save_job(job)


def update_claim_status(
    job_id: str,
    claim_id: int,
    status: str,
    proposal_id: Optional[str] = None,
    verdict: Optional[str] = None,
) -> dict:
    """Called by the feeder after each claim is attempted."""
    job = load_job(job_id)
    for c in job["claims"]:
        if c["id"] == claim_id:
            c["status"] = status
            if proposal_id is not None:
                c["proposal_id"] = proposal_id
            if verdict is not None:
                c["verdict"] = verdict
            c["updated_at"] = _now()
            break
    return save_job(job)


def set_job_status(job_id: str, status: str) -> dict:
    job = load_job(job_id)
    job["status"] = status
    return save_job(job)


def next_pending(job_id: str, limit: int = 1) -> list[dict]:
    """Return the next N pending+approved claims, in id order. Used by
    the resume/feeder loop."""
    job = load_job(job_id)
    out = []
    for c in job["claims"]:
        if c["status"] == "pending" and c.get("approved"):
            out.append(c)
            if len(out) >= limit:
                break
    return out


def _safe_slug(s: str) -> str:
    import re
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")[:40] or "job"
