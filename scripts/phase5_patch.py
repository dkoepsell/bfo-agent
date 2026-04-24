"""Phase 5 patch: ontology selector dropdown and lifecycle UI.

Adds to client/index.html:
  - CSS for ontology selector, panel, modal, badges
  - Markup: ontology selector in the header between title and stats
  - JS: load list, activate, create, finalize handlers
  - Read-only mode detection: when active is finalized, the Propose
    button is disabled and the Extract tab is hidden, but the Query
    button and graph/log tabs remain functional.

The patch is idempotent and backs up the original client file.

Usage:
    python scripts/phase5_patch.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client" / "index.html"
BACKUP = ROOT / "client" / "index.html.pre_phase5"


# ---------------------------------------------------------------------------
# CSS additions, inserted just before </style>
# ---------------------------------------------------------------------------

CSS_ADDITIONS = """
  /* --- phase 5: ontology selector --- */
  header { position: relative; }
  .ontology-selector {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 12px; background: var(--panel-2);
    border: 1px solid var(--border); border-radius: 20px;
    font-family: var(--font); font-size: 12px;
    color: var(--text); cursor: pointer;
    user-select: none;
    transition: border-color 0.15s ease;
  }
  .ontology-selector:hover { border-color: var(--accent); }
  .ontology-selector .os-name { font-weight: 600; }
  .ontology-selector .os-badge {
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.05em;
    padding: 1px 6px; border-radius: 10px;
  }
  .os-badge.active { background: rgba(125, 211, 252, 0.15); color: var(--accent); }
  .os-badge.finalized { background: rgba(252, 211, 77, 0.15); color: var(--warn); }
  .os-badge.inactive { background: var(--panel); color: var(--muted); }
  .ontology-selector .os-caret { color: var(--muted); font-size: 10px; }

  .ontology-panel {
    position: absolute; top: 44px; left: 50%; transform: translateX(-50%);
    min-width: 360px; max-width: 540px;
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; box-shadow: 0 8px 24px rgba(0,0,0,0.5);
    z-index: 100; padding: 8px;
    display: none;
  }
  .ontology-panel.open { display: block; }
  .ontology-panel .op-list { display: flex; flex-direction: column; gap: 4px; }
  .ontology-panel .op-entry {
    display: grid; grid-template-columns: 1fr auto auto; gap: 10px;
    align-items: center;
    padding: 8px 10px;
    background: var(--panel-2); border: 1px solid var(--border);
    border-radius: 6px;
    font-size: 12px;
  }
  .ontology-panel .op-entry.is-active { border-color: var(--accent); }
  .ontology-panel .op-name {
    font-family: var(--font); font-weight: 600;
    display: flex; align-items: center; gap: 8px;
    min-width: 0;
  }
  .ontology-panel .op-name .op-label { overflow: hidden; text-overflow: ellipsis; }
  .ontology-panel .op-stats {
    font-family: var(--font); font-size: 11px; color: var(--muted);
    white-space: nowrap;
  }
  .ontology-panel .op-entry button {
    padding: 4px 10px; font-size: 11px; border-radius: 4px;
  }
  .ontology-panel .op-entry button:disabled {
    opacity: 0.4; cursor: not-allowed;
  }
  .ontology-panel .op-divider {
    height: 1px; background: var(--border); margin: 8px 0;
  }
  .ontology-panel .op-actions {
    display: flex; gap: 6px;
  }
  .ontology-panel .op-actions button { flex: 1; font-size: 11px; padding: 6px 8px; }

  /* --- modal for new ontology --- */
  .modal-backdrop {
    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    display: none; align-items: center; justify-content: center;
    z-index: 200;
  }
  .modal-backdrop.open { display: flex; }
  .modal {
    background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 20px; min-width: 420px; max-width: 560px;
  }
  .modal h3 { margin: 0 0 14px 0; font-size: 14px; }
  .modal label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.04em; }
  .modal input, .modal textarea {
    width: 100%; background: var(--panel-2); color: var(--text);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 8px 10px; font: inherit; margin-bottom: 12px;
  }
  .modal textarea { min-height: 70px; resize: vertical; }
  .modal .hint { font-size: 11px; color: var(--muted); margin-top: -8px; margin-bottom: 12px; }
  .modal .actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }

  /* --- read-only mode indicators --- */
  body.readonly #btn-propose { opacity: 0.4; cursor: not-allowed; }
  body.readonly .tab[data-tab="extract"] { display: none; }
  body.readonly #utterance::placeholder {
    color: var(--warn);
  }
