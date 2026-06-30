"""Tests for the BFO-anchored SOoL/MLC extension (app/sool_extension.py).

BFO 2020 stays dominant: K_C is BFO-only, and every MLC node must reduce to a
BFO category. These tests pin the anchor contract.
"""
from app import bfo_catalog, sool_extension as sx


def test_every_mlc_anchor_is_a_real_bfo_category():
    # The extension can never drift off BFO: each anchor is in K_C.
    assert sx.self_check() == []
    for node in sx.MLC_NODES:
        assert node.bfo_anchor in bfo_catalog.KERNEL_CLASSES


def test_extension_adds_nothing_to_kc():
    # Sanity: the named SOoL anchors are BFO IRIs, not minted SOoL classes.
    for node in sx.MLC_NODES:
        assert node.bfo_anchor.startswith("BFO_")


def test_occupant_must_descend_from_node_anchor():
    # Disposition (BFO_0000016) is a Realizable (BFO_0000017) -> ok for Legal Effect.
    assert sx.occupant_anchored("BFO_0000016", 7).ok
    # Role for Actor-in-Role.
    assert sx.occupant_anchored("BFO_0000023", 3).ok
    # A material entity is not a Norm (SDC).
    assert not sx.occupant_anchored("BFO_0000040", 2).ok


def test_off_bfo_term_is_not_anchored():
    assert not sx.is_anchored("working:NormDependencyRelation")
    assert not sx.validate_term_anchored("working:AbsenceOfLegalExistence").ok
    assert sx.is_anchored("BFO_0000020")


def test_anchor_for_node_by_index_and_name():
    assert sx.anchor_for_node(3) == sx.anchor_for_node("Actor-in-Role")
    assert sx.anchor_for_node(7) == bfo_catalog.REALIZABLE


def test_mlc_is_sool_only():
    # MLC must apply to SOoL and NOTHING else (errs closed).
    assert sx.applies_to("SOoL_v1")
    assert sx.applies_to("ontology/library/SOoL_v1/working.owl")
    for other in ("SpinozaCorpus",
                  "ontology/library/LeibnitzPhilCorpus/working.owl",
                  "GeometryofTheGood", None, ""):
        assert not sx.applies_to(other)
