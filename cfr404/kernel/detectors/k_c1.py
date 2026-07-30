"""K-C1: continuant/occurrent straddle.

The headline number for §10. A term with both a continuant and an occurrent ancestor
claims to be a thing and a happening at once.
"""

from base import CONTINUANT_ROOTS, OCCURRENT_ROOTS, Flag, ancestors, local

PRIMITIVE = "K-C1"


def detect(ctx):
    flags = []
    for c in sorted(ctx.classes, key=str):
        anc = ancestors(ctx.g, c) | {c}
        cont = anc & CONTINUANT_ROOTS
        occ = anc & OCCURRENT_ROOTS
        if cont and occ:
            flags.append(Flag(
                primitive=PRIMITIVE, term=str(c), locus=ctx.locus.get(c, "L0"),
                section=ctx.section.get(c, "UNATTRIBUTED"),
                evidence=(f"{local(c)} inherits from continuant branch "
                          f"{sorted(local(x) for x in cont)} and occurrent branch "
                          f"{sorted(local(x) for x in occ)} simultaneously."),
                detector="k_c1"))
    return flags
