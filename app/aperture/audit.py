"""The disclosure log.

One JSON line per render, hash-chained so a deleted line is detectable. This is
the record that answers "what did we disclose to this client and when", so it is
a business record rather than a debug log: append only, never rewritten, and
verifiable after the fact.

The chain is simple on purpose. Each line carries the digest of the previous
line, so removing or editing any line breaks every line after it and
:func:`verify` reports the first break.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

GENESIS = "sha256:" + "0" * 64

_LOCK = threading.Lock()


def _root() -> Path:
    from .. import config

    return Path(os.environ.get(
        "APERTURE_OUT", str(Path(config.ROOT) / "out" / "aperture"))) / "audit"


def _month_file(when: datetime) -> Path:
    return _root() / f"{when.strftime('%Y-%m')}.jsonl"


def _digest(line: str) -> str:
    return "sha256:" + hashlib.sha256(line.encode("utf-8")).hexdigest()


def _last_digest(path: Path) -> str:
    if not path.exists():
        return GENESIS
    last = ""
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                last = line.rstrip("\n")
    return _digest(last) if last else GENESIS


def record(*, profile: str,
           engagement: str,
           artifact_sha256: str,
           chain_sha256: str,
           manifest_version: str,
           output: str,
           actor: str = "",
           extra: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Append one disclosure record. Returns the record as written.

    Never raises on a write failure. A disclosure that happened must not be
    undone by a logging problem, and the caller gets the record either way; the
    failure is logged loudly instead.
    """
    when = datetime.now(timezone.utc)
    path = _month_file(when)

    with _LOCK:
        prev = _last_digest(path)
        entry = {
            "ts": when.isoformat(),
            "profile": profile,
            "engagement": engagement,
            "actor": actor,
            "artifact_sha256": artifact_sha256,
            "chain_sha256": chain_sha256,
            "manifest_version": manifest_version,
            "output_sha256": "sha256:" + hashlib.sha256(
                (output or "").encode("utf-8")).hexdigest(),
            "prev": prev,
        }
        if extra:
            entry.update(extra)
        line = json.dumps(entry, sort_keys=True)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as e:
            log.error("APERTURE DISCLOSURE LOG WRITE FAILED for %s (%s): %s",
                      engagement, profile, e)
    return entry


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    files: int
    lines: int
    first_break: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "files": self.files,
            "lines": self.lines,
            "first_break": self.first_break,
        }


def _iter_files() -> Iterator[Path]:
    root = _root()
    if root.exists():
        yield from sorted(root.glob("*.jsonl"))


def verify() -> VerifyResult:
    """Walk every month file and report the first broken link."""
    files = 0
    lines = 0
    for path in _iter_files():
        files += 1
        prev = GENESIS
        with path.open("r", encoding="utf-8") as fh:
            for number, raw in enumerate(fh, start=1):
                raw = raw.rstrip("\n")
                if not raw.strip():
                    continue
                lines += 1
                try:
                    entry = json.loads(raw)
                except ValueError:
                    return VerifyResult(False, files, lines, {
                        "file": path.name, "line": number,
                        "problem": "line is not valid JSON",
                    })
                if entry.get("prev") != prev:
                    return VerifyResult(False, files, lines, {
                        "file": path.name, "line": number,
                        "problem": "chain broken: a preceding line was removed "
                                   "or altered",
                        "expected_prev": prev,
                        "found_prev": entry.get("prev"),
                    })
                prev = _digest(raw)
    return VerifyResult(True, files, lines)
