"""Re-emit a schema 1.0 report as schema 2.0, so the two can be diffed.

The point is reviewability. The reference bundle exists at 1.0; the fixes change
what the numbers mean, and the only way to see that clearly is to put the two
shapes side by side. This does the mechanical part of that: it moves fields to
where 2.0 expects them and marks what 1.0 could not have carried.

It does not recompute anything. A migrated report says ``migrated_from: "1.0"``
and leaves ``cd_core`` absent rather than inventing one, because the 1.0 debt
figure was computed by the rule this work replaces and restating it under a new
name would launder it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .bundle import FAILED, OK, SKIPPED, STATUS_MEANING

MIGRATION_NOTE = (
    "Migrated from report schema 1.0. Fields absent here are ones 1.0 did not "
    "carry. The contradiction debt figure is deliberately not carried across: "
    "1.0 computed it by summing coverage predicates alongside defect ones, and "
    "restating that number under a 2.0 name would give it a credibility it does "
    "not have. Re-run the report to get a 2.0 debt figure."
)


def migrate(report: dict[str, Any]) -> dict[str, Any]:
    """Return ``report`` reshaped as schema 2.0."""
    schema = str(report.get("report_schema") or "1.0")
    if schema.startswith("2."):
        return dict(report)

    sections = []
    metrics: dict[str, Any] = {}

    for raw in report.get("sections") or ():
        section = dict(raw)
        status = str(section.get("status") or OK)
        section["status"] = status if status in STATUS_MEANING else OK
        section["status_meaning"] = STATUS_MEANING.get(section["status"], "")
        # 1.0 had no gates, so nothing can be asserted about validity.
        section.setdefault("gates", [])
        section["gates_passed"] = 0
        section["gates_total"] = 0

        # Lift the statistics section into top-level metrics, renaming the two
        # collisions so the migrated shape is at least self-consistent.
        if section.get("id") == "statistics" and isinstance(section.get("data"), dict):
            for key, value in section["data"].items():
                metrics[_rename(key)] = value
        if section.get("id") == "kernel_audit" and isinstance(section.get("data"), dict):
            for key, value in (section["data"].get("metrics") or {}).items():
                metrics.setdefault(_rename(key), value)

        sections.append(section)

    out = dict(report)
    out.update({
        "report_schema": "2.0",
        "migrated_from": schema,
        "migration_note": MIGRATION_NOTE,
        "metrics": metrics,
        "kernel_registry_version": "",
        "kernel_registry_digest": "",
        "provenance": _provenance_from(report),
        "status_vocabulary": dict(STATUS_MEANING),
        "sections": sections,
        "sections_ok": [s["id"] for s in sections if s.get("status") == OK],
        "sections_unreliable": [],
        "sections_failed": [s["id"] for s in sections if s.get("status") == FAILED],
        "sections_skipped": [s["id"] for s in sections if s.get("status") == SKIPPED],
        "sections_not_applicable": [],
    })
    return out


# 1.0 metric names, mapped onto names that state their scope.
_RENAMES = {
    "object_properties": "object_properties_local",
    "classes": "named_classes_local",
    "individuals": "named_individuals_local",
    "named_classes": "named_classes_local",
    "named_individuals": "named_individuals_local",
    "classes_with_definition": "classes_with_iao_definition_local",
    "defined_classes_equivalentClass": "classes_with_equivalent_class_local",
    "disjointWith_axioms": "disjoint_with_axioms_local",
    "classes_directly_bfo_anchored": "classes_anchored_direct",
    "classes_directly_anchored": "classes_anchored_direct",
    "classes_anchored": "classes_anchored_transitive",
}


def _rename(key: str) -> str:
    return _RENAMES.get(key, key)


def _provenance_from(report: dict[str, Any]) -> dict[str, Any]:
    """Best effort reconstruction from whatever 1.0 buried in the manifest."""
    manifest = {}
    for raw in report.get("sections") or ():
        if raw.get("id") == "manifest" and isinstance(raw.get("data"), dict):
            manifest = raw["data"]
            break

    seeded_from = manifest.get("seeded_from")
    source_text = manifest.get("source_text")
    derivation = manifest.get("derivation")
    if not derivation:
        derivation = "seeded_bootstrap" if seeded_from and not source_text else "unknown"

    return {
        "derivation": derivation,
        "source_text": source_text,
        "source_text_sha256": manifest.get("source_text_sha256"),
        "seeded_from": seeded_from,
        "fidelity": manifest.get("fidelity", ""),
        # 1.0 could not have computed this, so it is left false rather than
        # guessed. Re-running the report populates it.
        "name_implies_source": False,
        "implied_source": "",
    }


def migrate_file(source: str | Path, destination: str | Path | None = None) -> dict[str, Any]:
    src = Path(source)
    migrated = migrate(json.loads(src.read_text()))
    if destination:
        Path(destination).write_text(json.dumps(migrated, indent=2, default=str))
    return migrated


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m app.report.migrate_1_to_2 <report.json> [out.json]")
        return 2
    out = argv[1] if len(argv) > 1 else None
    migrated = migrate_file(argv[0], out)
    if not out:
        print(json.dumps(migrated, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
