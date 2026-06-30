"""The load-time BFO-correctness guard: an ontology must never carry a
disjointness between two BFO classes in a subclass relationship (which would make
the subclass unsatisfiable). The canonical bug was
Disposition (BFO_0000016) disjointWith Function (BFO_0000034) -- Function is a
subclass of Disposition, so that axiom made Function inconsistent and ran every
feed to "inconsistent".
"""
from app import bfo_catalog


def test_function_is_subclass_of_disposition():
    # The fact that makes the disjointness invalid.
    assert bfo_catalog.is_descendant_of("BFO_0000034", "BFO_0000016")
    assert not bfo_catalog.is_descendant_of("BFO_0000016", "BFO_0000034")


def test_role_disposition_function_validity():
    # Role is genuinely disjoint from both Disposition and Function (siblings).
    assert not bfo_catalog.is_descendant_of("BFO_0000023", "BFO_0000016")
    assert not bfo_catalog.is_descendant_of("BFO_0000016", "BFO_0000023")
    assert not bfo_catalog.is_descendant_of("BFO_0000023", "BFO_0000034")


def test_seeds_have_no_invalid_subclass_disjointness():
    # Guard the seed templates directly: no seed may declare a disjointness
    # between two BFO classes where one is an ancestor of the other.
    from pathlib import Path
    import re

    repo = Path(__file__).resolve().parents[1]
    pat = re.compile(
        r"obo:(BFO_\d+)\s+owl:disjointWith\s+obo:(BFO_\d+)\s*\."
    )
    offenders = []
    for ttl in repo.glob("ontology/library/*/seed/*.ttl"):
        for a, b in pat.findall(ttl.read_text(encoding="utf-8")):
            if bfo_catalog.is_descendant_of(a, b) or bfo_catalog.is_descendant_of(b, a):
                offenders.append(f"{ttl.name}: {a} disjointWith {b}")
    assert not offenders, "invalid BFO subclass-pair disjointness in seeds: " + \
        "; ".join(offenders)
