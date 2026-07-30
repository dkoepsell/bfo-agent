"""K-A1: direct logical contradiction. A class is both asserted into and excluded
from the same class.

Structural, not reasoner-dependent: it looks for a term that inherits from D while
also carrying an explicit complementOf D or a disjointWith D against an ancestor.
"""

from base import Flag, ancestors, local

PRIMITIVE = "K-A1"


def detect(ctx):
    flags = []
    for c in sorted(ctx.classes, key=str):
        anc = ancestors(ctx.g, c)
        excluded = ctx.complements(c) | ctx.disjoints(c)
        for x in sorted(excluded, key=str):
            if x in anc:
                flags.append(Flag(
                    primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                    section=ctx.section.get(c, "UNATTRIBUTED"),
                    evidence=(f"{local(c)} inherits from {local(x)} through the subsumption "
                              f"hierarchy while also being asserted outside it "
                              f"(complementOf or disjointWith {local(x)})."),
                    detector="k_a1"))
    return flags
