#!/usr/bin/env python3
"""ontology_audit.py: read-only quality audit for bfo-agent OWL artifacts.

Implements the metrics defined in SPEC-bfo-agent-quality.md (section G).
Never writes to the ontology. Output is a JSON report plus a short
human-readable summary; `--fail-on` turns selected metrics into CI gates.

Usage:
    python scripts/ontology_audit.py path/to/working.owl
    python scripts/ontology_audit.py working.owl --json out.json
    python scripts/ontology_audit.py working.owl --fail-on visibility_ratio<0.99 \
        --fail-on malformed_iris>0 --fail-on punned_triples>0
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import warnings

import rdflib
from rdflib import OWL, RDF, RDFS, BNode, URIRef

OBO = "http://purl.obolibrary.org/obo/"
IAO_DEF = URIRef(OBO + "IAO_0000115")
SKOS_DEF = URIRef("http://www.w3.org/2004/02/skos/core#definition")
STD_NS = (str(RDF), str(RDFS), str(OWL), "http://www.w3.org/2001/XMLSchema#")
ANNOTATION_PREDICATES = {
    RDFS.label, RDFS.comment, RDFS.seeAlso, RDFS.isDefinedBy, IAO_DEF, SKOS_DEF,
    OWL.versionInfo, OWL.deprecated,
}
UPPER_ID = re.compile(r"(?:BFO|RO|IAO)_\d+$")
BAD_CHARS = re.compile(r'[\s<>"{}|\\^`]')
META_LABEL = re.compile(r"\((?:reuse|instance|class|example[^)]*)\)|^example", re.I)
SUFFIXES = {"process", "disposition", "quality", "role", "function", "legal",
            "the", "of", "class", "act"}
FANOUT_LIMIT = 40


def is_std(p) -> bool:
    return any(str(p).startswith(ns) for ns in STD_NS)


def label(g, x) -> str:
    v = g.value(x, RDFS.label)
    return str(v) if v is not None else re.split(r"[#/]", str(x))[-1]


def named_parents(g, c):
    return [p for p in g.objects(c, RDFS.subClassOf) if isinstance(p, URIRef)]


def ancestors(g, c, cache):
    if c in cache:
        return cache[c]
    cache[c] = set()  # cycle guard
    out = set()
    for p in named_parents(g, c):
        out.add(p)
        out |= ancestors(g, p, cache)
    cache[c] = out
    return out


def norm_key(lbl: str) -> str:
    lbl = re.sub(r"\(.*?\)", "", lbl)
    lbl = re.sub(r"([a-z])([A-Z])", r"\1 \2", lbl).lower()
    toks = {t for t in re.findall(r"[a-z]+", lbl) if t not in SUFFIXES}
    return " ".join(sorted(toks))


def audit(path: str) -> dict:
    g = rdflib.Graph()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g.parse(path)

    classes = {c for c in g.subjects(RDF.type, OWL.Class) if isinstance(c, URIRef)}
    local = {c for c in classes if not str(c).startswith(OBO)}
    inds = set(g.subjects(RDF.type, OWL.NamedIndividual))
    declared_props = set(g.subjects(RDF.type, OWL.ObjectProperty))
    restrictions = list(g.subjects(RDF.type, OWL.Restriction))

    # ---- malformed IRIs (construction-tier defects) -------------------
    # "Local" means any IRI outside OBO and the W3C vocabularies, so artifacts
    # that mix an http working namespace with a file:// base are both covered.
    def is_local(t) -> bool:
        return isinstance(t, URIRef) and not str(t).startswith(OBO) and not is_std(t)

    malformed = collections.Counter()
    malformed_ex = collections.defaultdict(list)
    file_ns = set()
    for t in {x for tr in g for x in tr if is_local(x)}:
        s = str(t)
        if s.startswith("file:"):
            file_ns.add(s.split("#", 1)[0])
        frag = re.split(r"[#/]", s, maxsplit=0)[-1] if "#" not in s else s.split("#", 1)[1]
        kind = None
        if BAD_CHARS.search(frag):
            kind = "whitespace_or_illegal_char"
        elif ":" in frag:
            kind = "curie_in_fragment"
        elif UPPER_ID.match(frag):
            kind = "upper_ontology_id_in_local_ns"
        if kind:
            malformed[kind] += 1
            if len(malformed_ex[kind]) < 5:
                malformed_ex[kind].append(frag[:80])

    # ---- relation usage ------------------------------------------------
    known_std = {RDF.type, RDFS.subClassOf, RDFS.subPropertyOf, RDFS.domain,
                 RDFS.range, RDF.first, RDF.rest}
    used_props = collections.Counter()
    punned = collections.Counter()
    ind_assertions = 0
    mangled_std = 0
    for s, p, o in g:
        pf = str(p).split("#", 1)[-1]
        if is_local(p) and re.match(r"^(rdf|rdfs|owl):", pf):
            mangled_std += 1          # e.g. working#rdfs:subClassOf
            continue
        if str(p).startswith(str(OWL)) and pf in ("subClassOf", "type"):
            mangled_std += 1          # e.g. owl:subClassOf (no such term)
            continue
        if is_std(p) or p in ANNOTATION_PREDICATES or not isinstance(s, URIRef):
            continue
        if not isinstance(o, URIRef):
            continue
        used_props[p] += 1
        sk = "C" if s in classes else ("I" if s in inds else "?")
        ok = "C" if o in classes else ("I" if o in inds else "?")
        if sk == "I" and ok == "I":
            ind_assertions += 1
        else:
            punned[f"{sk}->{ok}"] += 1

    undeclared = sorted(str(p) for p in used_props if p not in declared_props)
    unused_declared = sorted(str(p) for p in declared_props if p not in used_props
                             and not any(g.triples((None, OWL.onProperty, p))))

    # ---- restrictions -----------------------------------------------------
    empty_r = 0
    upper_filler = 0
    domain_filler = 0
    restr_props = collections.Counter()
    for r in restrictions:
        prop = g.value(r, OWL.onProperty)
        filler = (g.value(r, OWL.someValuesFrom) or g.value(r, OWL.allValuesFrom)
                  or g.value(r, OWL.hasValue))
        if prop is None or filler is None:
            empty_r += 1
            continue
        restr_props[str(prop)] += 1
        if isinstance(filler, URIRef) and str(filler).startswith(OBO):
            upper_filler += 1
        else:
            domain_filler += 1

    # ---- visibility ratio -------------------------------------------------
    real_sub = sum(1 for s, o in g.subject_objects(RDFS.subClassOf) if s in local)
    class_assert = sum(1 for i in inds for t in g.objects(i, RDF.type)
                       if t != OWL.NamedIndividual)
    disj = len(list(g.triples((None, OWL.disjointWith, None)))) + \
        len(list(g.subjects(RDF.type, OWL.AllDisjointClasses)))
    equiv = len(list(g.triples((None, OWL.equivalentClass, None))))
    visible = real_sub + class_assert + ind_assertions + disj + equiv
    invisible = sum(punned.values()) + mangled_std
    intended = visible + invisible
    visibility = visible / intended if intended else 1.0

    # ---- hierarchy shape -------------------------------------------------
    cache: dict = {}
    redundant = 0
    multi_parent = 0
    for c in local:
        ps = named_parents(g, c)
        if len(ps) > 1:
            multi_parent += 1
        for p in ps:
            if any(p in ancestors(g, q, cache) for q in ps if q != p):
                redundant += 1
    fan = collections.Counter()
    for s, o in g.subject_objects(RDFS.subClassOf):
        if s in local and isinstance(o, URIRef):
            fan[o] += 1
    hubs = [(label(g, k), v) for k, v in fan.most_common() if v > FANOUT_LIMIT]
    no_anchor = [label(g, c) for c in local
                 if not any(str(a).startswith(OBO) for a in ancestors(g, c, cache))]

    # ---- annotations and labels ------------------------------------------
    defined = sum(1 for c in local
                  if any((c, p, None) in g for p in (IAO_DEF, SKOS_DEF)))
    commented = sum(1 for c in local if (c, RDFS.comment, None) in g)
    meta_labels = sorted(label(g, x) for x in local | inds if META_LABEL.search(label(g, x)))
    clusters = collections.defaultdict(list)
    for c in local:
        clusters[norm_key(label(g, c))].append(label(g, c))
    syn = sorted((v for v in clusters.values() if len(v) > 1), key=len, reverse=True)

    local_disj = sum(1 for s, o in g.subject_objects(OWL.disjointWith)
                     if not str(s).startswith(OBO) or not str(o).startswith(OBO))

    return {
        "file": path,
        "counts": {
            "local_classes": len(local), "individuals": len(inds),
            "restrictions": len(restrictions), "declared_object_properties": len(declared_props),
        },
        "malformed_iris": sum(malformed.values()),
        "malformed_breakdown": dict(malformed),
        "malformed_examples": dict(malformed_ex),
        "file_scheme_namespaces": sorted(file_ns),
        "mangled_standard_predicates": mangled_std,
        "punned_triples": sum(punned.values()),
        "punned_breakdown": dict(punned),
        "visibility_ratio": round(visibility, 4),
        "visible_axioms": visible, "invisible_axioms": invisible,
        "undeclared_properties_used": undeclared,
        "declared_properties_unused": unused_declared,
        "empty_restrictions": empty_r,
        "restrictions_upper_level_filler": upper_filler,
        "restrictions_domain_filler": domain_filler,
        "domain_filler_share": round(domain_filler / max(1, upper_filler + domain_filler), 4),
        "definition_coverage": round(defined / max(1, len(local)), 4),
        "comment_coverage": round(commented / max(1, len(local)), 4),
        "multi_parent_classes": multi_parent,
        "redundant_parent_assertions": redundant,
        "max_fanout": fan.most_common(1)[0][1] if fan else 0,
        "hubs_over_limit": hubs,
        "unanchored_classes": no_anchor,
        "local_disjointness_axioms": local_disj,
        "synonym_clusters": len(syn),
        "classes_in_synonym_clusters": sum(len(v) for v in syn),
        "synonym_cluster_examples": syn[:10],
        "meta_labels": len(meta_labels),
        "meta_label_examples": meta_labels[:15],
    }


OPS = {"<": lambda a, b: a < b, ">": lambda a, b: a > b,
       "<=": lambda a, b: a <= b, ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}


def check_gates(report: dict, gates: list[str]) -> list[str]:
    failures = []
    for gate in gates:
        m = re.match(r"^(\w+)\s*(<=|>=|==|<|>)\s*([\d.]+)$", gate)
        if not m:
            raise SystemExit(f"bad --fail-on expression: {gate}")
        key, op, val = m.group(1), m.group(2), float(m.group(3))
        if key not in report:
            raise SystemExit(f"unknown metric in --fail-on: {key}")
        v = report[key]
        v = len(v) if isinstance(v, list) else v
        if OPS[op](v, val):
            failures.append(f"{key}={v} violates gate '{gate}'")
    return failures


def summary(r: dict) -> str:
    c = r["counts"]
    lines = [
        f"{r['file']}",
        f"  classes {c['local_classes']}, individuals {c['individuals']}, "
        f"restrictions {c['restrictions']}, declared props {c['declared_object_properties']}",
        f"  visibility ratio {r['visibility_ratio']} "
        f"({r['invisible_axioms']} reasoner-invisible of {r['visible_axioms'] + r['invisible_axioms']})",
        f"  malformed IRIs {r['malformed_iris']} {r['malformed_breakdown']}; "
        f"mangled std predicates {r['mangled_standard_predicates']}; punned {r['punned_triples']}",
        f"  file:// namespaces {len(r['file_scheme_namespaces'])}",
        f"  undeclared props used {len(r['undeclared_properties_used'])}; "
        f"declared unused {len(r['declared_properties_unused'])}",
        f"  restrictions: empty {r['empty_restrictions']}, domain-filler share {r['domain_filler_share']}",
        f"  definition coverage {r['definition_coverage']}, comment coverage {r['comment_coverage']}",
        f"  redundant parents {r['redundant_parent_assertions']}, max fan-out {r['max_fanout']}, "
        f"hubs>{FANOUT_LIMIT}: {len(r['hubs_over_limit'])}",
        f"  local disjointness axioms {r['local_disjointness_axioms']}",
        f"  synonym clusters {r['synonym_clusters']} covering {r['classes_in_synonym_clusters']} classes; "
        f"meta labels {r['meta_labels']}",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("owl")
    ap.add_argument("--json", help="write full JSON report here")
    ap.add_argument("--fail-on", action="append", default=[],
                    help="metric gate, e.g. 'visibility_ratio<0.99'")
    a = ap.parse_args(argv)
    r = audit(a.owl)
    print(summary(r))
    if a.json:
        with open(a.json, "w") as fh:
            json.dump(r, fh, indent=2)
    failures = check_gates(r, a.fail_on)
    for f in failures:
        print("FAIL:", f, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
