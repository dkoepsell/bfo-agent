"""Tests for the structural gate extensions (SPEC-bfo-agent-speed.md change 3).

Contract pinned here:
  - straddle rejections (class or individual) never spawn a JVM;
  - with GATE_REASONER_STRUCTURAL_SKIP on, a plain fully-anchored proposal
    accepts at the lint tier with reasoner_skipped, no dry-run;
  - restriction/class-expression proposals and unresolvable anchors still
    fall through to HermiT (never silently accepted);
  - with the flag off (the default), the reasoner runs exactly as before.

No test here runs a real reasoner: check_coherence_dry_run is monkeypatched
to either raise (must-not-run) or record calls (must-run).
"""
from pathlib import Path

import pytest

from app import coherence_gate as cg
from app import config
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


def _forbid_dry_run(manager, monkeypatch):
    def boom(proposal, exclude_axioms=None):
        raise AssertionError(
            "check_coherence_dry_run must not be called for this proposal"
        )

    monkeypatch.setattr(manager, "check_coherence_dry_run", boom)


def _spy_dry_run(manager, monkeypatch):
    calls: list = []

    def spy(proposal, exclude_axioms=None):
        calls.append(proposal)
        return True, [], ""

    monkeypatch.setattr(manager, "check_coherence_dry_run", spy)
    return calls


def _entity(label, bfo_type, bfo_label, kind):
    return Entity(
        label=label,
        iri_suggestion=f"working:{label}",
        bfo_type=bfo_type,
        bfo_label=bfo_label,
        kind=kind,
        rationale="test",
    )


def _quality_class(label="Force"):
    return _entity(label, "BFO_0000019", "quality", "class")


# ---------------------------------------------------------------------------
# (a) class straddle: rejected at LINT, JVM never starts.
# ---------------------------------------------------------------------------
def test_class_straddle_rejected_at_lint_without_jvm(manager, monkeypatch):
    _forbid_dry_run(manager, monkeypatch)
    p = Proposal(
        session_id="test",
        utterance="Force is a quality and a disposition",
        entities=[_quality_class()],
        relations=[
            Relation(s="working:Force", p="rdfs:subClassOf",
                     o="bfo:BFO_0000016", rationale="test")
        ],
    )
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.REJECT
    assert result.tier == GateTier.LINT
    assert result.clash_pair is not None
    assert set(result.clash_pair) == {"BFO_0000019", "BFO_0000016"}


# ---------------------------------------------------------------------------
# (b) individual typed under two disjoint categories: rejected at LINT.
# ---------------------------------------------------------------------------
def test_individual_type_straddle_rejected_at_lint(manager, monkeypatch):
    _forbid_dry_run(manager, monkeypatch)
    p = Proposal(
        session_id="test",
        utterance="Alice is a material entity and a process",
        entities=[_entity("Alice", "BFO_0000040", "material entity",
                          "individual")],
        relations=[
            Relation(s="working:Alice", p="rdf:type",
                     o="bfo:BFO_0000015", rationale="test")
        ],
    )
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.REJECT
    assert result.tier == GateTier.LINT
    assert result.clash_pair is not None
    assert set(result.clash_pair) == {"BFO_0000040", "BFO_0000015"}
    assert "individual" in result.reason


