"""Phase 3 patch: per-request ontology selection on read endpoints.

Adds an optional `ontology` parameter to the read endpoints:
  GET  /health?ontology=NAME
  GET  /graph?ontology=NAME
  GET  /graph/classes?ontology=NAME
  GET  /graph/individuals?ontology=NAME
  GET  /graph/bfo?ontology=NAME
  POST /query            {"ontology": "NAME", "question": "..."}

Default (no parameter): active ontology. Invalid names return 404.

Write endpoints (/propose, /commit, /extract/*, /jobs/*) are untouched;
they always operate on the active ontology. This is deliberate for
phase 3; phase 4 will add write-targeting as part of the lifecycle work.

Also adds a finalized-ontology guard: if the active ontology's manifest
has status == "finalized", write endpoints return HTTP 409. The
guard is wired up in phase 3 but has no effect yet because phase 4's
/ontologies/<name>/finalize is not yet built.

Idempotent; backs up pre-phase-3 orchestrator.

Usage:
    python scripts/phase3_patch.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "app" / "orchestrator.py"
BACKUP = ROOT / "app" / "orchestrator.py.pre_phase3"
SCHEMA = ROOT / "app" / "schema.py"
SCHEMA_BACKUP = ROOT / "app" / "schema.py.pre_phase3"
REGISTRY = ROOT / "app" / "registry.py"


# ---------------------------------------------------------------------------
# 1. Add optional `ontology` field to QueryRequest
# ---------------------------------------------------------------------------

SCHEMA_OLD = """class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None"""

SCHEMA_NEW = """class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None
    ontology: Optional[str] = None"""


# ---------------------------------------------------------------------------
# 2. Helpers to be inserted into the orchestrator (after the existing
#    _get_manager helper)
# ---------------------------------------------------------------------------

ORCH_HELPERS = '''

# ---------------------------------------------------------------------------
# Phase 3: per-request ontology resolution
# ---------------------------------------------------------------------------

def _resolve_ontology_for_read(name: str | None):
    """Return (manager, resolved_name) for a read endpoint.

    If `name` is None, returns the active manager. If `name` is given
    but unknown, raises KeyError which the caller should map to 404.
    """
    reg = _get_registry()
    if name is None:
        return reg.active_manager(), reg.active_name()
    mgr = reg.get(name)  # raises OntologyNotFoundError (a KeyError) if absent
    return mgr, name


def _ontology_query_arg():
    """Read the ?ontology=NAME query string, or None."""
    return request.args.get("ontology", None)


def _not_found_response(name: str):
    return jsonify({
        "status": "error",
        "error": f"ontology not found: {name!r}",
    }), 404


def _active_is_finalized() -> bool:
    """True iff the active ontology's manifest declares status=='finalized'.

    Used to guard write endpoints. Phase 4 is what actually sets that
    status; for now this returns False in normal operation.
    """
    try:
        reg = _get_registry()
        manifest = reg.manifest(reg.active_name())
        return manifest.get("status") == "finalized"
    except Exception:
        return False


def _finalized_guard_response():
    reg = _get_registry()
    return jsonify({
        "status": "error",
        "error": (
            f"active ontology {reg.active_name()!r} is finalized; "
            f"writes are disabled. Create a new active ontology first."
        ),
    }), 409
'''


# ---------------------------------------------------------------------------
# 3. Endpoint rewrites
# ---------------------------------------------------------------------------
# Each entry is (description, old_snippet, new_snippet). We apply via
# find-and-replace with uniqueness check to avoid surprises.


# Write endpoints that should refuse commits when the active ontology
# is finalized. We identify each by its (decorator, def_line) pair, which
# is unique across the file. The guard is inserted as the first statement
# in the function body (replacing nothing; additive).
WRITE_GUARD_SITES = [
    ('@app.post("/propose")', "def propose():"),
    ('@app.post("/commit")', "def commit():"),
    ('@app.post("/extract/prepare")', "def extract_prepare():"),
    ('@app.post("/extract/chunk")', "def extract_chunk():"),
    ('@app.post("/jobs")', "def jobs_create():"),
    ('@app.post("/jobs/<job_id>/append_claims")', "def jobs_append_claims(job_id):"),
    ('@app.post("/jobs/<job_id>/approve")', "def jobs_approve(job_id):"),
    ('@app.post("/jobs/<job_id>/feed_one")', "def jobs_feed_one(job_id):"),
]

WRITE_GUARD_BLOCK = """        # Phase 3: refuse writes against a finalized ontology.
        if _active_is_finalized():
            return _finalized_guard_response()
