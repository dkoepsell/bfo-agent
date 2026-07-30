#!/usr/bin/env python3
"""Structural validator. Reproduces every line of the Phase 1 baseline audit table
so regressions are visible at any later phase.

Usage:  python scripts/validate.py [ontology/cfr404-base.owl ...] [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import (  # noqa: E402
    BFO, CFR, OBO, REL, ROOT, ancestors, load, local, named_classes, straddles, unanchored,
)

BFO_LABELS = {v: k for k, v in BFO.items()}
REL_LABELS = {v: k for k, v in REL.items()}


def audit(path: Path, ns: str = str(CFR)) -> dict:
    g = load(path)
    classes = named_classes(g, ns)

    individuals = [
        i for i in set(g.subjects(RDF.type, OWL.NamedIndividual))
        if isinstance(i, URIRef) and str(i).startswith(ns)
    ]

    # Direct BFO groundings: named class -> BFO parent asserted directly.
    direct = Counter()
    for c in classes:
        for p in g.objects(c, RDFS.subClassOf):
            if isinstance(p, URIRef) and p in BFO_LABELS:
                direct[BFO_LABELS[p]] += 1

    # Restriction predicate census.
    preds = Counter()
    for b in g.subjects(RDF.type, OWL.Restriction):
        for op in g.objects(b, OWL.onProperty):
            preds[REL_LABELS.get(op, local(op))] += 1

    # Blank-node type assertions that are neither restrictions nor complements is
    # the "anonymous parent" defect; complements are a legitimate exclusion idiom.
    anon_types = []
    for s, p in ((s, p) for p in (RDFS.subClassOf, RDF.type) for s in g.subjects(p, None)):
        for o in g.objects(s, p):
            if not isinstance(o, BNode):
                continue
            if (o, RDF.type, OWL.Restriction) in g:
                continue
            if (o, OWL.complementOf, None) in g:
                anon_types.append((str(s), "complementOf"))
            elif (o, OWL.unionOf, None) in g or (o, OWL.intersectionOf, None) in g:
                anon_types.append((str(s), "union/intersection"))
            else:
                anon_types.append((str(s), "bare"))

    disjoint = len(list(g.subject_objects(OWL.disjointWith))) + len(
        list(g.subjects(RDF.type, OWL.AllDisjointClasses))
    )
    complements = len(list(g.subject_objects(OWL.complementOf)))

    imports = [str(o) for o in g.objects(None, OWL.imports)]
    version = [str(o) for o in g.objects(None, OWL.versionIRI)]
    ont = [str(s) for s in g.subjects(RDF.type, OWL.Ontology)]

    st = straddles(g, ns)
    un = unanchored(g, ns)

    # Annotation properties are declared once in the imported kernel vocabulary, so
    # counting declarations in this file alone understates provenance. Count the
    # properties actually used in assertions too.
    declared_ann = set(g.subjects(RDF.type, OWL.AnnotationProperty))
    used_ann = {p for p in set(g.predicates()) if str(p).startswith(str(CFR))}
    ann_assertions = sum(1 for p in g.predicates() if str(p).startswith(str(CFR)))

    # Owlready2 must also parse the file; rdflib alone is not a strong enough gate.
    # Our own module IRIs are resolved from disk rather than fetched.
    try:
        import owlready2

        w = owlready2.World()
        for sibling in sorted((ROOT / "kernel").glob("*.owl")) + sorted(
                (ROOT / "ontology").glob("*.owl")):
            if sibling.resolve() == path.resolve():
                continue
            try:
                w.get_ontology(sibling.resolve().as_uri()).load(only_local=False)
            except Exception:
                pass
        w.get_ontology(path.resolve().as_uri()).load(only_local=False)
        owlready_ok = True
        owlready_err = None
    except Exception as exc:  # pragma: no cover - reported, not raised
        owlready_ok = False
        owlready_err = f"{type(exc).__name__}: {exc}"

    return {
        "file": str(path.relative_to(ROOT) if ROOT in path.parents else path),
        "named_classes": len(classes),
        "triples": len(g),
        "named_individuals": len(individuals),
        "object_properties": len(set(g.subjects(RDF.type, OWL.ObjectProperty))),
        "annotation_properties": len(declared_ann),
        "annotation_properties_used": sorted(local(p) for p in used_ann),
        "annotation_assertions": ann_assertions,
        "datatype_properties": len(set(g.subjects(RDF.type, OWL.DatatypeProperty))),
        "ontology_iri": ont,
        "version_iri": version,
        "imports": imports,
        "straddles": [str(x) for x in st],
        "straddle_count": len(st),
        "unanchored": [str(x) for x in un],
        "unanchored_count": len(un),
        "disjointness_axioms": disjoint,
        "complement_axioms": complements,
        "anon_type_bare": sum(1 for _, k in anon_types if k == "bare"),
        "anon_type_detail": Counter(k for _, k in anon_types),
        "direct_bfo_groundings": dict(direct.most_common()),
        "restriction_predicates": dict(preds.most_common()),
        "rdflib_ok": True,
        "owlready2_ok": owlready_ok,
        "owlready2_error": owlready_err,
    }


def render(a: dict) -> str:
    L = [f"### {a['file']}", ""]
    L.append("| Property | Value |")
    L.append("|---|---|")
    rows = [
        ("Named classes (local namespace)", f"{a['named_classes']:,}"),
        ("RDF triples", f"{a['triples']:,}"),
        ("Named individuals", f"{a['named_individuals']:,}"),
        ("Object properties", a["object_properties"]),
        ("Annotation properties declared here", a["annotation_properties"]),
        ("cfr: annotation assertions", f"{a['annotation_assertions']:,}"),
        ("cfr: annotation properties used", ", ".join(a["annotation_properties_used"]) or "**none**"),
        ("Datatype properties", a["datatype_properties"]),
        ("Ontology IRI", ", ".join(a["ontology_iri"]) or "—"),
        ("Version IRI", ", ".join(a["version_iri"]) or "**none**"),
        ("Imports", ", ".join(a["imports"]) or "**none**"),
        ("Continuant/occurrent straddles (K-C1)", a["straddle_count"]),
        ("Classes with no BFO ancestor", a["unanchored_count"]),
        ("Disjointness axioms", a["disjointness_axioms"]),
        ("Complement axioms", a["complement_axioms"]),
        ("Bare blank-node types", a["anon_type_bare"]),
        ("Parses under rdflib", "yes" if a["rdflib_ok"] else "no"),
        ("Parses under owlready2", "yes" if a["owlready2_ok"] else f"no ({a['owlready2_error']})"),
    ]
    for k, v in rows:
        L.append(f"| {k} | {v} |")
    L.append("")
    L.append("Direct BFO groundings: " + ", ".join(
        f"{k} {v}" for k, v in a["direct_bfo_groundings"].items()) + ".")
    L.append("")
    L.append("Restriction predicates: " + ", ".join(
        f"`{k}` {v}" for k, v in a["restriction_predicates"].items()) + ".")
    if a["unanchored"]:
        L.append("")
        L.append("Unanchored: " + ", ".join(local(x) for x in a["unanchored"][:20]))
    if a["straddles"]:
        L.append("")
        L.append("Straddles: " + ", ".join(local(x) for x in a["straddles"][:20]))
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*", default=None)
    ap.add_argument("--ns", default=str(CFR), help="local namespace prefix to count")
    ap.add_argument("--json", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if straddles, unanchored classes, or bare "
                         "blank-node types are present")
    args = ap.parse_args()

    files = [Path(f) for f in (args.files or [ROOT / "ontology" / "cfr404.owl"])]
    results = []
    for f in files:
        a = audit(f, args.ns)
        results.append(a)
        print(render(a))
        print()

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    if args.strict:
        bad = [a for a in results
               if a["straddle_count"] or a["unanchored_count"] or a["anon_type_bare"]
               or not a["owlready2_ok"]]
        if bad:
            print("STRICT FAIL: " + ", ".join(a["file"] for a in bad), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
