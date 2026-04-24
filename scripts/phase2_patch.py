"""Phase 2 patch: introduce OntologyRegistry and route orchestrator through it.

Creates app/registry.py, updates app/orchestrator.py to use
registry.active_manager() in place of the module-level _manager singleton.

No external API changes. Before and after phase 2, /health returns the same
stats, /query returns the same answers, and the UI is unaware of the refactor.

Safety:
- Backs up app/orchestrator.py as app/orchestrator.py.pre_phase2
- Idempotent: running twice is a no-op
- Rolls back automatically if the modified orchestrator fails to compile

Usage:
    python scripts/phase2_patch.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REGISTRY_TARGET = ROOT / "app" / "registry.py"
ORCH_TARGET = ROOT / "app" / "orchestrator.py"
ORCH_BACKUP = ROOT / "app" / "orchestrator.py.pre_phase2"


REGISTRY_CONTENTS = '''"""Ontology registry: discovers and loads all ontologies under the library.

Replaces the phase-1 model in which a single OntologyManager was a module
global in orchestrator.py. After phase 2, every manager lookup goes through
the registry, which tracks (a) the set of loaded ontologies and (b) the
currently active one.

Layout assumption (established in phase 1):

    ontology/
      bfo.owl
      active.txt                     <- name of the active ontology
      library/
        <name>/
          working.owl
          manifest.json
          seed/
            *.ttl

Each subdirectory of library/ that contains manifest.json is treated as
a loadable ontology. Directories starting with "_" (e.g. _archive/) are
ignored.

Design notes:
- Eager loading. Every ontology is loaded at registry construction time.
  This is fine for a library of a few ontologies but becomes expensive past
  ten. Lazy loading is a future phase (2.5 or later) if needed.
- The registry is constructed once at app startup and held by the
  orchestrator. It is not global module state.
- Thread safety: read access (active_manager, get, list_ontologies) is
  safe to call concurrently after construction. Mutations added in
  phase 4 (create/finalize/activate) will acquire the orchestrator lock.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .ontology_manager import OntologyManager


class OntologyNotFoundError(KeyError):
    """Raised when a lookup for an ontology name fails."""


