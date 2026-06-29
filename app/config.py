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

# ----- Coherence gate -----
# Whether the in-loop BFO coherence gate runs at all. When off, the loop
# reverts to the legacy consistency-only behavior.
ENABLE_COHERENCE_GATE = os.getenv("ENABLE_COHERENCE_GATE", "true").lower() == "true"
# How the loop reacts when the gate fires: reject_resample | repair | reground.
GATE_POLICY = os.getenv("GATE_POLICY", "reject_resample")
# Run the (expensive) reasoner tier inside the gate. Lint tier always runs.
GATE_RUN_REASONER = os.getenv("GATE_RUN_REASONER", "true").lower() == "true"
# Max resample/reground attempts before giving up and flagging for review.
GATE_MAX_ATTEMPTS = int(os.getenv("GATE_MAX_ATTEMPTS", "2"))
# Relation-aware scaffolding: when a dependent-continuant class is committed,
# add the constraint its BFO category requires (inheres_in / realized_in).
ENABLE_SCAFFOLDING = os.getenv("ENABLE_SCAFFOLDING", "true").lower() == "true"

# ----- Construction linter (bfo-agent-spec.md §6, PC-1..PC-6) -----
# The construction tier runs first in the gate. It rejects privation
# primitives, relation-baked class names, untyped entities, invented
# predicates, and continuant/occurrent conflations that drive class
# proliferation. Always-on PC-1/PC-2/PC-3/PC-5/PC-6.
ENABLE_CONSTRUCTION_LINTER = (
    os.getenv("ENABLE_CONSTRUCTION_LINTER", "true").lower() == "true"
)
# Strict closed-vocabulary mode (PC-4): forbid ALL new classes; everything must
# be an individual or a class expression over BFO. This is the spec's
# case-fragment mode; OFF by default because the workbench builds domain
# classes on top of BFO.
STRICT_CLOSED_VOCAB = (
    os.getenv("STRICT_CLOSED_VOCAB", "false").lower() == "true"
)

# ----- Class-count budget (bfo-agent-spec.md FR-7) -----
# Soft cap on how many NEW classes a single proposal may mint. Exceeding it
# raises a warning (logged + surfaced on the proposal), never a silent accept.
# 0 disables the per-proposal warning.
CLASS_BUDGET_PER_PROPOSAL = int(os.getenv("CLASS_BUDGET_PER_PROPOSAL", "5"))
# Soft cap on the total working-class count; once the active ontology grows
# past this, every commit logs a proliferation warning. 0 disables.
CLASS_BUDGET_WARN_TOTAL = int(os.getenv("CLASS_BUDGET_WARN_TOTAL", "0"))

# ----- Accounts / billing / managed-jobs (Phase 1: accounts + BYOK) -----
# Flask session signing key. Required in production; a dev fallback keeps
# local/test runs working without extra setup.
SECRET_KEY = os.getenv("SECRET_KEY", "dev-insecure-change-me")
# Fernet key (base64 32-byte) used to encrypt BYOK Anthropic keys at rest.
# Lives in the environment, never in the DB. Empty disables BYOK storage.
BYOK_ENCRYPTION_KEY = os.getenv("BYOK_ENCRYPTION_KEY", "")
# SQLite database for users/jobs/usage. Lives outside the ontology library.
DATA_DIR = ROOT / "data"
DB_PATH = ROOT / os.getenv("DB_PATH", "data/app.db")
# Free-tier limits (free jobs run on the owner key).
FREE_CHAR_LIMIT = int(os.getenv("FREE_CHAR_LIMIT", "20000"))
FREE_JOB_QUOTA = int(os.getenv("FREE_JOB_QUOTA", "1"))
# Hard owner-key token ceiling per free job (char cap != token cap, since
# resamples multiply calls). 0 disables the ceiling.
FREE_JOB_TOKEN_CAP = int(os.getenv("FREE_JOB_TOKEN_CAP", "200000"))

WORKING_NS = "http://davidkoepsell.com/bfo-agent/working#"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
JOBS_DIR.mkdir(parents=True, exist_ok=True)


def require_api_key():
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY.startswith("sk-ant-..."):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in."
        )


def require_secret():
    """Fail fast in production if the session secret was left at the default."""
    if not SECRET_KEY or SECRET_KEY == "dev-insecure-change-me":
        raise RuntimeError(
            "SECRET_KEY not set. Set a strong random value in .env for production."
        )
