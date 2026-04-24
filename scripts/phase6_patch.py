"""Phase 6 patch: UI polish (delete button) and cleanup.

Changes:
  1. Client: add a per-entry Delete button in the ontology panel.
     - Enabled only when the entry is inactive AND not finalized.
     - Calls DELETE /ontologies/<n>, confirms, reloads on success.
  2. .gitignore: ignore pre-phase-* backup files.
  3. Moves existing pre-phase-* files out of git tracking.

The Hetzner migration is a separate script (hetzner_v2_migrate.sh) since
it runs on a different machine.

Idempotent; backs up the client file if not already backed up.

Usage:
    python scripts/phase6_patch.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client" / "index.html"
BACKUP = ROOT / "client" / "index.html.pre_phase6"
GITIGNORE = ROOT / ".gitignore"


# ---------------------------------------------------------------------------
# 1. Client: add delete button to each non-active, non-finalized entry
# ---------------------------------------------------------------------------

# The op-entry markup needs an extra button column. The CSS grid was
# 1fr auto auto (name, stats, button); we make it 1fr auto auto auto.

CSS_COLUMNS_OLD = """  .ontology-panel .op-entry {
    display: grid; grid-template-columns: 1fr auto auto; gap: 10px;
    align-items: center;"""

CSS_COLUMNS_NEW = """  .ontology-panel .op-entry {
    display: grid; grid-template-columns: 1fr auto auto auto; gap: 10px;
    align-items: center;"""


# The entry template in renderOntologyPanel gets a new button.
JS_ENTRY_OLD = '''    const entry = el(`
      <div class="op-entry ${o.active ? "is-active" : ""}">
        <div class="op-name">
          <span class="op-label">${esc(o.name)}</span>
          <span class="os-badge ${esc(status === "finalized" ? "finalized" : (o.active ? "active" : "inactive"))}">${esc(status)}</span>
        </div>
        <div class="op-stats">${classes} classes, ${individuals} indivs</div>
        <button class="${o.active ? "secondary" : ""}" ${o.active ? "disabled" : ""}
                data-activate="${esc(o.name)}">${o.active ? "current" : "Activate"}</button>
      </div>
    `);'''

JS_ENTRY_NEW = '''    const canDelete = !o.active && status !== "finalized";
    const deleteTitle = o.active
      ? "Cannot delete the active ontology. Activate a different one first."
      : (status === "finalized"
          ? "Finalized ontologies are preserved for audit."
          : "Delete this ontology from the library.");
    const entry = el(`
      <div class="op-entry ${o.active ? "is-active" : ""}">
        <div class="op-name">
          <span class="op-label">${esc(o.name)}</span>
          <span class="os-badge ${esc(status === "finalized" ? "finalized" : (o.active ? "active" : "inactive"))}">${esc(status)}</span>
        </div>
        <div class="op-stats">${classes} classes, ${individuals} indivs</div>
        <button class="${o.active ? "secondary" : ""}" ${o.active ? "disabled" : ""}
                data-activate="${esc(o.name)}">${o.active ? "current" : "Activate"}</button>
        <button class="danger" ${canDelete ? "" : "disabled"}
                data-delete="${esc(o.name)}" title="${esc(deleteTitle)}">Delete</button>
      </div>
    `);'''


# Add click handler for the new delete button. We extend the existing
# panel click handler.

JS_HANDLER_OLD = '''document.getElementById("ontology-panel").addEventListener("click", (e) => {
  // Activate buttons
  const actBtn = e.target.closest("[data-activate]");
  if (actBtn && !actBtn.disabled) {
    e.stopPropagation();
    activateOntology(actBtn.dataset.activate);
    return;
  }
  // Don't close when clicking inside the panel
  e.stopPropagation();
});'''

JS_HANDLER_NEW = '''document.getElementById("ontology-panel").addEventListener("click", (e) => {
  // Activate buttons
  const actBtn = e.target.closest("[data-activate]");
  if (actBtn && !actBtn.disabled) {
    e.stopPropagation();
    activateOntology(actBtn.dataset.activate);
    return;
  }
  // Delete buttons (phase 6)
  const delBtn = e.target.closest("[data-delete]");
  if (delBtn && !delBtn.disabled) {
    e.stopPropagation();
    deleteOntology(delBtn.dataset.delete);
    return;
  }
  // Don't close when clicking inside the panel
  e.stopPropagation();
});

async function deleteOntology(name) {
  if (!confirm(
    `Delete ontology "${name}"?\\n\\n` +
    "This removes the directory, working.owl, seeds, sessions, and jobs " +
    "for this ontology. The action is recorded in git, so it can be " +
    "recovered by a git revert if needed, but the in-place data will " +
    "be gone.\\n\\nContinue?"
  )) return;
  try {
    const r = await fetch(API + "/ontologies/" + encodeURIComponent(name), {
      method: "DELETE",
    });
    if (!r.ok && r.status !== 204) {
      const err = await r.json().catch(() => ({}));
      alert("Delete failed: " + (err.error || r.statusText));
      return;
    }
    await refreshOntologies();
  } catch (e) {
    alert("Delete failed: " + e.message);
  }
}'''


# ---------------------------------------------------------------------------
# 2. .gitignore updates
# ---------------------------------------------------------------------------

GITIGNORE_ADDITIONS = [
    "# Phase 6: ignore per-phase backup files (rollback via git tags instead)",
    "app/*.pre_phase*",
    "client/*.pre_phase*",
    "scripts/phase*_patch.py.bak",
    "backup_pre_phase*/",
]


def _apply_client_patch() -> bool:
    src = CLIENT.read_text()
    if "data-delete=" in src:
        print("  client: delete button already present, skipping")
        return True
    if not BACKUP.exists():
        shutil.copy(CLIENT, BACKUP)
        print(f"  backed up {CLIENT.name}")

    for label, old, new in [
        ("CSS columns", CSS_COLUMNS_OLD, CSS_COLUMNS_NEW),
        ("entry template", JS_ENTRY_OLD, JS_ENTRY_NEW),
        ("click handler", JS_HANDLER_OLD, JS_HANDLER_NEW),
    ]:
        n = src.count(old)
        if n == 0:
            print(f"  ERROR: {label} snippet not found")
            shutil.copy(BACKUP, CLIENT)
            return False
        if n > 1:
            print(f"  ERROR: {label} snippet matches {n} places")
            shutil.copy(BACKUP, CLIENT)
            return False
        src = src.replace(old, new, 1)
        print(f"  client: patched {label}")

    CLIENT.write_text(src)
    return True


def _apply_gitignore_patch() -> None:
    existing = GITIGNORE.read_text() if GITIGNORE.exists() else ""
    to_add = [line for line in GITIGNORE_ADDITIONS if line not in existing]
    if not to_add:
        print("  .gitignore: already up to date")
        return
    with GITIGNORE.open("a") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write("\n" + "\n".join(to_add) + "\n")
    print(f"  .gitignore: appended {len(to_add)} entries")


def _untrack_backup_files() -> None:
    """Remove .pre_phase* files from git tracking but leave them on disk.

    Using `git rm --cached` so the actual files stay for safety.
    """
    patterns = [
        "app/*.pre_phase*",
        "client/*.pre_phase*",
    ]
    found = []
    for pattern in patterns:
        for path in ROOT.glob(pattern):
            rel = path.relative_to(ROOT)
            # Is it tracked?
            r = subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(rel)],
                cwd=str(ROOT), capture_output=True,
            )
            if r.returncode == 0:
                found.append(str(rel))
    if not found:
        print("  git: no tracked backup files to untrack")
        return
    for rel in found:
        subprocess.run(
            ["git", "rm", "--cached", rel],
            cwd=str(ROOT), capture_output=True, text=True,
        )
    print(f"  git: untracked {len(found)} backup files")


def _compile_check() -> bool:
    # Client is HTML+JS; can't py_compile. Just verify it still has
    # balanced script tags and the key JS functions.
    src = CLIENT.read_text()
    checks = {
        "<script>": 1,
        "</script>": 1,
        "function deleteOntology(": 1,
        "data-delete=": 1,   # attribute in rendered template
        "[data-delete]": 1,  # selector in click handler
    }
    for marker, expected in checks.items():
        got = src.count(marker)
        if got < expected:
            print(f"VERIFY FAIL: {marker!r} count {got}, expected >= {expected}")
            return False
    return True


def main() -> int:
    print("Phase 6 patch: UI delete button and git hygiene")

    if not _apply_client_patch():
        return 1

    if not _compile_check():
        print("Client verification failed, restoring backup")
        shutil.copy(BACKUP, CLIENT)
        return 1

    _apply_gitignore_patch()
    _untrack_backup_files()

    print("DONE")
    print()
    print("The client now shows a Delete button on each non-active,")
    print("non-finalized ontology entry. Reload the browser to see it.")
    print()
    print("Next: the Hetzner migration. See scripts/hetzner_v2_migrate.sh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
