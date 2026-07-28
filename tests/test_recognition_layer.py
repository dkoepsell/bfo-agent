"""Tests for the recognition layer (SPEC-recognition-layer.md).

Covers the vocabulary (P1), the manifest profile (P2), the proposer rules (P3),
the chain-aware lint rules (P4), the ledger triples (P5), the Stratum D
detectors and the falsifiable control prediction (P6, P9), and the reporting
invariant (P7).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import construction_linter, llm_proposer, recognition as rec
from app import recognition_audit
from app.schema import Entity, Proposal, Relation


# --------------------------------------------------------------------------
# P1: the vocabulary
# --------------------------------------------------------------------------

def test_kernel_and_chain_are_complete():
    assert rec.self_check() == []
    assert len(rec.KERNEL) == 12
    assert len(rec.CHAIN) == 7
    assert set(rec.KERNEL_BY_STRATUM) == {"A", "B", "C", "D"}
    assert all(len(v) == 3 for v in rec.KERNEL_BY_STRATUM.values())


def test_twelve_social_domains_plus_the_control():
    # Table 1 has twelve rows; the scientific-reference control is the
    # thirteenth profile and is not one of them.
    assert len(rec.DOMAIN_PROFILES) == 13
    social = [p for p in rec.DOMAIN_PROFILES.values() if p.has_chain]
    assert len(social) == 12


def test_every_fr5_legal_type_resolves_to_a_kernel_locus_pair():
    from app import mlc

    for name in mlc.CONTRADICTION_TYPES:
        typed = rec.typed_defect_for_legal(name)
        assert typed is not None, name
        assert typed.kernel in rec.KERNEL
        assert typed.locus in rec.CHAIN_BY_LOCUS


def test_mlc_nodes_map_onto_the_chain():
    from app import sool_extension as sx

    for node in sx.MLC_NODES:
        assert node.index in rec.MLC_NODE_TO_LOCUS
    # Node 6 (Target) and node 7 (Legal Effect) both sit at the effect locus.
    assert rec.MLC_NODE_TO_LOCUS[6] == rec.MLC_NODE_TO_LOCUS[7] == rec.Locus.EFFECT


def test_stratum_d_fires_only_where_there_are_acts():
    control = rec.profile_for("scientific_reference")
    legal = rec.profile_for("legal_order")
    assert control.active_strata == ("A", "B", "C")
    assert legal.active_strata == ("A", "B", "C", "D")
    assert not any(c.startswith("K-D") for c in rec.active_primitives(control))
    assert {"K-D1", "K-D2", "K-D3"} <= set(rec.active_primitives(legal))


def test_recognition_only_institution_has_a_thin_d():
    icd = rec.profile_for("clinical_nosology")
    assert icd.has_chain and icd.stratum_d_thin
    # Falsification needs world-facing data the artifact does not contain.
    assert "K-D1" not in rec.active_primitives(icd)
    assert "K-D3" in rec.active_primitives(icd)


def test_unknown_domain_falls_back_to_the_control_never_to_an_institution():
    assert rec.profile_for("not_a_domain").key == "scientific_reference"
    assert rec.profile_for(None).key == "scientific_reference"
    assert not rec.profile_for("").has_chain


def test_locus_anchors_reject_a_status_modelled_as_a_process():
    # A conferred status is a realizable, not the act that confers it.
    assert rec.anchor_ok(rec.Locus.EFFECT, "BFO_0000017")
    assert not rec.anchor_ok(rec.Locus.EFFECT, "BFO_0000015")
    assert rec.anchor_ok(rec.Locus.ACT, "BFO_0000015")
    assert not rec.anchor_ok(rec.Locus.ACT, "BFO_0000023")


# --------------------------------------------------------------------------
# P7: the reporting invariant
# --------------------------------------------------------------------------

def test_completeness_table_covers_all_twelve_primitives():
    profile = rec.profile_for("clinical_nosology")
    rows = rec.completeness(profile, {"K-A1", "K-B3"})
    assert len(rows) == 12
    by_code = {r.code: r for r in rows}
    assert by_code["K-A1"].status == rec.INSTRUMENTED
    assert by_code["K-B3"].status == rec.INSTRUMENTED
    # Not instrumented is SILENT, never a zero measurement.
    assert by_code["K-B2"].status == rec.SILENT_BY_PRINCIPLE
    # K-D1 cannot fire on a recognition-only institution's artifact.
    assert by_code["K-D1"].status == rec.NOT_APPLICABLE
    assert "external" in by_code["K-D1"].note


def test_control_profile_marks_every_d_primitive_not_applicable():
    rows = {r.code: r for r in rec.completeness(rec.SCIENTIFIC_REFERENCE, set())}
    for code in ("K-D1", "K-D2", "K-D3"):
        assert rows[code].status == rec.NOT_APPLICABLE
        assert "no chain" in rows[code].note


def test_debt_is_a_lower_bound_and_separates_translation_defects():
    profile = rec.profile_for("clinical_nosology")
    findings = [
        {"kernel_code": "K-B3", "locus": "criteria", "attribution": "source"},
        {"kernel_code": "K-C1", "locus": "effect", "attribution": "translation"},
        {"kernel_code": "K-A2", "locus": "criteria"},
    ]
    debt = rec.contradiction_debt(findings, profile, {"K-B3", "K-A2"})
    assert debt["lower_bound"] is True
    assert debt["calibrated"] is False
    # The translation defect is not the institution's debt.
    assert debt["attributed_to_translation"] == 1
    assert debt["cd"] == 2
    assert debt["per_type"] == {"K-B3": 1, "K-A2": 1}
    assert debt["attribution_undetermined"] == 1
    assert len(debt["completeness"]) == 12


def test_sampled_types_do_not_understate_the_debt():
    profile = rec.profile_for("clinical_nosology")
    findings = [{"kernel_code": "K-B3", "locus": "criteria"} for _ in range(3)]
    debt = rec.contradiction_debt(findings, profile, {"K-B3"},
                                  totals={"K-B3": 50})
    assert debt["per_type"]["K-B3"] == 50
    assert debt["cd"] == 50
    assert debt["sampled_types"] == {"K-B3": 3}
    assert debt["attribution_undetermined"] == 50


# --------------------------------------------------------------------------
# P3: the proposer rules
# --------------------------------------------------------------------------

def test_recognition_rules_are_empty_for_a_scientific_reference_ontology():
    assert llm_proposer.recognition_rules(
        {"domain": "scientific_reference", "has_chain": False}) == ""
    assert llm_proposer.recognition_rules({}) == ""


def test_recognition_rules_name_the_institutions_own_chain():
    text = llm_proposer.recognition_rules(
        {"domain": "refugee_status", "has_chain": True})
    assert "recognition_locus" in text
    assert "determination officer" in text
    assert "non-refoulement" in text
    # Rule 19: the chain is annotation, not classes to mint.
    assert "NOT a set of classes" in text


# --------------------------------------------------------------------------
# P4: the chain-aware lint rules
# --------------------------------------------------------------------------

def _entity(label, bfo_type, locus=None, parent="owl:Thing", kind="class"):
    return Entity(
        label=label, kind=kind, bfo_type=bfo_type, bfo_label="x",
        parent_class=parent, iri_suggestion=f"http://ex.org/w#{label}",
        rationale="test", recognition_locus=locus,
    )


def _proposal(entities=(), relations=()):
    return Proposal(session_id="s", utterance="t", entities=list(entities),
                    relations=list(relations))


def test_pc9_rejects_a_status_anchored_as_a_process():
    p = _proposal([_entity("RefugeeStatus", "BFO_0000015", locus="effect")])
    report = construction_linter.lint(p, chain_active=True)
    assert not report.ok
    assert any(v.rule == "PC-9" for v in report.violations)


def test_pc9_accepts_a_status_anchored_as_a_realizable():
    p = _proposal([_entity("RefugeeStatus", "BFO_0000017", locus="effect")])
    report = construction_linter.lint(p, chain_active=True)
    assert not any(v.rule == "PC-9" for v in report.violations)


def test_chain_rules_do_not_run_without_a_declared_chain():
    p = _proposal([_entity("RefugeeStatus", "BFO_0000015", locus="effect")])
    report = construction_linter.lint(p, chain_active=False)
    assert not any(v.rule.startswith("PC-9") for v in report.violations)
    assert report.findings == []


def test_pc12_rejects_minting_the_chain_itself_as_a_class():
    p = _proposal([_entity("Criteria", "BFO_0000031", locus="criteria")])
    report = construction_linter.lint(p, chain_active=True)
    assert any(v.rule == "PC-12" for v in report.violations)


def test_pc10_records_a_residual_category_without_rejecting_it():
    p = _proposal([_entity("OtherSpecifiedAnxietyDisorder", "BFO_0000016",
                           locus="effect")])
    report = construction_linter.lint(p, chain_active=True)
    residual = [f for f in report.findings if f.rule == "PC-10"]
    assert len(residual) == 1
    assert residual[0].kernel_code == "K-B3"
    # A source defect is recorded, never rewritten: the extraction keeps it.
    assert not any(v.rule == "PC-10" for v in report.violations)


def test_findings_never_default_to_blaming_the_source():
    p = _proposal([_entity("UnspecifiedDisorder", "BFO_0000016", locus="effect")])
    report = construction_linter.lint(p, chain_active=True)
    assert report.findings[0].attribution == "undetermined"


def test_pc13_flags_siblings_with_no_disjointness_asserted():
    ents = [
        _entity("Parent", "BFO_0000016"),
        _entity("KidA", "BFO_0000016"),
        _entity("KidB", "BFO_0000016"),
    ]
    rels = [
        Relation(s="KidA", p="subClassOf", o="Parent", rationale="t"),
        Relation(s="KidB", p="subClassOf", o="Parent", rationale="t"),
    ]
    report = construction_linter.lint(_proposal(ents, rels), chain_active=True)
    ct1 = [f for f in report.findings if f.rule == "PC-13"]
    assert len(ct1) == 1
    assert ct1[0].kernel_code == "K-A1"
    # Flagged, never repaired: asserting the disjointness would be our claim.
    assert report.ok or all(v.rule != "PC-13" for v in report.violations)


def test_pc13_silent_once_disjointness_is_asserted():
    ents = [_entity("Parent", "BFO_0000016"), _entity("KidA", "BFO_0000016"),
            _entity("KidB", "BFO_0000016")]
    rels = [
        Relation(s="KidA", p="subClassOf", o="Parent", rationale="t"),
        Relation(s="KidB", p="subClassOf", o="Parent", rationale="t"),
        Relation(s="KidA", p="disjointWith", o="KidB", rationale="t"),
    ]
    report = construction_linter.lint(_proposal(ents, rels), chain_active=True)
    assert not [f for f in report.findings if f.rule == "PC-13"]


def test_pc11_flags_a_criterion_doing_double_duty():
    ents = [_entity("Cond", "BFO_0000016"), _entity("Cat", "BFO_0000016")]
    rels = [
        Relation(s="Cat", p="hasInclusionCriterion", o="Cond", rationale="t"),
        Relation(s="Cat", p="hasExclusionCriterion", o="Cond", rationale="t"),
    ]
    report = construction_linter.lint(_proposal(ents, rels), chain_active=True)
    dd = [f for f in report.findings if f.rule == "PC-11"]
    assert len(dd) == 1
    assert dd[0].kernel_code == "K-B2"


# --------------------------------------------------------------------------
# P5: ledger triples
# --------------------------------------------------------------------------

def test_ledger_records_kernel_findings_as_typed_triples(tmp_path):
    from app import incoherence_ledger

    working = tmp_path / "working.owl"
    working.write_text("<rdf/>")
    ids = incoherence_ledger.record_kernel_findings(working, [
        {"rule": "PC-10", "kernel_code": "K-B3", "locus": "criteria",
         "term": "Unspecified", "detail": "residual"},
    ])
    assert len(ids) == 1
    entries = incoherence_ledger.kernel_findings(working)
    assert len(entries) == 1
    e = entries[0]
    assert (e["kernel_code"], e["locus"]) == ("K-B3", "criteria")
    assert e["attribution"] == "undetermined"


# --------------------------------------------------------------------------
# P2: the manifest profile
# --------------------------------------------------------------------------

def test_profile_round_trips_through_the_manifest(tmp_path, monkeypatch):
    from app import config, registry

    lib = tmp_path / "library"
    (lib / "Demo").mkdir(parents=True)
    (lib / "Demo" / "manifest.json").write_text(json.dumps({"name": "Demo"}))
    monkeypatch.setattr(config, "LIBRARY_ROOT", lib)

    reg = registry.OntologyRegistry.__new__(registry.OntologyRegistry)
    reg._library_root = lib
    reg._managers = {"Demo": object()}
    reg._manifests = {"Demo": {"name": "Demo"}}
    reg._active_name = "Demo"

    undeclared = reg.recognition_profile("Demo")
    assert undeclared["domain"] == "scientific_reference"
    assert undeclared["declared"] is False
    assert undeclared["has_chain"] is False

    declared = reg.set_recognition_profile("Demo", "professional_licensure")
    assert declared["has_chain"] is True
    assert declared["declared"] is True
    assert "D" in declared["active_strata"]
    on_disk = json.loads((lib / "Demo" / "manifest.json").read_text())
    assert on_disk["recognition"]["domain"] == "professional_licensure"

    with pytest.raises(ValueError):
        reg.set_recognition_profile("Demo", "atlantis")


# --------------------------------------------------------------------------
# P6 / P9: Stratum D and the falsifiable control prediction
# --------------------------------------------------------------------------

_OWL_HEADER = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xml:base="http://ex.org/w">
  <owl:Ontology rdf:about="http://ex.org/w"/>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/BFO_0000015"/>
  <owl:Class rdf:about="http://purl.obolibrary.org/obo/BFO_0000017"/>
"""
_OWL_FOOTER = "</rdf:RDF>\n"


