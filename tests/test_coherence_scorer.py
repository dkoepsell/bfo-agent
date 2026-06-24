"""Tests for the coherence-aware win condition (evaluation/coherence_scorer.py)."""
from pathlib import Path

import pytest

from app.ontology_manager import OntologyManager
from app.schema import Entity, Proposal, Relation
from evaluation import coherence_scorer as scorer

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


def _force_quality() -> Proposal:
    return Proposal(
        session_id="t", utterance="Force is a quality",
        entities=[Entity(label="Force", iri_suggestion="working:Force",
                         bfo_type="BFO_0000019", bfo_label="quality",
                         kind="class", rationale="r")],
    )


def test_coherent_ontology_scores_positive(tmp_path):
    pytest.importorskip("owlready2")
    working = tmp_path / "working.owl"
    mgr = OntologyManager(bfo_path=BFO_PATH, working_path=working)
    mgr.commit_proposal(_force_quality())

    result = scorer.score_ontology(working, BFO_PATH, gate_log=[])
    assert result["coherence"]["coherent"] is True
    assert result["score"] >= 0.0


def test_incoherent_ontology_scores_zero(tmp_path):
    pytest.importorskip("owlready2")
    working = tmp_path / "working.owl"
    mgr = OntologyManager(bfo_path=BFO_PATH, working_path=working)
    mgr.commit_proposal(_force_quality())
    # Force the straddle directly into the committed ontology (bypassing the
    # gate) so Force becomes unsatisfiable: Quality and Realizable are disjoint.
    mgr.commit_proposal(Proposal(
        session_id="t", utterance="Force is a disposition",
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="bfo:BFO_0000016", rationale="r")],
    ))

    result = scorer.score_ontology(working, BFO_PATH, gate_log=[])
    assert result["coherence"]["coherent"] is False
    assert result["score"] == 0.0
    assert any("Force" in iri for iri in result["coherence"]["unsatisfiable_classes"])


def test_coherence_preservation_counts():
    gate_log = [
        # run A: lint clash then recovered (accept)
        {"proposal_id": "A", "tier": "lint", "outcome": "reject"},
        {"proposal_id": "A", "tier": "none", "outcome": "accept"},
        # run B: clean accept, never clashed
        {"proposal_id": "B", "tier": "none", "outcome": "accept"},
        # run C: reasoner clash, not recovered
        {"proposal_id": "C", "tier": "reasoner", "outcome": "reject"},
    ]
    p = scorer.coherence_preservation(gate_log)
    assert p["proposed_clashes"] == 2
    assert p["recovered_clashes"] == 1
    assert p["reasoner_tier_clashes"] == 1
    assert p["lint_tier_clashes"] == 1
    assert p["never_clashed"] is False
