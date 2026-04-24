"""Phase 1 migration: reorganize to library/<name>/ layout.

Non-destructive: backs up the full ontology/, sessions/, jobs/ state
into a backup directory before moving anything. Safe to rerun.

After this script runs:
  ontology/library/SOoL_v1/working.owl       (was ontology/working.owl)
  ontology/library/SOoL_v1/manifest.json     (new)
  ontology/library/SOoL_v1/seed/*.ttl        (was ontology/seed/)
  ontology/library/SOoL_v1/sessions/*.jsonl  (was sessions/)
  ontology/library/SOoL_v1/jobs/*.json       (was jobs/)
  ontology/library/_archive/*.owl            (old snapshots, non-active)
  ontology/active.txt                        (contains: SOoL_v1)
  ontology/bfo.owl                           (unchanged)

The script does NOT modify app/ code. That's a separate step after you
run this and confirm the filesystem looks right.

Usage:
    python scripts/phase1_migrate.py           # dry run by default
    python scripts/phase1_migrate.py --apply   # actually move files
    python scripts/phase1_migrate.py --rollback # restore from backup
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The name we assign the currently-live SOoL ontology
ACTIVE_NAME = "SOoL_v1"

# Files/directories at current top-level that move into the active ontology
# (source_path, destination_relative_to_library_name)
MIGRATIONS = [
    ("ontology/working.owl",                  "working.owl"),
    ("ontology/seed/legal_seed.ttl",          "seed/legal_seed.ttl"),
    ("ontology/seed/bfo_relations.ttl",       "seed/bfo_relations.ttl"),
]

# Directories whose *contents* move in bulk
DIR_MIGRATIONS = [
    ("sessions",                              "sessions"),
    ("jobs",                                  "jobs"),
]

# Old snapshots and backups move to library/_archive/
ARCHIVE_FILES = [
    "ontology/SOoL_final.owl",
    "ontology/SOoL_full_raw.owl",
    "ontology/SOoL_pilot_1243_20260421.owl",
    "ontology/SOoL_pilot_final.owl",
    "ontology/working.canonicalized.owl",
    "ontology/working.pre_canonicalize.owl",
    "ontology/working.pre_patches.owl",
    "ontology/working.pre_relations.owl",
    "ontology/working_SOoL_partial_376claims.owl",
]


def backup_path() -> Path:
    return ROOT / f"backup_pre_phase1_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def latest_backup() -> Path | None:
    candidates = sorted(ROOT.glob("backup_pre_phase1_*"))
    return candidates[-1] if candidates else None


def do_backup(dry_run: bool) -> Path:
    dst = backup_path()
    print(f"  Creating backup at {dst}")
    if dry_run:
        return dst
    dst.mkdir(parents=True, exist_ok=True)
    # Backup ontology/, sessions/, jobs/ into the backup directory
    for name in ("ontology", "sessions", "jobs"):
        src = ROOT / name
        if src.exists():
            shutil.copytree(src, dst / name, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
            print(f"    copied {name}/ -> backup/")
    return dst


def count_ontology_contents() -> dict:
    """Use owlready2 to peek at what's in the active working ontology."""
    sys.path.insert(0, str(ROOT))
    try:
        from app.ontology_manager import OntologyManager
        from app import config
        mgr = OntologyManager(
            bfo_path=config.BFO_PATH,
            working_path=config.WORKING_PATH,
            seed_path=config.SEED_PATH,
        )
        return {
            "classes": len(list(mgr.working.classes())),
            "individuals": len(list(mgr.working.individuals())),
        }
    except Exception as e:
        return {"error": str(e)}


