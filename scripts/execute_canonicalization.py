"""Execute canonicalization merges from a detection report.

This version works at the RDF triple level using rdflib, which avoids
the owlready2 class-lifecycle issues that made the previous
implementation silently not-actually-delete merged classes.

For each (canonical, merged) pair:
  1. Every triple (merged, P, O) becomes (canonical, P, O).
  2. Every triple (S, P, merged) becomes (S, P, canonical).
  3. The merged local name is added as a skos:altLabel on canonical.
  4. Self-subclass loops created by the rewrite are removed.
  5. Duplicate triples are implicitly removed by set semantics.
  6. The modified graph is written as RDF/XML, and HermiT is run on
     the result.

SAFETY:
  - The feed MUST be paused before running this.
  - Produces a NEW OWL file; does not overwrite the working ontology.
  - Reversible by restoring the original working.owl.

Usage:
    python scripts/execute_canonicalization.py \\
        --report evaluation/canonicalization_report.json --dry-run
    python scripts/execute_canonicalization.py \\
        --report evaluation/canonicalization_report.json \\
        --out ontology/working.canonicalized.owl
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config


WORKING_BASE = "http://davidkoepsell.com/bfo-agent/working"


def check_feed_paused(sessions_dir: Path, min_idle_seconds: int = 60) -> bool:
    if not sessions_dir.exists():
        return True
    latest = 0.0
    for s in sessions_dir.glob("feed_*.jsonl"):
        latest = max(latest, s.stat().st_mtime)
    idle = time.time() - latest
    if idle < min_idle_seconds:
        print(f"REFUSING: feed session modified {int(idle)}s ago; "
              f"need {min_idle_seconds}s idle.")
        return False
    return True


def choose_canonical(members: list[str]) -> str:
    valid = [m for m in members if m and m[0].isupper()]
    if not valid:
        valid = list(members)
    return min(valid, key=lambda m: (len(m), m))


def filter_candidates(cands, kinds, min_confidence):
    conf_rank = {"high": 0, "medium": 1, "review_required": 2}
    threshold = conf_rank.get(min_confidence, 0)
    out = []
    for c in cands:
        if kinds and c["kind"] not in kinds:
            continue
        if conf_rank.get(c["confidence"], 3) > threshold:
            continue
        out.append(c)
    return out


def build_merge_plan(cands):
    plan, seen = [], set()
    for c in cands:
        members = c["members"]
        canonical = choose_canonical(members)
        for m in members:
            if m == canonical or m in seen:
                continue
            seen.add(m)
            plan.append({"canonical": canonical, "merged": m,
                         "kind": c["kind"], "confidence": c["confidence"]})
    return plan


def resolve_class_iri(graph, name):
    """Find the full IRI for a class by local name."""
    from rdflib import URIRef, RDF, OWL
    candidate = URIRef(f"{WORKING_BASE}#{name}")
    if (candidate, RDF.type, OWL.Class) in graph:
        return str(candidate)
    for s in graph.subjects(RDF.type, OWL.Class):
        s_str = str(s)
        if s_str.endswith(f"#{name}") or s_str.endswith(f"/{name}"):
            return s_str
    return None


def apply_merges_rdflib(input_path, output_path, plan, dry_run=False):
    from rdflib import Graph, URIRef, Literal, RDF, RDFS, OWL
    SKOS_ALT = URIRef("http://www.w3.org/2004/02/skos/core#altLabel")

    print(f"Loading {input_path} as RDF ...")
    g = Graph()
    g.parse(str(input_path), format="xml")
    print(f"  loaded {len(g)} triples")

    iri_map, skipped = {}, []
    for a in plan:
        m_iri = resolve_class_iri(g, a["merged"])
        c_iri = resolve_class_iri(g, a["canonical"])
        if not m_iri or not c_iri:
            skipped.append({"action": a,
                            "reason": f"m={bool(m_iri)}, c={bool(c_iri)}"})
            continue
        iri_map[URIRef(m_iri)] = URIRef(c_iri)
    print(f"  resolved {len(iri_map)} merges, skipped {len(skipped)}")

    if dry_run:
        for i, (m, c) in enumerate(list(iri_map.items())[:10]):
            mn = str(m).rsplit('#', 1)[-1].rsplit('/', 1)[-1]
            cn = str(c).rsplit('#', 1)[-1].rsplit('/', 1)[-1]
            print(f"  [DRY] {mn} -> {cn}")
        if len(iri_map) > 10:
            print(f"  ... and {len(iri_map) - 10} more")
        return {"merges_attempted": len(plan),
                "merges_resolved": len(iri_map),
                "skipped": skipped, "triples_before": len(g),
                "triples_after": len(g)}

    # Step 1: preserve merged names as altLabels
    altlabel_adds = 0
    for merged, canonical in iri_map.items():
        mn = str(merged).rsplit('#', 1)[-1].rsplit('/', 1)[-1]
        labels = [Literal(mn)]
        for _, _, lit in g.triples((merged, RDFS.label, None)):
            labels.append(lit)
        for lbl in labels:
            t = (canonical, SKOS_ALT, lbl)
            if t not in g:
                g.add(t)
                altlabel_adds += 1

    # Step 2: rewrite all triples
    to_remove, to_add = [], []
    for s, p, o in g:
        ns = iri_map.get(s, s)
        no = iri_map.get(o, o) if isinstance(o, URIRef) else o
        if ns != s or no != o:
            to_remove.append((s, p, o))
            to_add.append((ns, p, no))
    for t in to_remove:
        g.remove(t)
    for t in to_add:
        g.add(t)

    # Step 3: belt-and-suspenders
    for merged in iri_map.keys():
        g.remove((merged, None, None))
        g.remove((None, None, merged))

    # Step 4: remove self-subclass loops
    self_loops = [(s, p, o) for s, p, o in g.triples((None, RDFS.subClassOf, None))
                  if s == o]
    for t in self_loops:
        g.remove(t)

    print(f"  triples rewritten: {len(to_remove)}")
    print(f"  altLabel additions: {altlabel_adds}")
    print(f"  self-loops removed: {len(self_loops)}")
    print(f"  final triples: {len(g)}")

    print(f"Writing {output_path} ...")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(output_path), format="xml")

    return {"merges_attempted": len(plan),
            "merges_resolved": len(iri_map),
            "skipped": skipped,
            "triples_rewritten": len(to_remove),
            "altlabel_adds": altlabel_adds,
            "self_loops_removed": len(self_loops),
            "triples_after": len(g)}


def verify_output(output_path):
    from owlready2 import World, onto_path
    onto_path.append(str(config.BFO_PATH.parent))
    w = World()
    onto = w.get_ontology(f"file://{output_path.resolve()}").load()
    classes = list(onto.classes())
    return len(classes)


def run_consistency_check(output_path):
    from app.ontology_manager import OntologyManager
    mgr = OntologyManager(
        bfo_path=config.BFO_PATH,
        working_path=output_path,
        seed_path=config.SEED_PATH,
    )
    class Empty:
        entities = []
        relations = []
    return mgr.check_consistency_dry_run(Empty())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--kinds", nargs="+", default=["wordbag_identity"])
    ap.add_argument("--min-confidence", default="high",
                    choices=["high", "medium", "review_required"])
    ap.add_argument("--out", default="ontology/working.canonicalized.owl")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-consistency-check", action="store_true")
    args = ap.parse_args()

    if not args.dry_run and not check_feed_paused(config.SESSIONS_DIR):
        return 1

    report = json.loads(Path(args.report).read_text())
    print(f"Loaded report: {report['candidates_total']} candidates total")

    selected = filter_candidates(report["candidates"], set(args.kinds),
                                  args.min_confidence)
    print(f"After filters (kinds={sorted(args.kinds)}, "
          f"min_confidence={args.min_confidence}): {len(selected)} candidates")
    plan = build_merge_plan(selected)
    print(f"Merge plan: {len(plan)} actions\n")

    if not plan:
        print("Nothing to do.")
        return 0

    input_path = Path(config.WORKING_PATH)
    output_path = Path(args.out)

    stats = apply_merges_rdflib(input_path, output_path, plan,
                                 dry_run=args.dry_run)
    print()
    print(f"Merges attempted: {stats['merges_attempted']}")
    print(f"Merges resolved:  {stats['merges_resolved']}")
    if stats.get("skipped"):
        print(f"Skipped:          {len(stats['skipped'])}")

    if args.dry_run:
        return 0

    print()
    print("Verifying output file ...")
    n_classes = verify_output(output_path)
    print(f"  classes in output: {n_classes}")

    if not args.skip_consistency_check:
        print()
        print("Running HermiT on canonicalized ontology ...")
        ok, detail = run_consistency_check(output_path)
        print(f"  consistent: {ok}")
        if not ok:
            print(detail[:1500])
            return 2

    print()
    print("=" * 60)
    print("CANONICALIZATION COMPLETE")
    print("=" * 60)
    print(f"  Output file:     {output_path}")
    print(f"  Classes before:  {report['ontology_class_count']}")
    print(f"  Classes after:   {n_classes}")
    print(f"  Reduction:       {report['ontology_class_count'] - n_classes}")
    print()
    print("To accept:")
    print(f"  cp {config.WORKING_PATH} {config.WORKING_PATH}.pre_canonicalize")
    print(f"  cp {output_path} {config.WORKING_PATH}")
    print("  # Restart Flask")
    return 0


if __name__ == "__main__":
    sys.exit(main())
