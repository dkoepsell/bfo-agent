"""Schema 2.0: status vocabulary, gates, dispersion, provenance, manifest checks.

Where a number appears below it is the reference artifact's, because the point
of this work is that those specific numbers stop meaning the wrong thing.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
import rdflib

from app import kernel_audit
from app.chain_dispersion import (
    Dispersion,
    compute_dispersion,
    dispersion_gates,
    render_jaccard,
)
from app.report import metrics as metrics_mod
from app.report.bundle import (
    FAILED,
    NOT_APPLICABLE,
    OK,
    SKIPPED,
    STATUSES,
    UNRELIABLE,
    BundleManifestError,
    Gate,
    Provenance,
    Report,
    Section,
    build_provenance,
    report_markdown,
    write_bundle,
)
from app.report.migrate_1_to_2 import migrate

FIXTURE = Path(__file__).parent / "fixtures" / "cfr_disability" / "working.owl"
BFO = Path(__file__).parent.parent / "ontology" / "bfo.owl"

HEADER = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Ontology rdf:about="http://example.org/t"/>
"""


def owl_file(tmp_path, body: str, name="t.owl") -> str:
    p = tmp_path / name
    p.write_text(HEADER + body + "</rdf:RDF>\n")
    return str(p)


# --------------------------------------------------------------------------
# R4: the residual detector is structural, not lexical
# --------------------------------------------------------------------------

def test_the_label_heuristic_is_gone():
    """'nec' as a substring matched Cell Necrosis, Connective Tissue and
    Felony-Connected. All five reference findings were that one bug."""
    source = Path(kernel_audit.__file__).read_text()
    assert "RESIDUAL_LABEL_MARKERS" not in source.split("RESIDUAL_LABEL_MARKERS =")[-1] \
        or "residual label:" not in source


def test_a_lexical_lookalike_is_not_a_finding(tmp_path):
    """Cell Necrosis and Connective Tissue are anatomy, not residual categories."""
    path = owl_file(tmp_path, """
      <owl:ObjectProperty rdf:about="http://example.org/t#partOf"/>
      <owl:Class rdf:about="http://example.org/t#Tissue"/>
      <owl:Class rdf:about="http://example.org/t#ConnectiveTissue">
        <rdfs:label>Connective Tissue</rdfs:label>
        <rdfs:subClassOf rdf:resource="http://example.org/t#Tissue"/>
        <owl:equivalentClass>
          <owl:Restriction>
            <owl:onProperty rdf:resource="http://example.org/t#partOf"/>
            <owl:someValuesFrom rdf:resource="http://example.org/t#Tissue"/>
          </owl:Restriction>
        </owl:equivalentClass>
      </owl:Class>
      <owl:Class rdf:about="http://example.org/t#CellNecrosis">
        <rdfs:label>Cell Necrosis</rdfs:label>
        <rdfs:subClassOf rdf:resource="http://example.org/t#Tissue"/>
        <owl:equivalentClass>
          <owl:Restriction>
            <owl:onProperty rdf:resource="http://example.org/t#partOf"/>
            <owl:someValuesFrom rdf:resource="http://example.org/t#Tissue"/>
          </owl:Restriction>
        </owl:equivalentClass>
      </owl:Class>
    """)
    led = kernel_audit.audit(path, None, sample=25)
    assert len(led["findings_sample"].get("K-B3", [])) == 0


def test_a_true_residual_class_is_found(tmp_path):
    """Defined only by complement: membership fixed negatively."""
    path = owl_file(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Listed"/>
      <owl:Class rdf:about="http://example.org/t#Unlisted">
        <rdfs:label>Other Unspecified Impairment</rdfs:label>
        <owl:equivalentClass>
          <owl:Class>
            <owl:complementOf rdf:resource="http://example.org/t#Listed"/>
          </owl:Class>
        </owl:equivalentClass>
      </owl:Class>
    """)
    led = kernel_audit.audit(path, None, sample=25)
    hits = led["findings_sample"].get("K-B3", [])
    assert len(hits) == 1
    assert "complement" in hits[0]["detail"]
    assert "label" not in hits[0]["detail"]


def test_the_detail_names_structure_not_the_label(tmp_path):
    path = owl_file(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Listed"/>
      <owl:Class rdf:about="http://example.org/t#Unlisted">
        <rdfs:label>Whatever</rdfs:label>
        <owl:equivalentClass>
          <owl:Class><owl:complementOf rdf:resource="http://example.org/t#Listed"/></owl:Class>
        </owl:equivalentClass>
      </owl:Class>
    """)
    hits = kernel_audit.audit(path, None)["findings_sample"]["K-B3"]
    assert "Whatever" not in hits[0]["detail"]


