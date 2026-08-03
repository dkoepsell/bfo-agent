"""Extract norm tuples from act-thick legal text, then run K-D3 over them.

Companion to `scripts/extract_claims.py`, which it does not replace: corpora
routed to both extractors need both runs.

Usage:
  python scripts/extract_norms.py --input frcp.txt --corpus frcp --output out/frcp_norms.json
  python scripts/extract_norms.py --input frcp.txt --corpus frcp --output out/frcp_norms.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import ANTHROPIC_EXTRACTOR_MODEL, require_api_key  # noqa: E402
from app.extractor import chunk_text  # noqa: E402
from chainlab.corpora.loader import load_corpus, record_source  # noqa: E402
from chainlab.corpora.routing import NORM, extractors_for  # noqa: E402
from chainlab.detectors.stratum_d import detect_kd3  # noqa: E402
from chainlab.norm_extractor import NormExtractor  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to plain text corpus")
    parser.add_argument("--corpus", required=True, help="Corpus id, e.g. frcp")
    parser.add_argument("--output", required=True, help="Path for JSON output")
    parser.add_argument("--model", default=ANTHROPIC_EXTRACTOR_MODEL)
    parser.add_argument("--chunk-chars", type=int, default=8000)
    parser.add_argument("--overlap", type=int, default=400)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--no-preprocess", action="store_true",
                        help="Skip the FRCP front-matter trim")
    parser.add_argument("--corpus-root", default=None,
                        help="Where to write the corpus source.json provenance record")
    parser.add_argument("--dry-run", action="store_true", help="Chunk only, don't call API")
    parser.add_argument("--limit-chunks", type=int, default=0,
                        help="Stop after N chunks (0 = all). For pilots.")
    args = parser.parse_args()

    corpus_id = args.corpus.strip().lower()
    if NORM not in extractors_for(corpus_id):
        print(f"Warning: corpus '{corpus_id}' is not routed to the norm extractor "
              f"(routes: {extractors_for(corpus_id)}). Proceeding anyway.", file=sys.stderr)

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input not found: {in_path}", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    corpus = load_corpus(corpus_id, in_path, preprocess=not args.no_preprocess)
    text, span = corpus.text, corpus.span
    if span is not None:
        print(f"Preprocess: retained {span.retained_chars:,} of {span.total_chars:,} chars "
              f"({span.dropped_chars:,} dropped){' — ' + span.notes if span.notes else ''}")
    source_json = record_source(corpus, corpus_root=args.corpus_root)
    print(f"Provenance: {source_json}")

    chunks = chunk_text(text, args.chunk_chars, args.overlap)
    if args.limit_chunks:
        chunks = chunks[: args.limit_chunks]
    print(f"Input: {in_path} ({len(text):,} chars, {len(chunks)} chunks)")

    if args.dry_run:
        for i, c in enumerate(chunks):
            head = c[:120].replace("\n", " ")
            print(f"  chunk {i}: {len(c):,} chars :: {head}...")
        return 0

    try:
        require_api_key()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    extractor = NormExtractor(model=args.model)
    tuples = []
    n_errors = 0
    for i, chunk in enumerate(chunks):
        print(f"[chunk {i+1}/{len(chunks)}] extracting...", flush=True)
        try:
            found = extractor.extract_chunk(chunk, corpus_id=corpus_id, chunk_index=i)
        except Exception as e:
            n_errors += 1
            print(f"  failed: {e}", file=sys.stderr)
            continue
        tuples.extend(found)
        print(f"  {len(found)} norm tuples")

    run_id = args.run_id or str(uuid.uuid4())
    result = detect_kd3(tuples, corpus_id=corpus_id, run_id=run_id)

    out = {
        "corpus_id": corpus_id,
        "run_id": run_id,
        "source_file": in_path.name,
        "model": args.model,
        "chunk_chars": args.chunk_chars,
        "n_chunks": len(chunks),
        "n_errors": n_errors,
        "n_tuples": len(tuples),
        "retained_span": span.to_dict() if span else None,
        "kd3": result.summary(),
        "tuples": [t.model_dump(mode="json") for t in tuples],
        "capacities": [c.model_dump(mode="json") for c in result.capacities],
        "findings": [f.model_dump(mode="json") for f in result.findings],
    }
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    print(f"\nWrote {len(tuples)} norm tuples to {out_path}")
    print(f"K-D3: {json.dumps(result.summary())}")
    if result.n_external:
        print(f"  ({result.n_external} capacities excluded as external — reported, not scored)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
