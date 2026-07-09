"""Acceptance tests for the in-loop coherence gate (app/coherence_gate.py).

The canonical case from SPEC Task 2: feed SubClassOf(Force, Quality) then
SubClassOf(Force, Disposition). The gate must ACCEPT the first and REJECT the
second, naming the quality/disposition clash.
"""
from pathlib import Path

import pytest

from app import coherence_gate as cg
from app.coherence_gate import GateOutcome, GateTier
from app.ontology_manager import OntologyManager
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.fixture()
def manager(tmp_path):
    pytest.importorskip("owlready2")
    working = tmp_path / "working.owl"
    return OntologyManager(bfo_path=BFO_PATH, working_path=working)


def _force_as(bfo_type: str, bfo_label: str, is_new: bool = True) -> Proposal:
    return Proposal(
        session_id="test",
        utterance=f"Force is a {bfo_label}",
        entities=[
            Entity(
                label="Force",
                iri_suggestion="working:Force",
                bfo_type=bfo_type,
                bfo_label=bfo_label,
                kind="class",
                rationale="test",
                is_new=is_new,
            )
        ],
    )


def test_first_axiom_accepted_then_straddle_rejected_lint(manager):
    # 1. Force as a Quality: clean.
    p1 = _force_as("BFO_0000019", "quality")
    r1 = cg.lint_check(p1, manager)
    assert r1 is None  # lint clean
    manager.commit_proposal(p1)

    # 2. Force also as a Disposition: straddle, caught at lint with a reason.
    p2 = Proposal(
        session_id="test",
        utterance="Force is a disposition",
        relations=[
            Relation(
                s="working:Force",
                p="rdfs:subClassOf",
                o="bfo:BFO_0000016",
                rationale="test",
            )
        ],
    )
    r2 = cg.lint_check(p2, manager)
    assert r2 is not None
    assert r2.outcome == GateOutcome.REJECT
    assert r2.tier == GateTier.LINT
    assert r2.clash_pair is not None
    assert set(r2.clash_pair) == {"BFO_0000019", "BFO_0000016"}
    assert "quality" in r2.reason.lower()
    assert "disposition" in r2.reason.lower()


def test_clean_proposal_passes_full_gate(manager):
    p = _force_as("BFO_0000019", "quality")
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.ACCEPT


def test_realized_in_restriction_is_not_a_straddle(manager):
    """Regression: a disposition subclassed to (realized_in some Process) must
    NOT be read as "also a process". The lint straddle detector used to fold a
    restriction object's filler in as a named parent, fabricating a
    disposition/process clash and rejecting every correct rule-14 disease
    proposal. The reasoner tier (which does run for restriction objects)
    confirms the shape is coherent, so the whole gate must ACCEPT.
    """
    p = Proposal(
        session_id="test",
        utterance="Cholera",
        entities=[
            Entity(label="Cholera", iri_suggestion="working:Cholera",
                   bfo_type="BFO_0000016", bfo_label="disposition",
                   kind="class", rationale="disease disposition"),
            Entity(label="Cholera Infection Process",
                   iri_suggestion="working:CholeraProcess",
                   bfo_type="BFO_0000015", bfo_label="process",
                   kind="class", rationale="pathological process"),
        ],
        relations=[
            Relation(s="working:Cholera", p="rdfs:subClassOf",
                     o="bfo:BFO_0000054 some working:CholeraProcess",
                     rationale="realized in"),
            Relation(s="working:Cholera", p="owl:disjointWith",
                     o="working:CholeraProcess",
                     rationale="disposition != process"),
        ],
    )
    # Lint must not fire a false straddle.
    assert cg.lint_check(p, manager) is None
    # And the full gate (construction + lint + reasoner) accepts.
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.ACCEPT, result.reason


def test_reasoner_tier_catches_unsatisfiable_class(manager):
    """The coherence-correct dry-run flags an unsatisfiable class even when the
    ontology stays consistent (no individual instantiates it). This is the
    consistency-vs-coherence distinction the old stdout check missed."""
    # Commit Force as a Quality.
    manager.commit_proposal(_force_as("BFO_0000019", "quality"))

    # Now add Force under Disposition. Disposition is a Realizable, and BFO
    # asserts Quality disjoint Realizable, so Force becomes unsatisfiable.
    p2 = Proposal(
        session_id="test",
        utterance="Force is a disposition",
        relations=[
            Relation(s="working:Force", p="rdfs:subClassOf",
                     o="bfo:BFO_0000016", rationale="test")
        ],
    )
    result = cg.reasoner_check(p2, manager)
    assert result is not None
    assert result.outcome == GateOutcome.REJECT
    assert result.tier == GateTier.REASONER
    assert any("Force" in iri for iri in result.unsat_classes)


def _straddle_proposal() -> Proposal:
    return Proposal(
        session_id="test",
        utterance="Force is a disposition",
        relations=[
            Relation(s="working:Force", p="rdfs:subClassOf",
                     o="bfo:BFO_0000016", rationale="test")
        ],
    )


def test_policy_repair_recovers(manager):
    manager.commit_proposal(_force_as("BFO_0000019", "quality"))
    run = cg.run_with_policy(
        _straddle_proposal(), manager, cg.GatePolicy.REPAIR,
        run_reasoner=False, max_attempts=2,
    )
    assert run.outcome == GateOutcome.ACCEPT
    # The realizable claim was re-homed to a separate class, not lost.
    names = [e.label for e in run.proposal.entities]
    assert any("Realizable" in n for n in names)
    # Events record the repair action.
    assert any(ev.get("policy_action", "").startswith("re-homed")
               or "dropped" in ev.get("policy_action", "")
               for ev in run.events)


def test_policy_reject_resample_recovers_with_clean_resample(manager):
    manager.commit_proposal(_force_as("BFO_0000019", "quality"))

    def resample_fn(prev, result, neighborhood):
        # Model the proposer self-correcting: propose nothing contentious.
        return Proposal(session_id="test", utterance="corrected")

    run = cg.run_with_policy(
        _straddle_proposal(), manager, cg.GatePolicy.REJECT_RESAMPLE,
        resample_fn=resample_fn, run_reasoner=False, max_attempts=2,
    )
    assert run.outcome == GateOutcome.ACCEPT
    assert run.attempts >= 1


def test_policy_reject_resample_gives_up_without_resampler(manager):
    manager.commit_proposal(_force_as("BFO_0000019", "quality"))
    run = cg.run_with_policy(
        _straddle_proposal(), manager, cg.GatePolicy.REJECT_RESAMPLE,
        resample_fn=None, run_reasoner=False, max_attempts=2,
    )
    assert run.outcome == GateOutcome.REJECT
    assert run.result.tier == GateTier.LINT


def test_no_em_dashes_in_source():
    src = (REPO / "app" / "coherence_gate.py").read_text(encoding="utf-8")
    assert "—" not in src
