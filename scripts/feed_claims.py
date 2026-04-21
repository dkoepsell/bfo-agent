"""Feed approved extracted claims into the BFO-Agent orchestrator.

Takes a claims JSON file produced by extract_claims.py and, for each claim
whose `approved` flag is true, calls /propose against a running orchestrator.
Commits either interactively (default) or in batch with --auto-accept.

Usage:
  # Interactive (recommended for first pass on a new chapter)
  python scripts/feed_claims.py --input extracted/chapter04.json

  # Auto-accept only claims with consistent reasoner verdict (for bulk reruns)
  python scripts/feed_claims.py --input extracted/chapter04.json --auto-accept

  # Approve everything above 'medium' confidence without editing the file first
  python scripts/feed_claims.py --input extracted/chapter04.json --approve-all-above medium

  # Dry run: propose but never commit, just log the verdicts
  python scripts/feed_claims.py --input extracted/chapter04.json --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_API = "http://localhost:5000"


def interactive_review(claim: dict, proposal: dict) -> str:
    """Return one of: 'accept', 'edit', 'reject', 'quit'."""
    print("\n" + "=" * 70)
    print(f"CLAIM   [{claim['confidence']}]: {claim['claim']}")
    print(f"SOURCE: {claim['source_quote']}")
    if claim.get("note"):
        print(f"NOTE:   {claim['note']}")
    print("-" * 70)
    verdict = proposal.get("reasoner_verdict", "?")
    print(f"PROPOSAL (reasoner: {verdict})")
    if proposal.get("rationale_summary"):
        print(f"  summary: {proposal['rationale_summary']}")
    for e in proposal.get("entities", []):
        kind = e.get("kind", "?")
        print(f"  [{kind:10s}] {e.get('iri_suggestion'):30s} :: {e.get('bfo_label')} ({e.get('bfo_type')})")
    for r in proposal.get("relations", []):
        print(f"  [rel]        {r.get('s')} -- {r.get('p')} --> {r.get('o')}")
    if proposal.get("open_questions"):
        print("  open questions:")
        for q in proposal["open_questions"]:
            print(f"    - {q}")
    print("-" * 70)

    while True:
        ans = input("Action [a]ccept / [s]kip / [r]eject / [q]uit: ").strip().lower()
        if ans in ("a", "accept"):
            return "accept"
        if ans in ("s", "skip", ""):
            return "skip"
        if ans in ("r", "reject"):
            return "reject"
        if ans in ("q", "quit"):
            return "quit"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="extracted claims JSON")
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--auto-accept", action="store_true",
                        help="Accept every proposal whose reasoner verdict is 'consistent', skip others, no prompts.")
    parser.add_argument("--approve-all-above", choices=["low", "medium", "high"], default=None,
                        help="Treat all claims at/above this confidence as approved for this run, ignoring file's 'approved' array.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Call /propose only, never /commit.")
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--max", type=int, default=None,
                        help="Stop after feeding N claims this run")
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()

    in_path = Path(args.input)
    data = json.loads(in_path.read_text(encoding="utf-8"))
    claims = data["claims"]
    approved_flags = data.get("approved", [False] * len(claims))

    if args.approve_all_above:
        rank = {"low": 0, "medium": 1, "high": 2}
        threshold = rank[args.approve_all_above]
        approved_flags = [
            rank.get(c.get("confidence", "low"), 0) >= threshold for c in claims
        ]

    session_id = args.session_id or f"feed_{in_path.stem}"

    # Preflight: is the orchestrator up?
    try:
        h = requests.get(f"{args.api}/health", timeout=10).json()
        print(f"orchestrator ok: {h.get('stats')}")
    except Exception as e:
        print(f"cannot reach orchestrator at {args.api}: {e}", file=sys.stderr)
        return 1

    stats = {"proposed": 0, "committed": 0, "rejected": 0, "skipped": 0,
             "inconsistent": 0, "errors": 0}
    outcomes = []

    fed = 0
    for i, claim in enumerate(claims):
        if i < args.start_index:
            continue
        if not approved_flags[i]:
            continue
        if args.max is not None and fed >= args.max:
            print(f"reached --max {args.max}, stopping")
            break

        print(f"\n[{i+1}/{len(claims)}] proposing: {claim['claim']}")
        try:
            r = requests.post(
                f"{args.api}/propose",
                json={"utterance": claim["claim"], "session_id": session_id},
                timeout=120,
            )
            r.raise_for_status()
            proposal = r.json()
            stats["proposed"] += 1
        except Exception as e:
            print(f"  propose error: {e}", file=sys.stderr)
            stats["errors"] += 1
            outcomes.append({"index": i, "result": "propose_error", "error": str(e)})
            continue

        verdict = proposal.get("reasoner_verdict")
        if verdict == "inconsistent":
            stats["inconsistent"] += 1

        # Decide action
        if args.dry_run:
            action = "skip"
        elif args.auto_accept:
            action = "accept" if verdict == "consistent" else "skip"
        else:
            action = interactive_review(claim, proposal)

        if action == "quit":
            print("quit requested")
            break
        if action == "accept":
            try:
                r = requests.post(
                    f"{args.api}/commit",
                    json={
                        "proposal_id": proposal["proposal_id"],
                        "session_id": session_id,
                        "proposal": proposal,
                        "user_decision": "accept",
                        "user_notes": f"from {data['source_file']} / {data['section']}",
                    },
                    timeout=60,
                )
                r.raise_for_status()
                stats["committed"] += 1
                outcomes.append({"index": i, "result": "committed",
                                 "proposal_id": proposal["proposal_id"],
                                 "verdict": verdict})
                print(f"  committed ({verdict})")
            except Exception as e:
                print(f"  commit error: {e}", file=sys.stderr)
                stats["errors"] += 1
                outcomes.append({"index": i, "result": "commit_error", "error": str(e)})
        elif action == "reject":
            try:
                requests.post(
                    f"{args.api}/commit",
                    json={
                        "proposal_id": proposal["proposal_id"],
                        "session_id": session_id,
                        "proposal": proposal,
                        "user_decision": "reject",
                        "user_notes": "rejected during extraction review",
                    },
                    timeout=30,
                )
            except Exception:
                pass
            stats["rejected"] += 1
            outcomes.append({"index": i, "result": "rejected", "verdict": verdict})
        else:
            stats["skipped"] += 1
            outcomes.append({"index": i, "result": "skipped", "verdict": verdict})

        fed += 1
        time.sleep(args.delay)

    # Persist a run log next to the claims file
    log_path = in_path.with_suffix(".feed_log.json")
    log = {
        "input": str(in_path),
        "session_id": session_id,
        "api": args.api,
        "dry_run": args.dry_run,
        "auto_accept": args.auto_accept,
        "approve_all_above": args.approve_all_above,
        "stats": stats,
        "outcomes": outcomes,
    }
    log_path.write_text(json.dumps(log, indent=2))
    print(f"\ndone. stats: {stats}")
    print(f"log: {log_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
