"""K-A2: unsatisfiable class under the criteria.

Reasoner-dependent. Consumes the HermiT run performed by scripts/run_reasoner.py;
if no verdict is available it reports nothing rather than guessing, and the absence
is recorded by the runner.
"""

from base import Flag, local

PRIMITIVE = "K-A2"


def detect(ctx):
    if not ctx.reasoner or ctx.reasoner.get("verdict") is None:
        return []
    flags = []
    for iri in ctx.reasoner.get("unsatisfiable_classes", []):
        if iri.endswith("owl#Nothing"):
            continue  # owl:Nothing is unsatisfiable by definition, not a finding
        from rdflib import URIRef
        c = URIRef(iri)
        flags.append(Flag(
            primitive=PRIMITIVE, term=iri, locus=ctx.locus.get(c, "L0"),
            section=ctx.section.get(c, "UNATTRIBUTED"),
            evidence=(f"HermiT reports {local(iri)} unsatisfiable: no individual can "
                      f"instantiate it under the asserted criteria."),
            detector="k_a2"))
    return flags
