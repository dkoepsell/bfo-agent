"""Reduced dry-run world (SPEC-bfo-agent-speed.md change 1).

With INMEM_DRY_RUN + REDUCED_REASONING_WORLD on, dry-run reasoning runs over
BFO + the working TBox mirror + only the individuals the proposal touches
(depth-1), so HermiT's per-claim cost stops scaling with the ABox. Under test:
verdict parity with the full scratch path, touched-individual extraction with
depth-1 pull-in, that the reduction actually excludes unrelated individuals,
that a proposal's own new-class restriction reaches the reduced world, the
documented soundness gap (a distant-individual inconsistency the reduced
world misses) and its closure by verify_full, and the incremental TBox-mirror
maintenance with checkpoint rebuild.
"""
from pathlib import Path

import pytest

from app import config
from app.ontology_manager import AppliedDelta, OntologyManager
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
    monkeypatch.setattr(config, "REDUCED_REASONING_WORLD", True)
    return _make_manager(tmp_path)


def _local(iri):
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def _ind(label, bfo_type="BFO_0000040", bfo_label="material entity"):
    return Entity(label=label, iri_suggestion=f"working:{label}",
                  kind="individual", bfo_type=bfo_type, bfo_label=bfo_label,
                  rationale="t")


def _quality_force(label="Force"):
    return Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label=label, iri_suggestion="working:Force",
                         kind="class",
                         bfo_type="BFO_0000019", bfo_label="quality",
                         rationale="t")],
    )


# ---------------------------------------------------------- verdict parity


def test_verdict_parity_reduced_vs_full_scratch(tmp_path, monkeypatch):
    """Same proposals as the inmem parity test, separate manager instances,
    reduced world on/off (INMEM on for both): identical verdicts."""
    monkeypatch.setattr(config, "INMEM_DRY_RUN", True)
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
    for flag, name in ((False, "full.owl"), (True, "reduced.owl")):
        monkeypatch.setattr(config, "REDUCED_REASONING_WORLD", flag)
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
    assert verdicts[True][2][0] is False     # restriction clash rejected


# ------------------------------------------------ touched extraction, depth-1


def test_touched_extraction_with_depth1_neighbours(manager):
    """A proposal relating A to B pulls in both, plus B's depth-1 individual
    neighbour C (via B's subject-triples referencing it)."""
    setup = Proposal(
        session_id="t", utterance="u",
        entities=[
            _ind("Alpha"),
            _ind("Beta", bfo_type="BFO_0000015", bfo_label="process"),
            _ind("Gamma", bfo_type="BFO_0000015", bfo_label="process"),
        ],
        relations=[
            # Beta occurrent-part-of Gamma: Gamma is Beta's neighbour
            Relation(s="working:Beta", p="obo:BFO_0000132",
                     o="working:Gamma", rationale="t"),
        ],
    )
    manager.commit_proposal(setup, verify=False)

    prop = Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Alpha", p="obo:BFO_0000056",
                            o="working:Beta", rationale="t")],
    )
    delta = AppliedDelta()
    assert manager.apply_proposal(prop, delta=delta) == []
    try:
        touched = manager._touched_individual_iris(prop, delta)
        assert sorted(_local(t) for t in touched) == ["Alpha", "Beta"]
        _w, onto = manager._reduced_world(prop, delta)
    finally:
        manager.rollback(delta)
    names = {i.name for i in onto.individuals()}
    # both endpoints, plus Beta's depth-1 neighbour Gamma
    assert {"Alpha", "Beta", "Gamma"} <= names


# --------------------------------------------------------- reduction is real


def test_reduced_world_excludes_untouched_individuals(manager):
    unrelated = [_ind(f"Filler{i}") for i in range(10)]
    setup = Proposal(session_id="t", utterance="u",
                     entities=unrelated + [_ind("Alpha")])
    manager.commit_proposal(setup, verify=False)

    prop = Proposal(
        session_id="t", utterance="u",
        entities=[_ind("Pace", bfo_type="BFO_0000015", bfo_label="process")],
        relations=[Relation(s="working:Alpha", p="obo:BFO_0000056",
                            o="working:Pace", rationale="t")],
    )
    delta = AppliedDelta()
    assert manager.apply_proposal(prop, delta=delta) == []
    try:
        _w, onto = manager._reduced_world(prop, delta)
    finally:
        manager.rollback(delta)
    names = {i.name for i in onto.individuals()}
    assert {"Alpha", "Pace"} <= names
    assert not any(n.startswith("Filler") for n in names)
    # ... while the full working ABox does hold them all
    assert sum(1 for _ in manager.working.individuals()) == 11