"""

CSS_ANCHOR_OLD = "  .row-flex { display: flex; gap: 8px; align-items: center; }\n</style>"
CSS_ANCHOR_NEW = (
    "  .row-flex { display: flex; gap: 8px; align-items: center; }\n"
    + CSS_ADDITIONS
    + "\n</style>"
)


# ---------------------------------------------------------------------------
# HTML: replace the header to include the ontology selector
# ---------------------------------------------------------------------------

HEADER_OLD = """<header>
  <h1>BFO-Agent</h1>
  <div class="stats" id="stats">connecting...</div>
</header>"""

HEADER_NEW = """<header>
  <h1>BFO-Agent</h1>

  <div class="ontology-selector" id="ontology-selector" title="Switch ontology">
    <span class="os-label">ontology:</span>
    <span class="os-name" id="os-name">loading...</span>
    <span class="os-badge inactive" id="os-badge">-</span>
    <span class="os-caret">▼</span>
  </div>

  <div class="ontology-panel" id="ontology-panel">
    <div class="op-list" id="op-list"></div>
    <div class="op-divider"></div>
    <div class="op-actions">
      <button class="secondary" id="btn-new-ontology">New ontology...</button>
      <button class="secondary" id="btn-finalize-active">Finalize current</button>
    </div>
  </div>

  <div class="stats" id="stats">connecting...</div>
</header>

<!-- Phase 5: modal for creating a new ontology -->
<div class="modal-backdrop" id="modal-new-ontology">
  <div class="modal">
    <h3>Create new ontology</h3>
    <label for="new-onto-name">Name</label>
    <input type="text" id="new-onto-name" placeholder="e.g. Peirce_categorial" maxlength="64" />
    <div class="hint">Letters, digits, underscore, hyphen. Starts with a letter. Max 64 chars.</div>
    <label for="new-onto-desc">Description</label>
    <textarea id="new-onto-desc" placeholder="What this ontology captures, and how it was seeded."></textarea>
    <label for="new-onto-source">Source text (optional)</label>
    <input type="text" id="new-onto-source" placeholder="e.g. Peirce, Collected Papers Vol. 5" />
    <label for="new-onto-clone">Clone seeds from</label>
    <select id="new-onto-clone" style="width:100%; background:var(--panel-2); color:var(--text); border:1px solid var(--border); border-radius:4px; padding:8px 10px; font:inherit; margin-bottom:12px;"></select>
    <div class="actions">
      <button class="secondary" id="btn-new-onto-cancel">Cancel</button>
      <button id="btn-new-onto-submit">Create</button>
    </div>
  </div>
</div>"""


# ---------------------------------------------------------------------------
# JS additions, inserted just before "refreshGraph();" at end of script
# ---------------------------------------------------------------------------

JS_ANCHOR_OLD = "refreshGraph();\n</script>"

JS_ADDITIONS = r"""
// =========================================================================
// Phase 5: ontology selector + lifecycle UI
// =========================================================================

let currentOntologies = [];
let currentActive = null;

async function refreshOntologies() {
  try {
    const data = await api("/ontologies");
    currentOntologies = data.ontologies || [];
    currentActive = data.active;
    updateSelector();
    renderOntologyPanel();
    applyReadonlyMode();
  } catch (e) {
    console.error("Failed to load ontologies:", e);
  }
}

function findOntology(name) {
  return currentOntologies.find(o => o.name === name);
}

function updateSelector() {
  const active = findOntology(currentActive);
  document.getElementById("os-name").textContent = currentActive || "(none)";
  const badge = document.getElementById("os-badge");
  if (!active) {
    badge.className = "os-badge inactive";
    badge.textContent = "-";
    return;
  }
  const status = active.manifest?.status || "active";
  badge.className = "os-badge " + (status === "finalized" ? "finalized" : "active");
  badge.textContent = status;
}

