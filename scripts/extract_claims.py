"""Extract atomic ontological claims from a philosophy text.

Thin CLI wrapper around `app.extractor`. For interactive per-chapter use
from the browser, the orchestrator now exposes /extract/prepare and
/extract/chunk endpoints that drive the same logic with progress feedback.

Usage:
  python scripts/extract_claims.py --input path/to/chapter.txt --output extracted/chapter04.json
  python scripts/extract_claims.py --input chapter.txt --section "Ch.4 Legal Reality" --chunk-chars 8000
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import ANTHROPIC_MODEL, require_api_key  # noqa: E402
from app.extractor import ClaimExtractor, chunk_text  # noqa: E402


@dataclass
class ExtractedClaim:
    claim: str
    source_quote: str
    confidence: str
    note: str
    section: str
    chunk_index: int
    source_file: str


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to plain text file")
    parser.add_argument("--output", required=True, help="Path for JSON output")
    parser.add_argument("--section", default=None, help="Section label for provenance")
    parser.add_argument("--model", default=ANTHROPIC_MODEL)
    parser.add_argument("--chunk-chars", type=int, default=8000)
    parser.add_argument("--overlap", type=int, default=400)
    parser.add_argument(
        "--min-confidence",
        choices=["low", "medium", "high"],
        default="low",
        help="Drop claims below this confidence in the output",
    )
    parser.add_argument("--dry-run", action="store_true", help="Chunk only, don't call API")
    args = parser.parse_args()

    try:
        require_api_key()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Input not found: {in_path}", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    text = in_path.read_text(encoding="utf-8", errors="replace")
    section = args.section or in_path.stem
    chunks = chunk_text(text, args.chunk_chars, args.overlap)
    print(f"Input: {in_path} ({len(text):,} chars, {len(chunks)} chunks)")

    if args.dry_run:
        for i, c in enumerate(chunks):
            head = c[:120].replace("\n", " ")
            print(f"  chunk {i}: {len(c):,} chars :: {head}...")
        return 0

    extractor = ClaimExtractor(model=args.model)
    rank = {"low": 0, "medium": 1, "high": 2}
    min_rank = rank[args.min_confidence]

    all_claims: list[ExtractedClaim] = []
    for i, chunk in enumerate(chunks):
        print(f"[chunk {i+1}/{len(chunks)}] extracting...", flush=True)
        try:
            raw = extractor.extract_chunk(chunk, section=section)
        except Exception as e:
            print(f"  failed: {e}", file=sys.stderr)
            continue
        kept = 0
        for c in raw:
            conf = c.get("confidence", "low").lower()
            if rank.get(conf, 0) < min_rank:
                continue
            all_claims.append(
                ExtractedClaim(
                    claim=c.get("claim", ""),
                    source_quote=c.get("source_quote", ""),
                    confidence=conf,
                    note=c.get("note", ""),
                    section=section,
                    chunk_index=i,
                    source_file=str(in_path.name),
                )
            )
            kept += 1
        print(f"  extracted {len(raw)} claims, kept {kept} at >= {args.min_confidence}")
        time.sleep(0.4)

    out = {
        "source_file": str(in_path.name),
        "section": section,
        "model": args.model,
        "chunk_chars": args.chunk_chars,
        "min_confidence": args.min_confidence,
        "n_chunks": len(chunks),
        "n_claims": len(all_claims),
        "claims": [asdict(c) for c in all_claims],
        "approved": [False] * len(all_claims),
        "review_notes": [""] * len(all_claims),
    }
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"\nWrote {len(all_claims)} claims to {out_path}")
    print("Next step: review the file, set 'approved[i] = true' for claims to feed.")
    print(f"Then: python scripts/feed_claims.py --input {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