def test_an_artifact_with_no_definitions_reports_silent_not_zero(tmp_path):
    path = owl_file(tmp_path, """
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <owl:Class rdf:about="http://example.org/t#Beta">
        <rdfs:label>Other specified thing</rdfs:label>
        <rdfs:subClassOf rdf:resource="http://example.org/t#Alpha"/>
      </owl:Class>
    """)
    led = kernel_audit.audit(path, None)
    assert led["findings_sample"].get("K-B3") is None
    silent = {s["kernel_code"]: s for s in led["silent_by_principle"]}
    assert silent["K-B3"]["requires"] == "definitions"
    assert "not a measurement" in silent["K-B3"]["detail"]


@pytest.mark.skipif(not FIXTURE.exists(), reason="reference fixture not present")
def test_the_fixture_yields_zero_or_one_never_five():
    led = kernel_audit.audit(str(FIXTURE), str(BFO) if BFO.exists() else None)
    assert len(led["findings_sample"].get("K-B3", [])) in (0, 1)
    if not led["findings_sample"].get("K-B3"):
        codes = {s["kernel_code"] for s in led["silent_by_principle"]}
        assert "K-B3" in codes


@pytest.mark.skipif(not FIXTURE.exists(), reason="reference fixture not present")
def test_the_valuable_dependence_findings_are_preserved():
    """The five K-C3 hits are the substantively useful result and must survive."""
    led = kernel_audit.audit(str(FIXTURE), str(BFO) if BFO.exists() else None)
    assert led["finding_counts"].get("K-C3") == 5


@pytest.mark.skipif(not FIXTURE.exists(), reason="reference fixture not present")
def test_the_total_findings_figure_names_its_scope():
    led = kernel_audit.audit(str(FIXTURE), str(BFO) if BFO.exists() else None)
    assert "total_findings_abc" in led
    assert "total_findings" not in led


# --------------------------------------------------------------------------
# R3: dispersion
# --------------------------------------------------------------------------

def smeared():
    """The reference shape: everything bound to almost everything."""
    classes = [f"C{i}" for i in range(1000)]
    return {
        "authority": classes[:600], "criteria": classes[:800],
        "assessor": classes[:100], "facts": classes[:500],
        "act": classes[:500], "effect": classes[:800],
        "remedy": classes[:790],
    }


def clean():
    return {
        "authority": ["A1", "A2", "A3"], "criteria": ["C1", "C2"],
        "assessor": ["S1"], "facts": ["F1"], "act": ["T1"],
        "effect": ["E1"], "remedy": ["R1"],
    }


def test_dispersion_is_computed_over_full_sets_not_samples():
    d = compute_dispersion(smeared(), named_classes=1000)
    assert d.bindings_total == 4090
    assert d.bindings_per_class_mean > 3.0


def test_all_four_gates_fail_on_a_smear():
    gates = dispersion_gates(compute_dispersion(smeared(), named_classes=1000))
    assert len(gates) == 4
    assert [g["passed"] for g in gates] == [False, False, False, False]
    assert {g["id"] for g in gates} == {
        "binding.dispersion", "binding.exclusivity",
        "binding.capture", "binding.jaccard"}


def test_all_four_gates_pass_on_a_clean_binding():
    gates = dispersion_gates(compute_dispersion(clean(), named_classes=100))
    assert all(g["passed"] for g in gates), [g for g in gates if not g["passed"]]


def test_exclusivity_counts_classes_bound_to_exactly_one_link():
    d = compute_dispersion(clean(), named_classes=100)
    assert d.exclusivity_rate == 1.0


def test_identical_link_sets_are_caught_by_jaccard():
    per_locus = {"criteria": ["A", "B", "C"], "effect": ["A", "B", "C"],
                 "authority": ["D"]}
    d = compute_dispersion(per_locus, named_classes=10)
    assert d.jaccard["criteria"]["effect"] == 1.0
    gate = next(g for g in dispersion_gates(d) if g["id"] == "binding.jaccard")
    assert gate["passed"] is False


