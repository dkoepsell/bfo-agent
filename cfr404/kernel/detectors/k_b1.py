"""K-B1: grounding cycle, cyclic subsumption, or temporal inversion.

Three shapes:

1. a cycle in rdfs:subClassOf among named classes;
2. a cycle in a mereological or grounding relation (part of / has part), which would
   make a whole a proper part of itself;
3. temporal inversion - two processes each asserted to precede the other, or a repair
   path that is a repair path for something that repairs it.
"""

from rdflib import URIRef
from rdflib.namespace import RDFS

from base import CFR, REL, Flag, local

PRIMITIVE = "K-B1"


def _cycles(edges):
    """Return one representative cycle per strongly connected component of size > 1."""
    found = []
    colour = {}

    def walk(node, path):
        colour[node] = 1
        for nxt in edges.get(node, ()):
            if colour.get(nxt) == 1:
                found.append(path[path.index(nxt):] + [nxt] if nxt in path
                             else [node, nxt])
            elif colour.get(nxt) is None:
                walk(nxt, path + [nxt])
        colour[node] = 2

    for n in list(edges):
        if colour.get(n) is None:
            walk(n, [n])
    return found


def detect(ctx):
    flags = []

    sub = {}
    for s, o in ctx.g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef) and s in ctx.classes:
            sub.setdefault(s, set()).add(o)
    for cyc in _cycles(sub):
        head = cyc[0]
        flags.append(Flag(
            primitive=PRIMITIVE, term=str(head), locus=ctx.locus.get(head, "L0"),
            section=ctx.section.get(head, "UNATTRIBUTED"),
            evidence=("cyclic subsumption: " + " -> ".join(local(x) for x in cyc)),
            detector="k_b1"))

    for rel, name in ((REL["part_of"], "part of"), (REL["has_part"], "has part")):
        edges = {}
        for c in ctx.classes:
            for prop, _q, filler in ctx.restrictions(c):
                if prop == rel and isinstance(filler, URIRef):
                    edges.setdefault(c, set()).add(filler)
        for cyc in _cycles(edges):
            head = cyc[0]
            flags.append(Flag(
                primitive=PRIMITIVE, term=str(head), locus=ctx.locus.get(head, "L0"),
                section=ctx.section.get(head, "UNATTRIBUTED"),
                evidence=(f"grounding cycle through '{name}': "
                          + " -> ".join(local(x) for x in cyc)),
                detector="k_b1"))

    for rel, name in ((REL["preceded_by"], "preceded by"),
                      (REL["precedes"], "precedes"),
                      (CFR["repairPathFor"], "repair path for")):
        edges = {}
        for c in ctx.classes:
            for prop, _q, filler in ctx.restrictions(c):
                if prop == rel and isinstance(filler, URIRef):
                    edges.setdefault(c, set()).add(filler)
        for cyc in _cycles(edges):
            head = cyc[0]
            flags.append(Flag(
                primitive=PRIMITIVE, term=str(head), locus=ctx.locus.get(head, "L0"),
                section=ctx.section.get(head, "UNATTRIBUTED"),
                evidence=(f"temporal or repair inversion through '{name}': "
                          + " -> ".join(local(x) for x in cyc)),
                detector="k_b1"))
    return flags
