"""Kernel-extension requests (bfo-agent-spec.md §2/§8).

The agent never mints a genuinely-new primitive. When it believes one is
required, it emits a ``kernel-extension-request`` for human review and proceeds
WITHOUT the term (anchor, don't regenerate). This module gives that request a
structured, persistable form (``*.kext.json``) so the paper trail is machine-
readable, deduplicated, and reproducible.

BFO-general: any ontology can raise a kext. The request is advisory -- it never
blocks a commit; it records what the closed vocabulary could not express.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .stable_iri import content_hash

KEXT_PREFIX = "KEXT:"


@dataclass
class KernelExtensionRequest:
    term: str                 # the primitive the agent wanted but did not mint
    justification: str        # why the closed vocabulary could not express it
    source: str               # the utterance/case that triggered it
    kernel_version: str       # versionIRI of the kernel in force
    id: str                   # stable hash (term + kernel_version) for dedup

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def filename(self) -> str:
        return f"{self.id}.kext.json"


def parse(open_question: str, source: str, kernel_version: str
          ) -> KernelExtensionRequest | None:
    """Parse a ``KEXT: <term> -- <justification>`` open-question into a request.

    Returns None if the line is not a kext marker. The term is everything up to
    the first ``--``/``:``; the rest is the justification.
    """
    raw = (open_question or "").strip()
    if not raw or not raw.upper().startswith(KEXT_PREFIX):
        return None
    body = raw[len(KEXT_PREFIX):].strip()
    term, justification = body, ""
    for sep in (" -- ", " — ", ": ", " - "):
        if sep in body:
            term, justification = body.split(sep, 1)
            break
    term = term.strip()
    return KernelExtensionRequest(
        term=term,
        justification=justification.strip(),
        source=(source or "").strip(),
        kernel_version=kernel_version,
        id=content_hash(term, kernel_version, length=12),
    )


def collect(proposal, kernel_version: str) -> list[KernelExtensionRequest]:
    """Every kext request carried by a proposal's open_questions."""
    out: list[KernelExtensionRequest] = []
    source = getattr(proposal, "utterance", "") or ""
    seen: set[str] = set()
    for q in getattr(proposal, "open_questions", []) or []:
        req = parse(str(q), source, kernel_version)
        if req is not None and req.id not in seen:
            seen.add(req.id)
            out.append(req)
    return out


def persist(req: KernelExtensionRequest, out_dir: str | Path) -> Path:
    """Write a request to ``<out_dir>/<id>.kext.json`` (idempotent: same request
    overwrites byte-identical content). Returns the path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / req.filename
    path.write_text(
        json.dumps(req.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return path
