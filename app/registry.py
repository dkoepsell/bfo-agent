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
        # working.owl may not exist yet for freshly-created ontologies;
        # OntologyManager will bootstrap it from BFO + seeds on first load.
        # We only require the containing directory to exist.

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

    # ------------------------------------------------------------------
    # Phase 4: lifecycle mutations
    # ------------------------------------------------------------------

    def reload(self) -> None:
        """Re-scan the library from disk and rebuild the manager pool.

        Called after lifecycle mutations (create/activate/delete) so
        endpoints see current on-disk state.
        """
        self._managers = {}
        self._manifests = {}
        self._discover_and_load()

    def create(
        self,
        name: str,
        description: str,
        source_text: str | None = None,
        author: str | None = None,
        clone_seeds_from: str | None = None,
    ) -> dict:
        """Create a new ontology directory with seeds and manifest.

        Does not activate. The newly created ontology is loaded into
        the registry before return.

        Raises:
          ValueError: if name is invalid or already exists.
          KeyError: if clone_seeds_from is given but refers to an
                    unknown ontology.
        """
        from datetime import datetime, timezone
        import json
        import re
        import shutil as _shutil

        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError(
                f"Invalid ontology name {name!r}. Must match "
                f"[A-Za-z][A-Za-z0-9_-]{{0,63}}."
            )

        target = self._library_root / name
        if target.exists():
            raise ValueError(f"Ontology {name!r} already exists.")

        # Determine seed source. Default: active ontology's seeds.
        seed_source_name = clone_seeds_from or self._active_name
        if seed_source_name not in self._managers:
            raise KeyError(
                f"Seed source {seed_source_name!r} not in library."
            )
        seed_src = self._library_root / seed_source_name / "seed"

        # Lay out the new ontology directory.
        target.mkdir(parents=True)
        (target / "sessions").mkdir()
        (target / "jobs").mkdir()
        new_seed = target / "seed"
        if seed_src.exists():
            _shutil.copytree(seed_src, new_seed)
        else:
            new_seed.mkdir()

        manifest = {
            "name": name,
            "description": description,
            "source_text": source_text,
            "author": author,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "inactive",
            "seeded_from": seed_source_name,
            "stats": {"note": "bootstrapped from seeds only"},
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2))

        # Instantiate a manager, which will bootstrap working.owl from
        # BFO + seeds since working.owl does not yet exist.
        self._load_one(target)

        return dict(manifest)

    def activate(self, name: str) -> tuple[str, str]:
        """Mark <name> as the active ontology.

        Writes ontology/active.txt and updates the in-memory state.
        Returns (new_active, previous_active). If already active, both
        values are equal (idempotent).

        Raises KeyError if name is not in the library.
        """
        if name not in self._managers:
            raise OntologyNotFoundError(name)
        previous = self._active_name
        if previous == name:
            return (name, previous)

        # Write active.txt atomically
        active_file = self._library_root.parent / "active.txt"
        tmp = active_file.with_suffix(".tmp")
        tmp.write_text(name + "\n")
        tmp.replace(active_file)

        self._active_name = name
        return (name, previous)

    def finalize(self, name: str) -> dict:
        """Set manifest status to 'finalized'. Idempotent.

        Raises KeyError if name is unknown.
        """
        import json

        if name not in self._managers:
            raise OntologyNotFoundError(name)

        manifest = dict(self._manifests[name])
        manifest["status"] = "finalized"
        from datetime import datetime, timezone
        manifest["finalized_at"] = datetime.now(timezone.utc).isoformat()

        manifest_path = self._library_root / name / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))

        self._manifests[name] = manifest
        return dict(manifest)

    def delete(self, name: str) -> None:
        """Remove an ontology from the library.

        Refuses to delete the active ontology or any finalized ontology.
        Raises ValueError in those cases, KeyError if name is unknown.
        """
        import shutil as _shutil

        if name not in self._managers:
            raise OntologyNotFoundError(name)
        if name == self._active_name:
            raise ValueError(
                f"Cannot delete active ontology {name!r}. "
                f"Activate a different ontology first."
            )
        if self._manifests[name].get("status") == "finalized":
            raise ValueError(
                f"Cannot delete finalized ontology {name!r}. "
                f"Finalized artifacts are preserved for audit."
            )

        target = self._library_root / name
        if target.exists():
            _shutil.rmtree(target)
        self._managers.pop(name, None)
        self._manifests.pop(name, None)

    # ------------------------------------------------------------------
    # Phase 7: import existing OWL as a new ontology
    # ------------------------------------------------------------------

    def preview_owl(self, file_path: Path) -> dict:
        """Inspect an OWL file without committing it.

        Returns a structured report with class/individual counts, BFO
        alignment summary, and HermiT consistency under standard BFO
        axioms (using the active ontology's seeds).

        The file_path can point anywhere readable; nothing is moved.
        """
        from owlready2 import World
        import json as _json

        report = {
            "filename": file_path.name,
            "size_bytes": file_path.stat().st_size,
            "loaded": False,
            "classes": 0,
            "individuals": 0,
            "object_properties": 0,
            "data_properties": 0,
            "annotation_properties": 0,
            "base_iri": None,
            "bfo_aligned_classes": 0,
            "consistent": None,
            "consistency_detail": None,
            "warnings": [],
        }

        try:
            w = World()
            # Load BFO first so cross-references can resolve
            w.get_ontology(str(self._bfo_path)).load()
            onto = w.get_ontology(file_path.as_uri()).load()
        except Exception as e:
            report["warnings"].append(f"Failed to load: {e}")
            return report

        report["loaded"] = True
        report["base_iri"] = str(onto.base_iri) if onto.base_iri else None

        classes = list(onto.classes())
        individuals = list(onto.individuals())
        report["classes"] = len(classes)
        report["individuals"] = len(individuals)
        report["object_properties"] = len(list(onto.object_properties()))
        report["data_properties"] = len(list(onto.data_properties()))
        report["annotation_properties"] = len(list(onto.annotation_properties()))

        # BFO alignment: count classes whose ancestor chain includes a
        # BFO_xxxxxxx class.
        bfo_count = 0
        for c in classes:
            ancestors = c.ancestors() if hasattr(c, "ancestors") else set()
            for a in ancestors:
                name = getattr(a, "name", "") or ""
                if name.startswith("BFO_"):
                    bfo_count += 1
                    break
        report["bfo_aligned_classes"] = bfo_count

        # Consistency check using owlready2/HermiT
        try:
            from owlready2 import sync_reasoner_hermit
            with onto:
                sync_reasoner_hermit(w, infer_property_values=False,
                                    debug=0)
            report["consistent"] = True
        except Exception as e:
            report["consistent"] = False
            report["consistency_detail"] = str(e)[:500]

        # Warnings
        if report["classes"] == 0:
            report["warnings"].append("Ontology has no classes")
        if bfo_count == 0 and report["classes"] > 0:
            report["warnings"].append(
                "No BFO-aligned classes detected. Imported ontology will "
                "load but lacks formal BFO grounding."
            )
        elif bfo_count < report["classes"] / 2:
            report["warnings"].append(
                f"Only {bfo_count}/{report['classes']} classes have BFO "
                f"ancestors. Coverage is partial."
            )
        if not report["consistent"]:
            report["warnings"].append("Ontology is inconsistent under HermiT")

        return report

    def import_from_file(
        self,
        file_path: Path,
        name: str,
        description: str,
        source_text: str | None = None,
        author: str | None = None,
        clone_seeds_from: str | None = None,
    ) -> dict:
        """Create a new library entry whose working.owl is the imported file.

        Validates name and uniqueness, copies the file in, optionally
        clones seeds from another library entry (default: active),
        writes manifest, and registers.

        Raises ValueError on bad name or duplicate, KeyError on bad
        clone source.
        """
        from datetime import datetime, timezone
        import json as _json
        import re
        import shutil as _shutil

        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", name):
            raise ValueError(
                f"Invalid ontology name {name!r}. Must match "
                f"[A-Za-z][A-Za-z0-9_-]{{0,63}}."
            )

        target = self._library_root / name
        if target.exists():
            raise ValueError(f"Ontology {name!r} already exists.")

        if not file_path.exists():
            raise FileNotFoundError(f"Source file not found: {file_path}")

        seed_source_name = clone_seeds_from or self._active_name
        if seed_source_name not in self._managers:
            raise KeyError(
                f"Seed source {seed_source_name!r} not in library."
            )
        seed_src = self._library_root / seed_source_name / "seed"

        # Lay out the new ontology directory.
        target.mkdir(parents=True)
        (target / "sessions").mkdir()
        (target / "jobs").mkdir()
        new_seed = target / "seed"
        if seed_src.exists():
            _shutil.copytree(seed_src, new_seed)
        else:
            new_seed.mkdir()

        # Copy the OWL into place as working.owl
        _shutil.copy(file_path, target / "working.owl")

        # Run the preview again on the now-canonical location to get
        # final stats for the manifest.
        preview = self.preview_owl(target / "working.owl")

        manifest = {
            "name": name,
            "description": description,
            "source_text": source_text,
            "author": author,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "inactive",
            "imported_from": file_path.name,
            "import_preview": {
                "classes": preview["classes"],
                "individuals": preview["individuals"],
                "bfo_aligned_classes": preview["bfo_aligned_classes"],
                "consistent": preview["consistent"],
                "warnings": preview["warnings"],
            },
            "stats": {
                "classes": preview["classes"],
                "individuals": preview["individuals"],
            },
        }
        (target / "manifest.json").write_text(_json.dumps(manifest, indent=2))

        # Register in the live registry. Use _load_one which now
        # tolerates an existing working.owl (post phase-4 fix).
        self._load_one(target)

        return dict(manifest)

