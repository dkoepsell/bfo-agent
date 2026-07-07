"""Faithful-extraction mode (fidelity-mode-spec.md): annotate, don't repair.

FA-1: a straddle the text asserts is committed as-asserted, annotated, and
      ledgered — never repaired or resampled.
FA-2: after a flag, later claims are still gated on their own merits via the
      coherent view (working minus ledgered clash axioms).
FA-3: ANNOTATE never invokes repair or content resampling; construction-tier
      violations still resample (the proposer's rendering, not the text).
FA-4: retracting the ledgered clash axioms from the persisted file yields a
      coherent ontology (ledger completeness).
FA-5: curated mode is unchanged (regression lives in test_commit_guard.py /
      test_coherence_gate.py; here we pin the mode-resolution defaults).
FA-6: mode is stamped on ledger entries.
"""
from pathlib import Path

import pytest

from app import coherence_gate as gate_mod
from app import incoherence_ledger as ledger_mod
from app.coherence_gate import GateOutcome, GatePolicy
from app.ontology_manager import OntologyManager
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"


@pytest.fixture
def manager(tmp_path):
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    return OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "working.owl")


def _quality(name):
    return Proposal(
        session_id="t", utterance=f"{name} is a quality",
        entities=[Entity(label=name, iri_suggestion=f"working:{name}",
                         kind="class",
                         bfo_type="BFO_0000019", bfo_label="quality",
                         rationale="t")],
    )


def _straddle(name):
    """The text also asserts {name} is a disposition — disjoint with quality."""
    return Proposal(
        session_id="t", utterance=f"{name} is a disposition",
        relations=[Relation(s=f"working:{name}", p="rdfs:subClassOf",
                            o="bfo:BFO_0000016", rationale="t")],
    )


def _flag_straddle(manager, name="Force"):
    """Commit `name` as a quality, then run the text's disposition claim
    through the ANNOTATE policy and commit it as-asserted with a ledger
    entry — the exact faithful-mode flow the orchestrator drives."""
    manager.commit_proposal(_quality(name))
    straddle = _straddle(name)

    run = gate_mod.run_with_policy(
        straddle, manager, policy=GatePolicy.ANNOTATE, resample_fn=None,
    )
    assert run.outcome == GateOutcome.FLAG
    manager.commit_proposal(run.proposal, verify=False, faithful=True)
    exclude = gate_mod.clash_exclusion_triples(run.proposal, run.result)
    subjects = [run.result.subject] if run.result.subject else []
    entry_id = ledger_mod.record_flag(
        manager, run.proposal, run.result.to_dict(), exclude, subjects,
        provenance={"session_id": "t", "source_text": straddle.utterance},
    )
    return run, entry_id


