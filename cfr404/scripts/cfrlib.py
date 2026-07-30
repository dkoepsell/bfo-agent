"""Shared constants and graph helpers for the cfr404 build."""

from __future__ import annotations

from pathlib import Path

from rdflib import Graph, Namespace, URIRef
from rdflib.namespace import OWL, RDF, RDFS

ROOT = Path(__file__).resolve().parent.parent

CFR = Namespace("http://davidkoepsell.com/cfr404#")
CFR_BASE_IRI = URIRef("http://davidkoepsell.com/cfr404")
CFR_VERSION_IRI = URIRef("http://davidkoepsell.com/cfr404/2026-07-30")
OBO = Namespace("http://purl.obolibrary.org/obo/")
BFO_IMPORT = URIRef("http://purl.obolibrary.org/obo/bfo.owl")

# Namespaces the baseline used; everything under these gets rewritten into CFR.
LEGACY_NS = (
    "http://davidkoepsell.com/bfo-agent/working#",
    "http://davidkoepsell.com/bfo-agent/seed#",
)
LEGACY_ONTOLOGY_IRIS = (
    "http://davidkoepsell.com/bfo-agent/working",
    "http://davidkoepsell.com/bfo-agent/seed",
    "http://davidkoepsell.com/bfo-agent",
)

# BFO 2020 anchors used throughout.
BFO = {
    "entity": OBO.BFO_0000001,
    "continuant": OBO.BFO_0000002,
    "occurrent": OBO.BFO_0000003,
    "ic": OBO.BFO_0000004,  # independent continuant
    "process": OBO.BFO_0000015,
    "disposition": OBO.BFO_0000016,
    "realizable": OBO.BFO_0000017,
    "quality": OBO.BFO_0000019,
    "sdc": OBO.BFO_0000020,  # specifically dependent continuant
    "role": OBO.BFO_0000023,
    "site": OBO.BFO_0000029,
    "object": OBO.BFO_0000030,
    "gdc": OBO.BFO_0000031,  # generically dependent continuant
    "material_entity": OBO.BFO_0000040,
    "function": OBO.BFO_0000034,
    "temporal_region": OBO.BFO_0000008,
    "process_boundary": OBO.BFO_0000035,
    "history": OBO.BFO_0000182,
}

# BFO relations (RO/BFO object properties) used by the build.
REL = {
    "occurs_in": OBO.BFO_0000066,
    "realized_in": OBO.BFO_0000054,
    "realizes": OBO.BFO_0000055,
    "has_participant": OBO.BFO_0000057,
    "participates_in": OBO.BFO_0000056,
    "bearer_of": OBO.BFO_0000196,
    "inheres_in": OBO.BFO_0000197,
    "has_part": OBO.BFO_0000051,
    "part_of": OBO.BFO_0000050,
    "concretizes": OBO.BFO_0000058,
    "is_concretized_by": OBO.BFO_0000059,
    "has_history": OBO.BFO_0000185,
    "has_role": OBO.RO_0000087,
    "preceded_by": OBO.BFO_0000062,
    "precedes": OBO.BFO_0000063,
}

# Everything a continuant can be, and everything an occurrent can be.
CONTINUANT_ROOTS = {
    BFO["continuant"], BFO["ic"], BFO["sdc"], BFO["gdc"], BFO["material_entity"],
    BFO["object"], BFO["quality"], BFO["realizable"], BFO["disposition"],
    BFO["role"], BFO["function"], BFO["site"],
}
OCCURRENT_ROOTS = {
    BFO["occurrent"], BFO["process"], BFO["temporal_region"],
    BFO["process_boundary"], BFO["history"],
}

# BFO's four top-level categories are pairwise disjoint. The artifact only *imports*
# bfo.owl, so BFO's own subsumptions are not in the merged graph and cannot be walked;
# the members of each category are therefore enumerated here, exactly as the
# continuant/occurrent roots above are. A term reachable into two of these is
# unsatisfiable, and inconsistent as soon as anything instantiates it.
TOP_CATEGORIES = {
    "independent continuant": {
        OBO.BFO_0000004, OBO.BFO_0000030, OBO.BFO_0000040, OBO.BFO_0000029,
        OBO.BFO_0000027, OBO.BFO_0000024, OBO.BFO_0000140, OBO.BFO_0000006,
        OBO.BFO_0000141, OBO.BFO_0000142, OBO.BFO_0000146, OBO.BFO_0000147,
    },
    "specifically dependent continuant": {
        OBO.BFO_0000020, OBO.BFO_0000019, OBO.BFO_0000017, OBO.BFO_0000016,
        OBO.BFO_0000023, OBO.BFO_0000034, OBO.BFO_0000145,
    },
    "generically dependent continuant": {OBO.BFO_0000031},
    "occurrent": {
        OBO.BFO_0000003, OBO.BFO_0000015, OBO.BFO_0000035, OBO.BFO_0000008,
        OBO.BFO_0000182, OBO.BFO_0000011, OBO.BFO_0000038, OBO.BFO_0000148,
        OBO.BFO_0000202, OBO.BFO_0000203,
    },
}

