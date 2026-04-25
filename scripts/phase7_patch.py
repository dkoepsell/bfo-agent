"""Phase 7 patch: import an existing OWL file as a new named ontology.

Adds a two-step import flow:

  POST /ontologies/preview-import   (multipart: file)
    Returns a structured preview of what's in the OWL:
      - class count, individual count, property count
      - BFO alignment summary (how many classes have BFO ancestors)
      - HermiT consistency under standard BFO disjointness
      - Detected base IRI / namespace
      - Any warnings (no BFO, inconsistent, malformed)

  POST /ontologies/import           (multipart: file, name, description, ...)
    Commits the upload as a new library entry.

The UI gets a third button in the dropdown action row: "Import OWL...",
opening a modal that uploads, previews, then commits on confirm.

Idempotent; backs up modified files.

Usage:
    python scripts/phase7_patch.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORCH = ROOT / "app" / "orchestrator.py"
ORCH_BACKUP = ROOT / "app" / "orchestrator.py.pre_phase7"
REGISTRY = ROOT / "app" / "registry.py"
REGISTRY_BACKUP = ROOT / "app" / "registry.py.pre_phase7"
CLIENT = ROOT / "client" / "index.html"
CLIENT_BACKUP = ROOT / "client" / "index.html.pre_phase7"


# ---------------------------------------------------------------------------
# Registry: add import_from_file method
# ---------------------------------------------------------------------------

REGISTRY_IMPORT_METHOD = '''

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
'''


# ---------------------------------------------------------------------------
# Orchestrator: two new endpoints
# ---------------------------------------------------------------------------

ORCH_IMPORT_ENDPOINTS = '''

    @app.post("/ontologies/preview-import")
    def preview_import_ontology():
        """Inspect an uploaded OWL file. Does NOT commit."""
        try:
            if "file" not in request.files:
                return jsonify({"error": "missing file upload"}), 400
            uploaded = request.files["file"]
            if not uploaded.filename:
                return jsonify({"error": "empty filename"}), 400

            import tempfile
            from pathlib import Path as _Path
            with tempfile.NamedTemporaryFile(
                suffix=_Path(uploaded.filename).suffix or ".owl",
                delete=False,
            ) as tmp:
                uploaded.save(tmp.name)
                tmp_path = _Path(tmp.name)

            try:
                reg = _get_registry()
                report = reg.preview_owl(tmp_path)
                return jsonify(report)
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500

    @app.post("/ontologies/import")
    def import_ontology():
        """Import an OWL file as a new library entry."""
        try:
            if "file" not in request.files:
                return jsonify({"error": "missing file upload"}), 400
            uploaded = request.files["file"]
            if not uploaded.filename:
                return jsonify({"error": "empty filename"}), 400

            name = (request.form.get("name") or "").strip()
            description = (request.form.get("description") or "").strip()
            if not name:
                return jsonify({"error": "name required"}), 400
            if not description:
                return jsonify({"error": "description required"}), 400

            import tempfile
            from pathlib import Path as _Path
            with tempfile.NamedTemporaryFile(
                suffix=_Path(uploaded.filename).suffix or ".owl",
                delete=False,
            ) as tmp:
                uploaded.save(tmp.name)
                tmp_path = _Path(tmp.name)

            try:
                with _lock:
                    reg = _get_registry()
                    try:
                        manifest = reg.import_from_file(
                            file_path=tmp_path,
                            name=name,
                            description=description,
                            source_text=request.form.get("source_text") or None,
                            author=request.form.get("author") or None,
                            clone_seeds_from=request.form.get("clone_seeds_from") or None,
                        )
                    except ValueError as e:
                        return jsonify({"error": str(e)}), 400
                    except KeyError as e:
                        return jsonify({"error": f"not found: {e}"}), 404

                    lib_entry = config.LIBRARY_ROOT / name
                    git_commit_library_change(
                        [lib_entry],
                        f"Import ontology {name} (from {uploaded.filename})",
                    )

                return jsonify({
                    "name": name,
                    "manifest": manifest,
                    "status": "imported",
                }), 201
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
        except Exception as e:
            return jsonify({"status": "error", "error": str(e)}), 500
'''


# Insertion anchor: just before the @app.delete line for /ontologies/<n>
# Note: the route variable is spelled out as <name> in the file; some terminal
# renderings collapse it to <n>. We construct the anchor explicitly.
_ROUTE_VAR = "<" + "name" + ">"
ORCH_ANCHOR_OLD = (
    f'    @app.delete("/ontologies/{_ROUTE_VAR}")\n'
    f'    def delete_ontology(name):'
)
ORCH_ANCHOR_NEW = ORCH_IMPORT_ENDPOINTS + (
    f'\n\n    @app.delete("/ontologies/{_ROUTE_VAR}")\n'
    f'    def delete_ontology(name):'
)


# ---------------------------------------------------------------------------
# Client: add Import button + modal
# ---------------------------------------------------------------------------

CLIENT_OP_ACTIONS_OLD = '''    <div class="op-actions">
      <button class="secondary" id="btn-new-ontology">New ontology...</button>
      <button class="secondary" id="btn-finalize-active">Finalize current</button>
    </div>'''

CLIENT_OP_ACTIONS_NEW = '''    <div class="op-actions">
      <button class="secondary" id="btn-new-ontology">New ontology...</button>
      <button class="secondary" id="btn-import-ontology">Import OWL...</button>
      <button class="secondary" id="btn-finalize-active">Finalize current</button>
    </div>'''


CLIENT_IMPORT_MODAL = '''

<!-- Phase 7: import modal -->
<div class="modal-backdrop" id="modal-import-ontology">
  <div class="modal" style="min-width:560px;">
    <h3>Import OWL file</h3>

    <div id="import-step-1">
      <label for="import-file">OWL file</label>
      <input type="file" id="import-file" accept=".owl,.rdf,.ttl,.xml" />
      <div class="hint">RDF/XML, Turtle, or N-Triples. The file will be parsed and validated before commit.</div>
      <div class="actions">
        <button class="secondary" id="btn-import-cancel">Cancel</button>
        <button id="btn-import-preview">Preview</button>
      </div>
    </div>

    <div id="import-step-2" style="display:none;">
      <div id="import-preview-summary" style="background:var(--panel-2); border:1px solid var(--border); border-radius:4px; padding:12px; margin-bottom:14px; font-size:12px;"></div>

      <label for="import-name">Name</label>
      <input type="text" id="import-name" placeholder="e.g. Peirce_categorial_v1" maxlength="64" />

      <label for="import-desc">Description</label>
      <textarea id="import-desc" placeholder="What this ontology captures and where it came from."></textarea>

      <label for="import-source">Source text (optional)</label>
      <input type="text" id="import-source" placeholder="e.g. Peirce, On a New List of Categories (1867)" />

      <label for="import-author">Author / curator (optional)</label>
      <input type="text" id="import-author" />

      <div class="actions">
        <button class="secondary" id="btn-import-back">Back</button>
        <button id="btn-import-submit">Commit import</button>
      </div>
    </div>
  </div>
</div>'''


CLIENT_IMPORT_JS = '''

// =========================================================================
// Phase 7: import OWL flow
// =========================================================================

let pendingImportFile = null;
let pendingImportPreview = null;

function openImportModal() {
  pendingImportFile = null;
  pendingImportPreview = null;
  document.getElementById("import-file").value = "";
  document.getElementById("import-name").value = "";
  document.getElementById("import-desc").value = "";
  document.getElementById("import-source").value = "";
  document.getElementById("import-author").value = "";
  document.getElementById("import-step-1").style.display = "block";
  document.getElementById("import-step-2").style.display = "none";
  document.getElementById("modal-import-ontology").classList.add("open");
}

function closeImportModal() {
  document.getElementById("modal-import-ontology").classList.remove("open");
}

async function previewImport() {
  const f = document.getElementById("import-file").files[0];
  if (!f) { alert("Choose a file first."); return; }
  pendingImportFile = f;

  const fd = new FormData();
  fd.append("file", f);

  const previewBtn = document.getElementById("btn-import-preview");
  const oldText = previewBtn.textContent;
  previewBtn.textContent = "Loading...";
  previewBtn.disabled = true;

  try {
    const r = await fetch(API + "/ontologies/preview-import", {
      method: "POST",
      body: fd,
    });
    const data = await r.json();
    if (!r.ok) {
      alert("Preview failed: " + (data.error || r.statusText));
      return;
    }
    pendingImportPreview = data;
    renderImportPreview(data);
    document.getElementById("import-step-1").style.display = "none";
    document.getElementById("import-step-2").style.display = "block";
    // Suggest name from filename
    const suggested = (f.name || "").replace(/\\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]/g, "_");
    if (suggested && /^[A-Za-z]/.test(suggested)) {
      document.getElementById("import-name").value = suggested;
    }
  } catch (e) {
    alert("Preview failed: " + e.message);
  } finally {
    previewBtn.textContent = oldText;
    previewBtn.disabled = false;
  }
}

function renderImportPreview(data) {
  const div = document.getElementById("import-preview-summary");
  if (!data.loaded) {
    div.innerHTML = `<div style="color:var(--warn);"><b>Failed to load:</b><br>${data.warnings.map(esc).join("<br>")}</div>`;
    document.getElementById("btn-import-submit").disabled = true;
    return;
  }
  document.getElementById("btn-import-submit").disabled = false;
  const consistent = data.consistent ? '<span style="color:var(--accent);">consistent</span>' : '<span style="color:var(--warn);">INCONSISTENT</span>';
  const bfo_pct = data.classes > 0 ? Math.round(100 * data.bfo_aligned_classes / data.classes) : 0;
  const warns = data.warnings.length > 0
    ? `<div style="margin-top:8px; padding-top:8px; border-top:1px solid var(--border); color:var(--warn);"><b>Warnings:</b><br>${data.warnings.map(esc).join("<br>")}</div>`
    : "";
  div.innerHTML = `
    <div><b>${esc(data.filename)}</b> &mdash; ${(data.size_bytes/1024).toFixed(1)} KB</div>
    <div style="margin-top:6px;">
      ${data.classes} classes, ${data.individuals} individuals,
      ${data.object_properties} object properties
    </div>
    <div style="margin-top:6px;">BFO-aligned classes: ${data.bfo_aligned_classes} (${bfo_pct}%)</div>
    <div style="margin-top:6px;">HermiT: ${consistent}</div>
    ${data.base_iri ? `<div style="margin-top:6px; color:var(--muted); font-size:11px;">Base IRI: ${esc(data.base_iri)}</div>` : ""}
    ${warns}
  `;
}

async function submitImport() {
  if (!pendingImportFile) { alert("No file to import."); return; }
  const name = document.getElementById("import-name").value.trim();
  const description = document.getElementById("import-desc").value.trim();
  const source_text = document.getElementById("import-source").value.trim();
  const author = document.getElementById("import-author").value.trim();

  if (!name) { alert("Name is required."); return; }
  if (!description) { alert("Description is required."); return; }
  if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name)) {
    alert("Invalid name. Letters, digits, _, -. Starts with a letter. Max 64 chars.");
    return;
  }

  const fd = new FormData();
  fd.append("file", pendingImportFile);
  fd.append("name", name);
  fd.append("description", description);
  if (source_text) fd.append("source_text", source_text);
  if (author) fd.append("author", author);

  const submitBtn = document.getElementById("btn-import-submit");
  const oldText = submitBtn.textContent;
  submitBtn.textContent = "Importing...";
  submitBtn.disabled = true;

  try {
    const r = await fetch(API + "/ontologies/import", { method: "POST", body: fd });
    const data = await r.json();
    if (!r.ok) {
      alert("Import failed: " + (data.error || r.statusText));
      return;
    }
    closeImportModal();
    await refreshOntologies();
    if (confirm(`Imported "${name}". Activate it now?`)) {
      await activateOntology(name);
    }
  } catch (e) {
    alert("Import failed: " + e.message);
  } finally {
    submitBtn.textContent = oldText;
    submitBtn.disabled = false;
  }
}

document.getElementById("btn-import-ontology").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleOntologyPanel(false);
  openImportModal();
});
document.getElementById("btn-import-cancel").addEventListener("click", closeImportModal);
document.getElementById("btn-import-back").addEventListener("click", () => {
  document.getElementById("import-step-1").style.display = "block";
  document.getElementById("import-step-2").style.display = "none";
});
document.getElementById("btn-import-preview").addEventListener("click", previewImport);
document.getElementById("btn-import-submit").addEventListener("click", submitImport);
document.getElementById("modal-import-ontology").addEventListener("click", (e) => {
  if (e.target.id === "modal-import-ontology") closeImportModal();
});
'''


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_unique(src: str, old: str, new: str, label: str) -> str:
    n = src.count(old)
    if n == 0:
        raise RuntimeError(f"[{label}] expected snippet not found")
    if n > 1:
        raise RuntimeError(f"[{label}] snippet matches {n} places")
    return src.replace(old, new, 1)


def _apply_registry() -> bool:
    src = REGISTRY.read_text()
    if "def preview_owl(" in src:
        print("  registry.py already has phase-7 methods, skipping")
        return True
    if not REGISTRY_BACKUP.exists():
        shutil.copy(REGISTRY, REGISTRY_BACKUP)
        print(f"  backed up registry.py")
    REGISTRY.write_text(src.rstrip() + REGISTRY_IMPORT_METHOD + "\n")
    print("  registry.py: added preview_owl and import_from_file")
    return True


def _apply_orch() -> bool:
    src = ORCH.read_text()
    if "def preview_import_ontology():" in src:
        print("  orchestrator.py already has phase-7 endpoints, skipping")
        return True
    if not ORCH_BACKUP.exists():
        shutil.copy(ORCH, ORCH_BACKUP)
        print(f"  backed up orchestrator.py")
    src = _apply_unique(src, ORCH_ANCHOR_OLD, ORCH_ANCHOR_NEW, "import endpoints")
    ORCH.write_text(src)
    print("  orchestrator.py: added preview-import and import endpoints")
    return True


def _apply_client() -> bool:
    src = CLIENT.read_text()
    if "btn-import-ontology" in src:
        print("  client already has phase-7 UI, skipping")
        return True
    if not CLIENT_BACKUP.exists():
        shutil.copy(CLIENT, CLIENT_BACKUP)
        print(f"  backed up index.html")
    src = _apply_unique(src, CLIENT_OP_ACTIONS_OLD, CLIENT_OP_ACTIONS_NEW, "op-actions row")
    # Insert import modal before the existing modal-new-ontology div
    new_onto_modal_anchor = '<!-- Phase 5: modal for creating a new ontology -->'
    if new_onto_modal_anchor in src:
        src = src.replace(new_onto_modal_anchor, CLIENT_IMPORT_MODAL.lstrip() + "\n\n" + new_onto_modal_anchor, 1)
    else:
        # Fallback: insert before </body>
        src = src.replace("</body>", CLIENT_IMPORT_MODAL + "\n</body>", 1)
    print("  client: added import modal markup")
    # Append JS just before "</script>"
    src = src.replace("</script>", CLIENT_IMPORT_JS + "\n</script>", 1)
    print("  client: appended import JS")
    CLIENT.write_text(src)
    return True


def _compile_check() -> bool:
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(ORCH), str(REGISTRY)],
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
        (CLIENT_BACKUP, CLIENT),
    ]:
        if backup.exists():
            shutil.copy(backup, target)
            print(f"  restored {target.name}")


def main() -> int:
    print("Phase 7 patch: OWL import endpoints and UI")
    try:
        _apply_registry()
        _apply_orch()
        _apply_client()
    except Exception as e:
        print(f"PATCH FAILED: {e}")
        _rollback()
        return 1

    if not _compile_check():
        print("Rolling back ...")
        _rollback()
        return 1

    print("DONE")
    print()
    print("Smoke tests:")
    print("  # 1. Preview an OWL")
    print("  curl -s -F 'file=@ontology/library/SOoL_v1/working.owl' \\\\")
    print("    http://127.0.0.1:5000/ontologies/preview-import | python3 -m json.tool")
    print()
    print("  # 2. Import the same file as a new ontology (will refuse")
    print("  #    if you give the same name as an existing one)")
    print("  curl -s -F 'file=@some_external.owl' \\\\")
    print("    -F 'name=imported_test' -F 'description=test import' \\\\")
    print("    http://127.0.0.1:5000/ontologies/import | python3 -m json.tool")
    return 0


if __name__ == "__main__":
    sys.exit(main())