def _class(name, parent):
    return (f'  <owl:Class rdf:about="http://ex.org/w#{name}">\n'
            f'    <rdfs:subClassOf rdf:resource="{parent}"/>\n'
            f'  </owl:Class>\n')


def _write_ontology(path: Path, body: str) -> str:
    path.write_text(_OWL_HEADER + body + _OWL_FOOTER)
    return str(path)


BFO_PROCESS = "http://purl.obolibrary.org/obo/BFO_0000015"
BFO_REALIZABLE = "http://purl.obolibrary.org/obo/BFO_0000017"


def test_stratum_d_is_empty_for_the_scientific_reference_control(tmp_path):
    """The control prediction of SPEC P9. A D finding here is a bug in the
    implementation, not a discovery."""
    target = _write_ontology(
        tmp_path / "control.owl",
        _class("Status", BFO_REALIZABLE) + _class("Conferral", BFO_PROCESS),
    )
    findings = recognition_audit._stratum_d(
        target, rec.SCIENTIFIC_REFERENCE, unsat_names=["Conferral"])
    assert findings == []


def test_k_d3_fires_for_an_act_thick_institution_with_unrealizable_capacities(tmp_path):
    target = _write_ontology(
        tmp_path / "legal.owl",
        _class("Liability", BFO_REALIZABLE) + _class("Judgment", BFO_PROCESS),
    )
    findings = recognition_audit._stratum_d(
        target, rec.profile_for("legal_order"), unsat_names=[])
    d3 = [f for f in findings if f["kernel_code"] == "K-D3"]
    assert d3, "a conferred capacity with no realization link is a modal clash"
    assert "Liability" in d3[0]["subjects"]


