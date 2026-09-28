"""The commit-time coherence backstop: a commit that would make the persisted
ontology inconsistent/incoherent is rolled back and raises CommitCoherenceError,
so a malformed ontology can never accumulate on disk even if an upstream gate
tier is skipped."""
from pathlib import Path

import pytest

from app.ontology_manager import CommitCoherenceError, OntologyManager
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.fixture
def manager(tmp_path):
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    return OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "working.owl")


def _quality_force():
    return Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Force", iri_suggestion="working:Force",
                         kind="class",
                         bfo_type="BFO_0000019", bfo_label="quality",
                         rationale="t")],
    )


def test_coherent_commit_persists(manager):
    manager.commit_proposal(_quality_force())
    ok, _ = manager._verify_saved_coherent()
    assert ok
    assert "Force" in manager.working_path.read_text()


def test_incoherent_commit_is_rolled_back(manager):
    # 1. Force as a quality — coherent, commits fine.
    manager.commit_proposal(_quality_force())
    before = manager.working_path.read_text()

    # 2. Force ALSO under disposition (disjoint from quality via realizable):
    #    would make Force unsatisfiable. This bypasses lint by going straight
    #    to commit — the backstop must catch it.
    straddle = Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="bfo:BFO_0000016", rationale="t")],
    )
    with pytest.raises(CommitCoherenceError):
        manager.commit_proposal(straddle)

    # 3. Rolled back: persisted file is byte-for-byte the pre-commit state and
    #    verifies coherent again.
    assert manager.working_path.read_text() == before
    ok, _ = manager._verify_saved_coherent()
    assert ok


def test_verify_can_be_disabled(manager):
    # verify=False keeps the old fast path (no reasoner) for callers that have
    # already reasoned; it must not raise even on a straddle.
    manager.commit_proposal(_quality_force())
    straddle = Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="bfo:BFO_0000016", rationale="t")],
    )
    manager.commit_proposal(straddle, verify=False)  # no exception


# ---------------------------------------------------------------------------
# QS-A4 predicate allowlist, QS-A5 one local namespace, QS-A6 sentences.
# ---------------------------------------------------------------------------
_W = "http://davidkoepsell.com/bfo-agent/working#"


def _two_classes(p, o="working:Institution"):
    return Proposal(
        session_id="t", utterance="u",
        entities=[
            Entity(label="Court", iri_suggestion="working:Court", kind="class",
                   bfo_type="BFO_0000040", bfo_label="material entity",
                   rationale="t"),
            Entity(label="Institution", iri_suggestion="working:Institution",
                   kind="class", bfo_type="BFO_0000040",
                   bfo_label="material entity", rationale="t"),
        ],
        relations=[Relation(s="working:Court", p=p, o=o, rationale="t")],
    )


@pytest.mark.parametrize("pred", [
    "working:rdfs:subClassOf",   # mangled standard predicate
    "owl:subClassOf",            # not an OWL term
    "working:regulates",         # undeclared local property
])
def test_qs_a4_disallowed_predicates_are_refused(manager, pred):
    from rdflib import URIRef
    warnings = manager.apply_proposal(_two_classes(pred))
    assert len(warnings) == 1 and "failed" in warnings[0]
    g = manager.world.as_rdflib_graph()
    assert not list(g.triples(
        (URIRef(_W + "Court"), None, URIRef(_W + "Institution"))))


@pytest.mark.parametrize("pred", [
    "rdfs:subClassOf", "owl:disjointWith", "owl:equivalentClass",
    "BFO_0000176",   # declared object property (continuant part of)
])
def test_qs_a4_allowed_predicates_are_written(manager, pred):
    assert manager.apply_proposal(_two_classes(pred)) == []


def test_qs_a4_declared_annotation_property_is_allowed(manager):
    # bfoagent:distinctFrom is declared by the vocabulary seed (QS-B4).
    assert manager.apply_proposal(_two_classes("bfoagent:distinctFrom")) == []


def test_qs_a5_new_ontology_uses_working_namespace(manager):
    manager.commit_proposal(_quality_force())
    assert "file://" not in manager.working_path.read_text().split("owl:imports")[0]
    assert manager.world[_W + "Force"] is not None


def test_qs_a5_legacy_file_base_mints_new_entities_in_working_ns(tmp_path):
    """A legacy working.owl serialized under a file:// base stays readable,
    but new entities go under WORKING_IRI, never the file:// base."""
    legacy = tmp_path / "working.owl"
    legacy.write_text(
        '<?xml version="1.0"?>\n'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
        '  xmlns:owl="http://www.w3.org/2002/07/owl#"\n'
        '  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"\n'
        '  xml:base="file:///old/ontology/working.owl">\n'
        '<owl:Ontology rdf:about="file:///old/ontology/working.owl">\n'
        f'  <owl:imports rdf:resource="{BFO_PATH.resolve().as_uri()}"/>\n'
        '</owl:Ontology>\n'
        '<owl:Class rdf:about="file:///old/ontology/working.owl#Institution">\n'
        '  <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000040"/>\n'
        '</owl:Class>\n'
        '</rdf:RDF>\n')
    m = OntologyManager(bfo_path=BFO_PATH, working_path=legacy)
    warnings = m.apply_proposal(Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Court", iri_suggestion="working:Court",
                         kind="class", parent_class="working:Institution",
                         bfo_type="BFO_0000040", bfo_label="material entity",
                         rationale="t")]))
    assert warnings == []
    court = m.world[_W + "Court"]
    assert court is not None
    # the legacy parent was found by local name, not forked into a twin
    assert m.world["file:///old/ontology/working.owl#Institution"] in court.is_a
    assert m.world[_W + "Institution"] is None


def test_qs_a5_upper_ontology_id_is_never_minted_locally(manager):
    warnings = manager.apply_proposal(Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="bad", iri_suggestion="BFO_0000040",
                         kind="class", bfo_type="BFO_0000040",
                         bfo_label="material entity", rationale="t")]))
    assert len(warnings) == 1
    assert manager.world[_W + "BFO_0000040"] is None


def test_qs_a6_sentence_object_on_annotation_becomes_literal(manager):
    manager.apply_proposal(_two_classes("rdfs:subClassOf"))
    text = "A court is an institution that adjudicates disputes."
    assert manager.apply_proposal(Proposal(
        session_id="t", utterance="u", relations=[Relation(
            s="working:Court", p="rdfs:comment", o=text, rationale="t")])) == []
    assert text in [str(c) for c in manager.world[_W + "Court"].comment]


def test_qs_a6_sentence_object_on_object_property_is_refused(manager):
    warnings = manager.apply_proposal(_two_classes(
        "BFO_0000176", o="Courts are part of the judicial branch of government."))
    assert any("rdfs:comment" in w for w in warnings)