"""


ENDPOINT_PATCHES = [

    # --- /health now accepts ?ontology=NAME ---
    (
        "/health",
        '''    @app.get("/health")
    def health():
        try:
            mgr = _get_manager()
            return jsonify({"status": "ok", "stats": mgr.stats()})
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500''',
        '''    @app.get("/health")
    def health():
        try:
            name = _ontology_query_arg()
            try:
                mgr, resolved = _resolve_ontology_for_read(name)
            except KeyError:
                return _not_found_response(name)
            return jsonify({
                "status": "ok",
                "ontology": resolved,
                "stats": mgr.stats(),
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500''',
    ),

    # --- /graph ---
    (
        "/graph",
        '''    @app.get("/graph")
    def graph():
        mgr = _get_manager()
        return jsonify(
            {
                "stats": mgr.stats(),
                "recent_commits": recent_commits(10),
            }
        )''',
        '''    @app.get("/graph")
    def graph():
        name = _ontology_query_arg()
        try:
            mgr, resolved = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(
            {
                "ontology": resolved,
                "stats": mgr.stats(),
                # recent_commits is still global across all ontologies;
                # scoping per-ontology is deferred to phase 6.
                "recent_commits": recent_commits(10),
            }
        )''',
    ),

    # --- /graph/classes ---
    (
        "/graph/classes",
        '''    @app.get("/graph/classes")
    def graph_classes():
        return jsonify(_get_manager().list_working_classes())''',
        '''    @app.get("/graph/classes")
    def graph_classes():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(mgr.list_working_classes())''',
    ),

    # --- /graph/individuals ---
    (
        "/graph/individuals",
        '''    @app.get("/graph/individuals")
    def graph_individuals():
        return jsonify(_get_manager().list_individuals())''',
        '''    @app.get("/graph/individuals")
    def graph_individuals():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(mgr.list_individuals())''',
    ),

    # --- /graph/bfo ---
    # BFO itself is shared across all ontologies (they all import the
    # same bfo.owl), so the param is accepted but doesn't change the
    # result. We still honor the 404 on unknown name for consistency.
    (
        "/graph/bfo",
        '''    @app.get("/graph/bfo")
    def graph_bfo():
        return jsonify(_get_manager().list_bfo_classes())''',
        '''    @app.get("/graph/bfo")
    def graph_bfo():
        name = _ontology_query_arg()
        try:
            mgr, _ = _resolve_ontology_for_read(name)
        except KeyError:
            return _not_found_response(name)
        return jsonify(mgr.list_bfo_classes())''',
    ),
]


# --- /query: reads the ontology from the JSON body, not the query string ---
# This is a bigger block; handled separately.

QUERY_OLD_MARKER = '        with _lock:\n            mgr = _get_manager()\n            proposer = _get_proposer()\n            # For MVP we pass the full class/individual list as text context.'

QUERY_NEW_MARKER = '''        # Phase 3: /query can target a specific ontology via body.ontology.
        # Defaults to active if not given. Unknown name returns 404.
        try:
            mgr, resolved_ontology = _resolve_ontology_for_read(body.ontology)
        except KeyError:
            return _not_found_response(body.ontology)

        with _lock:
            proposer = _get_proposer()
            # For MVP we pass the full class/individual list as text context.'''


# ---------------------------------------------------------------------------
# 4. Write-endpoint finalized guard
# ---------------------------------------------------------------------------
# Inject an early-return into /propose, /commit, /extract/prepare,
# /extract/chunk, /jobs POST, /jobs/<id>/append_claims,
# /jobs/<id>/approve, /jobs/<id>/feed_one. For each, we add the guard
# immediately after the function definition line (before validation).




# ---------------------------------------------------------------------------
# Apply helpers
# ---------------------------------------------------------------------------


def _apply_unique_replacement(src: str, old: str, new: str, label: str) -> str:
    """Replace exactly one occurrence of `old` with `new`.

    Refuses if `old` occurs zero or multiple times. This is a safety
    measure: if the orchestrator is drifting from expectations we'd
    rather know than silently corrupt the file.
    """
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{label}] expected snippet not found")
    if count > 1:
        raise RuntimeError(f"[{label}] expected snippet matches {count} places")
    return src.replace(old, new, 1)


def _insert_guards(src: str) -> str:
    """Insert the finalized-ontology write-guard into each target function.

    For each (decorator, def_line) pair, locate the def line preceded by
    the matching decorator, and splice the guard in as the first
    statement of the function body (right after the def line).
    """
    for decorator, def_line in WRITE_GUARD_SITES:
        anchor = f"    {decorator}\n    {def_line}\n"
        count = src.count(anchor)
        if count == 0:
            # Maybe the decorator line is there but def line differs;
            # report which one.
            has_dec = (f"    {decorator}\n" in src)
            raise RuntimeError(
                f"Write-guard site not found for {decorator}. "
                f"Decorator present: {has_dec}"
            )
        if count > 1:
            raise RuntimeError(
                f"Write-guard site ambiguous ({count} matches) for {decorator}"
            )
        replacement = anchor + WRITE_GUARD_BLOCK
        src = src.replace(anchor, replacement, 1)
    return src


def _apply_schema_patch() -> bool:
    src = SCHEMA.read_text()
    if "ontology: Optional[str] = None" in src and "class QueryRequest" in src:
        # Already has the field; check it's on QueryRequest
        qr_block = src[src.index("class QueryRequest"):]
        qr_block = qr_block[:qr_block.index("\nclass ") if "\nclass " in qr_block[20:] else len(qr_block)]
        if "ontology: Optional[str]" in qr_block:
            print("  schema.py already has ontology field on QueryRequest")
            return True
    if not SCHEMA_BACKUP.exists():
        shutil.copy(SCHEMA, SCHEMA_BACKUP)
        print(f"  backed up schema.py to {SCHEMA_BACKUP.name}")
    new_src = _apply_unique_replacement(
        src, SCHEMA_OLD, SCHEMA_NEW, "QueryRequest"
    )
    SCHEMA.write_text(new_src)
    print("  schema.py: added ontology field to QueryRequest")
    return True


def _apply_orch_patch() -> bool:
    src = ORCH.read_text()

    if "def _resolve_ontology_for_read(" in src:
        print("  orchestrator.py already has phase-3 helpers, skipping")
        return True

    if not BACKUP.exists():
        shutil.copy(ORCH, BACKUP)
        print(f"  backed up orchestrator.py to {BACKUP.name}")

    # 1. Insert helpers after _get_extractor definition
    insertion_anchor = "def _get_extractor() -> ClaimExtractor:"
    # Find end of _get_extractor function by looking for the next blank line
    # followed by `def ` or a decorator
    idx = src.index(insertion_anchor)
    # Find the end of this function: look for "\n\n\n" or "\n\ndef " after idx
    # Simpler: find next occurrence of "def create_app()"
    create_app_idx = src.index("def create_app()", idx)
    # Walk back to find the preceding blank line (end of _get_extractor)
    before_create_app = src[:create_app_idx].rstrip() + "\n"
    rest = src[create_app_idx:]
    src = before_create_app + ORCH_HELPERS + "\n\n" + rest
    print("  orchestrator.py: inserted phase-3 helpers")

    # 2. Endpoint rewrites
    for label, old, new in ENDPOINT_PATCHES:
        src = _apply_unique_replacement(src, old, new, label)
        print(f"  orchestrator.py: patched {label}")

    # 3. /query body handling
    src = _apply_unique_replacement(
        src, QUERY_OLD_MARKER, QUERY_NEW_MARKER, "/query body"
    )
    print("  orchestrator.py: patched /query body handling")

    # 4. Write-endpoint guards
    src = _insert_guards(src)
    print("  orchestrator.py: inserted finalized-ontology write guards")

    ORCH.write_text(src)
    return True


def _compile_check() -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(ORCH), str(SCHEMA)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"COMPILE ERROR:\n{r.stderr}")
        return False
    return True


def _rollback() -> None:
    if BACKUP.exists():
        shutil.copy(BACKUP, ORCH)
        print(f"  restored orchestrator.py from backup")
    if SCHEMA_BACKUP.exists():
        shutil.copy(SCHEMA_BACKUP, SCHEMA)
        print(f"  restored schema.py from backup")


def main() -> int:
    print("Phase 3 patch: per-request ontology selection on read endpoints")

    try:
        if not _apply_schema_patch():
            return 1
        if not _apply_orch_patch():
            return 1
    except Exception as e:
        print(f"PATCH FAILED: {e}")
        _rollback()
        return 1

    if not _compile_check():
        print("Rolling back ...")
        _rollback()
        return 1

    print("COMPILES")
    print()
    print("Next: restart Flask and run smoke tests.")
    print("  python run.py")
    print()
    print("Smoke tests:")
    print("  curl -s http://127.0.0.1:5000/health | python3 -m json.tool")
    print("  curl -s 'http://127.0.0.1:5000/health?ontology=SOoL_v1' | python3 -m json.tool")
    print("  curl -s 'http://127.0.0.1:5000/health?ontology=nonexistent' -o /dev/null -w '%{http_code}\\n'")
    print("  # expected: 200, 200 (same stats), 404")
    return 0


if __name__ == "__main__":
    sys.exit(main())
