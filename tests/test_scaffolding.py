"""Tests for relation-aware scaffolding at emission (SPEC Task 4)."""
from pathlib import Path

import pytest

from app import coherence_gate as cg
from app import config
from app import relation_vocab as rv
from app.ontology_manager import OntologyManager
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.fixture()
def upper_fillers(monkeypatch):
    """Legacy (pre-QS-D2) behaviour: scaffold with top-level BFO fillers."""
    monkeypatch.setattr(config, "SCAFFOLD_UPPER_FILLERS", True)


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


def test_quality_gets_inheres_in_directive(manager, upper_fillers):
    p = _quality_class()
    directives = cg.scaffolding_directives(p, manager)
    assert len(directives) == 1
    d = directives[0]
    assert d["kind"] == "quality"
    assert d["prop"] == "BFO_0000197"  # inheres_in
    assert d["filler"] == "BFO_0000004"  # independent continuant


def test_disposition_gets_realized_in_directive(manager, upper_fillers):
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


def test_scaffolding_applies_and_reasons_coherently(manager, upper_fillers):
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


def test_scaffolding_idempotent(manager, upper_fillers):
    p = _quality_class()
    manager.commit_proposal(p)
    cg.apply_scaffolding(cg.scaffolding_directives(p, manager), manager)
    # Second pass should add nothing (restriction already present).
    again = cg.apply_scaffolding(cg.scaffolding_directives(p, manager), manager)
    assert again == []


# ---------------------------------------------------------------- QS-D2

def test_qs_d2_no_upper_level_filler_by_default(manager):
    assert config.SCAFFOLD_UPPER_FILLERS is False
    assert cg.scaffolding_directives(_quality_class(), manager) == []


def _quality_with_domain_filler(prop="BFO_0000197") -> Proposal:
    p = _quality_class()
    p.entities.append(Entity(label="Glass", iri_suggestion="working:Glass",
                             bfo_type="BFO_0000040", bfo_label="material entity",
                             kind="class", rationale="r"))
    p.relations.append(Relation(s="working:Brittleness", p=prop,
                                o="working:Glass", rationale="r"))
    return p


def test_qs_d2_domain_filler_emits_directive(manager):
    directives = cg.scaffolding_directives(_quality_with_domain_filler(), manager)
    assert len(directives) == 1
    assert directives[0]["filler"] == "working:Glass"


def test_qs_d2_ro_alias_counts_as_inheres_in(manager):
    directives = cg.scaffolding_directives(
        _quality_with_domain_filler("RO_0000052"), manager)
    assert [d["prop"] for d in directives] == ["BFO_0000197"]


def test_qs_d2_scaffolded_annotation_present(manager, upper_fillers):
    p = _quality_class()
    manager.commit_proposal(p)
    applied = cg.apply_scaffolding(cg.scaffolding_directives(p, manager), manager)
    assert len(applied) == 1
    from rdflib import OWL, URIRef
    g = manager.world.as_rdflib_graph()
    flagged = list(g.subjects(URIRef(rv.bfoagent("scaffolded")), None))
    assert len(flagged) == 1
    assert (flagged[0], OWL.annotatedSource,
            URIRef("http://davidkoepsell.com/bfo-agent/working#Brittleness")) in g
