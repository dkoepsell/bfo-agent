"""In-memory dry-run (SPEC-bfo-agent-speed.md change 2).

The safety contract under test: with INMEM_DRY_RUN on, a dry-run applies the
proposal to the LIVE world, reasons in a disposable scratch world, and rolls
the live world back EXACTLY -- the persistent graph must be triple-for-triple
identical afterwards, whatever the verdict. A rollback bug here silently
corrupts the persistent ontology, so these tests assert triple-set equality,
per-mutation-kind rollback, FM-9 exclusion restore, verdict parity with the
legacy disk-reload path, the targeted per-proposal sanitizer guards, and the
commit path's memory/file restore.
"""
from pathlib import Path

import pytest

from app import config
from app.ontology_manager import (
    AppliedDelta,
    CommitCoherenceError,
    OntologyManager,
)
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


def _make_manager(tmp_path, name="working.owl"):
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    return OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / name)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "INMEM_DRY_RUN", True)
    return _make_manager(tmp_path)


def _triples(mgr):
    return set(mgr.world.as_rdflib_graph().triples((None, None, None)))


def _local(iri):
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _quality_force(label="Force"):
    return Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label=label, iri_suggestion="working:Force",
                         kind="class",
                         bfo_type="BFO_0000019", bfo_label="quality",
                         rationale="t")],
    )


def _straddle_relation():
    # Force (a quality) also under disposition: unsatisfiable straddle.
    return Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="bfo:BFO_0000016", rationale="t")],
    )


# ------------------------------------------------- triple-set equality


def test_accepted_dry_run_leaves_graph_identical(manager):
    before = _triples(manager)
    ok, unsat, _detail = manager.check_coherence_dry_run(_quality_force())
    assert ok and unsat == []
    assert _triples(manager) == before
    assert not manager.iri_exists("working:Force")


def test_rejected_dry_run_leaves_graph_identical(manager):
    manager.commit_proposal(_quality_force(), verify=False)
    before = _triples(manager)
    ok, unsat, _detail = manager.check_coherence_dry_run(_straddle_relation())
    assert not ok
    assert any(_local(u) == "Force" for u in unsat)
    assert _triples(manager) == before


def test_no_residue_second_dry_run_same_verdict(manager):
    """A dry-run that REASONED must leave no residue: the same dry-run again
    gives the same verdict, and the live graph never gains inferred triples."""
    manager.commit_proposal(_quality_force(), verify=False)
    before = _triples(manager)
    first = manager.check_coherence_dry_run(_straddle_relation())
    assert _triples(manager) == before
    second = manager.check_coherence_dry_run(_straddle_relation())
    assert _triples(manager) == before
    assert first[0] == second[0] is False
    assert sorted(_local(u) for u in first[1]) == \
        sorted(_local(u) for u in second[1])


# ------------------------------------------- rollback of each mutation kind
# These exercise apply+rollback directly (no reasoner needed): the contract
# is exact triple restoration per mutation kind.


def test_rollback_new_class(manager):
    before = _triples(manager)
    delta = AppliedDelta()
    assert manager.apply_proposal(_quality_force(), delta=delta) == []
    assert len(delta.new_entities) == 1
    manager.rollback(delta)
    assert _triples(manager) == before
    assert not manager.iri_exists("working:Force")


def test_rollback_new_individual(manager):
    before = _triples(manager)
    delta = AppliedDelta()
    prop = Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Bob", iri_suggestion="working:Bob",
                         kind="individual",
                         bfo_type="BFO_0000040", bfo_label="material entity",
                         rationale="t")],
    )
    assert manager.apply_proposal(prop, delta=delta) == []
    assert len(delta.new_entities) == 1
    manager.rollback(delta)
    assert _triples(manager) == before
    assert not manager.iri_exists("working:Bob")


def test_rollback_is_a_added_to_existing_class(manager):
    manager.commit_proposal(_quality_force(), verify=False)
    before = _triples(manager)
    delta = AppliedDelta()
    assert manager.apply_proposal(_straddle_relation(), delta=delta) == []
    manager.rollback(delta)
    assert _triples(manager) == before


