"""Measure the baseline defects D1-D8 of SPEC-legal-drafting.md on any OWL
file (section 8.1), and the per-run metrics of section 8.2.

Pure graph inspection (rdflib); no reasoner, no LLM. Used by
``tests/test_legal_eval.py`` on the frozen baseline draft and by
``evaluation/legal/run_regression.py`` on a fresh run.
"""
from __future__ import annotations

import re
from pathlib import Path

OBO = "http://purl.obolibrary.org/obo/"
LOF = "https://w3id.org/lof/"
BEARER_SIDE = {OBO + p for p in ("BFO_0000196", "RO_0000053", "RO_0000087",
                                  "RO_0000091", "RO_0000085")}
INHERES = {OBO + "BFO_0000197", OBO + "RO_0000052"}
IC = OBO + "BFO_0000004"
SDC = OBO + "BFO_0000020"
IAO_DEF = OBO + "IAO_0000115"
IAO_DEF_SRC = OBO + "IAO_0000119"


def _norm(s: str) -> str:
    s = re.sub(r"[‘’'\"]", "", s or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _normalize_ws(s: str) -> str:
    from .legal_source import normalize
    return normalize(s)


def load(path, bfo_path=None):
    import rdflib
    g = rdflib.Graph()
    g.parse(path)
    if bfo_path:
        b = rdflib.Graph()
        b.parse(bfo_path)
        return g, b
    return g, None


def _ancestors(g, bfo, c) -> set:
    from rdflib import URIRef
    from rdflib.namespace import RDFS
    seen, todo = set(), [c]
    while todo:
        x = todo.pop()
        for graph in (g, bfo):
            if graph is None:
                continue
            for o in graph.objects(x, RDFS.subClassOf):
                if isinstance(o, URIRef) and o not in seen:
                    seen.add(o)
                    todo.append(o)
    return {str(a) for a in seen}


def _restrictions(g, c):
    """(property, quantifier, filler) of class ``c``'s restriction parents."""
    from rdflib import BNode
    from rdflib.namespace import OWL, RDFS
    out = []
    for r in g.objects(c, RDFS.subClassOf):
        if not isinstance(r, BNode):
            continue
        p = g.value(r, OWL.onProperty)
        for q, pred in (("some", OWL.someValuesFrom), ("only", OWL.allValuesFrom),
                        ("value", OWL.hasValue)):
            f = g.value(r, pred)
            if p is not None and f is not None:
                out.append((str(p), q, f))
    return out


def measure(path, source_text: str = "", bfo_path=None) -> dict:
    """D1-D8 counts for an ontology file. ``source_text`` (the legal text the
    draft came from) enables the D5 verbatim check."""
    from rdflib import BNode, Literal, URIRef
    from rdflib.namespace import OWL, RDF, RDFS

    g, bfo = load(path, bfo_path)
    from .relation_vocab import BFOAGENT_NS
    span_p = URIRef(BFOAGENT_NS + "sourceSpan")
    classes = {s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
    individuals = {s for s in g.subjects(RDF.type, OWL.NamedIndividual) if isinstance(s, URIRef)}
    working = {c for c in classes if not str(c).startswith((OBO, LOF))}

    d1, d2 = [], []
    for c in working:
        anc = _ancestors(g, bfo, c)
        rs = _restrictions(g, c)
        if IC in anc or any(a in anc for a in (OBO + "BFO_0000040", OBO + "BFO_0000030")):
            for p, q, f in rs:
                if p in BEARER_SIDE and q == "some":
                    d1.append((str(c), p.rsplit("/", 1)[-1], str(f)))
        if SDC in anc or any(a in anc for a in (OBO + "BFO_0000019", OBO + "BFO_0000023",
                                                OBO + "BFO_0000016", OBO + "BFO_0000034",
                                                OBO + "BFO_0000017")):
            bearers = [f for p, q, f in rs if p in INHERES and q == "some"]
            if len(bearers) > 1:
                d2.append((str(c), [str(b) for b in bearers]))

    labels: dict[str, list[tuple[str, str]]] = {}
    for s, lab in g.subject_objects(RDFS.label):
        if s in classes or s in individuals:
            labels.setdefault(_norm(str(lab)), []).append(
                (str(s), "class" if s in classes else "individual"))
    d3 = [(lab, hits) for lab, hits in labels.items()
          if len({i for i, _ in hits}) > 1 and lab]

    defined = [c for c in working if (c, URIRef(IAO_DEF), None) in g]
    with_source = [c for c in defined if (c, URIRef(IAO_DEF_SRC), None) in g
                   or (c, URIRef(BFOAGENT_NS + "definedIn"), None) in g]
    scoped = list(g.subject_objects(URIRef(BFOAGENT_NS + "scopedSenseOf")))

    spans = [str(o) for o in g.objects(None, span_p)]
    src = _normalize_ws(source_text) if source_text else ""
    verbatim = [s for s in spans if src and _normalize_ws(s) in src]

    isolated = []
    for c in working:
        sups = [o for o in g.objects(c, RDFS.subClassOf)]
        has_rel = any(isinstance(o, BNode) for o in sups) or \
            any(isinstance(o, URIRef) and not str(o).startswith(OBO) for o in sups)
        used = any(True for s in g.subjects(None, c) if isinstance(s, BNode)) or \
            any(True for s in g.subjects(RDFS.subClassOf, c))
        if not has_rel and not used:
            isolated.append(str(c))

    lof_content = [i for i in individuals if (i, RDF.type, URIRef(LOF + "ODP001_0001001")) in g]
    lof_enact = [i for i in individuals if (i, RDF.type, URIRef(LOF + "ODP001_0001003")) in g]
    lof_doc = [i for i in individuals if (i, RDF.type, URIRef(LOF + "ODP001_0001002")) in g]
    under_legal_role = [c for c in working if LOF + "ODP002_0001000" in _ancestors(g, bfo, c)]
    lof_positions = [c for c in working if any(a.startswith(LOF + "ODP003_") for a in _ancestors(g, bfo, c))]

    all_labels = [o for s, o in g.subject_objects(RDFS.label) if s in working or s in individuals]
    untagged = [o for o in all_labels if isinstance(o, Literal) and not o.language]
    onts = list(g.subjects(RDF.type, OWL.Ontology))
    ont = onts[0] if onts else None
    header = {}
    if ont is not None:
        from rdflib.namespace import DCTERMS
        header = {"license": (ont, DCTERMS.license, None) in g,
                  "versionInfo": (ont, OWL.versionInfo, None) in g,
                  "versionIRI": (ont, OWL.versionIRI, None) in g,
                  "title": (ont, DCTERMS.title, None) in g,
                  "source": (ont, DCTERMS.source, None) in g}
    working_ns = any("davidkoepsell.com/bfo-agent/working" in str(c) for c in working)

    return {
        "classes": len(working), "individuals": len(individuals), "triples": len(g),
        "D1_bearer_universal": {"count": len(d1), "items": d1},
        "D2_multi_bearer": {"count": len(d2), "items": d2},
        "D3_shared_labels": {"count": len(d3), "items": d3},
        "D4_definitions": {"defined": len(defined), "with_source_act": len(with_source),
                           "scoped_senses": len(scoped)},
        "D5_provenance": {"spans": len(spans), "verbatim": len(verbatim) if src else None,
                          "rate": (len(verbatim) / len(spans)) if (src and spans) else None},
        "D6_isolated": {"count": len(isolated), "items": isolated},
        "D7_legal_structure": {"legal_content": len(lof_content), "enactments": len(lof_enact),
                               "documents": len(lof_doc), "under_legal_role": len(under_legal_role),
                               "under_lof_positions": len(lof_positions)},
        "D8_finishing": {"labels": len(all_labels), "untagged_labels": len(untagged),
                         "header": header, "working_namespace": working_ns},
    }


def summary_line(m: dict) -> str:
    return (f"D1 {m['D1_bearer_universal']['count']} | D2 {m['D2_multi_bearer']['count']} | "
            f"D3 {m['D3_shared_labels']['count']} | D4 {m['D4_definitions']['with_source_act']}/"
            f"{m['D4_definitions']['defined']} sourced, {m['D4_definitions']['scoped_senses']} senses | "
            f"D5 {m['D5_provenance']['rate']} | D6 {m['D6_isolated']['count']} | "
            f"D7 {m['D7_legal_structure']} | D8 untagged {m['D8_finishing']['untagged_labels']}/"
            f"{m['D8_finishing']['labels']}")