def test_type_edge_onto_existing_individual_straddles(manager, monkeypatch):
    # Commit Alice as a material entity (verify=False keeps the test JVM-free).
    p1 = Proposal(
        session_id="test",
        utterance="Alice is a material entity",
        entities=[_entity("Alice", "BFO_0000040", "material entity",
                          "individual")],
    )
    manager.commit_proposal(p1, verify=False)
    assert manager.committed_individual_anchors("working:Alice") >= {
        "BFO_0000040", "BFO_0000004", "BFO_0000002"
    }

    _forbid_dry_run(manager, monkeypatch)
    # Alice appears nowhere in proposal.entities: the rdf:type edge alone must
    # fold her committed types into the straddle test.
    p2 = Proposal(
        session_id="test",
        utterance="Alice is a process",
        relations=[
            Relation(s="working:Alice", p="rdf:type",
                     o="bfo:BFO_0000015", rationale="test")
        ],
    )
    result = cg.gate(p2, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.REJECT
    assert result.tier == GateTier.LINT


# ---------------------------------------------------------------------------
# (b2) relation-signature clash: rejected at LINT without a reasoner.
# ---------------------------------------------------------------------------
def test_relation_signature_range_clash_rejected_at_lint(manager, monkeypatch):
    _forbid_dry_run(manager, monkeypatch)
    # inheres in (BFO_0000197) declares range independent continuant
    # (BFO_0000004); a process object clashes with every anchor.
    p = Proposal(
        session_id="test",
        utterance="The pain inheres in the run",
        entities=[
            _entity("Pain", "BFO_0000019", "quality", "individual"),
            _entity("Run", "BFO_0000015", "process", "individual"),
        ],
        relations=[
            Relation(s="working:Pain", p="BFO_0000197",
                     o="working:Run", rationale="test")
        ],
    )
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.REJECT
    assert result.tier == GateTier.LINT
    assert "signature" in result.reason


def test_relation_signature_unresolvable_endpoint_does_not_reject(
    manager, monkeypatch
):
    # Unresolvable object: the signature check must NOT reject; the proposal
    # falls through to the reasoner instead (spy returns coherent).
    calls = _spy_dry_run(manager, monkeypatch)
    p = Proposal(
        session_id="test",
        utterance="The pain inheres in something unknown",
        entities=[_entity("Pain", "BFO_0000019", "quality", "individual")],
        relations=[
            Relation(s="working:Pain", p="BFO_0000197",
                     o="working:NoSuchThing", rationale="test")
        ],
    )
    monkeypatch.setattr(config, "GATE_REASONER_STRUCTURAL_SKIP", True)
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.ACCEPT
    assert result.reasoner_skipped is False
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# (c) flag on: plain fully-anchored proposal accepts at LINT, no JVM.
# ---------------------------------------------------------------------------
def test_structural_skip_accepts_plain_proposal_without_jvm(
    manager, monkeypatch
):
    monkeypatch.setattr(config, "GATE_REASONER_STRUCTURAL_SKIP", True)
    _forbid_dry_run(manager, monkeypatch)
    p = Proposal(
        session_id="test",
        utterance="Force is a quality",
        entities=[_quality_class()],
    )
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.ACCEPT
    assert result.tier == GateTier.LINT
    assert result.reasoner_skipped is True
    assert result.to_dict().get("reasoner_skipped") is True
    assert "reasoner skipped (structural)" in result.reason


# ---------------------------------------------------------------------------
# (d) restriction / class-expression object: reasoner IS invoked.
# ---------------------------------------------------------------------------
def test_restriction_proposal_still_reasons(manager, monkeypatch):
    monkeypatch.setattr(config, "GATE_REASONER_STRUCTURAL_SKIP", True)
    calls = _spy_dry_run(manager, monkeypatch)
    p = Proposal(
        session_id="test",
        utterance="Force is a quality that inheres in some material entity",
        entities=[_quality_class()],
        relations=[
            Relation(s="working:Force", p="rdfs:subClassOf",
                     o="_:x BFO_0000197 BFO_0000040", rationale="test")
        ],
    )
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.ACCEPT
    assert result.reasoner_skipped is False
    assert len(calls) == 1


def test_class_expression_object_still_reasons(manager, monkeypatch):
    monkeypatch.setattr(config, "GATE_REASONER_STRUCTURAL_SKIP", True)
    calls = _spy_dry_run(manager, monkeypatch)
    p = Proposal(
        session_id="test",
        utterance="Force is not a disposition",
        entities=[_quality_class()],
        relations=[
            Relation(s="working:Force", p="rdfs:subClassOf",
                     o="not BFO_0000016", rationale="test")
        ],
    )
    needed, why = cg.proposal_needs_reasoner(p, manager, True)
    assert needed
    result = cg.gate(p, manager, run_reasoner=True)
    assert len(calls) == 1
    assert result.reasoner_skipped is False


# ---------------------------------------------------------------------------
# (e) unresolvable anchor: falls through to the reasoner, never a silent skip.
# ---------------------------------------------------------------------------
def test_unresolvable_anchor_falls_through_to_reasoner(manager, monkeypatch):
    monkeypatch.setattr(config, "GATE_REASONER_STRUCTURAL_SKIP", True)
    calls = _spy_dry_run(manager, monkeypatch)
    # A subClassOf edge between two references that resolve to nothing: the
    # lint cannot anchor either side, so structure cannot decide.
    p = Proposal(
        session_id="test",
        utterance="Ghost is a kind of AlsoGhost",
        relations=[
            Relation(s="working:Ghost", p="rdfs:subClassOf",
                     o="working:AlsoGhost", rationale="test")
        ],
    )
    needed, why = cg.proposal_needs_reasoner(p, manager, False)
    assert needed
    assert "resolve" in why
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.ACCEPT
    assert result.reasoner_skipped is False
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# (f) flag off (the default): reasoner invoked exactly as before.
# ---------------------------------------------------------------------------
def test_flag_off_reasoner_runs_as_before(manager, monkeypatch):
    # Pin the flag rather than asserting the ambient default: a deployed
    # box's .env may (correctly) have the flip live while running the suite.
    monkeypatch.setattr(config, "GATE_REASONER_STRUCTURAL_SKIP", False)
    calls = _spy_dry_run(manager, monkeypatch)
    p = Proposal(
        session_id="test",
        utterance="Force is a quality",
        entities=[_quality_class()],
    )
    result = cg.gate(p, manager, run_reasoner=True)
    assert result.outcome == GateOutcome.ACCEPT
    assert result.tier == GateTier.NONE
    assert result.reasoner_skipped is False
    assert "reasoner_skipped" not in result.to_dict()
    assert len(calls) == 1
