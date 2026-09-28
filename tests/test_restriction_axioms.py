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


# ---------------------------------------------------------------------------
# QS-A3: object-property relations between classes are converted to class
# axioms instead of being written as punned triples the reasoner ignores.
# ---------------------------------------------------------------------------
_W = "http://davidkoepsell.com/bfo-agent/working#"
_OBO = "http://purl.obolibrary.org/obo/"
_QDEF = "http://davidkoepsell.com/bfo-agent/meta#quantifierDefaulted"


def _qs_a3_world(manager, *relations):
    ents = [
        _legal_role_entity().model_copy(update={"kind": "class"}),
        Entity(label="Person", iri_suggestion="working:Person", kind="class",
               bfo_type="BFO_0000040", bfo_label="material entity",
               rationale="test"),
        Entity(label="alice", iri_suggestion="working:alice", kind="individual",
               bfo_type="working:Person", bfo_label="person", rationale="test"),
        Entity(label="role1", iri_suggestion="working:role1", kind="individual",
               bfo_type="working:LegalRole", bfo_label="role", rationale="test"),
    ]
    prop = Proposal(session_id="t", utterance="u", entities=ents,
                    relations=list(relations))
    warnings = manager.apply_proposal(prop)
    return warnings, manager.world[_W + "LegalRole"]


def _defaulted(manager, cls, restriction):
    ann = manager.world[_QDEF]
    return ann is not None and bool(ann[cls, rdfs_subclassof, restriction])


from owlready2 import rdfs_subclassof  # noqa: E402


def test_qs_a3_class_to_class_becomes_some_restriction(manager):
    warnings, cls = _qs_a3_world(manager, Relation(
        s="working:LegalRole", p="RO_0000052", o="working:Person",
        rationale="t"))
    assert not warnings
    inheres = manager.world[_OBO + "BFO_0000197"]
    r = [m for m in cls.is_a if getattr(m, "property", None) is inheres]
    assert len(r) == 1 and r[0].type == 24  # owlready2 SOME
    assert _defaulted(manager, cls, r[0])
    # no punned class->class triple
    g = manager.world.as_rdflib_graph()
    from rdflib import URIRef
    assert (URIRef(_W + "LegalRole"), URIRef(_OBO + "BFO_0000197"),
            URIRef(_W + "Person")) not in g


def test_qs_a3_stated_only_quantifier_is_not_marked_defaulted(manager):
    warnings, cls = _qs_a3_world(manager, Relation(
        s="working:LegalRole", p="BFO_0000197", o="working:Person",
        rationale="t", quantifier="only"))
    assert not warnings
    r = [m for m in cls.is_a if getattr(m, "type", None) == 25]  # ONLY
    assert len(r) == 1
    assert not _defaulted(manager, cls, r[0])


def test_qs_a3_class_to_individual_becomes_has_value(manager):
    warnings, cls = _qs_a3_world(manager, Relation(
        s="working:LegalRole", p="BFO_0000197", o="working:alice",
        rationale="t"))
    assert not warnings
    r = [m for m in cls.is_a if getattr(m, "type", None) == 29]  # VALUE
    assert len(r) == 1 and r[0].value is manager.world[_W + "alice"]


def test_qs_a3_individual_to_individual_is_a_property_assertion(manager):
    warnings, _ = _qs_a3_world(manager, Relation(
        s="working:role1", p="BFO_0000197", o="working:alice", rationale="t"))
    assert not warnings
    from rdflib import URIRef
    assert (URIRef(_W + "role1"), URIRef(_OBO + "BFO_0000197"),
            URIRef(_W + "alice")) in manager.world.as_rdflib_graph()


def test_qs_a3_individual_to_class_is_refused(manager):
    warnings, _ = _qs_a3_world(manager, Relation(
        s="working:role1", p="BFO_0000197", o="working:Person", rationale="t"))
    assert any("individual" in w and "class" in w for w in warnings)


def test_qs_a3_unknown_endpoint_is_refused(manager):
    warnings, _ = _qs_a3_world(manager, Relation(
        s="working:LegalRole", p="BFO_0000197", o="working:Nowhere",
        rationale="t"))
    assert any("cannot classify" in w for w in warnings)


def test_qs_a3_rollback_removes_restriction_and_annotation(manager):
    from app.ontology_manager import AppliedDelta
    base = Proposal(session_id="t", utterance="u", entities=[
        _legal_role_entity().model_copy(update={"kind": "class"}),
        Entity(label="Person", iri_suggestion="working:Person", kind="class",
               bfo_type="BFO_0000040", bfo_label="material entity",
               rationale="test")])
    manager.apply_proposal(base)
    delta = AppliedDelta()
    manager.apply_proposal(Proposal(session_id="t", utterance="u", relations=[
        Relation(s="working:LegalRole", p="RO_0000052", o="working:Person",
                 rationale="t")]), delta=delta)
    manager.rollback(delta)
    cls = manager.world[_W + "LegalRole"]
    assert all(getattr(m, "property", None) is None for m in cls.is_a)
    from rdflib import URIRef
    g = manager.world.as_rdflib_graph()
    assert not list(g.triples((None, URIRef(_QDEF), None)))