function applyReadonlyMode() {
  const active = findOntology(currentActive);
  const isFinalized = active?.manifest?.status === "finalized";
  document.body.classList.toggle("readonly", isFinalized);
  const proposeBtn = document.getElementById("btn-propose");
  if (proposeBtn) proposeBtn.disabled = isFinalized;
  // If the user is sitting on the Extract tab and it just got hidden,
  // fall back to Proposal tab.
  if (isFinalized) {
    const extractTab = document.querySelector('.tab[data-tab="extract"]');
    if (extractTab && extractTab.classList.contains("active")) {
      selectTab("proposal");
    }
  }
}

function renderOntologyPanel() {
  const list = document.getElementById("op-list");
  list.innerHTML = "";
  currentOntologies.forEach(o => {
    const status = o.manifest?.status || "active";
    const classes = o.stats?.classes ?? "?";
    const individuals = o.stats?.individuals ?? "?";
    const entry = el(`
      <div class="op-entry ${o.active ? "is-active" : ""}">
        <div class="op-name">
          <span class="op-label">${esc(o.name)}</span>
          <span class="os-badge ${esc(status === "finalized" ? "finalized" : (o.active ? "active" : "inactive"))}">${esc(status)}</span>
        </div>
        <div class="op-stats">${classes} classes, ${individuals} indivs</div>
        <button class="${o.active ? "secondary" : ""}" ${o.active ? "disabled" : ""}
                data-activate="${esc(o.name)}">${o.active ? "current" : "Activate"}</button>
      </div>
    `);
    list.appendChild(entry);
  });
  // Populate the "clone from" select in the new-ontology modal
  const clone = document.getElementById("new-onto-clone");
  if (clone) {
    const preselect = currentActive;
    clone.innerHTML = currentOntologies.map(o =>
      `<option value="${esc(o.name)}" ${o.name === preselect ? "selected" : ""}>${esc(o.name)}</option>`
    ).join("");
  }
  // Disable finalize button if active is already finalized
  const active = findOntology(currentActive);
  const finBtn = document.getElementById("btn-finalize-active");
  if (finBtn) {
    finBtn.disabled = active?.manifest?.status === "finalized";
    finBtn.title = finBtn.disabled ? "Already finalized" : "Mark the current ontology as finalized (read-only)";
  }
}

function toggleOntologyPanel(forceOpen) {
  const panel = document.getElementById("ontology-panel");
  const shouldOpen = forceOpen !== undefined ? forceOpen : !panel.classList.contains("open");
  panel.classList.toggle("open", shouldOpen);
}

async function activateOntology(name) {
  if (!confirm(`Activate ontology "${name}"?\n\nWrites (Propose, Feed, Extract) will now target this ontology.`)) return;
  try {
    await api(`/ontologies/${encodeURIComponent(name)}/activate`, { method: "POST" });
    await refreshOntologies();
    toggleOntologyPanel(false);
    // Also refresh graph since we're now viewing a different ontology's stats
    refreshGraph();
  } catch (e) {
    alert("Activation failed: " + e.message);
  }
}

async function finalizeActiveOntology() {
  const name = currentActive;
  if (!name) return;
  if (!confirm(
    `Finalize "${name}"?\n\n` +
    "Once finalized, Propose, Commit, Extract, and Feed are blocked for this ontology. " +
    "Query and read endpoints continue to work. Finalization cannot be undone through the UI."
  )) return;
  try {
    await api(`/ontologies/${encodeURIComponent(name)}/finalize`, { method: "POST" });
    await refreshOntologies();
  } catch (e) {
    alert("Finalize failed: " + e.message);
  }
}

function openNewOntologyModal() {
  document.getElementById("new-onto-name").value = "";
  document.getElementById("new-onto-desc").value = "";
  document.getElementById("new-onto-source").value = "";
  document.getElementById("modal-new-ontology").classList.add("open");
  document.getElementById("new-onto-name").focus();
}

function closeNewOntologyModal() {
  document.getElementById("modal-new-ontology").classList.remove("open");
}

async function submitNewOntology() {
  const name = document.getElementById("new-onto-name").value.trim();
  const description = document.getElementById("new-onto-desc").value.trim();
  const source_text = document.getElementById("new-onto-source").value.trim() || null;
  const clone_seeds_from = document.getElementById("new-onto-clone").value || null;

  if (!name) { alert("Name is required."); return; }
  if (!description) { alert("Description is required."); return; }
  if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(name)) {
    alert("Invalid name. Letters, digits, _, -. Starts with a letter. Max 64 chars.");
    return;
  }

  try {
    await api("/ontologies", {
      method: "POST",
      body: JSON.stringify({ name, description, source_text, clone_seeds_from }),
    });
    closeNewOntologyModal();
    await refreshOntologies();
    // Offer to activate it immediately
    if (confirm(`Created "${name}". Activate it now?`)) {
      await activateOntology(name);
    } else {
      toggleOntologyPanel(true);
    }
  } catch (e) {
    alert("Create failed: " + e.message);
  }
}

