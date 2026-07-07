"""Configuration loaded from environment variables.

Library-aware version (phase 1): paths are derived from the active
ontology named in ontology/active.txt. Falls back to legacy singleton
paths if the library layout is not yet in place.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Ensure the HermiT reasoner can find ``java`` regardless of how the process was
# launched. On hosts where java lives in ~/.local/bin (e.g. the DGX), a launcher
# that does not export it (bare ``python run.py``, cron, pytest) leaves owlready2
# unable to reason -- it then silently fails, which the gate reads as
# "inconsistent", wrongly rejecting every claim. Prepending ~/.local/bin only
# when java is otherwise absent is a no-op anywhere java is already on PATH.
import shutil as _shutil

if _shutil.which("java") is None:
    _local_bin = os.path.join(os.path.expanduser("~"), ".local", "bin")
    if os.path.isdir(_local_bin) and _local_bin not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _local_bin + os.pathsep + os.environ.get("PATH", "")

ROOT = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_EXTRACTOR_MODEL = os.getenv(
    "ANTHROPIC_EXTRACTOR_MODEL",
    "claude-haiku-4-5-20251001",
)

# ----- Kernel identity (bfo-agent-spec.md FR-1/§8) -----
# The dominant kernel is BFO 2020. Its versionIRI stamps emitted fragments and
# kernel-extension-requests so provenance is unambiguous.
KERNEL_VERSION_IRI = os.getenv(
    "KERNEL_VERSION_IRI", "http://purl.obolibrary.org/obo/bfo/2020/bfo.owl"
)

# ----- Validation gate (bfo-agent-spec.md: owltesterservice) -----
# When set, the agent POSTs each emitted fragment to the external
# owltesterservice for validation/repair before writing it. When empty, the
# gate falls back to the in-process coherence gate (construction + lint +
# local HermiT reasoner) plus the file-level owl_checks validators.
OWLTESTER_URL = os.getenv("OWLTESTER_URL", "").strip()
OWLTESTER_TIMEOUT = float(os.getenv("OWLTESTER_TIMEOUT", "30"))

# ----- Deterministic individual IRIs (bfo-agent-spec.md FR-6) -----
# When on, emitted individuals get a stable, content-hashed local name so two
# runs over the same source produce the same IRIs (diffability). Off by default
# to preserve the interactive workbench's human-readable suggested names; the
# batch/case path turns it on.
STABLE_INDIVIDUAL_IRIS = os.getenv("STABLE_INDIVIDUAL_IRIS", "false").lower() in (
    "1", "true", "yes", "on",
)

# ----- Prompt caching (bfo-agent-spec.md §9, MC-5) -----
# Cache TTL for the stable system prefix (BFO kernel + rules + few-shot). "5m"
# keeps the cache hot for back-to-back corpus runs; "1h" (2x write, 0.1x read)
# is better when calls are spaced >5 min apart. Anything else falls back to 5m.
CACHE_TTL = os.getenv("CACHE_TTL", "5m")

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

# ----- Server-side job runner -----
# When a feed run completes or fails, POST a plain-text notification here.
# ntfy.sh-compatible (body = message, "Title" header = subject); empty
# disables notifications.
NOTIFY_URL = os.getenv("BFO_NOTIFY_URL", "").strip()
# On server boot, restart the feeder for any job left in status "feeding"
# (i.e. a run interrupted by a restart or crash picks up where it left off).
AUTORESUME_JOBS = os.getenv("AUTORESUME_JOBS", "true").lower() == "true"

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

# ----- Extraction fidelity (fidelity-mode-spec.md FM-1) -----
# Default fidelity stamped into NEWLY created ontologies only; an existing
# manifest without a "fidelity" field always means "curated" (FM-1).
#   curated  -- today's behavior: gate rejects/repairs, commit backstop rolls back.
#   faithful -- annotate, don't repair: the extracted ontology stays true to the
#              source text including its errors; incoherence is evidence, not a
#              defect to fix.
FIDELITY_DEFAULT = os.getenv("FIDELITY_DEFAULT", "curated")

# ----- FOL gate (fol-gate-spec.md) -----
# Out-of-loop Prover9/Mace4 audit of committed ontologies against the BFO 2020
# first-order axioms. Evidence-only (FG-0): never blocks, never writes to the
# ontology. Soft-disabled when the binaries are absent.
FOL_GATE_ENABLED = os.getenv("FOL_GATE_ENABLED", "false").lower() == "true"
FOL_PROVER9_BIN = os.getenv("FOL_PROVER9_BIN", "prover9")
FOL_MACE4_BIN = os.getenv("FOL_MACE4_BIN", "mace4")
FOL_TIMEOUT_SECS = int(os.getenv("FOL_TIMEOUT_SECS", "60"))
FOL_PROBE_TIMEOUT_SECS = int(os.getenv("FOL_PROBE_TIMEOUT_SECS", "10"))
FOL_MACE4_MAX_DOMAIN = int(os.getenv("FOL_MACE4_MAX_DOMAIN", "8"))
FOL_AXIOMS_DIR = ROOT / os.getenv("FOL_AXIOMS_DIR", "ontology/bfo-2020-fol")
# Sub-theory profile: "default" (declaration/instantiation/mereology/dependence/
# participation/temporalized-relations) or "full" (adds spatial, temporal,
# material-entity, history, order, occurrent-mereology, spatiotemporal).
FOL_AXIOM_PROFILE = os.getenv("FOL_AXIOM_PROFILE", "default")
# Cap on per-class unsatisfiability probes per audit (P-2; cap is logged).
FOL_PROBES_MAX_CLASSES = int(os.getenv("FOL_PROBES_MAX_CLASSES", "25"))

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
