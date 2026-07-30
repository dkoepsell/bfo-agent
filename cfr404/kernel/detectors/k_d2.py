"""K-D2: performative self-defeat.

An act whose own performance undoes what it purports to do. Two shapes:

1. an act that is a repair path for itself, so performing it reopens the very thing it
   concludes;
2. an act whose precondition is also asserted as something it excludes, so the act can
   only be performed under a condition it itself removes.

The spec expects this to be rare or absent in this corpus, and says to report the null
honestly if so.
"""

from rdflib import URIRef

from base import CFR, Flag, local

PRIMITIVE = "K-D2"


def detect(ctx):
    flags = []
    for c in sorted(ctx.classes, key=str):
        rests = ctx.restrictions(c)
        repairs = {f for p, _q, f in rests if p == CFR["repairPathFor"]}
        pre = {f for p, _q, f in rests if p == CFR["hasPrecondition"]}
        excl = {f for p, _q, f in rests if p == CFR["excludesPath"]}

        if c in repairs:
            flags.append(Flag(
                primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                section=ctx.section.get(c, "UNATTRIBUTED"),
                evidence=(f"{local(c)} is asserted as a repair path for itself: "
                          f"performing it reopens what it concludes."),
                detector="k_d2"))

        overlap = pre & excl
        for x in sorted(overlap, key=str):
            flags.append(Flag(
                primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                section=ctx.section.get(c, "UNATTRIBUTED"),
                evidence=(f"{local(c)} requires {local(x)} as a precondition while also "
                          f"excluding it. The act can only be performed under a condition "
                          f"its performance removes."),
                detector="k_d2"))
    return flags
