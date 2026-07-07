"""FOL gate (fol-gate-spec.md): Prover9/Mace4 audit, evidence-only.

A-1: toy consistent module (Mode A) -> Mace4 model, no Prover9 proof.
A-2: A <= B, A <= not B, A(a) (Mode A) -> proof of $F with the three source
     axioms as culprits; HermiT agrees (cross-check).
A-3: HermiT-consistent module violating a first-order BFO axiom OWL cannot
     express -> Mode B proof. The reason the gate exists.
A-4: whitelisted-out construct -> SKIPPED entry, verdict downgraded, no crash.
A-5: binaries absent -> gate soft-disables cleanly.
A-7: byte-identical ontology before and after every audit.
"""
import shutil
from pathlib import Path

import pytest

from app import fol_gate

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"

needs_ladr = pytest.mark.skipif(
    not fol_gate.binaries_available(), reason="prover9/mace4 not installed"
)


def _fresh_world():
    owl = pytest.importorskip("owlready2")
    return owl, owl.World()


def _save(onto, tmp_path, name):
    path = tmp_path / name
    onto.save(file=str(path), format="rdfxml")
    return path


@pytest.fixture
def toy_module(tmp_path):
    """B, A <= B, one A-instance. Coherent."""
    owl, world = _fresh_world()
    onto = world.get_ontology("http://example.org/toy")
    with onto:
        class B(owl.Thing):
            pass

        class A(B):
            pass

        A("a")
    return _save(onto, tmp_path, "toy.owl")


@pytest.fixture
def contradictory_module(tmp_path):
    """A <= B, A <= not B, A(a): inconsistent in OWL and FOL alike."""
    owl, world = _fresh_world()
    onto = world.get_ontology("http://example.org/contra")
    with onto:
        class B(owl.Thing):
            pass

        class A(B):
            pass

        A.is_a.append(owl.Not(B))
        A("a")
    return world, _save(onto, tmp_path, "contra.owl")


# ---------------------------------------------------------------- A-1, A-7
@needs_ladr
def test_a1_consistent_module_gets_model_and_no_proof(toy_module):
    before = toy_module.read_bytes()
    rec = fol_gate.audit(toy_module, mode="A", write_record=False)
    assert rec["mace4"]["outcome"] == "model_found"
    assert rec["prover9_refutation"]["outcome"] in (
        "saturated_no_proof", "no_proof_within_budget"
    )
    assert rec["verdict"] == "consistent"
    assert rec["skipped_axioms"] == []
    # A-7: evidence, never modification -- byte-identical file.
    assert rec["file_unchanged"] is True
    assert toy_module.read_bytes() == before


# --------------------------------------------------------------------- A-2
@needs_ladr
def test_a2_contradiction_proved_with_culprits(contradictory_module):
    world, path = contradictory_module
    rec = fol_gate.audit(path, mode="A", write_record=False)
    assert rec["verdict"] == "inconsistent"
    refute = rec["prover9_refutation"]
    assert refute["outcome"] == "proved"
    assert refute["proof"]
    culprits = "\n".join(refute["culprit_axioms"])
    assert "c_a(i_a)" in culprits          # the instance assertion
    assert "-c_b" in culprits              # the complement axiom
    assert len(refute["culprit_axioms"]) == 3

    # Cross-check: HermiT agrees this module is inconsistent.
    owl = pytest.importorskip("owlready2")
    with pytest.raises(Exception):
        owl.sync_reasoner(world)


# --------------------------------------------------------------------- A-3
@needs_ladr
def test_a3_hermit_consistent_but_mode_b_inconsistent(tmp_path):
    """The classic straddle CLASS with no instance: HermiT reports a
    *consistent* ontology (one unsatisfiable class at most), but the BFO 2020
    FOL axioms make it a definite contradiction -- ``$F`` is provable because
    every universal must be instantiated at some time, and no instance of the
    straddling class is possible. The proof object names the exact axioms of
    the module and of ISO/IEC 21838-2 that jointly clash. This delta (OWL:
    satisfiability defect; FOL: inconsistency with an auditable proof) is the
    reason the gate exists."""
    owl = pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    from app.ontology_manager import OntologyManager
    from app.schema import Entity, Proposal, Relation

    mgr = OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "w.owl")
    mgr.commit_proposal(Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Force", iri_suggestion="working:Force",
                         kind="class", bfo_type="BFO_0000019",
                         bfo_label="quality", rationale="t")]))
    mgr.commit_proposal(Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="bfo:BFO_0000016", rationale="t")]),
        verify=False)
    path = tmp_path / "w.owl"

    # HermiT: does NOT raise -- the ontology is OWL-consistent; the straddle
    # shows up only as an unsatisfiable class.
    import io
    from contextlib import redirect_stderr, redirect_stdout
    mgr._load()
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        owl.sync_reasoner(mgr.world, infer_property_values=False)
    unsat = [c.iri for c in mgr.world.inconsistent_classes()
             if c is not owl.Nothing]
    assert any("Force" in iri for iri in unsat)

    # Mode B: definite first-order inconsistency with the culprit trail
    # running from the module's axioms to the ISO axioms they violate.
    rec = fol_gate.audit(path, mode="B", write_record=False, timeout=120)
    assert rec["verdict"] == "inconsistent"
    culprits = "\n".join(rec["prover9_refutation"]["culprit_axioms"])
    assert "universal(w_force)" in culprits
    assert "every-universal-is-instantiated" in culprits
    assert "w_force" in culprits and "disposition" in culprits