// --- wire up UI ---

document.getElementById("ontology-selector").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleOntologyPanel();
});

document.getElementById("ontology-panel").addEventListener("click", (e) => {
  // Activate buttons
  const actBtn = e.target.closest("[data-activate]");
  if (actBtn && !actBtn.disabled) {
    e.stopPropagation();
    activateOntology(actBtn.dataset.activate);
    return;
  }
  // Don't close when clicking inside the panel
  e.stopPropagation();
});

// Close panel on outside click
document.addEventListener("click", () => {
  toggleOntologyPanel(false);
});

document.getElementById("btn-new-ontology").addEventListener("click", (e) => {
  e.stopPropagation();
  toggleOntologyPanel(false);
  openNewOntologyModal();
});

document.getElementById("btn-finalize-active").addEventListener("click", (e) => {
  e.stopPropagation();
  finalizeActiveOntology();
});

document.getElementById("btn-new-onto-cancel").addEventListener("click", closeNewOntologyModal);
document.getElementById("btn-new-onto-submit").addEventListener("click", submitNewOntology);
document.getElementById("modal-new-ontology").addEventListener("click", (e) => {
  if (e.target.id === "modal-new-ontology") closeNewOntologyModal();
});

// Helper to programmatically select a tab (used when entering readonly mode)
function selectTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tab-pane").forEach(p => p.style.display = p.id === `tab-${name}` ? "block" : "none");
}

// Initial load of ontology info alongside the first graph refresh
refreshOntologies();
refreshGraph();
</script>"""


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _apply_unique(src: str, old: str, new: str, label: str) -> str:
    count = src.count(old)
    if count == 0:
        raise RuntimeError(f"[{label}] expected snippet not found")
    if count > 1:
        raise RuntimeError(f"[{label}] snippet matches {count} places")
    return src.replace(old, new, 1)


def _already_applied(src: str) -> bool:
    return "phase 5: ontology selector" in src or "ontology-selector" in src


def main() -> int:
    print("Phase 5 patch: ontology selector and lifecycle UI in the client")

    src = CLIENT.read_text()

    if _already_applied(src):
        print("  client already has phase-5 UI, skipping")
        return 0

    if not BACKUP.exists():
        shutil.copy(CLIENT, BACKUP)
        print(f"  backed up {CLIENT.name} to {BACKUP.name}")

    try:
        src = _apply_unique(src, CSS_ANCHOR_OLD, CSS_ANCHOR_NEW, "CSS block")
        print("  client: added CSS for selector, panel, modal")

        src = _apply_unique(src, HEADER_OLD, HEADER_NEW, "header + modal markup")
        print("  client: replaced header with selector markup + new-ontology modal")

        # The old JS block ends with "refreshGraph();\n</script>". After
        # we append, the combined init block calls refreshOntologies() and
        # refreshGraph().
        new_js_block = JS_ADDITIONS
        if JS_ANCHOR_OLD not in src:
            raise RuntimeError("JS anchor not found")
        src = src.replace(JS_ANCHOR_OLD, new_js_block.lstrip("\n"), 1)
        print("  client: appended JS for ontology operations")
    except Exception as e:
        print(f"PATCH FAILED: {e}")
        shutil.copy(BACKUP, CLIENT)
        print("  restored from backup")
        return 1

    CLIENT.write_text(src)
    print("DONE")
    print()
    print("Next: reload the browser page (Ctrl-R). No Flask restart needed.")
    print("  The client served by Flask is a static file; the new UI loads immediately.")
    print()
    print("You should see:")
    print("  - Header has an 'ontology:' pill showing the active name plus a badge")
    print("  - Click the pill to reveal the library listing and action buttons")
    print("  - 'New ontology...' opens a modal form")
    print("  - 'Finalize current' marks active as finalized and triggers read-only mode")
    return 0


if __name__ == "__main__":
    sys.exit(main())