# The seven recognition-chain loci (Phase 4).
LOCI = ["L1", "L2", "L3", "L4", "L5", "L6", "L7"]
LOCUS_NAME = {
    "L1": "source of authority",
    "L2": "criteria",
    "L3": "assessor in role",
    "L4": "presenting facts",
    "L5": "recognition act",
    "L6": "effect",
    "L7": "remedy",
}

# The twelve contradiction-kernel primitives.
PRIMITIVES = {
    "K-A1": "Direct logical contradiction (class both asserted and excluded)",
    "K-A2": "Unsatisfiable class under the criteria",
    "K-A3": "Definitional circularity / vacuous definition",
    "K-B1": "Grounding cycle, cyclic subsumption, or temporal inversion",
    "K-B2": "Authority inflation: capacity exercised beyond its grant",
    "K-B3": "Category straddle within the institutional layer",
    "K-C1": "Continuant/occurrent straddle",
    "K-C2": "Realizable misuse (role/disposition/function confusion)",
    "K-C3": "Relation misuse against domain, range, or cardinality",
    "K-D1": "Structural capacity granted but not available in practice",
    "K-D2": "Performative self-defeat",
    "K-D3": "Repair-path failure: competing remedies jointly unsatisfiable",
}
STRATUM = {
    "K-A1": "A", "K-A2": "A", "K-A3": "A",
    "K-B1": "B", "K-B2": "B", "K-B3": "B",
    "K-C1": "C", "K-C2": "C", "K-C3": "C",
    "K-D1": "D", "K-D2": "D", "K-D3": "D",
}
INSTRUMENT = {
    "K-A1": "description-logic reasoner",
    "K-A2": "description-logic reasoner",
    "K-A3": "structural analysis of axioms",
    "K-B1": "structural analysis of axioms",
    "K-B2": "structural analysis of axioms",
    "K-B3": "structural analysis of axioms",
    "K-C1": "description-logic reasoner",
    "K-C2": "structural analysis of axioms",
    "K-C3": "description-logic reasoner",
    "K-D1": "world-facing data",
    "K-D2": "process-level modelling",
    "K-D3": "process-level modelling",
}


def load(path: str | Path) -> Graph:
    g = Graph()
    g.parse(str(path))
    return g


def named_classes(g: Graph, ns: str = str(CFR)) -> set[URIRef]:
    return {
        c for c in g.subjects(RDF.type, OWL.Class)
        if isinstance(c, URIRef) and str(c).startswith(ns)
    }


def ancestors(g: Graph, node: URIRef, _seen: set | None = None) -> set[URIRef]:
    """Named rdfs:subClassOf / rdf:type ancestors, blank nodes skipped."""
    seen = _seen if _seen is not None else set()
    out: set[URIRef] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for p in (RDFS.subClassOf, RDF.type):
            for parent in g.objects(cur, p):
                if isinstance(parent, URIRef) and parent != OWL.Class:
                    if parent not in out:
                        out.add(parent)
                        stack.append(parent)
    return out


def straddles(g: Graph, ns: str = str(CFR)) -> list[URIRef]:
    """K-C1: entities with both a continuant and an occurrent BFO ancestor."""
    bad = []
    subjects = set(named_classes(g, ns)) | {
        i for i in g.subjects(RDF.type, OWL.NamedIndividual)
        if isinstance(i, URIRef) and str(i).startswith(ns)
    }
    for c in subjects:
        anc = ancestors(g, c) | {c}
        if (anc & CONTINUANT_ROOTS) and (anc & OCCURRENT_ROOTS):
            bad.append(c)
    return sorted(bad)


def unanchored(g: Graph, ns: str = str(CFR)) -> list[URIRef]:
    """Classes with no BFO ancestor at all."""
    bad = []
    for c in named_classes(g, ns):
        anc = ancestors(g, c)
        if not any(str(a).startswith(str(OBO) + "BFO_") for a in anc):
            bad.append(c)
    return sorted(bad)


def local(u) -> str:
    s = str(u)
    return s.rsplit("#", 1)[-1] if "#" in s else s.rsplit("/", 1)[-1]
