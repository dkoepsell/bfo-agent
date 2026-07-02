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
