"""QS-D5 transitive reduction, QS-D6 fan-out report, QS-D8 examples module
(SPEC-bfo-agent-quality.md Workstream D)."""
from pathlib import Path

import pytest

from app import content_quality as cq
from app.ontology_manager import AppliedDelta, OntologyManager

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.fixture()
def manager(tmp_path):
    pytest.importorskip("owlready2")
    return OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "working.owl")


def _chain(manager):
    """Court ⊑ Institution ⊑ object; Court also asserted ⊑ object."""
    obj = manager.world["http://purl.obolibrary.org/obo/BFO_0000030"]
    with manager.working:
        inst = type("Institution", (obj,), {})
        court = type("Court", (inst,), {})
        court.is_a.append(obj)
    return court, inst, obj


def _entailed_ancestors(cls):
    return {a.iri for a in cls.ancestors()}


def test_qs_d5_redundant_parent_removed(manager):
    court, inst, obj = _chain(manager)
    before = _entailed_ancestors(court)
    delta = AppliedDelta()
    removed = cq.reduce_redundant_parents(court, delta)
    assert removed == [(court.iri, obj.iri)]
    assert obj not in court.is_a and inst in court.is_a
    assert _entailed_ancestors(court) == before  # entailments unchanged
    assert delta.retracted == [(court.iri, obj.iri)]


def test_qs_d5_rollback_restores(manager):
    court, _inst, obj = _chain(manager)
    delta = AppliedDelta()
    cq.reduce_redundant_parents(court, delta)
    manager.rollback(delta)
    assert obj in court.is_a


def test_qs_d5_independent_parents_kept(manager):
    obj = manager.world["http://purl.obolibrary.org/obo/BFO_0000030"]
    role = manager.world["http://purl.obolibrary.org/obo/BFO_0000023"]
    with manager.working:
        a = type("A", (obj,), {})
        b = type("B", (role,), {})
        c = type("C", (a, b), {})
    assert cq.reduce_redundant_parents(c) == []
    assert set(c.is_a) == {a, b}


def test_qs_d6_fanout_report(manager):
    obj = manager.world["http://purl.obolibrary.org/obo/BFO_0000030"]
    with manager.working:
        hub = type("Hub", (obj,), {})
        for i in range(5):
            type(f"Kid{i}", (hub,), {})
    hubs = cq.fanout_report(manager.working.classes(), limit=4)
    assert [h["iri"] for h in hubs] == [hub.iri]
    assert hubs[0]["direct_subclasses"] == 5
    assert cq.fanout_report(manager.working.classes(), limit=5) == []


def test_qs_d8_examples_module(tmp_path):
    from rdflib import OWL, RDF, Graph, URIRef
    w = "http://davidkoepsell.com/bfo-agent/working"
    out = cq.write_examples_module(
        [(w + "#Alice", w + "#Judge", "Alice")], w, tmp_path / "examples.owl")
    cq.write_examples_module([(w + "#Bob", w + "#Judge", "Bob")], w, out)
    g = Graph().parse(out)
    assert (URIRef(w + "/examples"), OWL.imports, URIRef(w)) in g
    assert {str(s) for s in g.subjects(RDF.type, URIRef(w + "#Judge"))} == {
        w + "#Alice", w + "#Bob"}


def test_qs_d3_reuse_candidates_rank_exact_and_canonical_first():
    classes = [{"iri": "a", "label": "Legal Recognition Process"},
               {"iri": "b", "label": "Recognition"},
               {"iri": "c", "label": "Recognition Act Tribunal"},
               {"iri": "d", "label": "Contract"}]
    cands = cq.reuse_candidates("RecognitionDisposition", classes, canonical={"b"})
    assert [c["iri"] for c in cands][:2] == ["b", "a"]
    assert "d" not in [c["iri"] for c in cands]


def test_qs_d3_utterance_candidates():
    classes = [{"iri": "a", "label": "Court"}, {"iri": "b", "label": "Contract"}]
    cands = cq.utterance_candidates("The court hears the case.", classes)
    assert [c["iri"] for c in cands] == ["a"]
    assert "REUSE CANDIDATES" in cq.format_utterance_candidates(cands)


def test_qs_d1_annotate_new_class(manager):
    from types import SimpleNamespace
    from rdflib import Literal, URIRef
    from app import relation_vocab as rv
    iri = "http://davidkoepsell.com/bfo-agent/working#JudicialRole"
    ent = SimpleNamespace(definition="A role that a judge bears.",
                          source_span="the judge's role", definition_status=None,
                          distinct_from="http://x#Role")
    added = cq.annotate_new_class(manager.world, iri, ent, onto=manager.working)
    g = manager.world.as_rdflib_graph()
    assert (URIRef(iri), URIRef(rv.IAO_DEFINITION),
            Literal("A role that a judge bears.", lang="en")) in g
    assert len(added) == 3
