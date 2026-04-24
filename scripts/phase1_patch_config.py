"""Phase 1 code patch: rewrite app/config.py to be library-aware.

This reads ontology/active.txt to find the active ontology name, then
derives paths under ontology/library/<active>/. If active.txt is missing
or the library directory doesn't exist, falls back to the old behavior
(top-level ontology/working.owl), so the patch is safe to apply before
you run the filesystem migration or after a rollback.

Usage:
    python scripts/phase1_patch_config.py

Idempotent; safe to run multiple times. Backs up the original config.py.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "app" / "config.py"
BACKUP = ROOT / "app" / "config.py.pre_phase1"

NEW_CONFIG = '''"""Configuration loaded from environment variables.

Library-aware version (phase 1): paths are derived from the active
ontology named in ontology/active.txt. Falls back to legacy singleton
paths if the library layout is not yet in place.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_EXTRACTOR_MODEL = os.getenv(
    "ANTHROPIC_EXTRACTOR_MODEL",
    "claude-haiku-4-5-20251001",
)

# ----- Library-aware path resolution -----

LIBRARY_ROOT = ROOT / "ontology" / "library"
ACTIVE_FILE = ROOT / "ontology" / "active.txt"


def _read_active_name() -> str | None:
    """Return the name of the active ontology from ontology/active.txt,
    or None if the file is missing (indicating pre-migration state)."""
    if not ACTIVE_FILE.exists():
        return None
    name = ACTIVE_FILE.read_text().strip()
    return name or None


def active_ontology_name() -> str | None:
    """Public accessor. Returns None if no library layout is present."""
    return _read_active_name()


def _library_path(name: str, *parts: str) -> Path:
    return LIBRARY_ROOT / name / Path(*parts)


# Paths: prefer library layout if available, else legacy.
_active = _read_active_name()

BFO_PATH = ROOT / os.getenv("BFO_PATH", "ontology/bfo.owl")

if _active and (LIBRARY_ROOT / _active).exists():
    WORKING_PATH = _library_path(_active, "working.owl")
    SEED_PATH = _library_path(_active, "seed", "legal_seed.ttl")
    SESSIONS_DIR = _library_path(_active, "sessions")
    JOBS_DIR = _library_path(_active, "jobs")
else:
    # Legacy / pre-migration layout
    WORKING_PATH = ROOT / os.getenv("WORKING_PATH", "ontology/working.owl")
    SEED_PATH = ROOT / os.getenv("SEED_PATH", "ontology/seed/legal_seed.ttl")
    SESSIONS_DIR = ROOT / os.getenv("SESSIONS_DIR", "sessions")
    JOBS_DIR = ROOT / os.getenv("JOBS_DIR", "jobs")

FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

ENABLE_GIT_COMMITS = os.getenv("ENABLE_GIT_COMMITS", "true").lower() == "true"

WORKING_NS = "http://davidkoepsell.com/bfo-agent/working#"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def require_api_key():
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY.startswith("sk-ant-..."):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in."
        )
'''


def main():
    if not TARGET.exists():
        print(f"ERROR: {TARGET} not found")
        return 1

    # Back up if not already backed up
    if not BACKUP.exists():
        shutil.copy(TARGET, BACKUP)
        print(f"Backed up original to {BACKUP}")
    else:
        print(f"Backup already exists at {BACKUP} (keeping it)")

    TARGET.write_text(NEW_CONFIG)
    print(f"Wrote new {TARGET}")

    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(TARGET)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"COMPILE ERROR:\n{r.stderr}")
        print(f"Restoring from backup ...")
        shutil.copy(BACKUP, TARGET)
        return 1

    print("COMPILES")
    print()
    print("Next: verify by running Flask.")
    print("  python run.py")
    print("Then hit http://127.0.0.1:5000/health")
    print("You should see num_classes: 6881 if SOoL is the active ontology.")
    print()
    print("Also check that config can still find JOBS_DIR:")
    print("  python -c 'from app import config; print(config.JOBS_DIR)'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
