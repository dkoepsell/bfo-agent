"""Incoherence ledger for faithful-extraction mode (fidelity-mode-spec.md FM-7).

When an ontology is extracted from a text in "faithful" mode, clashing claims
are committed exactly as the text asserted them; this module records the
evidence. The ledger is append-only JSONL under the ontology's sessions/
directory, one entry per flagged clash (gate FLAG) or post-commit incoherence.
It is the machine-readable interface consumed by reports, by the coherent-view
dry-run (FM-9, via :func:`exclusion_triples`), and by the FOL gate's
provenance mapping (fol-gate-spec.md P-1).

Nothing here modifies the ontology's logical content. The one write the mode
permits -- a semantics-free ``incoherenceEvidence`` annotation on the flagged
class (FM-8) -- is applied by :func:`record_flag` through the manager.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LEDGER_FILENAME = "incoherence_ledger.jsonl"


def ledger_path(working_path: Path) -> Path:
    """The ledger lives next to the session logs of the owning ontology."""
    return Path(working_path).parent / "sessions" / LEDGER_FILENAME


def append(working_path: Path, entry: dict) -> str:
    """Append one entry; returns the assigned entry id."""
    path = ledger_path(working_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = dict(entry)
    entry.setdefault("id", f"inc-{uuid.uuid4().hex[:8]}")
    entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry["id"]


def read_all(working_path: Path) -> list[dict]:
    path = ledger_path(working_path)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def exclusion_triples(working_path: Path) -> list[dict]:
    """Union of all ledgered clash axioms ({s, p, o} dicts), deduplicated.

    This is what the coherent-view dry-run retracts (FM-9) so each new claim
    is judged on its own merits rather than against prior flagged clashes.
    """
    seen: set[tuple] = set()
    triples: list[dict] = []
    for entry in read_all(working_path):
        for t in entry.get("exclude_axioms") or []:
            key = (t.get("s"), t.get("p"), t.get("o"))
            if key in seen:
                continue
            seen.add(key)
            triples.append({"s": t.get("s"), "p": t.get("p"), "o": t.get("o")})
    return triples


def record_flag(
    manager,
    proposal,
    gate_result_dict: dict,
    exclude_axioms: list[dict],
    subjects: list[str],
    provenance: Optional[dict] = None,
    mode: str = "faithful",
) -> str:
    """Write one ledger entry for a flagged clash and annotate its classes.

    ``subjects`` are the class refs (local names or IRIs) the clash is about;
    each gets an ``incoherenceEvidence`` annotation carrying the entry id and
    the diagnosis (FM-8). The manager is saved iff an annotation was applied.
    Returns the ledger entry id.
    """
    entry = {
        "mode": mode,
        "subjects": subjects,
        "tier": gate_result_dict.get("tier"),
        "gate_result": gate_result_dict,
        "exclude_axioms": exclude_axioms,
        "provenance": provenance or {},
        "utterance": getattr(proposal, "utterance", None),
    }
    entry_id = append(manager.working_path, entry)

    reason = gate_result_dict.get("reason") or "incoherent under BFO"
    annotated = False
    for subject in subjects:
        if manager.annotate_incoherence(subject, f"[{entry_id}] {reason}"):
            annotated = True
    if annotated:
        manager.save()
    return entry_id
