"""Phase 4 patch: ontology lifecycle endpoints.

Adds four endpoints for managing the library:

  POST   /ontologies
         Body: {name, description, source_text?, author?, clone_seeds_from?}
         Creates a new ontology in library/<name>/. Does NOT activate it.
         Returns the new manifest. Status: "inactive".

  POST   /ontologies/<name>/activate
         Makes <name> the active writable ontology.
         Writes ontology/active.txt. Git-commits the change.
         Returns {active, previous_active}.

  POST   /ontologies/<name>/finalize
         Sets manifest status to "finalized". Writes to a finalized
         ontology are refused (guard wired in phase 3).
         Idempotent. Returns updated manifest.

  DELETE /ontologies/<name>
         Removes a non-active, non-finalized ontology.
         Refuses to delete the active or any finalized ontology.
         Returns 204 on success.

Design:
  - Separate atomic primitives (option A from design discussion).
  - UI in phase 5 composes these for "finalize current and start fresh".
  - All lifecycle mutations acquire the orchestrator _lock to serialize
    against in-flight propose/commit operations.
  - After each mutation, registry is reloaded so endpoints see the new
    state immediately.
  - Git commits record each lifecycle change for audit.

Idempotent; backs up pre-phase-4 orchestrator, registry, storage.

Usage:
    python scripts/phase4_patch.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "app" / "orchestrator.py"
ORCH_BACKUP = ROOT / "app" / "orchestrator.py.pre_phase4"
REGISTRY = ROOT / "app" / "registry.py"
REGISTRY_BACKUP = ROOT / "app" / "registry.py.pre_phase4"
STORAGE = ROOT / "app" / "storage.py"
STORAGE_BACKUP = ROOT / "app" / "storage.py.pre_phase4"


# ---------------------------------------------------------------------------
# Registry additions: lifecycle helpers and reload
# ---------------------------------------------------------------------------

REGISTRY_LIFECYCLE_ADDITIONS = '''

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
        tmp.write_text(name + "\\n")
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
'''


# ---------------------------------------------------------------------------
# Storage addition: git commit a library change
# ---------------------------------------------------------------------------

STORAGE_ADDITION = '''

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
'''


# ---------------------------------------------------------------------------
# Orchestrator additions: four new endpoints
# ---------------------------------------------------------------------------

ORCH_IMPORT_UPDATE_OLD = '''from .storage import (
    git_commit_working_ontology,
    load_session,
    log_event,
    recent_commits,
)'''

ORCH_IMPORT_UPDATE_NEW = '''from .storage import (
    git_commit_library_change,
    git_commit_working_ontology,
    load_session,
    log_event,
    recent_commits,
)'''


# The lifecycle endpoints are inserted immediately after the /ontologies
# (list) endpoint added in phase 2. The anchor is the closing of that
# endpoint's try/except block.

ORCH_LIFECYCLE_ANCHOR = '''    @app.get("/ontologies")
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
'''

ORCH_LIFECYCLE_REPLACEMENT = '''    @app.get("/ontologies")
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

    # ------------------------------------------------------------------
    # Phase 4: lifecycle endpoints (create, activate, finalize, delete)
    # ------------------------------------------------------------------

    @app.post("/ontologies")
    def create_ontology():
        """Create a new ontology in the library.

        Body: {
          "name": "Peirce_categorial",         # required
          "description": "...",                 # required
          "source_text": "...",                 # optional
          "author": "...",                      # optional
          "clone_seeds_from": "SOoL_v1"         # optional; default active
        }

        Does NOT activate. Use /ontologies/<name>/activate separately.
        """
        try:
            body = request.get_json(force=True) or {}
            name = (body.get("name") or "").strip()
            description = (body.get("description") or "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            if not description:
                return jsonify({"error": "description required"}), 400

            with _lock:
                reg = _get_registry()
                try:
                    manifest = reg.create(
                        name=name,
                        description=description,
                        source_text=body.get("source_text"),
                        author=body.get("author"),
                        clone_seeds_from=body.get("clone_seeds_from"),
                    )
                except ValueError as e:
                    return jsonify({"error": str(e)}), 400
                except KeyError as e:
                    return jsonify({"error": f"not found: {e}"}), 404

                lib_entry = config.LIBRARY_ROOT / name
                git_commit_library_change(
                    [lib_entry],
                    f"Create ontology {name}",
                )

            return jsonify({
                "name": name,
                "manifest": manifest,
                "status": "created",
            }), 201
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/ontologies/<name>/activate")
    def activate_ontology(name):
        """Make <name> the active writable ontology."""
        try:
            with _lock:
                reg = _get_registry()
                try:
                    new_active, previous = reg.activate(name)
                except KeyError:
                    return _not_found_response(name)

                if new_active != previous:
                    active_file = config.LIBRARY_ROOT.parent / "active.txt"
                    git_commit_library_change(
                        [active_file],
                        f"Activate ontology {new_active} (was {previous})",
                    )

            return jsonify({
                "active": new_active,
                "previous_active": previous,
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/ontologies/<name>/finalize")
    def finalize_ontology(name):
        """Mark <name> as finalized (read-only). Idempotent."""
        try:
            with _lock:
                reg = _get_registry()
                try:
                    manifest = reg.finalize(name)
                except KeyError:
                    return _not_found_response(name)

                manifest_path = config.LIBRARY_ROOT / name / "manifest.json"
                git_commit_library_change(
                    [manifest_path],
                    f"Finalize ontology {name}",
                )

            return jsonify({
                "name": name,
                "manifest": manifest,
                "status": "finalized",
            })
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.delete("/ontologies/<name>")
    def delete_ontology(name):
        """Delete a non-active, non-finalized ontology."""
        try:
            with _lock:
                reg = _get_registry()
                try:
                    reg.delete(name)
                except KeyError:
                    return _not_found_response(name)
                except ValueError as e:
                    return jsonify({"error": str(e)}), 409

                git_commit_library_change(
                    [config.LIBRARY_ROOT],
                    f"Delete ontology {name}",
                )

            return ("", 204)
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500
'''


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_unique_replacement(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{label}] expected snippet not found")
    if count > 1:
        raise RuntimeError(f"[{label}] expected snippet matches {count} places")
    return src.replace(old, new, 1)


def _registry_already_patched(src: str) -> bool:
    return "def reload(self)" in src and "def create(" in src


def _apply_registry_patch() -> None:
    src = REGISTRY.read_text()
    if _registry_already_patched(src):
        print("  registry.py already has phase-4 methods, skipping")
        return
    if not REGISTRY_BACKUP.exists():
        shutil.copy(REGISTRY, REGISTRY_BACKUP)
        print(f"  backed up registry.py")

    # Find end of class (the last line that's part of list_ontologies)
    # and insert before it. Easier: find the very end of the file, which
    # should be the end of list_ontologies method.
    stripped = src.rstrip()
    new_src = stripped + REGISTRY_LIFECYCLE_ADDITIONS + "\n"
    REGISTRY.write_text(new_src)
    print("  registry.py: added lifecycle methods")


def _storage_already_patched(src: str) -> bool:
    return "def git_commit_library_change(" in src


def _apply_storage_patch() -> None:
    src = STORAGE.read_text()
    if _storage_already_patched(src):
        print("  storage.py already has library-commit helper, skipping")
        return
    if not STORAGE_BACKUP.exists():
        shutil.copy(STORAGE, STORAGE_BACKUP)
        print(f"  backed up storage.py")
    STORAGE.write_text(src.rstrip() + STORAGE_ADDITION + "\n")
    print("  storage.py: added git_commit_library_change")


def _orch_already_patched(src: str) -> bool:
    return "def create_ontology():" in src


def _apply_orch_patch() -> None:
    src = ORCH.read_text()
    if _orch_already_patched(src):
        print("  orchestrator.py already has phase-4 endpoints, skipping")
        return
    if not ORCH_BACKUP.exists():
        shutil.copy(ORCH, ORCH_BACKUP)
        print(f"  backed up orchestrator.py")

    # 1. Import git_commit_library_change
    src = _apply_unique_replacement(
        src, ORCH_IMPORT_UPDATE_OLD, ORCH_IMPORT_UPDATE_NEW,
        "storage import",
    )

    # 2. Insert lifecycle endpoints after /ontologies (list) endpoint
    src = _apply_unique_replacement(
        src, ORCH_LIFECYCLE_ANCHOR, ORCH_LIFECYCLE_REPLACEMENT,
        "lifecycle insertion",
    )

    ORCH.write_text(src)
    print("  orchestrator.py: added four lifecycle endpoints")


def _compile_check() -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile",
         str(ORCH), str(REGISTRY), str(STORAGE)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"COMPILE ERROR:\n{r.stderr}")
        return False
    return True


def _rollback():
    for backup, target in [
        (ORCH_BACKUP, ORCH),
        (REGISTRY_BACKUP, REGISTRY),
        (STORAGE_BACKUP, STORAGE),
    ]:
        if backup.exists():
            shutil.copy(backup, target)
            print(f"  restored {target.name}")


def main() -> int:
    print("Phase 4 patch: lifecycle endpoints")
    try:
        _apply_registry_patch()
        _apply_storage_patch()
        _apply_orch_patch()
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
    print()
    print("Smoke tests (see accompanying phase4_smoke_test.sh if present):")
    print("  # 1. Create a sandbox ontology")
    print("  curl -s -X POST http://127.0.0.1:5000/ontologies \\\\")
    print("    -H 'Content-Type: application/json' \\\\")
    print("    -d '{\"name\":\"sandbox\",\"description\":\"smoke test\"}'")
    print()
    print("  # 2. Confirm it appears in the listing")
    print("  curl -s http://127.0.0.1:5000/ontologies | python3 -m json.tool")
    print()
    print("  # 3. Activate it")
    print("  curl -s -X POST http://127.0.0.1:5000/ontologies/sandbox/activate")
    print()
    print("  # 4. Switch back to SOoL_v1")
    print("  curl -s -X POST http://127.0.0.1:5000/ontologies/SOoL_v1/activate")
    print()
    print("  # 5. Delete sandbox")
    print("  curl -s -X DELETE http://127.0.0.1:5000/ontologies/sandbox -w '%{http_code}\\n'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
