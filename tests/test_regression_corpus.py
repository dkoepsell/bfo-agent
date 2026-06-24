"""Regression corpus: before/after the coherence gate (SPEC Task 6).

Before: the frozen baseline aero_straddle_baseline.owl has a known set of
unsatisfiable classes (the Force/Drag/*Load family, each straddling Quality and
Disposition). After: regenerating the same domain through the gate produces zero
unsatisfiable classes. This is the figure that demonstrates the gate works.
"""
from pathlib import Path

import pytest

from app import coherence_gate as cg
from app.ontology_manager import OntologyManager
from app.schema import Entity, Proposal, Relation
from evaluation import coherence_scorer as scorer

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"
BASELINE = REPO / "tests" / "corpus" / "aero_straddle_baseline.owl"

# The known unsatisfiable set frozen into the baseline fixture.
KNOWN_STRADDLERS = {
    "Force", "Weight", "Drag", "Lift", "Toughness", "NoiseNuisance",
    "InducedDrag", "WingLoad", "GustLoad", "LandingLoad",
}


def test_frozen_baseline_has_known_unsatisfiable_set():
    pytest.importorskip("owlready2")
    field = scorer.coherence_field(BASELINE, BFO_PATH)
    assert field["coherent"] is False
    names = {iri.split("#")[-1].split("/")[-1] for iri in field["unsatisfiable_classes"]}
    assert names == KNOWN_STRADDLERS


def test_regeneration_under_gate_is_coherent(tmp_path):
    """Feed each straddler through the gate under the repair policy; the result
    must have zero unsatisfiable classes."""
    pytest.importorskip("owlready2")
    mgr = OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "working.owl")

    blocked = 0
    for name in sorted(KNOWN_STRADDLERS):
        # First, the (clean) quality typing.
        p_quality = Proposal(
            session_id="regen", utterance=f"{name} is a quality",
            entities=[Entity(label=name, iri_suggestion=f"working:{name}",
                             bfo_type="BFO_0000019", bfo_label="quality",
                             kind="class", rationale="r")],
        )
        run = cg.run_with_policy(p_quality, mgr, cg.GatePolicy.REPAIR,
                                 run_reasoner=False, max_attempts=1)
        if run.outcome == cg.GateOutcome.ACCEPT:
            mgr.commit_proposal(run.proposal)

        # Now the incoherent disposition straddle. The gate must not let it land.
        p_straddle = Proposal(
            session_id="regen", utterance=f"{name} is a disposition",
            relations=[Relation(s=f"working:{name}", p="rdfs:subClassOf",
                                o="bfo:BFO_0000016", rationale="r")],
        )
        run = cg.run_with_policy(p_straddle, mgr, cg.GatePolicy.REPAIR,
                                 run_reasoner=False, max_attempts=1)
        # Under repair, the clashing edge is dropped; commit the repaired form.
        if run.outcome == cg.GateOutcome.ACCEPT:
            mgr.commit_proposal(run.proposal)
        else:
            blocked += 1

    mgr.save()
    field = scorer.coherence_field(mgr.working_path, BFO_PATH)
    assert field["coherent"] is True, field["unsatisfiable_classes"]
    assert field["unsatisfiable_classes"] == []