def test_rollback_retyped_existing_individual(manager):
    """Re-declaring an existing individual under a second type reopens it;
    the added rdf:type must be captured as is_a_added and rolled back."""
    bob = Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Bob", iri_suggestion="working:Bob",
                         kind="individual",
                         bfo_type="BFO_0000040", bfo_label="material entity",
                         rationale="t")],
    )
    manager.commit_proposal(bob, verify=False)
    before = _triples(manager)
    delta = AppliedDelta()
    retype = Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Bob", iri_suggestion="working:Bob",
                         kind="individual",
                         bfo_type="BFO_0000031",
                         bfo_label="generically dependent continuant",
                         rationale="t")],
    )
    assert manager.apply_proposal(retype, delta=delta) == []
    assert delta.new_entities == []
    assert len(delta.is_a_added) == 1
    manager.rollback(delta)
    assert _triples(manager) == before


def test_rollback_restriction_on_existing_class(manager):
    manager.commit_proposal(_quality_force(), verify=False)
    before = _triples(manager)
    delta = AppliedDelta()
    prop = Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="_:x BFO_0000197 BFO_0000040", rationale="t")],
    )
    assert manager.apply_proposal(prop, delta=delta) == []
    assert len(delta.is_a_added) == 1
    manager.rollback(delta)
    assert _triples(manager) == before


def test_rollback_raw_triple_between_existing_individuals(manager):
    setup = Proposal(
        session_id="t", utterance="u",
        entities=[
            Entity(label="Bob", iri_suggestion="working:Bob",
                   kind="individual", bfo_type="BFO_0000040",
                   bfo_label="material entity", rationale="t"),
            Entity(label="Run", iri_suggestion="working:Run",
                   kind="individual", bfo_type="BFO_0000015",
                   bfo_label="process", rationale="t"),
        ],
    )
    manager.commit_proposal(setup, verify=False)
    before = _triples(manager)
    delta = AppliedDelta()
    prop = Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Bob", p="obo:BFO_0000056",
                            o="working:Run", rationale="t")],
    )
    assert manager.apply_proposal(prop, delta=delta) == []
    assert len(delta.raw_triples) == 1
    manager.rollback(delta)
    assert _triples(manager) == before


def test_rollback_label_change_on_reopened_entity(manager):
    """Reusing an existing class with a NEW label replaces the label list;
    rollback must remove the added label AND restore the dropped one."""
    manager.commit_proposal(_quality_force("Force"), verify=False)
    before = _triples(manager)
    delta = AppliedDelta()
    assert manager.apply_proposal(_quality_force("Force v2"), delta=delta) == []
    assert delta.new_entities == []
    assert ("Force v2" in [l for _, l in delta.label_added])
    assert ("Force" in [l for _, l in delta.label_removed])
    manager.rollback(delta)
    assert _triples(manager) == before


# --------------------------------------------------------- FM-9 exclusions


def test_fm9_exclusion_retracted_then_restored(manager):
    manager.commit_proposal(_quality_force(), verify=False)
    before = _triples(manager)
    exclude = [{"s": "working:Force", "p": "rdfs:subClassOf",
                "o": "bfo:BFO_0000019"}]
    ok, _unsat, _detail = manager.check_coherence_dry_run(
        Proposal(session_id="t", utterance=""), exclude_axioms=exclude,
    )
    assert ok
    # the retracted parent edge is back, byte-for-byte
    assert _triples(manager) == before
    quality = manager.world["http://purl.obolibrary.org/obo/BFO_0000019"]
    force = next(c for c in manager.working.classes() if c.name == "Force")
    assert quality in force.is_a


# ---------------------------------------------------------- verdict parity


