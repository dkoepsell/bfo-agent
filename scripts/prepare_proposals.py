"""Submit a job's pending claims to the Anthropic Message Batches API
(SPEC-bfo-agent-speed.md change 5) and persist the parsed proposals into the
job file, so a subsequent feed run consumes them instead of calling the API
inline (~50% cheaper propose side).

Usage:
  python scripts/prepare_proposals.py <job_id> [--poll SECS]

Runs synchronously: submits the batch, polls until it ends, writes the
proposals, and prints the summary. Safe to re-run -- claims that already
carry a stored proposal are skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import batch_propose  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Batch-propose a job's pending claims via the "
                    "Anthropic Message Batches API."
    )
    ap.add_argument("job_id", help="job id (jobs/<job_id>.json)")
    ap.add_argument(
        "--poll", type=float, default=None, metavar="SECS",
        help="poll interval in seconds (default: config.BATCH_PROPOSE_POLL_SECS)",
    )
    args = ap.parse_args()

    def progress(counts) -> None:
        if counts is not None:
            print(
                f"[batch] processing={getattr(counts, 'processing', '?')} "
                f"succeeded={getattr(counts, 'succeeded', '?')} "
                f"errored={getattr(counts, 'errored', '?')}",
                flush=True,
            )

    result = batch_propose.prepare_job_proposals(
        args.job_id, poll_secs=args.poll, progress_cb=progress
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
