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


def session_path(session_id: str) -> Path:
    """Filesystem path of the main per-session event log (`<session>.jsonl`).

    Single source of truth for the main log location, shared by the loader
    (:func:`load_session`) and the SSE streamer so they never disagree.
    """
    return SESSIONS_DIR / f"{session_id}.jsonl"


def gate_log_path(session_id: str) -> Path:
    """Filesystem path of the per-session gate log (`<session>.gate.jsonl`)."""
    return SESSIONS_DIR / f"{session_id}.gate.jsonl"


def log_gate_events(session_id: str, events: list[dict], context: dict | None = None):
    """Append coherence-gate events to a dedicated per-session gate log.

    Kept separate from the main session log so the scorer (Task 5) can read
    the experimental record cleanly: proposal, tier that fired, policy action,
    outcome.
    """
    path = gate_log_path(session_id)
    with path.open("a", encoding="utf-8") as f:
        for ev in events:
            record = {"ts": _now(), "session_id": session_id, **(context or {}), **ev}
            f.write(json.dumps(record) + "\n")


def load_gate_log(session_id: str) -> list[dict]:
    path = gate_log_path(session_id)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def log_event(session_id: str, event_type: str, payload: dict[str, Any]):
    path = session_path(session_id)
    record = {
        "ts": _now(),
        "session_id": session_id,
        "event_type": event_type,
        "payload": payload,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def load_session(session_id: str) -> list[dict]:
    path = session_path(session_id)
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

def git_commit_library_change(paths: list, message: str):
    """Commit a library-layout change (active.txt, new/deleted manifest, etc).

    Used by phase-4 lifecycle operations. Silently skips if git is
    unavailable. Commits paths relative to the repo root.
    """
    if not ENABLE_GIT_COMMITS:
        return

    try:
        _run(["git", "rev-parse", "--git-dir"], check=True)
    except Exception:
        try:
            _run(["git", "init"], check=True)
            _run(["git", "config", "user.email", "bfo-agent@local"], check=True)
            _run(["git", "config", "user.name", "BFO Agent"], check=True)
        except Exception:
            return

    try:
        for p in paths:
            p = Path(p)
            rel = p.relative_to(ROOT) if p.is_absolute() else p
            _run(["git", "add", str(rel)], check=False)
        _run(["git", "commit", "-m", message, "--allow-empty"], check=False)
    except Exception:
        pass

