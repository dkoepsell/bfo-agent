"""Shared context and flag shape for the contradiction-kernel detectors.

A flag is a **candidate**: the detector's suspicion, not a reasoner-confirmed
contradiction. Every flag carries what the detector actually observed, so that a
hand adjudicator can decide whether the defect belongs to the regulation or to the
translation without re-running anything.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
from cfrlib import (  # noqa: E402
    BFO, CFR, CONTINUANT_ROOTS, OCCURRENT_ROOTS, OBO, REL, ancestors, local,
    named_classes,
)


@dataclass
class Flag:
    primitive: str
    term: str                 # IRI of the flagged class, or an edge descriptor
    locus: str
    evidence: str
    section: str = "UNATTRIBUTED"
    blanket: bool = False     # applied to a whole class family rather than term by term
    edge: str | None = None   # e.g. "L1->L3" for relational primitives
    detector: str = ""

    def key(self) -> tuple:
        return (self.primitive, self.term, self.edge)


@dataclass
class Context:
    g: Graph
    classes: set = field(default_factory=set)
    locus: dict = field(default_factory=dict)
    section: dict = field(default_factory=dict)
    label: dict = field(default_factory=dict)
    chunks: dict = field(default_factory=dict)
    reasoner: dict | None = None

    @classmethod
    def build(cls, g: Graph, chunks: dict, reasoner: dict | None = None) -> "Context":
        # The artifact only *imports* BFO, so BFO's own subsumptions are absent from
        # the graph. Without them every check that asks "is this term under BFO's
        # independent continuant?" answers no, and domain/range checking in
        # particular produces a flood of false positives against BFO's own
        # vocabulary. The vendored copy is merged in for reasoning only; it is never
        # written back into the artifact.
        bfo = Path(__file__).resolve().parent.parent / "bfo-2020.owl"
        if bfo.exists():
            reasoning_graph = Graph()
            for t in g:
                reasoning_graph.add(t)
            reasoning_graph.parse(str(bfo))
            g = reasoning_graph

        classes = named_classes(g)
        locus, section, label = {}, {}, {}
        for c in classes:
            lv = list(g.objects(c, CFR.chainLocus))
            locus[c] = str(lv[0]) if lv else "L0"
            sv = list(g.objects(c, CFR.sourceSection))
            section[c] = str(sv[0]) if sv else "UNATTRIBUTED"
            nv = list(g.objects(c, RDFS.label))
            label[c] = str(nv[0]) if nv else local(c)
        return cls(g=g, classes=classes, locus=locus, section=section, label=label,
                   chunks=chunks, reasoner=reasoner)

    # ---- convenience accessors --------------------------------------------
    def named_parents(self, c: URIRef) -> set:
        return {p for p in self.g.objects(c, RDFS.subClassOf) if isinstance(p, URIRef)}

    def restrictions(self, c: URIRef) -> list[tuple[URIRef, URIRef, URIRef]]:
        """(onProperty, quantifier, filler) for each restriction on c."""
        out = []
        for b in self.g.objects(c, RDFS.subClassOf):
            if isinstance(b, URIRef):
                continue
            if (b, RDF.type, OWL.Restriction) not in self.g:
                continue
            props = list(self.g.objects(b, OWL.onProperty))
            if not props:
                continue
            for q in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue, OWL.onClass):
                for f in self.g.objects(b, q):
                    out.append((props[0], q, f))
        return out

    def complements(self, c: URIRef) -> set:
        """Named classes c is asserted to be outside of."""
        out = set()
        for b in self.g.objects(c, RDFS.subClassOf):
            if isinstance(b, URIRef):
                continue
            for comp in self.g.objects(b, OWL.complementOf):
                if isinstance(comp, URIRef):
                    out.add(comp)
        return out

    def disjoints(self, c: URIRef) -> set:
        out = {d for d in self.g.objects(c, OWL.disjointWith) if isinstance(d, URIRef)}
        out |= {d for d in self.g.subjects(OWL.disjointWith, c) if isinstance(d, URIRef)}
        return out

    def side(self, c: URIRef) -> str | None:
        anc = ancestors(self.g, c) | {c}
        if anc & CONTINUANT_ROOTS:
            return "continuant"
        if anc & OCCURRENT_ROOTS:
            return "occurrent"
        return None

    def is_a(self, c: URIRef, root: URIRef) -> bool:
        return root in (ancestors(self.g, c) | {c})
