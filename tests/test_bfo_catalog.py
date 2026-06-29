"""Tests for the shared BFO bundle (app/bfo_catalog.py).

Includes a drift guard: re-parse the vendored bfo.owl and assert the pinned
disjointness and parent tables still match the file, so the hand-maintained
data cannot silently diverge from BFO 2020.
"""
from pathlib import Path

import pytest

from app import bfo_catalog as bc

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


def _frag(c):
    return c.iri.split("/")[-1]


@pytest.fixture(scope="module")
def bfo_world():
    owlready2 = pytest.importorskip("owlready2")
    w = owlready2.World()
    w.get_ontology(str(BFO_PATH)).load()
    return w


def test_disjoint_pairs_match_bfo_file(bfo_world):
    """The pinned DISJOINT_PAIRS equals the pairwise expansion of bfo.owl's groups."""
    derived = set()
    for d in bfo_world.disjoint_classes():
        members = sorted(_frag(e) for e in d.entities if hasattr(e, "iri"))
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                derived.add(frozenset({a, b}))
    assert set(bc.DISJOINT_PAIRS) == derived


def test_parent_map_matches_bfo_file(bfo_world):
    """The pinned BFO_PARENT matches each class's immediate BFO parent."""
    for c in bfo_world.classes():
        frag = _frag(c)
        if not frag.startswith("BFO_"):
            continue
        parents = [
            _frag(p) for p in c.is_a
            if hasattr(p, "iri") and _frag(p).startswith("BFO_")
        ]
        expected = parents[0] if parents else None
        assert bc.BFO_PARENT.get(frag) == expected, frag


def test_kernel_classes_match_bfo_file(bfo_world):
    """K_C equals exactly the BFO_ class fragments declared in bfo.owl."""
    derived = {
        _frag(c) for c in bfo_world.classes() if _frag(c).startswith("BFO_")
    }
    assert set(bc.KERNEL_CLASSES) == derived


def test_kernel_properties_match_bfo_file(bfo_world):
    """K_P equals exactly the BFO_ object-property fragments in bfo.owl."""
    derived = {
        _frag(p) for p in bfo_world.object_properties()
        if _frag(p).startswith("BFO_")
    }
    assert set(bc.KERNEL_PROPERTIES) == derived


def test_kernel_membership_helpers():
    assert bc.is_kernel_class("bfo:BFO_0000040")
    assert not bc.is_kernel_class("working:Norm")
    assert bc.is_kernel_property("BFO_0000197")        # inheres in
    assert not bc.is_kernel_property("RO_0000052")     # obsolete RO alias
    assert bc.is_meta_predicate("rdfs:subClassOf")
    assert bc.is_meta_predicate("rdf:type")


def test_quality_disposition_clash():
    """The canonical Force straddle: Quality vs Disposition must clash."""
    assert bc.clash(bc.QUALITY, bc.DISPOSITION)
    assert bc.clash(bc.DISPOSITION, bc.QUALITY)


def test_function_not_disjoint_from_disposition():
    """Function is a subClassOf Disposition in BFO 2020, so it must NOT clash."""
    assert not bc.clash(bc.FUNCTION, bc.DISPOSITION)


def test_continuant_occurrent_clash():
    assert bc.clash("BFO_0000040", bc.OCCURRENT)  # material entity vs occurrent
    assert bc.clash(bc.QUALITY, bc.PROCESS)        # quality vs process


def test_role_disposition_clash():
    assert bc.clash(bc.ROLE, bc.DISPOSITION)


def test_same_category_no_clash():
    assert not bc.clash(bc.QUALITY, bc.QUALITY)
    assert not bc.clash(bc.DISPOSITION, bc.DISPOSITION)


def test_straddles_reports_pair():
    hit, pair = bc.straddles([bc.QUALITY, bc.DISPOSITION])
    assert hit and pair is not None
    assert set(pair) == {bc.QUALITY, bc.DISPOSITION}


def test_straddles_clean_set():
    hit, pair = bc.straddles([bc.QUALITY, "BFO_0000145"])  # quality + relational quality
    assert not hit and pair is None


def test_normalize_fragment():
    assert bc.normalize_fragment("bfo:BFO_0000019") == "BFO_0000019"
    assert bc.normalize_fragment(
        "http://purl.obolibrary.org/obo/BFO_0000019"
    ) == "BFO_0000019"
    assert bc.normalize_fragment("BFO_0000019") == "BFO_0000019"


def test_no_em_dashes_in_source():
    src = (REPO / "app" / "bfo_catalog.py").read_text(encoding="utf-8")
    assert "—" not in src
