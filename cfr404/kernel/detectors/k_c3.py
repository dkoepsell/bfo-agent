"""K-C3: relation misuse against declared domain, range or cardinality.

For every restriction ``C subClassOf R some F`` where R declares a domain and a range,
C must fall under the domain and F under the range. A violation is a claim the
ontology makes that its own property declarations forbid.
"""

from rdflib import URIRef
from rdflib.namespace import RDFS

from base import Flag, local

PRIMITIVE = "K-C3"


def detect(ctx):
    domains, ranges = {}, {}
    for p, o in ctx.g.subject_objects(RDFS.domain):
        if isinstance(o, URIRef):
            domains.setdefault(p, set()).add(o)
    for p, o in ctx.g.subject_objects(RDFS.range):
        if isinstance(o, URIRef):
            ranges.setdefault(p, set()).add(o)

    flags = []
    for c in sorted(ctx.classes, key=str):
        for prop, _q, filler in ctx.restrictions(c):
            dom = domains.get(prop)
            if dom and not any(ctx.is_a(c, d) for d in dom):
                flags.append(Flag(
                    primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                    section=ctx.section.get(c, "UNATTRIBUTED"),
                    evidence=(f"{local(c)} carries a restriction on {local(prop)}, whose "
                              f"declared domain is {sorted(local(d) for d in dom)}, but "
                              f"{local(c)} is not under that domain."),
                    detector="k_c3"))
            rng = ranges.get(prop)
            if rng and isinstance(filler, URIRef) and filler in ctx.classes:
                if not any(ctx.is_a(filler, r) for r in rng):
                    flags.append(Flag(
                        primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                        section=ctx.section.get(c, "UNATTRIBUTED"),
                        evidence=(f"{local(c)} asserts {local(prop)} some "
                                  f"{local(filler)}, but the declared range of "
                                  f"{local(prop)} is {sorted(local(r) for r in rng)} and "
                                  f"{local(filler)} is not under it."),
                        detector="k_c3"))
    return flags
