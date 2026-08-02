"""Contradiction-Kernel audit of an OWL ontology (Strata A / B / C).

Static, reasoner-optional detector that scores an imported ontology against
the Contradiction Kernel typology (Koepsell, Contradiction Kernel v1.0) and the
BFO-ISO conformance clauses (ISO/IEC 21838-2:2021). It produces an *evidence*
ledger only -- it never edits the ontology (extraction-fidelity principle).

Strata detected here (Stratum D is pragmatic/act-involving and does not apply
to a scientific reference ontology such as ICD-11):

  A -- inconsistency
    K-A1  individual asserted under an (ancestrally) disjoint pair of types
    K-A3b class with neither an IAO definition nor an equivalentClass
          (declared-never-defined; the BFO-ISO "universal without a definition")
  B -- definitional incoherence
    K-B1  subClassOf cycle (definitional circularity)
    K-B3  residual category (equiv to complement-only intersection, or an
          "other specified / unspecified / NEC" label)
  C -- category / dependence violation
    K-C1  class whose BFO ancestor set contains a disjoint pair (a straddle,
          e.g. modelled as both disposition and process)
    K-C2  punning: one IRI used as both an owl:Class and an owl:NamedIndividual
    K-C3  specifically-dependent individual (disposition/quality/role/function)
          asserted with no bearer / realization relation

Usage:
    python -m app.kernel_audit TARGET.owl [--bfo bfo.owl] [--out ledger.json]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

BFO = "http://purl.obolibrary.org/obo/BFO_"
IAO_DEFINITION = URIRef("http://purl.obolibrary.org/obo/IAO_0000115")

# BFO-2020 specifically-dependent leaves that require a bearer/realization.
SDC_BFO = {
    "BFO_0000019",  # quality
    "BFO_0000020",  # specifically dependent continuant
    "BFO_0000017",  # realizable entity
    "BFO_0000016",  # disposition
    "BFO_0000034",  # function
    "BFO_0000023",  # role
}
# Object properties that discharge the K-C3 "needs a bearer" obligation.
BEARER_PROPS = {
    "RO_0000052",   # inheres in
    "RO_0000053",   # bearer of (inverse; still evidence of a link)
    "BFO_0000197",  # inheres in (BFO)
    "RO_0002314",   # inheres in part of
    "BFO_0000054",  # realized in
    "RO_0000091",   # has disposition (inverse)
}
RESIDUAL_LABEL_MARKERS = (
    "other specified",
    "unspecified",
    "not elsewhere classified",
    "nec",
    "other, ",
    "residual",
)


def _local(u) -> str:
    s = str(u)
    for sep in ("#", "/"):
        if sep in s:
            s = s.rsplit(sep, 1)[-1]
    return s


def _load(target: str, bfo_path: str | None) -> tuple[rdflib.Graph, set[str]]:
    """Parse target, then merge BFO for its disjointness/taxonomy axioms.

    Returns the merged graph and the set of *target-owned* class/individual
    IRIs (recorded before the BFO merge) so findings and coverage metrics are
    not contaminated by BFO's own classes.
    """
    g = rdflib.Graph()
    g.parse(target)
    owned = {str(s) for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
    owned |= {str(s) for s in g.subjects(RDF.type, OWL.NamedIndividual)
              if isinstance(s, URIRef)}
    if bfo_path and Path(bfo_path).exists():
        # Merge BFO so the disjointWith axioms and upper taxonomy are visible
        # even when the target only `owl:imports` them.
        g.parse(bfo_path)
    return g, owned


def _disjoint_pairs(g: rdflib.Graph) -> set[frozenset]:
    pairs = set()
    for a, b in g.subject_objects(OWL.disjointWith):
        if isinstance(a, URIRef) and isinstance(b, URIRef):
            pairs.add(frozenset((str(a), str(b))))
    return pairs


def _ancestors(g: rdflib.Graph) -> dict[str, set[str]]:
    """Reflexive-transitive rdfs:subClassOf closure over named classes."""
    parents = collections.defaultdict(set)
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            parents[str(s)].add(str(o))
    closure: dict[str, set[str]] = {}

    def anc(node: str, seen: set[str]) -> set[str]:
        if node in closure:
            return closure[node]
        acc = set()
        for p in parents.get(node, ()):  # noqa: PLC0206
            if p in seen:
                continue  # cycle guard; K-B1 reports it separately
            acc.add(p)
            acc |= anc(p, seen | {p})
        closure[node] = acc
        return acc

    for c in list(parents):
        anc(c, {c})
    return closure


def _cycles(g: rdflib.Graph) -> list[list[str]]:
    parents = collections.defaultdict(set)
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            parents[str(s)].add(str(o))
    found, seen = [], set()
    WHITE, GREY, BLACK = 0, 1, 2
    color = collections.defaultdict(int)
    stack: list[str] = []

    def dfs(u: str):
        color[u] = GREY
        stack.append(u)
        for v in parents.get(u, ()):
            if color[v] == GREY:  # back-edge -> cycle
                cyc = stack[stack.index(v):] + [v]
                key = frozenset(cyc)
                if key not in seen:
                    seen.add(key)
                    found.append(cyc)
            elif color[v] == WHITE:
                dfs(v)
        stack.pop()
        color[u] = BLACK

    for n in list(parents):
        if color[n] == WHITE:
            dfs(n)
    return found


def audit(target: str, bfo_path: str | None = None, sample: int = 25) -> dict:
    g, owned = _load(target, bfo_path)
    named = {str(c) for c in g.subjects(RDF.type, OWL.Class)
             if isinstance(c, URIRef) and str(c) in owned}
    individuals = {str(i) for i in g.subjects(RDF.type, OWL.NamedIndividual)
                   if str(i) in owned}
    labels = {str(s): str(o) for s, o in g.subject_objects(RDFS.label)}
    disj = _disjoint_pairs(g)
    anc = _ancestors(g)

    findings: dict[str, list[dict]] = collections.defaultdict(list)

    def add(prim: str, iri: str, detail: str):
        findings[prim].append(
            {"iri": iri, "label": labels.get(iri, _local(iri)), "detail": detail}
        )

    # ---- Stratum C: K-C1 BFO straddle ------------------------------------
    def disjoint_hit(cats: set[str]) -> tuple[str, str] | None:
        for pair in disj:
            a, b = tuple(pair)
            if a in cats and b in cats:
                return a, b
        return None

    for c in named:
        cats = {a for a in anc.get(c, set()) if a.startswith(BFO)}
        hit = disjoint_hit(cats)
        if hit:
            add("K-C1", c, f"BFO ancestors include disjoint pair "
                          f"{_local(hit[0])} / {_local(hit[1])}")

    # ---- Stratum A: K-A1 individual under disjoint asserted types --------
    for i in individuals:
        types = {str(t) for t in g.objects(URIRef(i), RDF.type)
                 if isinstance(t, URIRef) and str(t) != str(OWL.NamedIndividual)}
        # expand each type to its ancestors for an *ancestral* disjoint check
        expanded = set(types)
        for t in types:
            expanded |= anc.get(t, set())
        hit = disjoint_hit(expanded)
        if hit:
            add("K-A1", i, f"instance of disjoint categories "
                          f"{_local(hit[0])} / {_local(hit[1])}")

    # ---- Stratum A/BFO-ISO: K-A3b declared-never-defined -----------------
    defined = {str(s) for s in g.subjects(IAO_DEFINITION, None)}
    has_equiv = {str(s) for s in g.subjects(OWL.equivalentClass, None)}
    for c in named:
        if c not in defined and c not in has_equiv:
            add("K-A3b", c, "no IAO definition and no equivalentClass")

    # ---- Stratum B: K-B1 subClassOf cycles -------------------------------
    for cyc in _cycles(g):
        add("K-B1", cyc[0], "subClassOf cycle: "
                            + " -> ".join(_local(x) for x in cyc))

    # ---- Stratum B: K-B3 residual categories -----------------------------
    # axiom form: equivalentClass = intersectionOf whose members are all complementOf
    for c in has_equiv:
        for eq in g.objects(URIRef(c), OWL.equivalentClass):
            for inter in g.objects(eq, OWL.intersectionOf):
                members = list(rdflib.collection.Collection(g, inter))
                if members and all(
                    (m, OWL.complementOf, None) in g for m in members
                ):
                    add("K-B3", c, "defined only by complements (residual axiom)")
    # label form: "other specified / unspecified / NEC"
    for c in named:
        lab = labels.get(c, "").lower()
        if any(mk in lab for mk in RESIDUAL_LABEL_MARKERS):
            add("K-B3", c, f"residual label: {labels.get(c, '')!r}")

    # ---- Stratum C: K-C2 punning -----------------------------------------
    for iri in named & individuals:
        add("K-C2", iri, "IRI is both an owl:Class and an owl:NamedIndividual")

    # ---- Stratum C: K-C3 dependent individual without a bearer -----------
    for i in individuals:
        types = {str(t) for t in g.objects(URIRef(i), RDF.type) if isinstance(t, URIRef)}
        cats = set(types)
        for t in types:
            cats |= anc.get(t, set())
        cats = {_local(x) for x in cats if x.startswith(BFO)}
        if cats & SDC_BFO:
            props = {_local(p) for p in g.predicates(URIRef(i), None)}
            props |= {_local(p) for p in g.predicates(None, URIRef(i))}
            if not (props & BEARER_PROPS):
                add("K-C3", i, "specifically-dependent individual with no bearer/"
                              "realization relation")

    # ---- metrics ---------------------------------------------------------
    bfo_anchored = {
        str(s) for s, o in g.subject_objects(RDFS.subClassOf)
        if isinstance(s, URIRef) and str(o).startswith(BFO)
    }
    metrics = {
        "named_classes": len(named),
        "named_individuals": len(individuals),
        "classes_with_definition": len(defined & named),
        "definition_coverage_pct": round(100 * len(defined & named) / max(1, len(named)), 2),
        "defined_classes_equivalentClass": len(has_equiv & named),
        "disjointWith_axioms": len(list(g.triples((None, OWL.disjointWith, None)))),
        "object_properties": len(set(g.subjects(RDF.type, OWL.ObjectProperty))),
        "classes_directly_bfo_anchored": len(bfo_anchored),
    }

    summary = {prim: len(v) for prim, v in sorted(findings.items())}
    ledger = {
        "target": target,
        "standard": "Contradiction Kernel v1.0 (Strata A/B/C) + BFO-ISO 21838-2",
        "metrics": metrics,
        "finding_counts": summary,
        "total_findings": sum(summary.values()),
        "findings_sample": {
            prim: v[:sample] for prim, v in findings.items()
        },
    }
    return ledger


def main(argv=None):
    ap = argparse.ArgumentParser(description="Contradiction-Kernel audit (Strata A/B/C)")
    ap.add_argument("target", help="OWL/TTL file to audit")
    ap.add_argument("--bfo", default="bfo.owl", help="BFO file for disjointness axioms")
    ap.add_argument("--out", default=None, help="write full ledger JSON here")
    ap.add_argument("--sample", type=int, default=25)
    args = ap.parse_args(argv)

    ledger = audit(args.target, args.bfo, args.sample)
    m, fc = ledger["metrics"], ledger["finding_counts"]
    print(f"# Contradiction-Kernel audit: {args.target}")
    print(f"  named classes ............ {m['named_classes']}")
    print(f"  definition coverage ...... {m['classes_with_definition']} "
          f"({m['definition_coverage_pct']}%)")
    print(f"  defined classes (equiv) .. {m['defined_classes_equivalentClass']}")
    print(f"  disjointWith axioms ...... {m['disjointWith_axioms']}")
    print(f"  object properties ........ {m['object_properties']}")
    print(f"  BFO-anchored classes ..... {m['classes_directly_bfo_anchored']}")
    print("  --- kernel findings ---")
    for prim in sorted(fc):
        print(f"    {prim:6s} {fc[prim]}")
    print(f"  TOTAL findings ........... {ledger['total_findings']}")

    if args.out:
        Path(args.out).write_text(json.dumps(ledger, indent=2))
        print(f"\n  ledger written -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
