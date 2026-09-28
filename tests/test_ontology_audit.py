"""QS-G1 / QS-G3: audit metrics and finalization gates on hand-built
fixtures with known counts (tests/quality_fixture.py)."""
from __future__ import annotations

import pytest

from app import quality_gates as q
from tests.quality_fixture import clean_graph, defective_graph, write


@pytest.fixture
def clean(tmp_path):
    return q.run_audit(write(clean_graph(), tmp_path / "clean.owl"))


@pytest.fixture
def defective(tmp_path):
    return q.run_audit(write(defective_graph(), tmp_path / "defective.owl"))


def test_qs_g1_clean_fixture_metrics(clean):
    assert clean["counts"]["local_classes"] == 3
    assert clean["visibility_ratio"] == 1.0
    assert clean["malformed_iris"] == 0
    assert clean["punned_triples"] == 0
    assert clean["mangled_standard_predicates"] == 0
    assert clean["empty_restrictions"] == 0
    assert clean["domain_filler_share"] == 1.0
    assert clean["definition_coverage"] == 1.0
    assert clean["local_disjointness_axioms"] == 1
    assert clean["meta_labels"] == 0
    assert clean["redundant_parent_assertions"] == 0


def test_qs_g1_defective_fixture_metrics(defective):
    assert defective["counts"]["local_classes"] == 5
    # the minted 'working#ro:RO_0000052' class and the mangled predicate
    assert defective["malformed_breakdown"] == {"curie_in_fragment": 2}
    assert defective["mangled_standard_predicates"] == 1
    assert defective["punned_triples"] == 1
    assert defective["punned_breakdown"] == {"C->C": 1}
    assert defective["visibility_ratio"] < 0.99
    assert defective["meta_labels"] == 1
    assert defective["redundant_parent_assertions"] == 1
    assert defective["definition_coverage"] == 0.6


def test_qs_g1_absent_in_source_counts_only_in_faithful(defective):
    assert defective["definitions_absent_in_source"] == 1
    assert defective["definition_coverage_faithful"] == 0.8


def test_qs_g3_clean_passes_construction_gates(clean):
    assert q.construction_failures(clean) == []


def test_qs_g3_each_defect_blocks(defective):
    failed = {f.split()[0] for f in q.construction_failures(defective)}
    assert failed == {"malformed_iris", "mangled_standard_predicates",
                      "punned_triples", "visibility_ratio"}


def test_qs_g3_definitions_gate_curated_only(defective):
    ok, _ = q.definition_check(defective, "curated")
    assert not ok  # 0.6 < 0.95
    ok, detail = q.definition_check(defective, "faithful")
    assert ok and "report only" in detail


def test_qs_e3_certificate_fields(clean):
    cert = q.certificate(clean, fidelity="faithful",
                         verify={"ok": True, "unsat_classes": []},
                         profiles=["legal-hohfeld"])
    assert cert == {
        "consistent": True, "unsatisfiable_classes": [],
        "visibility_ratio": 1.0, "local_disjointness_axioms": 1,
        "profiles_active": ["legal-hohfeld"], "fidelity_mode": "faithful",
        "construction_failures": [],
    }


def test_qs_g2_summary_line_names_blockers(defective, clean):
    assert "finalization blocked" in q.summary_line(defective)
    assert "construction gates pass" in q.summary_line(clean)


def test_qs_g3_finalize_adds_unwaivable_quality_gates(tmp_path):
    from app.library import finalize

    working = write(defective_graph(), tmp_path / "working.owl")
    report = finalize.GateReport(ontology="fx")

    def add(gate_id, passed, detail):
        report.gates.append(finalize.GateResult(
            id=gate_id, passed=passed, detail=detail,
            waivable=finalize.GATES[gate_id]))

    class Reg:  # no fidelity(): falls back to the manifest
        pass

    finalize._check_quality(Reg(), "fx", {"fidelity": "curated",
                                          "profiles": ["legal-hohfeld"]},
                            working, report, add, run_reasoner=False)
    by_id = {g.id: g for g in report.gates}
    assert by_id["quality.construction"].blocking
    assert by_id["quality.definitions"].blocking  # curated, 0.6 < 0.95
    assert report.certificate["fidelity_mode"] == "curated"
    assert report.certificate["profiles_active"] == ["legal-hohfeld"]
    assert report.certificate["consistent"] is None  # reasoner not run


def test_qs_g2_job_completion_attaches_audit(tmp_path, monkeypatch):
    from app import job_runner

    lib = tmp_path / "Lib"
    (lib / "jobs").mkdir(parents=True)
    write(clean_graph(), lib / "working.owl")
    store = {"job_x": {"job_id": "job_x"}}
    monkeypatch.setattr(job_runner.jobs_store, "_job_path",
                        lambda jid: lib / "jobs" / f"{jid}.json")
    monkeypatch.setattr(job_runner.jobs_store, "load_job",
                        lambda jid: dict(store[jid]))
    monkeypatch.setattr(job_runner.jobs_store, "save_job",
                        lambda job: store.__setitem__(job["job_id"], job))
    flushed = []
    line = job_runner._quality_audit_on_complete("job_x", flushed.append)
    assert flushed == ["job_x"]
    assert "construction gates pass" in line
    assert store["job_x"]["quality_audit"]["visibility_ratio"] == 1.0
