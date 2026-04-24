"""Session logging and git-backed ontology versioning.

Every proposal, decision, and commit is appended to a per-session JSONL file
in `sessions/`. On commit, the working ontology is optionally committed to
git so every graph state is reachable and reviewable.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ENABLE_GIT_COMMITS, ROOT, SESSIONS_DIR, WORKING_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(session_id: str, event_type: str, payload: dict[str, Any]):
    path = SESSIONS_DIR / f"{session_id}.jsonl"
    record = {
        "ts": _now(),
        "session_id": session_id,
        "event_type": event_type,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_session(session_id: str) -> list[dict]:
    path = SESSIONS_DIR / f"{session_id}.jsonl"
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def git_commit_working_ontology(session_id: str, proposal_id: str, message: str):
    """Commit the current working ontology to git if enabled.

    Silently skips if git is unavailable or the repo is not initialized.
    """
    if not ENABLE_GIT_COMMITS:
        return

    try:
        # Ensure repo exists
        _run(["git", "rev-parse", "--git-dir"], check=True)
    except Exception:
        # Try to init
        try:
            _run(["git", "init"], check=True)
            _run(["git", "config", "user.email", "bfo-agent@local"], check=True)
            _run(["git", "config", "user.name", "BFO Agent"], check=True)
        except Exception:
            return

    try:
        _run(["git", "add", str(WORKING_PATH.relative_to(ROOT))], check=False)
        commit_msg = f"[{session_id[:8]}/{proposal_id[:8]}] {message}"
        _run(["git", "commit", "-m", commit_msg, "--allow-empty"], check=False)
    except Exception:
        # Don't let git failures block the user
        pass


def _run(cmd: list[str], check: bool = True):
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        check=check,
        capture_output=True,
        text=True,
    )


def recent_commits(n: int = 10) -> list[dict]:
    """Return the last N git commits on the working ontology."""
    if not ENABLE_GIT_COMMITS:
        return []
    try:
        result = _run(
            [
                "git",
                "log",
                f"-{n}",
                "--pretty=format:%H|%ai|%s",
                "--",
                str(WORKING_PATH.relative_to(ROOT)),
            ],
            check=False,
        )
        out = []
        for line in result.stdout.splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                out.append({"hash": parts[0], "time": parts[1], "message": parts[2]})
        return out
    except Exception:
        return []