def do_migrate(dry_run: bool):
    lib_root = ROOT / "ontology" / "library"
    active_root = lib_root / ACTIVE_NAME
    archive_root = lib_root / "_archive"

    print(f"\nPhase 1 migration ({'DRY RUN' if dry_run else 'APPLYING'})")
    print("=" * 60)

    # Step 1: verify preconditions
    print("\n[1/6] Checking preconditions...")
    working_owl = ROOT / "ontology" / "working.owl"
    if not working_owl.exists():
        print(f"  FATAL: {working_owl} not found. Nothing to migrate.")
        return 1
    print(f"  ok: {working_owl} exists")

    if lib_root.exists() and any(lib_root.iterdir()):
        print(f"  WARNING: {lib_root} already exists and is non-empty.")
        print(f"  This suggests migration was already done. Use --rollback if needed.")
        return 1
    print(f"  ok: {lib_root} does not exist or is empty")

    # Step 2: backup
    print("\n[2/6] Creating backup...")
    backup = do_backup(dry_run)

    # Step 3: create library structure
    print(f"\n[3/6] Creating {lib_root}/...")
    if not dry_run:
        active_root.mkdir(parents=True, exist_ok=True)
        (active_root / "seed").mkdir(exist_ok=True)
        (active_root / "sessions").mkdir(exist_ok=True)
        (active_root / "jobs").mkdir(exist_ok=True)
        archive_root.mkdir(exist_ok=True)
    print(f"  ok: {active_root}/")
    print(f"  ok: {active_root}/seed/")
    print(f"  ok: {active_root}/sessions/")
    print(f"  ok: {active_root}/jobs/")
    print(f"  ok: {archive_root}/")

    # Step 4: move files
    print("\n[4/6] Moving files into active ontology...")
    for src_rel, dst_rel in MIGRATIONS:
        src = ROOT / src_rel
        dst = active_root / dst_rel
        if not src.exists():
            print(f"  skip (missing): {src_rel}")
            continue
        print(f"  move: {src_rel} -> library/{ACTIVE_NAME}/{dst_rel}")
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))

    for src_rel, dst_rel in DIR_MIGRATIONS:
        src = ROOT / src_rel
        dst = active_root / dst_rel
        if not src.exists():
            print(f"  skip (missing): {src_rel}/")
            continue
        n = len(list(src.iterdir()))
        print(f"  move: {src_rel}/ ({n} items) -> library/{ACTIVE_NAME}/{dst_rel}/")
        if not dry_run:
            # Move contents, not the directory itself
            for item in src.iterdir():
                shutil.move(str(item), str(dst / item.name))
            # Remove the now-empty source directory
            src.rmdir()

    # Step 5: archive old snapshots
    print("\n[5/6] Archiving old snapshots...")
    archived = 0
    for src_rel in ARCHIVE_FILES:
        src = ROOT / src_rel
        if not src.exists():
            continue
        dst = archive_root / src.name
        print(f"  archive: {src_rel} -> library/_archive/{src.name}")
        if not dry_run:
            shutil.move(str(src), str(dst))
        archived += 1
    print(f"  archived {archived} files")

    # Step 6: write manifest and active.txt
    print(f"\n[6/6] Writing manifest and active.txt...")
    # Try to count ontology contents BEFORE committing to moved state
    # (for dry run this still works because working.owl is unchanged)
    stats = count_ontology_contents() if dry_run else None

    manifest = {
        "name": ACTIVE_NAME,
        "description": (
            "Formal rendering of David R. Koepsell's A Structural "
            "Ontology of the Law (forthcoming, Palgrave 2026). "
            "Pilot artifact derived via BFO-grounded dialogue agent; "
            "Zenodo DOI 10.5281/zenodo.19713357."
        ),
        "source_text": "A Structural Ontology of the Law (Koepsell, forthcoming)",
        "author": "David R. Koepsell",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "stats": stats or {"note": "recompute after migration"},
        "migration": {
            "phase": 1,
            "from_version": "v1-singleton-working-owl",
            "backup_location": str(backup.relative_to(ROOT)),
        },
    }
    manifest_path = active_root / "manifest.json"
    active_path = ROOT / "ontology" / "active.txt"

    print(f"  write: {manifest_path}")
    print(f"  write: {active_path} (content: {ACTIVE_NAME})")
    if not dry_run:
        manifest_path.write_text(json.dumps(manifest, indent=2))
        active_path.write_text(ACTIVE_NAME + "\n")

    print("\n" + "=" * 60)
    print("MIGRATION COMPLETE" if not dry_run else "DRY RUN COMPLETE")
    print("=" * 60)
    if dry_run:
        print("\nRe-run with --apply to actually move files.")
    else:
        print(f"\nBackup saved to: {backup}")
        print(f"\nTo roll back:")
        print(f"  python {Path(__file__).name} --rollback")
        print(f"\nNext step: update app/config.py to use the new paths.")
    return 0


def do_rollback():
    backup = latest_backup()
    if not backup:
        print("No backup directory found. Cannot rollback automatically.")
        return 1
    print(f"Rolling back from {backup.name} ...")
    # Restore ontology/, sessions/, jobs/ from backup
    for name in ("ontology", "sessions", "jobs"):
        current = ROOT / name
        saved = backup / name
        if saved.exists():
            if current.exists():
                print(f"  removing current {name}/")
                shutil.rmtree(current)
            print(f"  restoring {name}/ from backup")
            shutil.copytree(saved, current, symlinks=True)
    print("Rollback complete. Your original layout is restored.")
    print(f"The backup directory is still at {backup} if you want to inspect.")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="Actually move files (default is dry-run)")
    ap.add_argument("--rollback", action="store_true",
                    help="Restore from the most recent backup")
    args = ap.parse_args()

    if args.rollback:
        return do_rollback()
    return do_migrate(dry_run=not args.apply)


if __name__ == "__main__":
    sys.exit(main())