def test_verdict_parity_legacy_vs_inmem(tmp_path, monkeypatch):
    """Same proposals, separate manager instances, both flag states: the
    (ok, sorted unsat locals) verdicts must be identical."""
    proposals = [
        # coherent
        _quality_force(),
        # disjoint-parent straddle in a single proposal
        Proposal(
            session_id="t", utterance="u",
            entities=[Entity(label="Force", iri_suggestion="working:Force",
                             kind="class", bfo_type="BFO_0000019",
                             bfo_label="quality", rationale="t")],
            relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                                o="bfo:BFO_0000016", rationale="t")],
        ),
        # restriction clash: a quality subclass constrained as an
        # occurrent-part of some process
        Proposal(
            session_id="t", utterance="u",
            entities=[Entity(label="Wobble", iri_suggestion="working:Wobble",
                             kind="class", bfo_type="BFO_0000019",
                             bfo_label="quality", rationale="t")],
            relations=[Relation(s="working:Wobble", p="rdfs:subClassOf",
                                o="_:x BFO_0000132 BFO_0000015",
                                rationale="t")],
        ),
    ]

    verdicts = {}
    for flag, name in ((False, "legacy.owl"), (True, "inmem.owl")):
        monkeypatch.setattr(config, "INMEM_DRY_RUN", flag)
        mgr = _make_manager(tmp_path, name)
        outs = []
        for prop in proposals:
            ok, unsat, _detail = mgr.check_coherence_dry_run(prop)
            outs.append((ok, sorted(_local(u) for u in unsat)))
        verdicts[flag] = outs

    assert verdicts[True] == verdicts[False]
    assert verdicts[True][0] == (True, [])   # coherent one accepted
    assert verdicts[True][1][0] is False     # straddle rejected
    assert "Force" in verdicts[True][1][1]


# -------------------------------------------------------- proposal guards


def test_guard_rejects_bfo_subclass_pair_disjointness(manager):
    """A raw disjointWith between a BFO subclass pair (the seed bug that once
    made every individual inconsistent) must be caught by the per-proposal
    guard -- incoherent verdict, live world untouched, no reasoner needed."""
    before = _triples(manager)
    prop = Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="bfo:BFO_0000016", p="owl:disjointWith",
                            o="bfo:BFO_0000034", rationale="t")],
    )
    ok, unsat, detail = manager.check_coherence_dry_run(prop)
    assert not ok and unsat == []
    assert "proposal guard" in detail and "disjoint" in detail.lower()
    assert _triples(manager) == before


def test_guard_rejects_subclass_of_property(manager):
    before = _triples(manager)
    prop = Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Foo", p="rdfs:subClassOf",
                            o="obo:BFO_0000054", rationale="t")],
    )
    ok, unsat, detail = manager.check_coherence_dry_run(prop)
    assert not ok and unsat == []
    assert "proposal guard" in detail and "property" in detail.lower()
    assert _triples(manager) == before


def test_guard_direct_on_handcrafted_delta(manager):
    delta = AppliedDelta()
    delta.raw_triples.append((
        "http://purl.obolibrary.org/obo/BFO_0000034",
        "http://www.w3.org/2002/07/owl#disjointWith",
        "http://purl.obolibrary.org/obo/BFO_0000016",
    ))
    with pytest.raises(ValueError):
        manager._proposal_guards(delta)


# ------------------------------------------------------------- commit path


def test_commit_persists_with_flag_on(manager, tmp_path):
    manager.commit_proposal(_quality_force())  # verify=True, scratch verify
    assert "Force" in manager.working_path.read_text()
    assert manager.iri_exists("working:Force")
    # a fresh manager loading the saved file sees the entity
    fresh = OntologyManager(bfo_path=BFO_PATH,
                            working_path=manager.working_path)
    assert fresh.iri_exists("working:Force")


def test_commit_failed_verify_restores_memory_and_file(manager, monkeypatch):
    manager.commit_proposal(_quality_force(), verify=False)
    before_triples = _triples(manager)
    before_bytes = manager.working_path.read_bytes()

    monkeypatch.setattr(manager, "_verify_saved_coherent",
                        lambda exclude_axioms=None: (False, "forced failure"))
    bad = Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Blob", iri_suggestion="working:Blob",
                         kind="class", bfo_type="BFO_0000019",
                         bfo_label="quality", rationale="t")],
    )
    with pytest.raises(CommitCoherenceError):
        manager.commit_proposal(bad, verify=True)

    assert manager.working_path.read_bytes() == before_bytes
    assert _triples(manager) == before_triples
    assert not manager.iri_exists("working:Blob")
    assert not manager.working_path.with_suffix(
        manager.working_path.suffix + ".precommit").exists()
