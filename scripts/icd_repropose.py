"""Re-propose the reclaimed ICD-11 corpus through the real gate pipeline.

This is the rigour-injection step. The raw `icd11bfo.owl` was Claude-produced
and imported directly, so it never passed through
    proposer -> construction_linter (PC-1..PC-8) -> structural gate -> coherence
    gate -> kernel audit.
Here we build a fresh library entry, load the reconstructed claim corpus
(scripts.icd_reclaim), and feed every claim through `_feed_one_core` -- the same
loop that gives freshly-proposed ontologies their BFO anchoring, sibling
disjointness, non-circular definitions and defined classes.

MUST run on a box with an LLM backend (DGX/ollama, or Hetzner with a managed
key). The local dev box has neither. Use --limit for a cheap pilot first.

Usage:
    # 15-claim pilot (cheap; proves the mechanism end-to-end)
    python -m scripts.icd_repropose --claims icd_11_claims.json --limit 15

    # full run
    python -m scripts.icd_repropose --claims icd_11_claims.json

    # audit only, no LLM (diff the rebuilt ontology vs the baseline ledger)
    python -m scripts.icd_repropose --audit-only
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

NEW_NAME = "icd11bfo_v2"
DESC = "ICD-11 re-proposed through the BFO-agent gate (kernel-audited rigour pass)"


def _setup_entry(registry, source_name: str | None):
    """Create+activate the v2 library entry (idempotent-ish: reuse if present)."""
    names = {o["name"] if isinstance(o, dict) else o
             for o in registry.list_ontologies()}
    if NEW_NAME not in names:
        registry.create(
            name=NEW_NAME,
            description=DESC,
            source_text="Reclaimed ICD-11 claim corpus (see scripts/icd_reclaim.py)",
            author="BFO Agent",
            clone_seeds_from=source_name,  # inherit ICD's seed/relations if present
        )
    registry.activate(NEW_NAME)
    registry.reload()


def _load_job(claims_path: str, limit: int | None) -> str:
    from app import jobs as jobs_store
    claims = json.loads(Path(claims_path).read_text())
    if limit:
        claims = claims[:limit]
    job = jobs_store.create_job(
        name=f"icd11-repropose-{'pilot' if limit else 'full'}",
        meta={"source": "icd_reclaim", "fidelity": "faithful"},
    )
    jobs_store.append_claims(job["job_id"], claims)
    return job["job_id"]


def _feed_all(job_id: str):
    """Synchronous feed loop mirroring job_runner._run (no thread, with progress)."""
    from app import jobs as jobs_store
    from app import orchestrator
    fed = committed = flagged = 0
    t0 = time.time()
    while True:
        pending = jobs_store.next_pending(job_id, 1)
        if not pending:
            break
        res = orchestrator._feed_one_core(job_id, auto_accept=True)
        fed += 1
        verdict = (res or {}).get("verdict") or (res or {}).get("outcome")
        if (res or {}).get("committed"):
            committed += 1
        if verdict in ("FLAG", "flag", "curated_fail"):
            flagged += 1
        if fed % 25 == 0:
            rate = fed / max(1e-6, time.time() - t0)
            print(f"  fed {fed}  committed {committed}  flagged {flagged}  "
                  f"({rate:.2f} claims/s)", flush=True)
    print(f"DONE: fed {fed}, committed {committed}, flagged {flagged} "
          f"in {time.time()-t0:.0f}s")


def _audit_and_diff(registry, baseline_path: str, bfo_path: str, out_path: str):
    from app import kernel_audit
    working = registry.active_manager().working_path \
        if hasattr(registry.active_manager(), "working_path") else None
    # Fall back to config's resolved WORKING_PATH.
    if not working or not Path(str(working)).exists():
        from app import config
        working = config.WORKING_PATH
    ledger = kernel_audit.audit(str(working), bfo_path)
    Path(out_path).write_text(json.dumps(ledger, indent=2))

    print("\n=== rebuilt ontology audit ===")
    m = ledger["metrics"]
    print(f"  definition coverage .. {m['classes_with_definition']} "
          f"({m['definition_coverage_pct']}%)")
    print(f"  defined classes ...... {m['defined_classes_equivalentClass']}")
    print(f"  disjointWith axioms .. {m['disjointWith_axioms']}")
    print(f"  object properties .... {m['object_properties']}")
    print(f"  kernel findings ...... {ledger['finding_counts']}")

    if Path(baseline_path).exists():
        base = json.loads(Path(baseline_path).read_text())
        print("\n=== diff vs baseline ===")
        bm = base["metrics"]
        for k in ("definition_coverage_pct", "defined_classes_equivalentClass",
                  "disjointWith_axioms", "object_properties"):
            print(f"  {k}: {bm[k]} -> {m[k]}")
        for prim in sorted(set(base["finding_counts"]) | set(ledger["finding_counts"])):
            print(f"  {prim}: {base['finding_counts'].get(prim,0)} "
                  f"-> {ledger['finding_counts'].get(prim,0)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Re-propose ICD-11 through the gate")
    ap.add_argument("--claims", default="icd_11_claims.json")
    ap.add_argument("--source-name", default="icd11bfo",
                    help="existing library entry to clone seeds from")
    ap.add_argument("--limit", type=int, default=None, help="pilot: first N claims")
    ap.add_argument("--baseline", default="icd_kernel_ledger_baseline.json")
    ap.add_argument("--bfo", default="bfo.owl")
    ap.add_argument("--out", default="icd_kernel_ledger_rebuilt.json")
    ap.add_argument("--audit-only", action="store_true",
                    help="skip LLM; just audit the active v2 ontology and diff")
    args = ap.parse_args(argv)

    from app.registry import OntologyRegistry
    from app import config
    registry = OntologyRegistry(
        bfo_path=config.BFO_PATH,
        library_root=config.LIBRARY_ROOT,
        active_name=config.active_ontology_name(),
    )

    if not args.audit_only:
        _setup_entry(registry, args.source_name)
        job_id = _load_job(args.claims, args.limit)
        print(f"loaded job {job_id}; feeding through gate "
              f"(limit={args.limit or 'all'}) ...")
        _feed_all(job_id)

    _audit_and_diff(registry, args.baseline, args.bfo, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
