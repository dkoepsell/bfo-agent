"""QS-D1 / QS-D5 / QS-D8 wired through OntologyManager.apply_proposal."""
from pathlib import Path

import pytest

from app import relation_vocab as rv
from app.ontology_manager import AppliedDelta, OntologyManager, WORKING_IRI
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"
_W = WORKING_IRI + "#"


@pytest.fixture
def manager(tmp_path):
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    return OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "working.owl")


def _cls(label, parent=None, **kw):
    return Entity(label=label, iri_suggestion=f"working:{label}", kind="class",
                  bfo_type="BFO_0000040", bfo_label="material entity",
                  parent_class=parent, rationale="t", **kw)


def test_qs_d1_new_class_gets_definition_and_span(manager):
    from rdflib import URIRef
    manager.apply_proposal(Proposal(session_id="t", utterance="u", entities=[
        _cls("Court", definition="A material entity that adjudicates.",
             source_span="courts adjudicate")]))
    g = manager.world.as_rdflib_graph()
    s = URIRef(_W + "Court")
    assert str(g.value(s, URIRef(rv.IAO_DEFINITION))) == \
        "A material entity that adjudicates."
    assert str(g.value(s, URIRef(rv.bfoagent("sourceSpan")))) == "courts adjudicate"


def test_qs_d5_redundant_parent_dropped_and_rollback_restores(manager):
    manager.commit_proposal(Proposal(session_id="t", utterance="u",
                                     entities=[_cls("Institution")]))
    delta = AppliedDelta()
    manager.apply_proposal(Proposal(
        session_id="t", utterance="u", entities=[_cls("Court")],
        relations=[Relation(s="working:Court", p="rdfs:subClassOf",
                            o="working:Institution", rationale="t")]),
        delta=delta)
    court = manager.world[_W + "Court"]
    parents = {getattr(p, "iri", None) for p in court.is_a}
    assert _W + "Institution" in parents
    assert rv.OBO + "BFO_0000040" not in parents          # entailed, dropped
    assert (court.iri, rv.OBO + "BFO_0000040") in delta.retracted
    manager.rollback(delta)
    assert manager.world[_W + "Court"] is None or rv.OBO + "BFO_0000040" in {
        getattr(p, "iri", None) for p in manager.world[_W + "Court"].is_a}


def test_qs_d8_illustrative_individual_goes_to_examples_module(manager):
    manager.apply_proposal(Proposal(session_id="t", utterance="u", entities=[
        Entity(label="Alice", iri_suggestion="working:Alice", kind="individual",
               bfo_type="BFO_0000040", bfo_label="material entity",
               rationale="t", illustrative=True)]))
    assert manager.world[_W + "Alice"] is None
    manager.save()
    ex = manager.working_path.parent / "examples.owl"
    assert ex.exists() and "Alice" in ex.read_text()
    assert "Alice" not in manager.working_path.read_text()