class OntologyRegistry:
    def __init__(
        self,
        bfo_path: Path,
        library_root: Path,
        active_name: str,
    ):
        self._bfo_path = Path(bfo_path)
        self._library_root = Path(library_root)
        self._active_name = active_name
        self._managers: dict[str, OntologyManager] = {}
        self._manifests: dict[str, dict] = {}
        self._discover_and_load()

    # ------------------------------------------------------------------
    # Construction-time discovery
    # ------------------------------------------------------------------

    def _discover_and_load(self) -> None:
        if not self._library_root.exists():
            raise FileNotFoundError(
                f"Library root not found: {self._library_root}. "
                f"Did phase 1 migration run?"
            )

        for entry in sorted(self._library_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_"):
                # _archive/ and future underscore-prefixed dirs are hidden
                continue
            manifest_path = entry / "manifest.json"
            if not manifest_path.exists():
                print(f"[registry] skipping {entry.name}: no manifest.json")
                continue
            try:
                self._load_one(entry)
            except Exception as e:
                print(f"[registry] failed to load {entry.name}: {e}")

        if self._active_name not in self._managers:
            raise RuntimeError(
                f"Active ontology '{self._active_name}' not found in library. "
                f"Loaded: {sorted(self._managers)}"
            )

    def _load_one(self, entry: Path) -> None:
        name = entry.name
        manifest = json.loads((entry / "manifest.json").read_text())

        working_path = entry / "working.owl"
        if not working_path.exists():
            raise FileNotFoundError(f"{working_path} missing")

        # Pick a seed file to pass to OntologyManager. The manager's
        # _apply_seed scans the seed directory for all .ttl files, so the
        # specific path only matters for resolving the seed directory.
        seed_dir = entry / "seed"
        seed_path: Optional[Path] = None
        if seed_dir.exists():
            ttls = sorted(seed_dir.glob("*.ttl"))
            if ttls:
                seed_path = ttls[0]

        mgr = OntologyManager(
            bfo_path=self._bfo_path,
            working_path=working_path,
            seed_path=seed_path,
        )
        self._managers[name] = mgr
        self._manifests[name] = manifest
        print(
            f"[registry] loaded {name}: "
            f"{len(list(mgr.working.classes()))} classes, "
            f"{len(list(mgr.working.individuals()))} individuals"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def active_name(self) -> str:
        return self._active_name

    def active_manager(self) -> OntologyManager:
        """Return the OntologyManager for the active (writable) ontology."""
        return self._managers[self._active_name]

    def get(self, name: str) -> OntologyManager:
        """Return the manager for any loaded ontology. Raises if unknown."""
        if name not in self._managers:
            raise OntologyNotFoundError(name)
        return self._managers[name]

    def manifest(self, name: str) -> dict:
        if name not in self._manifests:
            raise OntologyNotFoundError(name)
        return dict(self._manifests[name])

    def list_ontologies(self) -> list[dict]:
        """List all loaded ontologies with manifest + live stats."""
        result = []
        for name, mgr in sorted(self._managers.items()):
            manifest = dict(self._manifests[name])
            live_stats = {
                "classes": len(list(mgr.working.classes())),
                "individuals": len(list(mgr.working.individuals())),
            }
            result.append({
                "name": name,
                "active": name == self._active_name,
                "manifest": manifest,
                "stats": live_stats,
            })
        return result
'''


# The new orchestrator. Structurally identical to the existing one, but
# references the registry rather than a direct manager singleton. We write
# out the file fresh rather than doing a series of fragile sed-edits.

NEW_ORCHESTRATOR = '''"""Flask orchestrator.

Endpoints:
  POST /propose       utterance -> structured proposal + reasoner verdict
  POST /commit        confirmed proposal -> write to working ontology + git
  GET  /graph         current ontology stats + recent commits
  GET  /graph/classes list working classes with parents
  GET  /graph/individuals  list named individuals with types
  POST /query         grounded Q&A against the working ontology
  GET  /session/<id>  replay the event log
  GET  /health        liveness
"""
from __future__ import annotations

import threading
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, request
from flask_cors import CORS
from pydantic import ValidationError

from . import config
from . import jobs as jobs_store
from .extractor import ClaimExtractor, chunk_text
from .llm_proposer import LLMProposer
from .ontology_manager import OntologyManager
from .registry import OntologyRegistry
from .schema import (
    CommitRequest,
    Proposal,
    ProposeRequest,
    QueryRequest,
    QueryResponse,
)
from .storage import (
    git_commit_working_ontology,
    load_session,
    log_event,
    recent_commits,
)


# Global single-instance state. The orchestrator is not designed for
# concurrent writers; a single lock serializes proposal/commit operations.
_lock = threading.Lock()
_registry: OntologyRegistry | None = None
_proposer: LLMProposer | None = None
_extractor: ClaimExtractor | None = None
# Small in-memory store of the latest proposal per id so /commit can round-trip
_proposal_cache: dict[str, Proposal] = {}


def _get_registry() -> OntologyRegistry:
    global _registry
    if _registry is None:
        active = config.active_ontology_name()
        if active is None:
            raise RuntimeError(
                "No active ontology name in config. Did phase 1 run?"
            )
        _registry = OntologyRegistry(
            bfo_path=config.BFO_PATH,
            library_root=config.LIBRARY_ROOT,
            active_name=active,
        )
    return _registry


def _get_manager() -> OntologyManager:
    """Backward-compatible shortcut to the active manager.

    Preserves the method name used throughout this module so the phase 2
    diff stays minimal. Phase 3 will add a dispatcher that honors an
    optional ontology-name parameter.
    """
    return _get_registry().active_manager()


def _get_proposer() -> LLMProposer:
    global _proposer
    if _proposer is None:
        _proposer = LLMProposer()
    return _proposer


def _get_extractor() -> ClaimExtractor:
    global _extractor
    if _extractor is None:
        _extractor = ClaimExtractor()
    return _extractor


def create_app() -> Flask:
    app = Flask(__name__)
    CORS(app)

    @app.get("/health")
    def health():
        try:
            mgr = _get_manager()
            return jsonify({"status": "ok", "stats": mgr.stats()})
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.get("/ontologies")
    def list_ontologies():
        """Phase 2 preview: read-only listing of the ontology library.

        Added in phase 2 so that post-migration we can inspect the registry
        state via curl. UI integration arrives in phase 5.
        """
        try:
            reg = _get_registry()
            return jsonify({
                "active": reg.active_name(),
                "ontologies": reg.list_ontologies(),
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    # All endpoints below are copied verbatim from pre-phase-2. They
    # reference _get_manager(), which now routes through the registry.
'''


def _read_orchestrator_body_after_health() -> str:
    """Extract the post-/health block from the pre-phase-2 orchestrator.

    We rewrite create_app() up through the /health endpoint and the new
    /ontologies endpoint, then append every subsequent endpoint unchanged.
    """
    src = ORCH_TARGET.read_text()

    # Find the line that ends the /health route's return, then pick up
    # from the next @app.post / @app.get onwards.
    lines = src.splitlines()
    # Locate the /health function and then its return statement.
    in_health = False
    health_return_idx = None
    for i, line in enumerate(lines):
        if "@app.get(\"/health\")" in line:
            in_health = True
        if in_health and line.strip().startswith("return jsonify") and "status" in line and "error" in line:
            # This is the error return line within /health
            health_return_idx = i
        if in_health and health_return_idx is not None and i > health_return_idx:
            # Next non-blank, non-indented-decorator line ends /health block
            if line.startswith("    @app."):
                return "\n".join(lines[i:])
    raise RuntimeError("Could not find end of /health in existing orchestrator")


def _write_new_orchestrator() -> None:
    tail = _read_orchestrator_body_after_health()
    full = NEW_ORCHESTRATOR + "\n" + tail
    ORCH_TARGET.write_text(full)


def _ensure_backups() -> None:
    if not ORCH_BACKUP.exists():
        shutil.copy(ORCH_TARGET, ORCH_BACKUP)
        print(f"Backed up orchestrator to {ORCH_BACKUP.name}")


def _compile_check() -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile",
         str(REGISTRY_TARGET), str(ORCH_TARGET)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"COMPILE ERROR:\n{r.stderr}")
        return False
    return True


def _already_applied() -> bool:
    """Detect whether phase 2 has already been applied."""
    if not REGISTRY_TARGET.exists():
        return False
    orch_src = ORCH_TARGET.read_text()
    return "from .registry import OntologyRegistry" in orch_src


def main() -> int:
    if _already_applied():
        print("Phase 2 already applied. Skipping.")
        return 0

    print("Phase 2 patch: installing registry and updating orchestrator")

    # 1. Back up orchestrator
    _ensure_backups()

    # 2. Write registry module
    REGISTRY_TARGET.write_text(REGISTRY_CONTENTS)
    print(f"Wrote {REGISTRY_TARGET}")

    # 3. Rewrite orchestrator
    try:
        _write_new_orchestrator()
        print(f"Wrote {ORCH_TARGET}")
    except Exception as e:
        print(f"Failed to rewrite orchestrator: {e}")
        print(f"Restoring from backup ...")
        shutil.copy(ORCH_BACKUP, ORCH_TARGET)
        return 1

    # 4. Compile check
    if not _compile_check():
        print("Restoring orchestrator from backup ...")
        shutil.copy(ORCH_BACKUP, ORCH_TARGET)
        REGISTRY_TARGET.unlink()
        return 1

    print("COMPILES")
    print()
    print("Next: restart Flask and run the phase 2 smoke tests.")
    print("  python run.py")
    print()
    print("Smoke tests:")
    print("  curl -s http://127.0.0.1:5000/health | python3 -m json.tool")
    print("  curl -s http://127.0.0.1:5000/ontologies | python3 -m json.tool")
    print("  # and a query from the UI to confirm nothing broke")
    return 0


if __name__ == "__main__":
    sys.exit(main())