# -------------------------------------- delta class content reaches the world


def test_new_class_restriction_reaches_reduced_world(manager):
    """A NEW class + its restriction bnode cluster is not in the TBox mirror
    yet (mirror updates on commit); the reduced world must carry it anyway."""
    prop = Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Force", iri_suggestion="working:Force",
                         kind="class", bfo_type="BFO_0000019",
                         bfo_label="quality", rationale="t")],
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="_:x BFO_0000197 BFO_0000040", rationale="t")],
    )
    delta = AppliedDelta()
    assert manager.apply_proposal(prop, delta=delta) == []
    try:
        _w, onto = manager._reduced_world(prop, delta)
    finally:
        manager.rollback(delta)
    force = next((c for c in onto.classes() if c.name == "Force"), None)
    assert force is not None
    # the restriction survived the buffer round-trip as a real Restriction
    assert any(hasattr(p, "property") and hasattr(p, "value")
               for p in force.is_a)


# ------------------------------------------- the documented gap + its closure


def test_gap_distant_individual_missed_then_caught_by_verify_full(manager):
    """The contract this feature lives by: the reduced world can miss an
    inconsistency involving an UNTOUCHED individual; verify_full sees the
    full graph and catches it. Here an incoherent type pair is injected
    directly into the live world (bypassing the gate); a reduced dry-run of
    an unrelated proposal still says ok, verify_full reports not-ok."""
    from rdflib import RDF, URIRef

    manager.commit_proposal(
        Proposal(session_id="t", utterance="u",
                 entities=[_ind("Odd", bfo_type="BFO_0000019",
                                bfo_label="quality")]),
        verify=False,
    )
    odd_iri = next(i.iri for i in manager.working.individuals()
                   if i.name == "Odd")
    # Inject a second, disjoint type (quality vs disposition) straight into
    # the live graph: the ontology as a whole is now inconsistent.
    with manager.working:
        manager.world.as_rdflib_graph().add((
            URIRef(odd_iri), RDF.type,
            URIRef("http://purl.obolibrary.org/obo/BFO_0000016"),
        ))

    # A proposal that does not touch Odd: the reduced world excludes Odd,
    # so the dry-run verdict is ok -- the documented soundness gap.
    ok, unsat, _detail = manager.check_coherence_dry_run(
        Proposal(session_id="t", utterance="u", entities=[_ind("Bob")])
    )
    assert ok and unsat == []

    # ... and the full-graph certificate closes it.
    report = manager.verify_full()
    assert report["ok"] is False


# --------------------------------------------------------- mirror maintenance


def test_mirror_incremental_append_and_checkpoint_rebuild(manager, monkeypatch):
    """A committed class shows up in the next reduced world WITHOUT a full
    index rebuild (incremental append); verify_full rebuilds (drift guard)."""
    rebuilds = []
    orig = manager._rebuild_reduced_indexes

    def spy():
        rebuilds.append(1)
        orig()

    monkeypatch.setattr(manager, "_rebuild_reduced_indexes", spy)

    manager.commit_proposal(_quality_force(), verify=False)
    assert rebuilds == []  # incremental path, no rebuild

    prop = Proposal(session_id="t", utterance="u", entities=[_ind("Bob")])
    delta = AppliedDelta()
    assert manager.apply_proposal(prop, delta=delta) == []
    try:
        _w, onto = manager._reduced_world(prop, delta)
    finally:
        manager.rollback(delta)
    assert rebuilds == []
    assert any(c.name == "Force" for c in onto.classes())

    report = manager.verify_full()
    assert report["ok"] is True
    assert rebuilds == [1]  # checkpoint boundary rebuilt the indexes
