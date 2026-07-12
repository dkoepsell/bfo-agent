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

# Sampling temperature for the proposer. BFO typing is a near-deterministic
# task -- a term has one correct top-level category -- so the default is 0.
# At the API default (1.0) the proposer stochastically assigns a class two
# disjoint BFO types (e.g. a disease as both process and disposition), which
# the construction linter rejects; that straddle-reject churn dominated the
# ICD-11 feed. Raise only if deterministic output collapses into a rut.
PROPOSER_TEMPERATURE = float(os.getenv("PROPOSER_TEMPERATURE", "0"))

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
# Watchdog: SIGKILL a HermiT run that exceeds this many seconds so a single
# pathological claim can't hang the reasoner for hours and pin the box's memory
# cgroup, freezing the whole app (incident 2026-07-12). 0 disables the watchdog.
REASONER_TIMEOUT_SECONDS = float(os.getenv("REASONER_TIMEOUT_SECONDS", "300"))
# Hard per-request timeout on the LLM (proposer/extractor) client. Without it the
# Anthropic SDK can block a feed's runner thread indefinitely on a stalled
# connection, leaving the job silently "feeding" forever (stall incident
# 2026-07-12). On timeout the SDK raises, which the feed loop handles as a claim
# error (retry/backoff, then pause after MAX_CONSECUTIVE_ERRORS). 0 = SDK default.
LLM_CALL_TIMEOUT_SECONDS = float(os.getenv("LLM_CALL_TIMEOUT_SECONDS", "180"))
# Last-resort stall backstop for the server-side feed loop (job_runner). If a run
# makes zero forward progress (no claim completes) for this many seconds -- a
# reasoner watchdog kill-miss, a lock deadlock, or any unforeseen wedge -- the
# monitor pauses the job and notifies instead of letting it sit silently
# in-progress forever. Must exceed a legitimate slow claim (one LLM call +
# reduced reasoning + an occasional checkpoint self-heal). 0 disables the monitor.
FEED_STALL_TIMEOUT_SECONDS = float(os.getenv("FEED_STALL_TIMEOUT_SECONDS", "1800"))
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

# ----- Speed instrumentation (SPEC-bfo-agent-speed.md Step 0) -----
# Per-claim phase timings logged as "claim_timing" events in the session log.
TIMING_INSTRUMENTATION = os.getenv("TIMING_INSTRUMENTATION", "true").lower() == "true"

# ----- Structural reasoner skip (SPEC-bfo-agent-speed.md change 3) -----
# When the construction+lint tiers fully resolve every touched entity's BFO
# anchors and the proposal introduces nothing structure cannot decide (no
# class expressions, restrictions, negations, equivalence/disjointness),
# skip the per-claim HermiT tier. The commit-time / checkpoint full pass
# remains the backstop; this changes when the JVM runs, not whether.
GATE_REASONER_STRUCTURAL_SKIP = (
    os.getenv("GATE_REASONER_STRUCTURAL_SKIP", "false").lower() == "true"
)

# ----- In-memory dry-run (SPEC-bfo-agent-speed.md change 2) -----
# Apply-and-rollback proposals against the live in-memory world and reason in
# a disposable scratch world serialized from memory, instead of reloading the
# working file from disk 2-5x per claim. The load-time sanitizer guards run
# once at startup plus a targeted per-proposal check (_proposal_guards); the
# guarantee is unchanged.
INMEM_DRY_RUN = os.getenv("INMEM_DRY_RUN", "false").lower() == "true"

# ----- Checkpointed full verification (SPEC-bfo-agent-speed.md change 6) -----
# When VERIFY_EVERY_COMMIT is false, the per-claim commit skips the full-graph
# post-commit reasoner pass; instead a full HermiT certificate runs every
# FULL_VERIFY_EVERY_K commits and, mandatorily, once at job completion before
# the job is marked completed. The artifact's final state is always fully
# certified; only the timing of the full check changes.
VERIFY_EVERY_COMMIT = os.getenv("VERIFY_EVERY_COMMIT", "true").lower() == "true"
FULL_VERIFY_EVERY_K = int(os.getenv("FULL_VERIFY_EVERY_K", "250"))
FINALIZE_REQUIRES_FULL_VERIFY = os.getenv("FINALIZE_REQUIRES_FULL_VERIFY", "true").lower() == "true"
# On checkpoint failure (curated mode), also flip the suspect window's claims
# from committed to needs_review. Off by default: evidence-first, the git
# per-commit history of working.owl makes bisection tractable.
CHECKPOINT_FAIL_MARK_REVIEW = os.getenv("CHECKPOINT_FAIL_MARK_REVIEW", "false").lower() == "true"
# Self-healing checkpoint (curated mode). When on, a failed full-graph
# certificate does not pause the run: the exact classes the certificate names
# unsatisfiable are quarantined (destroyed with their referencing triples),
# recorded in the incoherence ledger as evidence, and the artifact is
# re-certified before feeding continues. These are reduced-world
# false-coherent commits -- classes the per-claim gate admitted but that the
# full graph proves unsatisfiable -- so removal restores the gate's intended
# admission rather than corrupting a good artifact. A bare inconsistency with
# no named unsatisfiable class is handled by the ABox self-heal below. Off by
# default; an unattended long regate run turns it on so a lone poison class
# cannot stall the whole job.
CHECKPOINT_SELF_HEAL = os.getenv("CHECKPOINT_SELF_HEAL", "false").lower() == "true"
CHECKPOINT_SELF_HEAL_MAX_ROUNDS = int(os.getenv("CHECKPOINT_SELF_HEAL_MAX_ROUNDS", "6"))
# ABox extension of the self-heal. A BARE inconsistency (HermiT reports the
# ontology inconsistent and raises before naming any unsatisfiable class -- the
# icd11bfo_v2 case, where the reduced-world per-claim gate admits two
# individuals whose combined types/relations clash) names no class to
# quarantine, so the class sweep above cannot touch it and the run would pause
# forever. When on, the self-heal isolates the culprit individuals
# (OntologyManager.isolate_inconsistency_culprits, QuickXplain over the saved
# graph) and quarantines those instead, then re-certifies. Bounded by
# MAX_INDIVIDUALS reasoner calls; past that (or if removing individuals cannot
# restore consistency, i.e. a TBox cause) it still pauses. Gated under
# CHECKPOINT_SELF_HEAL; on by default when that is on.
CHECKPOINT_SELF_HEAL_ABOX = os.getenv("CHECKPOINT_SELF_HEAL_ABOX", "true").lower() == "true"
CHECKPOINT_SELF_HEAL_MAX_INDIVIDUALS = int(os.getenv("CHECKPOINT_SELF_HEAL_MAX_INDIVIDUALS", "400"))

