"""K-B2: authority inflation.

The shape the spec predicts for this corpus: a single function is assigned to
occupants holding *different* grants of authority. 404.1546 assigns responsibility
for the residual functional capacity assessment to State agency staff, to a
disability hearing officer, to the Associate Commissioner, to an administrative law
judge and to an administrative appeals judge. Those occupants do not hold the same
grant: the State agency's authority is delegated under 404.1503, the Appeals
Council's is its own.

The detector reads the L5 act -> L3 role edge (cfr:assessedBy) and the L3 role ->
L1 authority edge (cfr:derivesAuthorityFrom), and flags an act whose assessors trace
to more than one distinct grant of authority. The flag is on the L1->L3 edge, which
is where the inflation lives, not on the act.

This is only expressible because Phase 4 keeps AuthorityRole and AssessorRole apart.
Under the baseline's merged DeterminingAgencyRole the comparison has no terms.
"""

from rdflib import URIRef

from base import CFR, Flag, local

PRIMITIVE = "K-B2"


def detect(ctx):
    flags = []

    authority_of = {}
    for role in ctx.classes:
        grants = {f for p, _q, f in ctx.restrictions(role)
                  if p == CFR["derivesAuthorityFrom"] and isinstance(f, URIRef)}
        if grants:
            authority_of[role] = grants

    for act in sorted(ctx.classes, key=str):
        assessors = {f for p, _q, f in ctx.restrictions(act)
                     if p == CFR["assessedBy"] and isinstance(f, URIRef)}
        if len(assessors) < 2:
            continue
        grants = {}
        for a in assessors:
            for g in authority_of.get(a, ()):
                grants.setdefault(g, set()).add(a)
        if len(grants) < 2:
            continue
        detail = "; ".join(
            f"{local(g)} grants {sorted(local(a) for a in roles)}"
            for g, roles in sorted(grants.items(), key=lambda kv: str(kv[0])))
        flags.append(Flag(
            primitive=PRIMITIVE, term=str(act), locus="L3", edge="L1->L3",
            section=ctx.section.get(act, "UNATTRIBUTED"),
            evidence=(f"{local(act)} is assigned to {len(assessors)} assessor roles whose "
                      f"authority traces to {len(grants)} distinct grants: {detail}. The "
                      f"same function is exercised under unequal grants of authority."),
            detector="k_b2"))
    return flags
