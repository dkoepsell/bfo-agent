"""Ontology registry: discovers and loads all ontologies under the library.

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
