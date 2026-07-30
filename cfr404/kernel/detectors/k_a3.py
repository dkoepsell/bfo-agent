"""K-A3: definitional circularity, or a definition that says nothing.

Two shapes are looked for, both structural:

1. self-reference - a class whose own definition is stated in terms of itself, either
   directly (a restriction filler that is the class) or through a cycle of
   equivalentClass edges;
2. vacuity - a class whose only content is a BFO root, so the definition constrains
   nothing beyond what the upper ontology already says.

Vacuity is the common case in extracted legal ontologies and is reported separately
because it is a translation defect far more often than a defect of the regulation.
"""

from rdflib import URIRef
from rdflib.namespace import OWL, RDFS

from base import BFO, Flag, local

PRIMITIVE = "K-A3"
BFO_ROOTS = set(BFO.values())


def detect(ctx):
    flags = []

    # 1. self-reference through restriction fillers
    for c in sorted(ctx.classes, key=str):
        for prop, quant, filler in ctx.restrictions(c):
            if filler == c:
                flags.append(Flag(
                    primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                    section=ctx.section.get(c, "UNATTRIBUTED"),
                    evidence=(f"{local(c)} is defined by a restriction whose filler is "
                              f"{local(c)} itself, via {local(prop)}. The definition "
                              f"presupposes what it defines."),
                    detector="k_a3"))

    # 2. equivalentClass cycles among named classes
    eq = {}
    for s, o in ctx.g.subject_objects(OWL.equivalentClass):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            eq.setdefault(s, set()).add(o)
    for start in sorted(eq, key=str):
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            for nxt in eq.get(cur, ()):
                if nxt == start:
                    flags.append(Flag(
                        primitive=PRIMITIVE, term=str(start),
                        locus=ctx.locus.get(start, "L0"),
                        section=ctx.section.get(start, "UNATTRIBUTED"),
                        evidence=(f"{local(start)} participates in a cycle of "
                                  f"equivalentClass assertions, so its definition is "
                                  f"circular."),
                        detector="k_a3"))
                    stack = []
                    break
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)

    # 3. vacuity: nothing asserted but a BFO root
    for c in sorted(ctx.classes, key=str):
        parents = ctx.named_parents(c)
        if not parents:
            continue
        if parents <= BFO_ROOTS and not ctx.restrictions(c) and not ctx.disjoints(c):
            flags.append(Flag(
                primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                section=ctx.section.get(c, "UNATTRIBUTED"),
                evidence=(f"{local(c)} is asserted only under the BFO root(s) "
                          f"{sorted(local(p) for p in parents)} with no differentia, no "
                          f"restriction and no disjointness. The class adds a name and "
                          f"nothing else."),
                detector="k_a3"))
    return flags
