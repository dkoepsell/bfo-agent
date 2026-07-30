"""K-D3: repair-path failure.

Competing repair paths over the same recognition act whose availability conditions do
not compose. The spec names the site in advance: reopening and revision
(404.987-404.996), res judicata (404.957(c)(1)) and the medical improvement review
standard (404.1594), all of which govern when a determination may be revisited.

Two shapes are detected:

1. **mutual exclusion** - two paths over the same act where one excludes the other, so
   which repair is available depends on which is invoked first;
2. **exclusion cycle** - a set of paths over the same act whose exclusions form a
   cycle, meaning no ordering makes them jointly available.

Both are reported on the L7 edge.
"""

from rdflib import URIRef

from base import CFR, Flag, local

PRIMITIVE = "K-D3"


def detect(ctx):
    paths_for = {}
    excludes = {}
    preconds = {}
    for c in ctx.classes:
        for p, _q, f in ctx.restrictions(c):
            if not isinstance(f, URIRef):
                continue
            if p == CFR["repairPathFor"]:
                paths_for.setdefault(f, set()).add(c)
            elif p == CFR["excludesPath"]:
                excludes.setdefault(c, set()).add(f)
            elif p == CFR["hasPrecondition"]:
                preconds.setdefault(c, set()).add(f)

    flags = []
    for act, paths in sorted(paths_for.items(), key=lambda kv: str(kv[0])):
        if len(paths) < 2:
            continue
        for a in sorted(paths, key=str):
            for b in sorted(paths, key=str):
                if a is b or a == b:
                    continue
                if b in excludes.get(a, ()):
                    mutual = a in excludes.get(b, ())
                    flags.append(Flag(
                        primitive=PRIMITIVE, term=str(a), locus="L7", edge="L7",
                        section=ctx.section.get(a, "UNATTRIBUTED"),
                        evidence=(
                            f"{local(a)} and {local(b)} are both repair paths over "
                            f"{local(act)}, and {local(a)} excludes {local(b)}"
                            + (" while {} excludes {} in turn, so the two paths are "
                               "jointly unavailable and which repair a claimant gets "
                               "depends on the order of invocation rather than on the "
                               "merits".format(local(b), local(a)) if mutual else
                               ", so one path's availability is the other's bar")
                            + f". Preconditions on {local(a)}: "
                            + f"{sorted(local(x) for x in preconds.get(a, ())) or 'none'}."),
                        detector="k_d3"))

    # Shape 2: one path's precondition is another path's exclusion.
    # Two repair paths triggered by the *same* condition, where one of them bars
    # review. Res judicata and reopening are the case the spec names: 404.957(c)(1)
    # makes a determination having become final the trigger for refusing to hear it
    # again, and 404.988 makes that same finality the thing a reopening operates on.
    # The condition that opens one path is the condition that closes the other.
    for barring in sorted(excludes, key=str):
        for barred in sorted(excludes[barring], key=str):
            trigger = preconds.get(barring, set())
            if not trigger:
                continue
            for other in sorted(preconds, key=str):
                if other in (barring, barred):
                    continue
                shared = trigger & preconds[other]
                if not shared:
                    continue
                flags.append(Flag(
                    primitive=PRIMITIVE, term=str(barring), locus="L7", edge="L7",
                    section=ctx.section.get(barring, "UNATTRIBUTED"),
                    evidence=(
                        f"{sorted(local(x) for x in shared)} is a precondition of both "
                        f"{local(barring)} and {local(other)}. Its holding triggers "
                        f"{local(barring)}, which bars {local(barred)}, while the same "
                        f"condition keeps {local(other)} open. One path's precondition is "
                        f"another path's exclusion, so whether the determination can be "
                        f"revisited depends on which route is taken rather than on "
                        f"whether the condition obtains."),
                    detector="k_d3"))
    return flags