# ---------------------------------------------------------------- FA-1
def test_fa1_straddle_committed_as_asserted_and_ledgered(manager):
    run, entry_id = _flag_straddle(manager)

    # The proposal was not modified: the clashing edge survived the gate.
    assert run.proposal.relations and \
        run.proposal.relations[0].o == "bfo:BFO_0000016"

    # Both parents are in the persisted file — true to the text's error.
    text = manager.working_path.read_text()
    assert "BFO_0000019" in text and "BFO_0000016" in text

    # FM-8: the flagged class carries the evidence annotation.
    assert "incoherenceEvidence" in text and entry_id in text

    # FM-7: one ledger entry, with the diagnosis, provenance, and the
    # clash axioms to exclude from coherent-view dry-runs.
    entries = ledger_mod.read_all(manager.working_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["id"] == entry_id
    assert e["subjects"] == ["Force"]
    assert e["exclude_axioms"] == [
        {"s": "working:Force", "p": "rdfs:subClassOf", "o": "bfo:BFO_0000016"}
    ]
    assert e["provenance"]["source_text"] == "Force is a disposition"


# ---------------------------------------------------------------- FA-2
def test_fa2_later_claims_gated_on_their_own_merits(manager):
    _flag_straddle(manager)
    exclusions = ledger_mod.exclusion_triples(manager.working_path)

    # An unrelated coherent claim passes the reasoner tier despite the
    # flagged straddle sitting in the persisted file.
    ok_run = gate_mod.run_with_policy(
        _quality("Mass"), manager, policy=GatePolicy.ANNOTATE,
        exclude_axioms=exclusions,
    )
    assert ok_run.outcome == GateOutcome.ACCEPT
    assert not ok_run.result.degraded

    # An unrelated incoherent claim is flagged with its OWN diagnosis.
    manager.commit_proposal(_quality("Heat"), exclude_axioms=exclusions,
                            faithful=True)
    bad_run = gate_mod.run_with_policy(
        _straddle("Heat"), manager, policy=GatePolicy.ANNOTATE,
        exclude_axioms=exclusions,
    )
    assert bad_run.outcome == GateOutcome.FLAG
    assert bad_run.result.subject == "Heat"


# ---------------------------------------------------------------- FA-3
def test_fa3_annotate_never_repairs_or_content_resamples(manager, monkeypatch):
    manager.commit_proposal(_quality("Force"))

    def _no_repair(*a, **k):
        raise AssertionError("repair_proposal must not run under ANNOTATE")

    monkeypatch.setattr(gate_mod, "repair_proposal", _no_repair)
    calls = []

    def resample_fn(prev, result, neighborhood):
        calls.append(result.tier)
        return None

    run = gate_mod.run_with_policy(
        _straddle("Force"), manager, policy=GatePolicy.ANNOTATE,
        resample_fn=resample_fn,
    )
    assert run.outcome == GateOutcome.FLAG
    assert calls == []  # lint clash: no content resample under ANNOTATE


def test_fa3_construction_violations_still_resample():
    # PC-1 privation name: the proposer's rendering error, never the text's
    # claim — ANNOTATE keeps the construction-tier resample (FM-3).
    bad = Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="AbsenceOfForce",
                         iri_suggestion="working:AbsenceOfForce",
                         kind="class", bfo_type="BFO_0000019",
                         bfo_label="quality", rationale="t", is_new=True)],
    )
    calls = []

    def resample_fn(prev, result, neighborhood):
        calls.append(result.tier)
        return None  # could not re-render; gate should then REJECT

    run = gate_mod.run_with_policy(
        bad, manager=None, policy=GatePolicy.ANNOTATE,
        resample_fn=resample_fn, run_reasoner=False,
    )
    assert calls == [gate_mod.GateTier.CONSTRUCTION]
    assert run.outcome == GateOutcome.REJECT


# ---------------------------------------------------------------- FA-4
def test_fa4_removing_ledgered_axioms_restores_coherence(manager):
    _flag_straddle(manager)
    exclusions = ledger_mod.exclusion_triples(manager.working_path)

    # As persisted (true to the text) the ontology is incoherent...
    ok_full, _ = manager._verify_saved_coherent()
    assert not ok_full
    # ...and retracting exactly the ledgered clash axioms (in memory only —
    # the file is never modified) yields a coherent ontology.
    before = manager.working_path.read_bytes()
    ok_view, detail = manager._verify_saved_coherent(exclude_axioms=exclusions)
    assert ok_view, detail
    assert manager.working_path.read_bytes() == before


# ---------------------------------------------------------------- FA-5
def test_fa5_mode_resolution_defaults_to_curated():
    from app.registry import OntologyRegistry

    reg = object.__new__(OntologyRegistry)
    reg._manifests = {
        "plain": {"name": "plain"},
        "faith": {"name": "faith", "fidelity": "faithful"},
        "typo": {"name": "typo", "fidelity": "strict"},
    }
    reg._active_name = "plain"
    assert reg.fidelity("plain") == "curated"   # absent field
    assert reg.fidelity("faith") == "faithful"
    assert reg.fidelity("typo") == "curated"    # invalid value
    assert reg.fidelity() == "curated"          # active fallback
    assert reg.fidelity("missing") == "curated"


def test_fa5_curated_policy_unaffected(monkeypatch):
    from app import orchestrator

    monkeypatch.setattr(orchestrator.config, "GATE_POLICY", "repair")
    assert orchestrator._gate_policy(False) == GatePolicy.REPAIR
    assert orchestrator._gate_policy(True) == GatePolicy.ANNOTATE


# ---------------------------------------------------------------- FA-6
def test_fa6_mode_stamped_on_ledger_entries(manager):
    _, entry_id = _flag_straddle(manager)
    (entry,) = ledger_mod.read_all(manager.working_path)
    assert entry["mode"] == "faithful"
    assert entry["tier"] == "lint"
    assert entry["ts"] and entry["id"] == entry_id
