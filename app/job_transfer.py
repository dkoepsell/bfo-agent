"""Portable export/import of a job's extracted claims between deployments.

Extraction (pulling atomic claims from text) is cheap; feeding (propose -> gate
-> commit) is expensive. This module lets one deployment export the *extraction*
output of a job as a portable JSON envelope, so another deployment can import it
and run only the feed -- without re-extracting.

Everything here is pure (no Flask, no I/O) so it is unit-testable and identical
across the Hetzner and DGX deployments (its only dependency, ``app/jobs.py``, is
byte-for-byte identical on both). The import path deliberately routes through
``jobs.create_job`` + ``jobs.append_claims``: ``append_claims`` already assigns
fresh ids and re-initializes ``status="pending"``, ``proposal_id=None``,
``verdict=None`` while preserving ``approved`` -- exactly the reset a feedable
import needs.
"""
from __future__ import annotations

from typing import Any

from . import jobs


FORMAT_MARKER = "bfo_job_export"
FORMAT_VERSION = 1

# The extraction-relevant claim fields carried in an export. Feed-state fields
# (id, status, proposal_id, verdict, updated_at) are intentionally dropped --
# append_claims re-initializes them on import.
EXPORT_CLAIM_FIELDS = (
    "claim",
    "source_quote",
    "confidence",
    "note",
    "section",
    "chunk_index",
    "approved",
)

# Guard against absurdly large uploads. A real book runs to a few thousand
# claims; 20k is comfortably above any legitimate job.
MAX_IMPORT_CLAIMS = 20000

# Static per-deployment label stamped into the envelope's ``source.box`` and,
# on import, into ``meta.imported_from``. Optional; the parser tolerates its
# absence in older/foreign envelopes.
BOX_LABEL = "hetzner"


def build_export_envelope(job: dict, box: str = BOX_LABEL) -> dict:
    """Project a jobs.py job dict into a portable, versioned envelope.

    Exports ALL claims regardless of feed status; the target resets them to
    pending on import and re-feeds everything.
    """
    meta = job.get("meta") or {}
    claims_out = []
    for c in job.get("claims", []):
        out = {k: c.get(k) for k in EXPORT_CLAIM_FIELDS}
        # A precomputed proposal (batch propose, SPEC-bfo-agent-speed.md
        # change 5) is plain JSON and survives the trip; included only when
        # present so envelopes without one stay byte-identical to before.
        # append_claims preserves it on import and the target's gate
        # re-validates it at feed time.
        if c.get("proposal") is not None:
            out["proposal"] = c["proposal"]
            out["proposal_source"] = c.get("proposal_source") or "batch"
        claims_out.append(out)
    return {
        FORMAT_MARKER: True,
        "version": FORMAT_VERSION,
        "exported_at": jobs._now(),
        "source": {
            "job_id": job.get("job_id"),
            "session_id": job.get("session_id"),
            "model": meta.get("model"),
            "box": box,
        },
        "job": {
            "name": job.get("name") or "imported job",
            "meta": meta,
        },
        "claims": claims_out,
    }


def export_filename(job: dict) -> str:
    """A safe, human-readable download filename for a job's export."""
    return f"{jobs._safe_slug(job.get('name', ''))}_claims.json"


def parse_import_envelope(payload: Any) -> tuple[str, dict, list[dict]]:
    """Validate an import envelope and return ``(name, meta, claims)``.

    Raises ``ValueError`` (which the HTTP layer maps to 400) for any malformed,
    unversioned, empty, or oversized payload. The returned ``meta`` is the
    source job's meta augmented with import provenance.
    """
    if not isinstance(payload, dict):
        raise ValueError("import payload must be a JSON object")
    if payload.get(FORMAT_MARKER) is not True:
        raise ValueError(
            f"not a bfo-agent job export (missing '{FORMAT_MARKER}' marker)"
        )

    version = payload.get("version")
    if not isinstance(version, int):
        raise ValueError("export 'version' is missing or not an integer")
    if version > FORMAT_VERSION:
        raise ValueError(
            f"export version {version} is newer than supported ({FORMAT_VERSION}); "
            "upgrade this deployment"
        )

    job_block = payload.get("job")
    if not isinstance(job_block, dict):
        raise ValueError("export is missing its 'job' object")
    name = (job_block.get("name") or "").strip()
    if not name:
        raise ValueError("export has an empty job name")

    claims = payload.get("claims")
    if not isinstance(claims, list):
        raise ValueError("export 'claims' must be a list")
    if not claims:
        raise ValueError("export contains no claims")
    if len(claims) > MAX_IMPORT_CLAIMS:
        raise ValueError(
            f"export has {len(claims)} claims, over the {MAX_IMPORT_CLAIMS} limit"
        )

    src = payload.get("source") or {}
    base_meta = job_block.get("meta")
    meta = dict(base_meta) if isinstance(base_meta, dict) else {}
    # meta.model is free-form and carries the SOURCE box's model id (Claude vs
    # an ollama tag). It is informational only -- feeding always uses the target
    # box's own engine -- so pass it through untouched.
    meta.update(
        {
            "imported_from": src.get("box") or "unknown",
            "source_session_id": src.get("session_id"),
            "original_job_id": src.get("job_id"),
            "imported_at": jobs._now(),
            "import_format_version": version,
        }
    )
    return name, meta, claims