# --------------------------------------------------------------------- A-4
@needs_ladr
def test_a4_unsupported_construct_skipped_not_silent(tmp_path):
    owl, world = _fresh_world()
    onto = world.get_ontology("http://example.org/a4")
    with onto:
        class B(owl.Thing):
            pass

        class r(owl.ObjectProperty):
            pass

        class A(owl.Thing):
            pass

        A.is_a.append(r.min(2, B))  # cardinality: outside the whitelist
        A("a")
    path = _save(onto, tmp_path, "a4.owl")

    rec = fol_gate.audit(path, mode="A", write_record=False)
    assert len(rec["skipped_axioms"]) == 1
    assert "restriction" in rec["skipped_axioms"][0]["reason"]
    # TR-3: a partial translation can never claim plain consistency.
    assert rec["verdict"] in ("consistent_partial_translation", "inconclusive")


# --------------------------------------------------------------------- A-5
def test_a5_soft_disable_when_binaries_absent(toy_module, monkeypatch):
    monkeypatch.setattr(fol_gate.config, "FOL_PROVER9_BIN", "prover9-nope")
    monkeypatch.setattr(fol_gate.config, "FOL_MACE4_BIN", "mace4-nope")
    monkeypatch.setattr(fol_gate, "_availability_logged", False)
    assert fol_gate.binaries_available() is False

    rec = fol_gate.audit(toy_module, mode="A", write_record=False)
    assert rec["status"] == "disabled"
    assert "verdict" not in rec

    # CLI exits cleanly too (A-5), without touching the file.
    before = toy_module.read_bytes()
    assert fol_gate.main([str(toy_module)]) == 3
    assert toy_module.read_bytes() == before


# ------------------------------------------------------------------ probes
@needs_ladr
def test_p2_unsat_class_probe_finds_straddle(tmp_path):
    """Class-level incoherence (no instance asserted): invisible to the $F
    refutation in Mode A, caught by the per-class probe."""
    pytest.importorskip("owlready2")
    if not BFO_PATH.exists():
        pytest.skip("bfo.owl not present")
    from app.ontology_manager import OntologyManager
    from app.schema import Entity, Proposal, Relation

    mgr = OntologyManager(bfo_path=BFO_PATH, working_path=tmp_path / "w.owl")
    mgr.commit_proposal(Proposal(
        session_id="t", utterance="u",
        entities=[Entity(label="Force", iri_suggestion="working:Force",
                         kind="class", bfo_type="BFO_0000019",
                         bfo_label="quality", rationale="t")]))
    mgr.commit_proposal(Proposal(
        session_id="t", utterance="u",
        relations=[Relation(s="working:Force", p="rdfs:subClassOf",
                            o="bfo:BFO_0000016", rationale="t")]),
        verify=False)

    rec = fol_gate.audit(tmp_path / "w.owl", mode="A", probes=True,
                         write_record=False)
    proved = [p for p in rec["probes"] if p.get("outcome") == "proved"]
    assert any(p.get("class") == "c_force" for p in proved)


# ------------------------------------------------------------------ record
@needs_ladr
def test_r1_record_written_under_sessions(toy_module):
    rec = fol_gate.audit(toy_module, mode="A", write_record=True)
    path = Path(rec["record_path"])
    assert path.exists() and path.parent.name == "sessions"
    latest = fol_gate.latest_record(toy_module)
    assert latest and latest["sha256"] == rec["sha256"]
    assert "FOL audit" in fol_gate.summarize(rec)
