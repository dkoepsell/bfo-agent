"""K-C2: realizable misuse.

Roles, dispositions and functions are realizable entities and each carries its own
commitments. The misuses looked for here:

* something is realized in a process without being a realizable entity;
* a realizable inheres in something that is not an independent continuant;
* a role is borne by a process, or a process bears a role;
* a term at L3 (assessor) is grounded as a disposition or function rather than a role,
  which confuses a capacity someone has with an office someone holds.

The last of these is the one that matters institutionally: an assessor's authority is
an office, not a talent.
"""

from rdflib import URIRef

from base import BFO, Flag, local

PRIMITIVE = "K-C2"


def detect(ctx):
    flags = []
    from base import REL

    for c in sorted(ctx.classes, key=str):
        rests = ctx.restrictions(c)

        for prop, _q, filler in rests:
            if prop == REL["realized_in"] and not ctx.is_a(c, BFO["realizable"]):
                flags.append(Flag(
                    primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                    section=ctx.section.get(c, "UNATTRIBUTED"),
                    evidence=(f"{local(c)} is asserted 'realized in "
                              f"{local(filler)}' but is not grounded as a realizable "
                              f"entity. Only roles, dispositions and functions are "
                              f"realized."),
                    detector="k_c2"))
            if prop == REL["inheres_in"] and ctx.is_a(c, BFO["realizable"]):
                if isinstance(filler, URIRef) and ctx.side(filler) == "occurrent":
                    flags.append(Flag(
                        primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                        section=ctx.section.get(c, "UNATTRIBUTED"),
                        evidence=(f"{local(c)} is a realizable asserted to inhere in "
                                  f"{local(filler)}, which is an occurrent. Realizables "
                                  f"inhere in independent continuants, not in processes."),
                        detector="k_c2"))
            if prop == REL["bearer_of"] and ctx.side(c) == "occurrent":
                flags.append(Flag(
                    primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                    section=ctx.section.get(c, "UNATTRIBUTED"),
                    evidence=(f"{local(c)} is an occurrent asserted as 'bearer of "
                              f"{local(filler)}'. A process does not bear a realizable."),
                    detector="k_c2"))

        if ctx.locus.get(c) == "L3" and not ctx.is_a(c, BFO["role"]):
            if ctx.is_a(c, BFO["disposition"]) or ctx.is_a(c, BFO["function"]):
                kind = "disposition" if ctx.is_a(c, BFO["disposition"]) else "function"
                flags.append(Flag(
                    primitive=PRIMITIVE, term=str(c), locus="L3",
                    section=ctx.section.get(c, "UNATTRIBUTED"),
                    evidence=(f"{local(c)} sits at the assessor locus but is grounded as "
                              f"a {kind} rather than a role. That treats the authority to "
                              f"assess as a capacity the occupant has rather than an "
                              f"office the institution confers."),
                    detector="k_c2"))
    return flags
