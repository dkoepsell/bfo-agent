"""Reconstruct a persistent job from an existing feed_*.jsonl session log.

Use case: you had a feed running before the jobs system existed. You stopped
it partway, and you still have the session log but no job file. This script
replays the log and builds a job that knows which claims were already
committed, which were inconsistent, and which never ran.

Usage:
  python scripts/migrate_session_to_job.py \
      --session feed_A_Structural_Ontology_of_the_Law_-_Palgrave_mo7i5yz6 \
      --name "SOoL full book (resumed)"

The resulting job appears in the Jobs dropdown of the Extract tab and can
be resumed there (approve remaining claims, click Feed selected).

Caveats:
- Only committed/inconsistent/error claims that appear in the log become
  known. Claims that were extracted but never reached /propose cannot be
  reconstructed from the log; you'll need to re-extract the remaining
  chapters and append them to the migrated job.
- Each claim's text comes from the logged `proposal.utterance`, which is
  what was actually sent to the proposer. This is what you want.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import jobs as jobs_store  # noqa: E402

SESSIONS = ROOT / "sessions"


def load_events(path: Path) -> list[dict]:
    out = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def reconstruct_claims(events: list[dict]) -> list[dict]:
    """Walk the log in order, building one claim record per unique utterance.

    A claim's final status comes from the last event mentioning its
    proposal_id (or its utterance if proposal_id is not yet bound).
    """
    # Two indexes: utterance -> claim record, proposal_id -> utterance
    by_utterance: dict[str, dict] = {}
    pid_to_utt: dict[str, str] = {}
    order: list[str] = []

    for ev in events:
        t = ev.get("event_type")
        p = ev.get("payload", {}) or {}

        if t == "propose":
            prop = p.get("proposal", {}) or {}
            utt = (prop.get("utterance") or "").strip()
            if not utt:
                continue
            pid = prop.get("proposal_id")
            verdict = prop.get("reasoner_verdict")

            if utt not in by_utterance:
                by_utterance[utt] = {
                    "claim": utt,
                    "source_quote": utt,  # no source in log; use utterance as fallback
                    "confidence": "medium",  # unknown; default medium
                    "note": "migrated from session log",
                    "section": "migrated",
                    "chunk_index": 0,
                    "approved": True,  # it was approved at the time; preserve that
                    "proposal_id": pid,
                    "verdict": verdict,
                    "_final_status": (
                        "inconsistent" if verdict == "inconsistent"
                        else "error" if verdict == "error"
                        else "needs_review"  # proposed but not yet committed
                    ),
                }
                order.append(utt)
            else:
                rec = by_utterance[utt]
                rec["proposal_id"] = pid
                rec["verdict"] = verdict
                if verdict == "inconsistent":
                    rec["_final_status"] = "inconsistent"

            if pid:
                pid_to_utt[pid] = utt

        elif t == "commit":
            pid = p.get("proposal_id")
            utt = pid_to_utt.get(pid)
            if utt and utt in by_utterance:
                by_utterance[utt]["_final_status"] = "committed"

        elif t == "reject":
            pid = p.get("proposal_id")
            utt = pid_to_utt.get(pid)
            if utt and utt in by_utterance:
                by_utterance[utt]["_final_status"] = "rejected"

        elif t in ("propose_error", "commit_error"):
            # Best-effort attribution
            pid = p.get("proposal_id")
            utt = pid_to_utt.get(pid) if pid else None
            if not utt:
                utt = (p.get("utterance") or "").strip()
            if utt and utt in by_utterance:
                by_utterance[utt]["_final_status"] = "error"

    # Translate to job-claim shape
    claims = []
    for utt in order:
        rec = by_utterance[utt]
        claims.append(
            {
                "claim": rec["claim"],
                "source_quote": rec["source_quote"],
                "confidence": rec["confidence"],
                "note": rec["note"],
                "section": rec["section"],
                "chunk_index": rec["chunk_index"],
                "approved": rec["approved"],
                "_final_status": rec["_final_status"],
                "_proposal_id": rec["proposal_id"],
                "_verdict": rec["verdict"],
            }
        )
    return claims


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True,
                        help="Session id (with or without .jsonl)")
    parser.add_argument("--name", required=True, help="Name for the new job")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    session_name = args.session
    if not session_name.endswith(".jsonl"):
        session_name += ".jsonl"
    session_path = SESSIONS / session_name
    if not session_path.exists():
        print(f"Session log not found: {session_path}", file=sys.stderr)
        return 1

    events = load_events(session_path)
    print(f"Loaded {len(events)} events from {session_path.name}")

    claims = reconstruct_claims(events)
    tally = {"committed": 0, "inconsistent": 0, "needs_review": 0,
             "error": 0, "rejected": 0}
    for c in claims:
        tally[c["_final_status"]] = tally.get(c["_final_status"], 0) + 1
    print(f"Reconstructed {len(claims)} unique claims:")
    for k, v in tally.items():
        print(f"  {k}: {v}")

    if args.dry_run:
        print("\n--dry-run: not writing job file")
        return 0

    job = jobs_store.create_job(
        args.name,
        meta={
            "migrated_from_session": session_path.name,
            "reconstructed_claims": len(claims),
            "tally": tally,
        },
    )
    print(f"\nCreated job {job['job_id']} ({job['name']})")

    # Append all claims first (they start pending+approved)
    append_payload = [
        {k: v for k, v in c.items() if not k.startswith("_")}
        for c in claims
    ]
    jobs_store.append_claims(job["job_id"], append_payload)

    # Then set each one's true status from the log
    # Reload so we have assigned ids
    job = jobs_store.load_job(job["job_id"])
    for job_claim, reconstructed in zip(job["claims"], claims):
        final = reconstructed["_final_status"]
        if final == "needs_review":
            # If it was proposed but not committed, mark needs_review but
            # keep approved=True so user can decide whether to retry.
            jobs_store.update_claim_status(
                job["job_id"], job_claim["id"],
                "needs_review",
                proposal_id=reconstructed["_proposal_id"],
                verdict=reconstructed["_verdict"],
            )
        elif final in ("committed", "inconsistent", "error", "rejected"):
            jobs_store.update_claim_status(
                job["job_id"], job_claim["id"], final,
                proposal_id=reconstructed["_proposal_id"],
                verdict=reconstructed["_verdict"],
            )

    # Point the job at the ORIGINAL session id so future feeds continue logging
    # into the same jsonl file, giving you one continuous trace.
    job = jobs_store.load_job(job["job_id"])
    job["session_id"] = session_path.stem
    jobs_store.save_job(job)

    print(f"\nJob written. To resume:")
    print(f"  1. Start the server: python run.py")
    print(f"  2. Open client/index.html, go to Extract tab")
    print(f"  3. Select '{args.name}' from the job picker")
    print(f"  4. Extract more chapters (append) or click Feed selected")
    print(f"\nThe job is keyed to session '{session_path.stem}' so new feed")
    print(f"events will continue writing to the same session log.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
