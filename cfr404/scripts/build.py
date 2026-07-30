#!/usr/bin/env python3
"""Merge the modules into the release artifact ``ontology/cfr404.owl``.

Merge order is the one the spec fixes:
    cfr404-base.owl + cfr404-chain.owl + cfr404-adjudication.owl -> cfr404.owl

Two things happen beyond a union:

* **Locus precedence.** A term can pick up a ``cfr:chainLocus`` from more than one
  module. The hand-authored chain scaffold outranks the Phase 2 backfill, which
  outranks extraction, because the scaffold is the module whose locus assignments
  were reasoned about one at a time. Any term left with more than one locus after
  precedence is a build error, not a shrug.
* **Straddle re-check.** Must still be zero. This is a headline number for §10 and
  is re-asserted here rather than assumed to have survived the merge.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import (  # noqa: E402
    BFO_IMPORT, CFR, CFR_BASE_IRI, CFR_VERSION_IRI, ROOT, load, local,
    named_classes, straddles, unanchored,
)

MODULES = [
    ("base", ROOT / "ontology" / "cfr404-base.owl", 2),
    ("chain", ROOT / "ontology" / "cfr404-chain.owl", 0),        # highest precedence
    ("adjudication", ROOT / "ontology" / "cfr404-adjudication.owl", 1),
]
VOCAB = ROOT / "kernel" / "kernel-vocab.owl"
OUT = ROOT / "ontology" / "cfr404.owl"
REPORT = ROOT / "ontology" / "build-report.json"


def main() -> int:
    merged = Graph()
    merged.bind("cfr", CFR)
    provenance_by_module: dict[str, int] = {}
    locus_claims: dict[URIRef, list[tuple[int, str, str]]] = defaultdict(list)
    grounding_conflicts: list[dict] = []

    # Provenance for every subClassOf edge, so a straddle created by the merge itself
    # can be repaired by dropping the edge from the least authoritative module rather
    # than by hand-patching the output.
    edge_origin: dict[tuple, tuple[int, str]] = {}

    for name, path, rank in MODULES:
        if not path.exists():
            print(f"missing module: {path}", file=sys.stderr)
            return 1
        g = load(path)
        provenance_by_module[name] = len(named_classes(g))
        for s, p, o in g:
            if p == CFR.chainLocus:
                locus_claims[s].append((rank, str(o), name))
                continue
            if p == RDFS.subClassOf and isinstance(s, URIRef) and isinstance(o, URIRef):
                edge_origin.setdefault((s, o), (rank, name))
            if p in (RDF.type, OWL.versionIRI, OWL.imports, RDFS.label, DCTERMS.license,
                     RDFS.comment) and s in (
                    URIRef("http://davidkoepsell.com/cfr404/chain"),
                    URIRef("http://davidkoepsell.com/cfr404/adjudication")):
                continue  # module headers do not become part of the release header
            merged.add((s, p, o))

    # Locus precedence: lowest rank wins; ties must agree.
    conflicts = []
    for subj, claims in locus_claims.items():
        best_rank = min(r for r, _, _ in claims)
        winners = {v for r, v, _ in claims if r == best_rank}
        if len(winners) > 1:
            conflicts.append({"term": local(subj),
                              "competing": sorted(winners),
                              "claims": [{"module": m, "locus": v} for _, v, m in claims]})
            continue
        value = winners.pop()
        merged.add((subj, CFR.chainLocus, Literal(value)))
        overridden = sorted({v for r, v, _ in claims if r != best_rank and v != value})
        if overridden:
            src = next(m for r, v, m in claims if r == best_rank)
            merged.add((subj, CFR.reviewNote, Literal(
                f"locus {value} from the {src} module takes precedence over "
                f"{', '.join(overridden)} asserted elsewhere")))

    if conflicts:
        print(f"BUILD ERROR: {len(conflicts)} term(s) with irreconcilable loci",
              file=sys.stderr)
        for c in conflicts[:10]:
            print("  ", c, file=sys.stderr)
        return 1

    # ---- repair straddles the merge itself created ---------------------------
    # A term the extractor minted can collide with one the baseline or the scaffold
    # already grounds. Where the two land on opposite sides of the
    # continuant/occurrent split, the more authoritative module wins: base and chain
    # were audited or authored term by term, the extractor generalized from a head
    # noun. The losing edge is removed and reported, never left in.
    # BFO's four top-level categories are pairwise disjoint, so a term landing in two
    # of them is not merely odd, it is unsatisfiable, and if anything instantiates it
    # the whole ontology goes inconsistent. Checking only the continuant/occurrent
    # split would miss the specifically-vs-generically dependent continuant collisions,
    # which is where the real disagreements between the modules turned out to be.
    from cfrlib import TOP_CATEGORIES, ancestors as _anc

    def top_category(node):
        anc = _anc(merged, node) | {node}
        return {name for name, members in TOP_CATEGORIES.items() if anc & members}

    for _ in range(6):
        conflicted = []
        for c in named_classes(merged):
            cats = top_category(c)
            if len(cats) > 1:
                conflicted.append(c)
        if not conflicted:
            break
        for c in conflicted:
            sided = []
            for parent in list(merged.objects(c, RDFS.subClassOf)):
                if not isinstance(parent, URIRef):
                    continue
                cats = top_category(parent)
                if len(cats) == 1:
                    rank, mod = edge_origin.get((c, parent), (99, "?"))
                    sided.append((parent, rank, mod, cats.pop()))
            if not sided:
                continue
            best_rank = min(r for _, r, _, _ in sided)
            keep = {s for _, r, _, s in sided if r == best_rank}
            if len(keep) != 1:
                continue
            keep_cat = keep.pop()
            for parent, rank, mod, cat in sided:
                if cat != keep_cat:
                    merged.remove((c, RDFS.subClassOf, parent))
                    grounding_conflicts.append({
                        "term": local(c), "kept_category": keep_cat,
                        "removed_parent": local(parent), "removed_category": cat,
                        "removed_from_module": mod,
                        "why": "merge-time collision across BFO's disjoint top-level "
                               "categories; the more authoritative module's grounding "
                               "is kept and the other edge dropped",
                    })

    # The same collision one level down. An individual can carry both a direct BFO
    # type and membership in a cfr class whose grounding a more authoritative module
    # has since settled elsewhere. Unlike the class case this makes the ontology
    # outright inconsistent rather than merely incoherent, because something now
    # instantiates two disjoint categories. The direct BFO assertion is the redundant
    # one - it restates what class membership already implies - so it is the edge that
    # goes when the two disagree.
    individual_conflicts: list[dict] = []
    for ind in sorted({i for i in merged.subjects(RDF.type, OWL.NamedIndividual)
                       if isinstance(i, URIRef)}, key=str):
        types = [t for t in merged.objects(ind, RDF.type)
                 if isinstance(t, URIRef) and t != OWL.NamedIndividual]
        cats = {}
        for t in types:
            tc = top_category(t)
            if len(tc) == 1:
                cats.setdefault(tc.pop(), []).append(t)
        if len(cats) < 2:
            continue
        class_types = {c: [t for t in ts if str(t).startswith(str(CFR))]
                       for c, ts in cats.items()}
        keep_cat = next((c for c, ts in class_types.items() if ts), None)
        if keep_cat is None:
            continue
        for cat, ts in cats.items():
            if cat == keep_cat:
                continue
            for t in ts:
                merged.remove((ind, RDF.type, t))
                individual_conflicts.append({
                    "individual": local(ind), "kept_category": keep_cat,
                    "kept_via": [local(x) for x in class_types[keep_cat]],
                    "removed_type": local(t), "removed_category": cat,
                    "why": "the individual instantiated two of BFO's disjoint top-level "
                           "categories, which makes the ontology inconsistent rather than "
                           "merely incoherent; the redundant direct BFO type assertion "
                           "was dropped in favour of its class membership",
                })

    # Release header.
    for s in list(merged.subjects(RDF.type, OWL.Ontology)):
        for p, o in list(merged.predicate_objects(s)):
            merged.remove((s, p, o))
    merged.add((CFR_BASE_IRI, RDF.type, OWL.Ontology))
    merged.add((CFR_BASE_IRI, OWL.versionIRI, CFR_VERSION_IRI))
    merged.add((CFR_BASE_IRI, OWL.imports, BFO_IMPORT))
    merged.add((CFR_BASE_IRI, OWL.imports, URIRef("http://davidkoepsell.com/cfr404/kernel")))
    merged.add((CFR_BASE_IRI, RDFS.label, Literal(
        "cfr404: a Stratum D control built from 20 CFR Part 404")))
    merged.add((CFR_BASE_IRI, DCTERMS.license,
                URIRef("https://creativecommons.org/licenses/by/4.0/")))
    merged.add((CFR_BASE_IRI, DCTERMS.created, Literal(str(date.today()))))
    merged.add((CFR_BASE_IRI, RDFS.comment, Literal(
        "A research-grade formalization of 20 CFR Part 404. It is not the regulation "
        "and must not be used to determine anyone's entitlement to benefits.")))

    merged.serialize(destination=str(OUT), format="pretty-xml")

    classes = named_classes(merged)
    st = straddles(merged)
    un = unanchored(merged)
    loci = Counter()
    missing = Counter()
    for c in classes:
        vals = [str(v) for v in merged.objects(c, CFR.chainLocus)]
        if vals:
            loci[vals[0]] += 1
        else:
            missing["chainLocus"] += 1
        for p in ("sourceSection", "extractionConfidence", "approved"):
            if not list(merged.objects(c, CFR[p])):
                missing[p] += 1

    report = {
        "generated": str(date.today()),
        "merge_order": [m[0] for m in sorted(MODULES, key=lambda m: 0)],
        "classes_per_module_before_merge": provenance_by_module,
        "named_classes_merged": len(classes),
        "triples": len(merged),
        "size_target": "2,000 to 2,500 named classes after merge",
        "size_target_met": 2000 <= len(classes) <= 2500,
        "straddles": [local(x) for x in st],
        "straddle_count": len(st),
        "unanchored_count": len(un),
        "unanchored": [local(x) for x in un][:40],
        "chain_locus_distribution": dict(sorted(loci.items())),
        "locus_precedence": "chain > adjudication > base",
        "locus_conflicts": conflicts,
        "grounding_collisions_repaired": grounding_conflicts,
        "grounding_collision_count": len(grounding_conflicts),
        "individual_type_collisions_repaired": individual_conflicts,
        "individual_type_collision_count": len(individual_conflicts),
        "stamp_gaps": dict(missing),
    }
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"merged -> {OUT.relative_to(ROOT)}")
    print(f"classes {len(classes)} (target 2000-2500: "
          f"{'met' if report['size_target_met'] else 'MISSED'}), triples {len(merged)}")
    print(f"straddles {len(st)}, unanchored {len(un)}")
    print("locus:", dict(sorted(loci.items())))
    if missing:
        print("stamp gaps:", dict(missing))
    return 0 if not st and not un else 1


if __name__ == "__main__":
    raise SystemExit(main())
