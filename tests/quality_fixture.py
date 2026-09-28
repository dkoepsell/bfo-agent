"""Hand-built ontologies with known audit counts (QS-G1 tests, QS-G4 CI)."""
from __future__ import annotations

from pathlib import Path

from rdflib import BNode, Graph, Literal, OWL, RDF, RDFS, URIRef

W = "http://davidkoepsell.com/bfo-agent/working"
WN = W + "#"
OBO = "http://purl.obolibrary.org/obo/"
IAO_DEF = URIRef(OBO + "IAO_0000115")
BFOAGENT = "http://davidkoepsell.com/bfo-agent/meta#"


def _cls(g: Graph, local: str, label: str, parent: URIRef) -> URIRef:
    u = URIRef(WN + local)
    g.add((u, RDF.type, OWL.Class))
    g.add((u, RDFS.label, Literal(label)))
    g.add((u, RDFS.subClassOf, parent))
    return u


def clean_graph() -> Graph:
    """Passes every QS-G3 construction gate: declared property, real
    restriction with a domain filler, no punning, full definitions."""
    g = Graph()
    g.add((URIRef(W), RDF.type, OWL.Ontology))
    role, proc = URIRef(OBO + "BFO_0000023"), URIRef(OBO + "BFO_0000015")
    realizes = URIRef(OBO + "BFO_0000055")
    g.add((realizes, RDF.type, OWL.ObjectProperty))
    judge = _cls(g, "JudgeRole", "judge role", role)
    trial = _cls(g, "Trial", "trial", proc)
    hearing = _cls(g, "Hearing", "hearing", trial)
    r = BNode()
    g.add((r, RDF.type, OWL.Restriction))
    g.add((r, OWL.onProperty, realizes))
    g.add((r, OWL.someValuesFrom, judge))
    g.add((trial, RDFS.subClassOf, r))
    g.add((judge, OWL.disjointWith, trial))
    for c, d in ((judge, "A role borne by a judge."),
                 (trial, "A process that adjudicates."),
                 (hearing, "A trial that is heard.")):
        g.add((c, IAO_DEF, Literal(d)))
    return g


def defective_graph() -> Graph:
    """clean_graph plus exactly one of each construction defect."""
    g = clean_graph()
    judge, trial = URIRef(WN + "JudgeRole"), URIRef(WN + "Trial")
    # mangled standard predicate
    g.add((URIRef(WN + "Hearing"), URIRef(WN + "rdfs:subClassOf"), trial))
    # punned relation triple: object property between two classes
    g.add((trial, URIRef(OBO + "BFO_0000055"), judge))
    # malformed local IRI (CURIE in fragment)
    bad = URIRef(WN + "ro:RO_0000052")
    g.add((bad, RDF.type, OWL.Class))
    g.add((bad, RDFS.subClassOf, URIRef(OBO + "BFO_0000023")))
    # redundant asserted parent: Hearing ⊑ Trial ⊑ process
    g.add((URIRef(WN + "Hearing"), RDFS.subClassOf, URIRef(OBO + "BFO_0000015")))
    # meta-annotated label
    meta = _cls(g, "Verdict", "Verdict (reuse)", URIRef(OBO + "BFO_0000031"))
    g.add((meta, URIRef(BFOAGENT + "definitionStatus"), Literal("absent-in-source")))
    return g


def write(g: Graph, path: Path) -> Path:
    g.serialize(destination=str(path), format="xml")
    return path