# ----- Reduced reasoning world (SPEC-bfo-agent-speed.md change 1) -----
# Dry-run reasoning over BFO + working TBox + only the proposal's touched
# individuals instead of the full ABox. Sound for class satisfiability and
# the proposal's own assertions; the checkpoint/final full pass (change 6)
# reconciles cross-individual interactions. Requires INMEM_DRY_RUN.
REDUCED_REASONING_WORLD = os.getenv("REDUCED_REASONING_WORLD", "false").lower() == "true"

# ----- Batch propose (SPEC-bfo-agent-speed.md change 5) -----
# Decouple propose from commit: a prepare pass (app/batch_propose.py) submits
# all pending+approved claims to the Anthropic Message Batches API (~50%
# cheaper) against one context snapshot and persists the parsed proposals in
# the job file; the feed then consumes a stored proposal (consume-once)
# instead of calling the API inline. Every precomputed proposal still passes
# the full gate, and gate resamples always re-propose live. This feature is
# Anthropic/Hetzner-only and is never merged to the DGX fork.
BATCH_PROPOSE_ENABLED = os.getenv("BATCH_PROPOSE_ENABLED", "false").lower() == "true"
# Poll interval (seconds) while waiting for a submitted batch to end.
BATCH_PROPOSE_POLL_SECS = float(os.getenv("BATCH_PROPOSE_POLL_SECS", "20"))
# Commit-time IRI reservation: batched proposals cannot see classes minted by
# claims committed just before them, so at apply time each NEW entity's label
# is canonicalized (stable_iri.canonical_key) and, on a key hit against an
# already-committed entity of the same kind, the entity is rewritten to reuse
# that IRI (relations remapped in lockstep) instead of minting a
# near-duplicate.
IRI_RESERVATION_ENABLED = os.getenv("IRI_RESERVATION_ENABLED", "false").lower() == "true"

# ----- Amortized save (SPEC-bfo-agent-speed.md change 7) -----
# When false, the per-claim commit skips the O(N) RDF/XML serialization (and
# the per-claim git commit of working.owl); the file is written at checkpoint
# boundaries, the final pass, pause/runner exit, and graceful shutdown.
# Between saves the source of truth is the in-memory world; crash recovery
# resets claims committed after the last save back to pending so they re-feed
# (bounded to <= FULL_VERIFY_EVERY_K re-proposals; stable IRIs converge).
# Requires VERIFY_EVERY_COMMIT=false (the per-commit verify reasons over the
# saved file) and INMEM_DRY_RUN=true (the legacy dry-run reloads from disk,
# which would silently drop unsaved in-memory commits); refused otherwise.
def _sanitize_save_every_commit(save_every: bool, verify_every: bool,
                                inmem: bool) -> bool:
    """Refuse SAVE_EVERY_COMMIT=false unless its prerequisites hold."""
    if save_every:
        return True
    if verify_every or not inmem:
        import logging
        logging.getLogger(__name__).warning(
            "SAVE_EVERY_COMMIT=false requires VERIFY_EVERY_COMMIT=false and "
            "INMEM_DRY_RUN=true (got VERIFY_EVERY_COMMIT=%s, INMEM_DRY_RUN=%s)"
            "; forcing SAVE_EVERY_COMMIT=true", verify_every, inmem,
        )
        return True
    return False


SAVE_EVERY_COMMIT = _sanitize_save_every_commit(
    os.getenv("SAVE_EVERY_COMMIT", "true").lower() == "true",
    VERIFY_EVERY_COMMIT,
    INMEM_DRY_RUN,
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


def _warn_contradictory_flags():
    """Log (never raise) when flag combinations are inert or self-defeating."""
    import logging

    _log = logging.getLogger(__name__)
    if REDUCED_REASONING_WORLD and not INMEM_DRY_RUN:
        _log.warning(
            "REDUCED_REASONING_WORLD is set but INMEM_DRY_RUN is off: the "
            "reduced world requires INMEM_DRY_RUN; flag has no effect"
        )
    if REDUCED_REASONING_WORLD and VERIFY_EVERY_COMMIT:
        _log.warning(
            "REDUCED_REASONING_WORLD with VERIFY_EVERY_COMMIT=true: the "
            "reduced dry-run world still pays a full reasoner pass per "
            "commit; set VERIFY_EVERY_COMMIT=false to realize the savings"
        )


_warn_contradictory_flags()