def test_k_d2_types_an_unsatisfiable_act_at_the_act_locus(tmp_path):
    target = _write_ontology(
        tmp_path / "legal.owl",
        _class("Liability", BFO_REALIZABLE) + _class("Judgment", BFO_PROCESS),
    )
    findings = recognition_audit._stratum_d(
        target, rec.profile_for("legal_order"), unsat_names=["Judgment"])
    d2 = [f for f in findings if f["kernel_code"] == "K-D2"]
    assert len(d2) == 1
    assert d2[0]["locus"] == "act"
    assert d2[0]["subjects"] == ["Judgment"]


def test_k_d2_does_not_fire_on_an_unsatisfiable_non_act(tmp_path):
    target = _write_ontology(
        tmp_path / "legal.owl",
        _class("Liability", BFO_REALIZABLE) + _class("Judgment", BFO_PROCESS),
    )
    findings = recognition_audit._stratum_d(
        target, rec.profile_for("legal_order"), unsat_names=["Liability"])
    assert not [f for f in findings if f["kernel_code"] == "K-D2"]


def test_audit_of_the_control_reports_d_as_not_applicable(tmp_path):
    target = _write_ontology(
        tmp_path / "control.owl", _class("Status", BFO_REALIZABLE))
    report = recognition_audit.audit(
        target, rec.SCIENTIFIC_REFERENCE, run_reasoner=False)
    assert report["has_chain"] is False
    assert not [f for f in report["findings"] if f["kernel_code"].startswith("K-D")]
    rows = {r["code"]: r for r in report["contradiction_debt"]["completeness"]}
    for code in ("K-D1", "K-D2", "K-D3"):
        assert rows[code]["status"] == rec.NOT_APPLICABLE
    # The reporting invariant: the table ships with the number, always.
    assert len(report["contradiction_debt"]["completeness"]) == 12
    assert "lower bound" in report["note"]


def test_render_text_always_prints_the_completeness_table(tmp_path):
    target = _write_ontology(
        tmp_path / "control.owl", _class("Status", BFO_REALIZABLE))
    report = recognition_audit.audit(
        target, rec.SCIENTIFIC_REFERENCE, run_reasoner=False)
    text = recognition_audit.render_text(report)
    assert "Stratum completeness" in text
    for code in rec.KERNEL:
        assert code in text