def test_capture_flags_a_link_holding_most_of_the_ontology():
    d = compute_dispersion({"remedy": [f"C{i}" for i in range(790)]},
                           named_classes=1000)
    assert round(d.capture_pct["remedy"]) == 79
    gate = next(g for g in dispersion_gates(d) if g["id"] == "binding.capture")
    assert gate["passed"] is False


def test_top_multiplicity_makes_the_smear_visible():
    d = compute_dispersion(smeared(), named_classes=1000)
    assert d.top_multiplicity
    assert d.top_multiplicity[0]["links"] >= 6


def test_the_matrix_marks_tripped_cells():
    d = compute_dispersion({"criteria": ["A", "B"], "effect": ["A", "B"]},
                           named_classes=10)
    rendered = "\n".join(render_jaccard(d))
    assert "**1.00**" in rendered
    assert "holding substantially the same classes" in rendered


def test_unbound_classes_are_carried():
    d = compute_dispersion(clean(), named_classes=100, unbound_classes=13)
    assert d.unbound_classes == 13


# --------------------------------------------------------------------------
# R7: the status vocabulary
# --------------------------------------------------------------------------

def test_the_vocabulary_has_five_members():
    assert STATUSES == (OK, UNRELIABLE, FAILED, SKIPPED, NOT_APPLICABLE)


def test_a_section_with_a_failing_gate_may_not_report_ok():
    section = Section("binding", "Chain binding", data={})
    section.gates = [Gate("binding.dispersion", False, "3.35 links per class")]
    section.apply_gates()
    assert section.status == UNRELIABLE


def test_a_section_with_passing_gates_stays_ok():
    section = Section("binding", "Chain binding", data={})
    section.gates = [Gate("binding.dispersion", True, "")]
    section.apply_gates()
    assert section.status == OK


def test_a_failed_section_is_not_upgraded_by_gates():
    section = Section("x", "X", status=FAILED)
    section.gates = [Gate("g", True, "")]
    section.apply_gates()
    assert section.status == FAILED


def test_unreliable_output_is_still_readable_but_marked():
    report = Report("o", "now", "2.0", "a" * 64)
    section = Section("binding", "Chain binding", data={"x": 1})
    section.gates = [Gate("g", False, "d")]
    section.apply_gates()
    report.sections.append(section)
    assert report.data("binding") == {"x": 1}
    assert report.ids_with_status(UNRELIABLE) == ["binding"]


def test_a_skipped_section_records_its_cause():
    report = Report("o", "now", "2.0", "a" * 64)
    report.sections.append(Section(
        "scope", "Scope of review", status=SKIPPED,
        error="thickness contradiction",
        skipped_because={"section": "chain", "reason": "thickness_contradiction"}))
    blob = report.to_dict()
    assert blob["sections"][0]["skipped_because"]["section"] == "chain"
    text = report_markdown(report)
    assert "Why a section did not run" in text
    assert "downstream consequence" in text


def test_the_unreliable_warning_goes_above_the_statistics():
    report = Report("o", "now", "2.0", "a" * 64, metrics={"named_classes_local": 5})
    section = Section("binding", "Chain binding", data={})
    section.gates = [Gate("g", False, "d")]
    section.apply_gates()
    report.sections.append(section)
    text = report_markdown(report)
    assert text.index("did not meet their own") < text.index("## Statistics")


# --------------------------------------------------------------------------
# R8: provenance
# --------------------------------------------------------------------------

def test_a_seeded_bootstrap_named_after_a_regulation_needs_a_disclaimer():
    p = build_provenance("CFR-DisabilityRegs",
                         {"seeded_from": "GeometryofTheGood", "source_text": None,
                          "description": "20CFR404"})
    assert p.derivation == "seeded_bootstrap"
    assert p.name_implies_source is True
    assert "20 CFR 404" in p.implied_source
    assert "nothing in this artifact was derived from that text" in p.line()


def test_a_text_extraction_needs_no_disclaimer():
    p = build_provenance("CFR-DisabilityRegs",
                         {"source_text": "20cfr404.txt", "derivation": "text_extraction"})
    assert p.needs_disclaimer is False
    assert p.line() == ""


