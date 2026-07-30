"""K-B3: category straddle inside the institutional layer.

Not the continuant/occurrent straddle (that is K-C1) but a term whose chain locus and
whose BFO grounding disagree about what kind of thing it is. An L3 term that is not a
role, an L5 term that is not a process, an L2 term that is not a generically dependent
continuant: each is a claim that the recognition chain and the upper ontology disagree
about the same entity.
"""

from base import BFO, Flag, local

PRIMITIVE = "K-B3"

# What each locus commits the term to, per the Phase 4 table.
EXPECTED = {
    "L1": ("material entity or object bearing an authority role",
           (BFO["material_entity"], BFO["object"], BFO["ic"], BFO["role"])),
    "L2": ("generically dependent continuant", (BFO["gdc"],)),
    "L3": ("role", (BFO["role"],)),
    "L4": ("generically dependent continuant", (BFO["gdc"],)),
    "L5": ("process", (BFO["process"],)),
    "L6": ("role or disposition", (BFO["role"], BFO["disposition"], BFO["realizable"])),
    "L7": ("process", (BFO["process"],)),
}


def detect(ctx):
    flags = []
    for c in sorted(ctx.classes, key=str):
        loc = ctx.locus.get(c, "L0")
        if loc not in EXPECTED:
            continue
        wanted_desc, wanted = EXPECTED[loc]
        if any(ctx.is_a(c, w) for w in wanted):
            continue
        side = ctx.side(c) or "ungrounded"
        flags.append(Flag(
            primitive=PRIMITIVE, term=str(c), locus=loc,
            section=ctx.section.get(c, "UNATTRIBUTED"),
            evidence=(f"{local(c)} is placed at {loc}, which commits it to being a "
                      f"{wanted_desc}, but its BFO grounding makes it a {side} that is "
                      f"none of those."),
            detector="k_b3"))
    return flags
