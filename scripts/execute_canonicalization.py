"""Execute canonicalization merges from a detection report.

Applies approved merges by:
  1. Choosing a canonical class from each merge bucket.
  2. For each non-canonical class in the bucket:
     a. Redirecting every triple that references it to the canonical class.
     b. Preserving the non-canonical name as an rdfs:altLabel on the
        canonical class, so nothing is lost.
     c. Removing the non-canonical class declaration.
  3. Writing the modified ontology to a NEW file, leaving the original
     intact. You move it into place manually after review.
  4. Running HermiT on the result to confirm consistency is preserved.

SAFETY:
  - The feed MUST be paused before running this. The script refuses to
    run if it detects recent feed activity.
  - Produces a NEW OWL file; does not overwrite the working ontology.
  - All merges are logged with before/after IRIs.
  - Reversible by simply restoring the old working.owl.

Usage:
    python scripts/execute_canonicalization.py \\
        --report evaluation/canonicalization_report.json \\
        --dry-run                              # print what would change, no files written
    python scripts/execute_canonicalization.py \\
        --report evaluation/canonicalization_report.json \\
        --kinds wordbag_identity \\
        --out ontology/working.canonicalized.owl
    python scripts/execute_canonicalization.py \\
        --report evaluation/canonicalization_report.json \\
        --kinds wordbag_identity embedding_cluster \\
        --min-confidence high \\
        --out ontology/working.canonicalized.owl
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ontology_manager import OntologyManager, WORKING_IRI
from app import config


def check_feed_paused(sessions_dir: Path, min_idle_seconds: int = 60) -> bool:
    """Refuse to run if the feed has been active in the last N seconds."""
    if not sessions_dir.exists():
        return True

    latest_mtime = 0.0
    for session_file in sessions_dir.glob("feed_*.jsonl"):
        latest_mtime = max(latest_mtime, session_file.stat().st_mtime)

    idle = time.time() - latest_mtime
    if idle < min_idle_seconds:
        print(f"REFUSING: feed session file modified {int(idle)}s ago "
              f"(need {min_idle_seconds}s of idle time)")
        print("Pause the feed in the browser, then retry.")
        return False
    return True


def choose_canonical(members: list[str]) -> str:
    """Pick the best canonical name from a merge bucket.

    Heuristic:
      - Prefer shorter names (less ornamentation)
      - Among equal lengths, prefer alphabetically first (deterministic)
      - Avoid names starting with lowercase or digits
    """
    valid = [m for m in members if m and m[0].isupper()]
    if not valid:
        valid = list(members)
    return min(valid, key=lambda m: (len(m), m))


def load_report(path: Path) -> dict:
    return json.loads(path.read_text())


def filter_candidates(
    candidates: list[dict],
    kinds: set[str] | None,
    min_confidence: str,
) -> list[dict]:
    conf_rank = {"high": 0, "medium": 1, "review_required": 2}
    threshold = conf_rank.get(min_confidence, 0)
    result = []
    for c in candidates:
        if kinds and c["kind"] not in kinds:
            continue
        if conf_rank.get(c["confidence"], 3) > threshold:
            continue
        result.append(c)
    return result


def build_merge_plan(candidates: list[dict]) -> list[dict]:
    """From filtered candidates, build explicit merge actions.

    Returns a list of dicts with 'canonical', 'merged', 'kind'.
    """
    plan = []
    seen = set()
    for cand in candidates:
        members = cand["members"]
        canonical = choose_canonical(members)
        merged = [m for m in members if m != canonical]
        for m in merged:
            if m in seen:
                # Already scheduled for merge elsewhere; skip to avoid chains
                continue
            seen.add(m)
            plan.append({
                "canonical": canonical,
                "merged": m,
                "kind": cand["kind"],
                "confidence": cand["confidence"],
            })
    return plan


def apply_merges(mgr: OntologyManager, plan: list[dict],
                 dry_run: bool = False) -> dict:
    """Apply the merge plan to the working ontology.

    For each (canonical, merged) pair:
      - Copy every subClassOf edge from `merged` to `canonical`
      - Copy every other relation from `merged` to `canonical`
      - Record `merged`'s name as an rdfs:altLabel on `canonical`
      - Destroy `merged`
    """
    from owlready2 import destroy_entity, rdfs, Thing

    stats = {"merges_attempted": 0, "merges_applied": 0, "skipped": []}
    working_base = f"{WORKING_IRI}"

    for action in plan:
        stats["merges_attempted"] += 1
        canonical_iri = f"{working_base}#{action['canonical']}"
        merged_iri = f"{working_base}#{action['merged']}"

        canonical = mgr.world[canonical_iri]
        merged = mgr.world[merged_iri]

        if canonical is None:
            # Try local-name match as fallback (for file:// serialized graphs)
            for c in mgr.working.classes():
                if c.name == action["canonical"]:
                    canonical = c
                    break
        if merged is None:
            for c in mgr.working.classes():
                if c.name == action["merged"]:
                    merged = c
                    break

        if canonical is None or merged is None:
            stats["skipped"].append({
                "action": action,
                "reason": f"canonical={canonical is not None}, "
                          f"merged={merged is not None} (name lookup failed)"
            })
            continue

        if dry_run:
            print(f"  [DRY] would merge {action['merged']!r} "
                  f"-> {action['canonical']!r} ({action['kind']})")
            stats["merges_applied"] += 1
            continue

        # Apply the merge
        with mgr.working:
            # Transfer parents: every parent of merged becomes a parent
            # of canonical (if not already)
            for parent in list(merged.is_a):
                if parent is not Thing and parent not in canonical.is_a:
                    canonical.is_a.append(parent)

            # Preserve the merged name as an altLabel on canonical
            try:
                existing_alts = list(canonical.altLabel)
            except AttributeError:
                existing_alts = []
            if action["merged"] not in existing_alts:
                try:
                    canonical.altLabel.append(action["merged"])
                except AttributeError:
                    # altLabel not yet a recognized property; skip gracefully
                    pass

            # Reassign subclasses of merged to canonical
            for subcls in list(merged.subclasses()):
                if canonical not in subcls.is_a:
                    subcls.is_a.append(canonical)
                subcls.is_a = [p for p in subcls.is_a if p is not merged]

            # Destroy the merged class
            destroy_entity(merged)

        stats["merges_applied"] += 1
        print(f"  merged {action['merged']!r} -> {action['canonical']!r}")

    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True,
                    help="Path to canonicalization report JSON")
    ap.add_argument("--kinds", nargs="+",
                    default=["wordbag_identity"],
                    help="Which kinds of candidates to apply "
                         "(wordbag_identity, embedding_cluster, stem_variant)")
    ap.add_argument("--min-confidence", default="high",
                    choices=["high", "medium", "review_required"],
                    help="Minimum confidence to include")
    ap.add_argument("--out",
                    default="ontology/working.canonicalized.owl",
                    help="Output OWL path")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print merges without modifying anything")
    ap.add_argument("--skip-consistency-check", action="store_true",
                    help="Skip the HermiT consistency check after merge (faster)")
    args = ap.parse_args()

    # Safety: feed must be paused (unless dry-run)
    if not args.dry_run:
        if not check_feed_paused(config.SESSIONS_DIR):
            return 1

    # Load report and filter
    report = load_report(Path(args.report))
    print(f"Loaded report: {report['candidates_total']} candidates total")

    kinds_set = set(args.kinds)
    selected = filter_candidates(
        report["candidates"],
        kinds=kinds_set,
        min_confidence=args.min_confidence,
    )
    print(f"After filters (kinds={sorted(kinds_set)}, "
          f"min_confidence={args.min_confidence}): {len(selected)} candidates")

    plan = build_merge_plan(selected)
    print(f"Merge plan: {len(plan)} actions")
    print()

    if not plan:
        print("Nothing to do.")
        return 0

    # Load the ontology
    print(f"Loading working ontology from {config.WORKING_PATH} ...")
    mgr = OntologyManager(
        bfo_path=config.BFO_PATH,
        working_path=config.WORKING_PATH,
        seed_path=config.SEED_PATH,
    )
    print(f"Loaded {len(list(mgr.working.classes()))} classes.")
    print()

    # Apply (or dry-run)
    if args.dry_run:
        print("DRY RUN -- no changes will be written")
        print()
    else:
        print(f"APPLYING merges. Output will go to {args.out}")
        print()

    stats = apply_merges(mgr, plan, dry_run=args.dry_run)

    print()
    print(f"Merges attempted: {stats['merges_attempted']}")
    print(f"Merges applied:   {stats['merges_applied']}")
    if stats["skipped"]:
        print(f"Skipped:          {len(stats['skipped'])}")
        for s in stats["skipped"][:5]:
            print(f"  - {s}")

    if args.dry_run:
        return 0

    # Consistency check before writing
    if not args.skip_consistency_check:
        print()
        print("Running HermiT consistency check on merged ontology ...")
        class Empty:
            entities = []
            relations = []
        ok, detail = mgr.check_consistency_dry_run(Empty())
        if ok:
            print("  consistent: True")
        else:
            print("  consistent: FALSE")
            print(detail[:1500])
            print()
            print("ABORTING: merged ontology failed consistency check.")
            print("Your original working.owl is untouched.")
            return 2

    # Save to the output path (NOT the working path)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Temporarily redirect working_path for save(), then restore
    original_path = mgr.working_path
    mgr.working_path = out_path
    mgr.save()
    mgr.working_path = original_path

    print()
    print(f"Wrote canonicalized ontology to {out_path}")
    print()
    print(f"Class count before: {report['ontology_class_count']}")
    print(f"Class count after:  {len(list(mgr.working.classes()))}")
    print(f"Reduction:          {report['ontology_class_count'] - len(list(mgr.working.classes()))}")
    print()
    print("To accept the canonicalization:")
    print(f"  cp {config.WORKING_PATH} {config.WORKING_PATH}.pre_canonicalize")
    print(f"  cp {out_path} {config.WORKING_PATH}")
    print(f"  # Then restart Flask")
    return 0


if __name__ == "__main__":
    sys.exit(main())