def test_a_neutral_name_needs_no_disclaimer():
    p = build_provenance("GeometryofTheGood", {"seeded_from": "x"})
    assert p.name_implies_source is False


def test_the_provenance_line_is_above_the_fold():
    report = Report("CFR-DisabilityRegs", "now", "2.0", "a" * 64,
                    provenance=build_provenance(
                        "CFR-DisabilityRegs",
                        {"seeded_from": "GeometryofTheGood", "description": "20CFR404"}))
    text = report_markdown(report)
    assert text.index("**Provenance:**") < text.index("## Sections")


# --------------------------------------------------------------------------
# R5 and R6: bundle gates
# --------------------------------------------------------------------------

def base_report() -> Report:
    return Report("o", "now", "2.0", "a" * 64,
                  metrics={"named_classes_local": 1009,
                           "object_properties_local": 11,
                           "object_properties_with_imports": 75})


def test_the_bundle_refuses_to_write_on_a_metric_collision():
    report = base_report()
    report.sections.append(Section("a", "A", data={"object_properties_local": 75}))
    with pytest.raises(BundleManifestError) as e:
        write_bundle(report, "<rdf:RDF/>")
    assert "more than one value" in str(e.value)


def test_a_clean_bundle_writes():
    blob = write_bundle(base_report(), "<rdf:RDF/>")
    assert zipfile.ZipFile(io.BytesIO(blob)).namelist()


def test_the_readme_lists_exactly_what_is_present():
    """scope-client.md was advertised in a bundle that did not contain it."""
    blob = write_bundle(base_report(), "<rdf:RDF/>")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())
        readme = z.read("README.txt").decode()
    assert "scope-client.md" not in names
    assert "scope-client.md" not in readme
    for name in names:
        assert name in readme


def test_the_readme_lists_the_client_table_when_it_is_present():
    blob = write_bundle(base_report(), "<rdf:RDF/>", "the table")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert "scope-client.md" in z.namelist()
        assert "scope-client.md" in z.read("README.txt").decode()


def test_the_readme_warns_about_unreliable_sections():
    report = base_report()
    section = Section("binding", "Chain binding", data={})
    section.gates = [Gate("g", False, "d")]
    section.apply_gates()
    report.sections.append(section)
    with zipfile.ZipFile(io.BytesIO(write_bundle(report, "<rdf:RDF/>"))) as z:
        assert "unreliable" in z.read("README.txt").decode()


# --------------------------------------------------------------------------
# F3: the migrator
# --------------------------------------------------------------------------

def test_migration_moves_statistics_to_top_level_metrics():
    old = {"report_schema": "1.0", "sections": [
        {"id": "statistics", "status": "ok",
         "data": {"classes": 1009, "object_properties": 11}}]}
    new = migrate(old)
    assert new["report_schema"] == "2.0"
    assert new["migrated_from"] == "1.0"
    assert new["metrics"]["named_classes_local"] == 1009
    assert new["metrics"]["object_properties_local"] == 11


def test_migration_does_not_carry_the_old_debt_figure():
    old = {"report_schema": "1.0", "sections": [
        {"id": "recognition_audit", "status": "ok",
         "data": {"contradiction_debt": {"cd": 1020.0}}}]}
    new = migrate(old)
    assert "cd_core" not in json.dumps(new.get("metrics", {}))
    assert "would give it a credibility it does not have" in new["migration_note"]


def test_migration_reconstructs_provenance():
    old = {"report_schema": "1.0", "sections": [
        {"id": "manifest", "status": "ok",
         "data": {"seeded_from": "GeometryofTheGood", "source_text": None}}]}
    assert migrate(old)["provenance"]["derivation"] == "seeded_bootstrap"


def test_migration_is_idempotent():
    once = migrate({"report_schema": "1.0", "sections": []})
    assert migrate(once)["report_schema"] == "2.0"


def test_migrated_sections_carry_no_invented_gates():
    new = migrate({"report_schema": "1.0",
                   "sections": [{"id": "a", "status": "ok", "data": {}}]})
    assert new["sections"][0]["gates"] == []
    assert new["sections"][0]["gates_total"] == 0
