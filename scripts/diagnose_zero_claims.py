"""Confirm the schema-mismatch diagnosis before trusting it (spec §5).

Two runs. The first pushes one chunk of FRCP operative text through the
*existing* entity-relation extractor at min_confidence=low, logging the raw
model response before any filtering — that separates "produced nothing" from
"produced things the gate discarded". The second pushes a chunk of a corpus the
extractor is known to work on through the identical code path, to show the
pipeline is alive.

Usage:
  python scripts/diagnose_zero_claims.py --frcp frcp.txt --control dsm_chapter.txt
  python scripts/diagnose_zero_claims.py --frcp frcp.txt --frcp-match "Rule 45"
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import ANTHROPIC_EXTRACTOR_MODEL, require_api_key  # noqa: E402
from app.extractor import ClaimExtractor, chunk_text  # noqa: E402
from chainlab.corpora.loader import load_corpus  # noqa: E402


def _pick_chunk(text: str, match: str, chunk_chars: int, overlap: int) -> tuple[int, str]:
    chunks = chunk_text(text, chunk_chars, overlap)
    if match:
        for i, c in enumerate(chunks):
            if match.lower() in c.lower():
                return i, c
        print(f"  (no chunk contained {match!r}; using the first)", file=sys.stderr)
    return 0, chunks[0] if chunks else ""


def _run(label: str, extractor: ClaimExtractor, chunk: str, section: str) -> dict:
    print(f"\n=== {label} ===")
    print(f"  {len(chunk):,} chars, section={section!r}")
    try:
        claims = extractor.extract_chunk(chunk, section=section)
    except Exception as e:
        print(f"  ERROR: {e}", file=sys.stderr)
        return {"label": label, "error": str(e), "n_claims": 0}
    print(f"  raw claims returned by the model (pre-filter): {len(claims)}")
    for c in claims:
        print(f"    [{c.get('confidence')}] {c.get('claim')}")
    return {
        "label": label,
        "section": section,
        "chunk_chars": len(chunk),
        "n_claims": len(claims),
        "claims": claims,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frcp", required=True, help="Path to the FRCP text")
    parser.add_argument("--control", default=None,
                        help="Path to a corpus the extractor is known to work on (e.g. DSM)")
    parser.add_argument("--frcp-match", default="Rule 26",
                        help="Pick the FRCP chunk containing this string")
    parser.add_argument("--control-match", default="")
    parser.add_argument("--model", default=ANTHROPIC_EXTRACTOR_MODEL)
    parser.add_argument("--chunk-chars", type=int, default=8000)
    parser.add_argument("--overlap", type=int, default=400)
    parser.add_argument("--output", default=None, help="Write the raw responses to JSON")
    args = parser.parse_args()

    try:
        require_api_key()
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    extractor = ClaimExtractor(model=args.model)
    results = []

    corpus = load_corpus("frcp", args.frcp)
    span = corpus.span
    print(f"FRCP: {span.retained_chars:,} of {span.total_chars:,} chars are operative text")
    idx, chunk = _pick_chunk(corpus.text, args.frcp_match, args.chunk_chars, args.overlap)
    results.append(_run(f"FRCP chunk {idx} (match={args.frcp_match!r})",
                        extractor, chunk, section=f"frcp/chunk{idx}"))

    if args.control:
        ctext = Path(args.control).read_text(encoding="utf-8", errors="replace")
        cidx, cchunk = _pick_chunk(ctext, args.control_match, args.chunk_chars, args.overlap)
        results.append(_run(f"Control chunk {cidx} ({Path(args.control).name})",
                            extractor, cchunk, section=f"control/chunk{cidx}"))
    else:
        print("\n(no --control given; pipeline-alive check skipped)")

    print("\n--- diagnosis ---")
    frcp_n = results[0].get("n_claims", 0)
    if frcp_n == 0 and len(results) > 1 and results[1].get("n_claims", 0) > 0:
        print("Schema mismatch confirmed: the pipeline works, FRCP has no ontological")
        print("assertions for it to find. Run the norm extractor instead.")
    elif frcp_n > 0:
        print(f"NOT a pure schema mismatch: the model returned {frcp_n} claims on FRCP.")
        print("Something downstream discarded them; check the confidence gate and TBox.")
    else:
        print("FRCP returned nothing. Without a control run this does not yet")
        print("distinguish schema mismatch from a dead pipeline — rerun with --control.")

    if args.output:
        Path(args.output).write_text(json.dumps(
            {"retained_span": span.to_dict() if span else None,
             "sha256": corpus.sha256, "runs": results}, indent=2, ensure_ascii=False))
        print(f"\nWrote raw responses to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
