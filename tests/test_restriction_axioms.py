"""Regression tests for the restriction-axiom bug.

The proposer sometimes expresses an existential restriction as the object of a
subClassOf edge using a blank-node placeholder (``_:x BFO_0000197 BFO_0000040``
or a bare ``_:someLabel``). The old commit path wrote these verbatim, minting a
dangling ``#_:...`` IRI: an invalid, semantically-inert axiom. These tests pin
the fix: parseable forms become real ``owl:Restriction`` nodes, everything else
is skipped, and NO ``#_:`` IRI is ever persisted.
"""
from pathlib import Path

import pytest

from app.ontology_manager import OntologyManager
from app.schema import Entity, Proposal, Relation
from app import owl_checks

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.fixture
def manager(tmp_path):
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    return OntologyManager(BFO_PATH, tmp_path / "working.owl")


def _legal_role_entity():
    return Entity(
        label="Legal Role",
        iri_suggestion="working:LegalRole",
        bfo_type="BFO_0000023",  # role
        bfo_label="role",
        rationale="test",
    )


def _serialize(manager) -> str:
    return manager.world.as_rdflib_graph().serialize(format="xml")


def test_bnode_placeholder_object_is_skipped(manager):
    """A bare ``_:label`` object must be dropped with a warning, not persisted."""
    prop = Proposal(
        session_id="t",
        utterance="u",
        entities=[_legal_role_entity()],
        relations=[
            Relation(
                s="working:LegalRole",
                p="rdfs:subClassOf",
                o="_:legalRoleInheresInPerson",
                rationale="test",
            )
        ],
    )
    warnings = manager.apply_proposal(prop)
    assert any("blank-node placeholder" in w for w in warnings), warnings
    assert "_:" not in _serialize(manager)


def test_restriction_expression_is_materialized(manager):
    """``_:x PROP FILLER`` becomes a real (PROP some FILLER) restriction."""
    prop = Proposal(
        session_id="t",
        utterance="u",
        entities=[_legal_role_entity()],
        relations=[
            Relation(
                s="working:LegalRole",
                p="rdfs:subClassOf",
                o="_:x BFO_0000197 BFO_0000040",  # inheres_in some material entity
                rationale="test",
            )
        ],
    )
    warnings = manager.apply_proposal(prop)
    assert warnings == [], warnings
    assert manager.has_restriction_on("working:LegalRole", "BFO_0000197")
    assert "_:" not in _serialize(manager)


def test_restriction_on_kernel_class_is_refused(manager):
    """A restriction whose subject is a BFO kernel class must not mutate BFO."""
    prop = Proposal(
        session_id="t",
        utterance="u",
        relations=[
            Relation(
                s="BFO_0000023",  # role -- imported kernel class
                p="rdfs:subClassOf",
                o="_:x BFO_0000197 BFO_0000040",
                rationale="test",
            )
        ],
    )
    warnings = manager.apply_proposal(prop)
    assert any("kernel class" in w for w in warnings), warnings
    assert not manager.has_restriction_on("BFO_0000023", "BFO_0000197")
    assert "_:" not in _serialize(manager)


def test_unresolvable_restriction_is_skipped(manager):
    """A restriction naming an unknown property/filler is skipped, not minted."""
    prop = Proposal(
        session_id="t",
        utterance="u",
        entities=[_legal_role_entity()],
        relations=[
            Relation(
                s="working:LegalRole",
                p="rdfs:subClassOf",
                o="_:x working:NoSuchProp working:NoSuchFiller",
                rationale="test",
            )
        ],
    )
    warnings = manager.apply_proposal(prop)
    assert any("unresolvable restriction" in w for w in warnings), warnings
    assert "_:" not in _serialize(manager)


def test_check_bnode_iris_flags_fragment():
    raw = '<rdfs:subClassOf rdf:resource="#_:legalRoleInheresInPerson"/>'
    rep = owl_checks.check_bnode_iris(raw)
    assert not rep.passed
    assert any(f.code == "E_BNODE_IRI" for f in rep.findings)


def test_check_bnode_iris_clean_passes():
    raw = '<rdfs:subClassOf rdf:resource="#LegalRole"/>'
    assert owl_checks.check_bnode_iris(raw).passed
