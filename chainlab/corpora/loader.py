"""Uniform corpus interface and provenance record.

A corpus is a directory holding the raw text plus a `source.json`. The record
exists so a rate reported over a corpus has a reproducible denominator: which
bytes were retained, which were dropped as front matter, and the SHA-256 of the
file both were computed from.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .legal_frcp import RetainedSpan, preprocess_frcp
from .routing import extractors_for

CORPUS_ROOT = Path(__file__).resolve().parents[2] / "chainlab" / "corpora" / "data"

# Corpora with a front-matter trim. Anything else is used whole.
_PREPROCESSORS = {"frcp": preprocess_frcp}


@dataclass
class Corpus:
    corpus_id: str
    path: Path
    text: str                    # after preprocessing
    raw_chars: int
    sha256: str
    span: RetainedSpan | None
    extractors: tuple[str, ...]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_corpus(corpus_id: str, path: str | Path, preprocess: bool = True) -> Corpus:
    corpus_id = corpus_id.strip().lower()
    path = Path(path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    span = None
    text = raw
    fn = _PREPROCESSORS.get(corpus_id) if preprocess else None
    if fn is not None:
        text, span = fn(raw)
    return Corpus(
        corpus_id=corpus_id,
        path=path,
        text=text,
        raw_chars=len(raw),
        sha256=sha256_of(path),
        span=span,
        extractors=extractors_for(corpus_id),
    )


def record_source(corpus: Corpus, corpus_root: Path | None = None,
                  notes: str = "") -> Path:
    """Write `<corpus_root>/<corpus_id>/source.json`. Returns the path written."""
    root = Path(corpus_root) if corpus_root else CORPUS_ROOT
    out_dir = root / corpus.corpus_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "source.json"

    record = {
        "corpus_id": corpus.corpus_id,
        "source_file": corpus.path.name,
        "source_path": str(corpus.path),
        "sha256": corpus.sha256,
        "raw_chars": corpus.raw_chars,
        "retained_chars": len(corpus.text),
        "retained_span": corpus.span.to_dict() if corpus.span else None,
        "extractors": list(corpus.extractors),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "notes": notes,
    }
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return out_path
