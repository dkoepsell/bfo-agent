"""Tests for relation-aware scaffolding at emission (SPEC Task 4)."""
from pathlib import Path

import pytest

from app import coherence_gate as cg
from app.ontology_manager import OntologyManager
from app.schema import Entity, Proposal

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.fixture()
def manager(tmp_path):
    pytest.importorskip("owlready2")
    return OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "working.owl")


def _quality_class(name="Brittleness") -> Proposal:
    return Proposal(
        session_id="t", utterance=f"{name} is a quality",
        entities=[Entity(label=name, iri_suggestion=f"working:{name}",
                         bfo_type="BFO_0000019", bfo_label="quality",
                         kind="class", rationale="r")],
    )


def test_quality_gets_inheres_in_directive(manager):
    p = _quality_class()
    directives = cg.scaffolding_directives(p, manager)
    assert len(directives) == 1
    d = directives[0]
    assert d["kind"] == "quality"
    assert d["prop"] == "BFO_0000197"  # inheres_in
    assert d["filler"] == "BFO_0000004"  # independent continuant


def test_disposition_gets_realized_in_directive(manager):
    p = Proposal(
        session_id="t", utterance="Fragility is a disposition",
        entities=[Entity(label="Fragility", iri_suggestion="working:Fragility",
                         bfo_type="BFO_0000016", bfo_label="disposition",
                         kind="class", rationale="r")],
    )
    directives = cg.scaffolding_directives(p, manager)
    assert len(directives) == 1
    assert directives[0]["kind"] == "disposition"
    assert directives[0]["prop"] == "BFO_0000054"  # realized_in


def test_scaffolding_applies_and_reasons_coherently(manager):
    p = _quality_class()
    manager.commit_proposal(p)
    directives = cg.scaffolding_directives(p, manager)
    applied = cg.apply_scaffolding(directives, manager)
    assert len(applied) == 1
    assert manager.has_restriction_on("working:Brittleness", "BFO_0000197")
    manager.save()

    # The scaffolded ontology still reasons coherently.
    coherent, unsat, _ = manager.check_coherence_dry_run(
        Proposal(session_id="t", utterance="")
    )
    assert coherent
    assert unsat == []


def test_scaffolding_idempotent(manager):
    p = _quality_class()
    manager.commit_proposal(p)
    cg.apply_scaffolding(cg.scaffolding_directives(p, manager), manager)
    # Second pass should add nothing (restriction already present).
    again = cg.apply_scaffolding(cg.scaffolding_directives(p, manager), manager)
    assert again == []
