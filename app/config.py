"""Configuration loaded from environment variables.

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
