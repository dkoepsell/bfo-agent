"""The resolver: five verdicts, no sixth, and never a cleanliness judgment."""
from __future__ import annotations

import pytest

from app import recognition as rec
from app.aperture import chain as chain_mod
from app.aperture.loader import load_manifest
from app.aperture.probe import ProbeReport, ProbeResult
from app.aperture.resolve import (
    RESOLUTION_SCHEMA,
    COMPUTED,
    DECLARED,
    Verdict,
    resolve,
)

ALL_SATISFIED = {
    "formal_theory": True,
    "definitions": True,
    "complement_available": True,
    "upper_ontology": True,
    "bearer_relations": True,
    "acts": True,
    "individuals": True,
    "profile_dl": True,
}


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


def make_probe(**overrides) -> ProbeReport:
    values = dict(ALL_SATISFIED)
    values.update(overrides)
    results = {
        pid: ProbeResult(pid, val, {"stub": True},
                         "" if val is True else f"{pid} is not satisfied")
        for pid, val in values.items()
    }
    return ProbeReport(
        artifact_path="/stub/working.owl",
        artifact_sha256="a" * 64,
        probe_version="1.0.0",
        formal_theory_strict=True,
        results=results,
    )


def make_chain(**links) -> chain_mod.ChainDeclaration:
    states = {locus: "performed" for locus in chain_mod.LOCI}
    states.update(links)
    return chain_mod.validate(
        {"links": states, "basis": "the engagement scope guide, section 2",
         "declared_on": "2026-08-02"},
        engagement="stub", domain="legal_order",
    )


ALL_PERFORMED = None  # filled per test via make_chain()


# --------------------------------------------------------------------------
# The verdict vocabulary itself
# --------------------------------------------------------------------------

def test_there_are_exactly_five_verdicts():
    assert len(Verdict) == 5


def test_there_is_no_clean_verdict():
    """Cleanliness is a detector result. The resolver never produces one, and
    the type is what enforces it."""
    names = {v.name for v in Verdict}
    assert "CLEAN" not in names
    assert not any("CLEAN" in n or "PASS" in n or "OK" in n for n in names)


def test_no_row_can_carry_a_verdict_outside_the_enum(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    for row in report.rows:
        assert isinstance(row.verdict, Verdict)
        assert str(row.verdict) in {str(v) for v in Verdict}


def test_every_failure_is_resolved_exactly_once(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    ids = [r.failure_id for r in report.rows]
    assert sorted(ids) == sorted(rec.KERNEL)
    assert len(ids) == len(set(ids)) == 12


def test_report_carries_the_schema_and_both_digests(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    assert report.resolution_schema == RESOLUTION_SCHEMA
    assert report.manifest_digest.startswith("sha256:")
    assert report.chain_digest.startswith("sha256:")
    assert report.manifest_version == manifest.version


def test_counts_cover_every_verdict_key(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    assert set(report.counts) == {str(v) for v in Verdict}
    assert sum(report.counts.values()) == 12


# --------------------------------------------------------------------------
# Preconditions come first, and the reason is deterministic
# --------------------------------------------------------------------------

def test_an_unsatisfied_precondition_puts_the_stratum_out(manifest):
    report = resolve(manifest, make_probe(formal_theory=False), make_chain())
    for row in report.rows:
        assert row.verdict is Verdict.OUT_PRECONDITION
        assert row.reason == "formal_theory"
        assert row.basis == COMPUTED


def test_stratum_c_falls_to_upper_ontology(manifest):
    report = resolve(manifest, make_probe(upper_ontology=False), make_chain())
    for row in report.rows:
        if row.stratum in ("C", "D"):
            assert row.verdict is Verdict.OUT_PRECONDITION
            assert row.reason == "upper_ontology"
        else:
            assert row.verdict is not Verdict.OUT_PRECONDITION


def test_the_first_unsatisfied_precondition_is_the_one_reported(manifest):
    """Cumulative order, so a C-stratum failure with both definitions and
    upper_ontology missing reports definitions."""
    report = resolve(manifest,
                     make_probe(definitions=False, upper_ontology=False),
                     make_chain())
    assert report.verdict("K-C1") is Verdict.OUT_PRECONDITION
    assert report.by_id("K-C1").reason == "definitions"


def test_a_per_failure_requirement_only_gates_that_failure(manifest):
    report = resolve(manifest, make_probe(complement_available=False), make_chain())
    assert report.verdict("K-B3") is Verdict.OUT_PRECONDITION
    assert report.by_id("K-B3").reason == "complement_available"
    assert report.verdict("K-B1") is not Verdict.OUT_PRECONDITION


def test_bearer_relations_gates_only_the_dependence_failure(manifest):
    report = resolve(manifest, make_probe(bearer_relations=False), make_chain())
    assert report.by_id("K-C3").reason == "bearer_relations"
    assert report.verdict("K-C1") is not Verdict.OUT_PRECONDITION


def test_acts_gates_the_whole_pragmatic_stratum(manifest):
    report = resolve(manifest, make_probe(acts=False), make_chain())
    for row in report.rows:
        if row.stratum == "D":
            assert row.verdict is Verdict.OUT_PRECONDITION
            assert row.reason == "acts"


# --------------------------------------------------------------------------
# profile_dl is a hard gate that never assumes a pass
# --------------------------------------------------------------------------

def test_unverified_profile_blocks_the_pure_reasoner_failure(manifest):
    """K-A2 has only a reasoner detector, so it goes out."""
    report = resolve(manifest, make_probe(profile_dl=None), make_chain())
    row = report.by_id("K-A2")
    assert row.verdict is Verdict.OUT_PRECONDITION
    assert row.reason == "profile_dl"
    assert row.detectors == ()


def test_unknown_profile_is_treated_exactly_as_a_failure(manifest):
    unknown = resolve(manifest, make_probe(profile_dl=None), make_chain())
    failed = resolve(manifest, make_probe(profile_dl=False), make_chain())
    assert [r.verdict for r in unknown.rows] == [r.verdict for r in failed.rows]


def test_a_structural_detector_survives_the_profile_gate(manifest):
    """K-A1 is reachable by a reasoner and by a structural pass. Blocking the
    structural half would overstate what the profile failure costs."""
    report = resolve(manifest, make_probe(profile_dl=False), make_chain())
    row = report.by_id("K-A1")
    assert row.verdict is Verdict.IN_SCOPE_MECHANICAL
    assert row.detectors == ("app.kernel_audit.audit",)
    assert any("reasoner half" in n for n in row.notes)


def test_a_verified_profile_keeps_the_reasoner_detector(manifest):
    report = resolve(manifest, make_probe(profile_dl=True), make_chain())
    row = report.by_id("K-A1")
    assert "app.coherence_reason.coherence_check" in row.detectors
    assert row.notes == ()


def test_the_profile_gate_does_not_touch_a_process_model_failure(manifest):
    report = resolve(manifest, make_probe(profile_dl=None), make_chain())
    assert report.verdict("K-D2") is Verdict.IN_SCOPE_MECHANICAL


# --------------------------------------------------------------------------
# Chain gating
# --------------------------------------------------------------------------

def test_all_absent_links_put_the_pragmatic_stratum_out_of_chain(manifest):
    declaration = chain_mod.validate(
        {"links": {locus: "absent" for locus in chain_mod.LOCI},
         "basis": "no institutional role is claimed for this artifact"},
        engagement="stub", domain="scientific_reference")
    report = resolve(manifest, make_probe(), declaration)
    for row in report.rows:
        if row.stratum == "D":
            assert row.verdict is Verdict.OUT_CHAIN
            assert row.basis == DECLARED
        else:
            assert row.verdict in (Verdict.IN_SCOPE_MECHANICAL, Verdict.IN_SCOPE_HUMAN)


def test_all_external_links_gate_rare(manifest):
    declaration = chain_mod.validate(
        {"links": {locus: "external" for locus in chain_mod.LOCI},
         "basis": "every link is performed by the publishing body, not here"},
        engagement="stub", domain="clinical_nosology")
    report = resolve(manifest, make_probe(), declaration)
    for row in report.rows:
        if row.stratum == "D":
            assert row.verdict is Verdict.GATED_RARE
            assert row.reason == "chain_external"


def test_a_performed_link_brings_the_failure_into_scope(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    row = report.by_id("K-D2")
    assert row.verdict is Verdict.IN_SCOPE_MECHANICAL
    assert row.reason == "chain_performed"
    assert row.basis == DECLARED


def test_mixed_external_and_absent_gates_rare_and_names_the_loci(manifest):
    """K-D3 sits at assessor, criteria and effect."""
    declaration = chain_mod.validate(
        {"links": {locus: "external" for locus in chain_mod.LOCI}
                  | {"assessor": "absent", "remedy": "external"},
         "basis": "there is no assessor role in this system"},
        engagement="stub", domain="clinical_nosology")
    report = resolve(manifest, make_probe(), declaration)
    row = report.by_id("K-D3")
    assert row.verdict is Verdict.GATED_RARE
    assert any("mixed link states" in n and "assessor" in n for n in row.notes)


def test_one_performed_locus_is_enough(manifest):
    declaration = chain_mod.validate(
        {"links": {locus: "absent" for locus in chain_mod.LOCI}
                  | {"act": "performed", "remedy": "absent"},
         "basis": "the system performs the conferral itself"},
        engagement="stub", domain="legal_order")
    report = resolve(manifest, make_probe(), declaration)
    assert report.verdict("K-D2") is Verdict.IN_SCOPE_MECHANICAL   # loci: act
    assert report.verdict("K-D1") is Verdict.OUT_CHAIN             # loci: facts


def test_chain_state_does_not_gate_the_lower_strata(manifest):
    declaration = chain_mod.validate(
        {"links": {locus: "absent" for locus in chain_mod.LOCI},
         "basis": "a reference vocabulary with no institutional role"},
        engagement="stub", domain="scientific_reference")
    report = resolve(manifest, make_probe(), declaration)
    for row in report.rows:
        if row.stratum != "D":
            assert row.link_states == {}
            assert row.basis == COMPUTED


# --------------------------------------------------------------------------
# A null detector is human judgment in scope, never out of scope
# --------------------------------------------------------------------------

def test_the_two_detectorless_failures_resolve_to_human_judgment(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    assert report.verdict("K-B2") is Verdict.IN_SCOPE_HUMAN
    assert report.verdict("K-D1") is Verdict.IN_SCOPE_HUMAN


def test_human_judgment_survives_an_unverified_profile(manifest):
    """A framework whose non-mechanical checks silently disappear has inverted
    its own purpose, so the profile gate must not touch them."""
    report = resolve(manifest, make_probe(profile_dl=None), make_chain())
    assert report.verdict("K-B2") is Verdict.IN_SCOPE_HUMAN


def test_human_judgment_is_still_gated_by_the_chain(manifest):
    declaration = chain_mod.validate(
        {"links": {locus: "absent" for locus in chain_mod.LOCI},
         "basis": "no institutional role is claimed"},
        engagement="stub", domain="scientific_reference")
    report = resolve(manifest, make_probe(), declaration)
    assert report.verdict("K-D1") is Verdict.OUT_CHAIN


# --------------------------------------------------------------------------
# computed against declared
# --------------------------------------------------------------------------

def test_every_row_says_whether_it_was_computed_or_declared(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    for row in report.rows:
        assert row.basis in (COMPUTED, DECLARED)


def test_a_chain_verdict_is_declared_and_a_precondition_verdict_is_computed(manifest):
    report = resolve(manifest, make_probe(acts=False), make_chain())
    assert report.by_id("K-D1").basis == COMPUTED
    report2 = resolve(manifest, make_probe(), make_chain())
    assert report2.by_id("K-D1").basis == DECLARED


# --------------------------------------------------------------------------
# Agreement with the vocabulary the Audits tab already uses
# --------------------------------------------------------------------------

@pytest.mark.parametrize("domain,links", [
    ("scientific_reference", "absent"),
    ("clinical_nosology", "external"),
    ("legal_order", "performed"),
])
def test_anything_active_primitives_excludes_is_not_in_scope(manifest, domain, links):
    """The five verdicts refine the three statuses of recognition.completeness,
    so the two must never disagree about what can fire."""
    declaration = chain_mod.validate(
        {"links": {locus: links for locus in chain_mod.LOCI},
         "basis": "a declaration made for the purpose of this check"},
        engagement="stub", domain=domain)
    report = resolve(manifest, make_probe(), declaration)
    active = set(rec.active_primitives(declaration.to_profile()))
    for row in report.rows:
        if row.failure_id not in active:
            assert not row.in_scope, row.failure_id


@pytest.mark.parametrize("domain,links", [
    ("scientific_reference", "absent"),
    ("clinical_nosology", "external"),
])
def test_anything_completeness_calls_not_applicable_is_not_in_scope(manifest, domain, links):
    declaration = chain_mod.validate(
        {"links": {locus: links for locus in chain_mod.LOCI},
         "basis": "a declaration made for the purpose of this check"},
        engagement="stub", domain=domain)
    report = resolve(manifest, make_probe(), declaration)
    rows = rec.completeness(declaration.to_profile(), set(rec.KERNEL))
    not_applicable = {r.code for r in rows if r.status == rec.NOT_APPLICABLE}
    for code in not_applicable:
        assert not report.by_id(code).in_scope, code


# --------------------------------------------------------------------------
# Serialisation stays comparable across runs
# --------------------------------------------------------------------------

def test_serialised_rows_carry_stable_identifiers(manifest):
    report = resolve(manifest, make_probe(), make_chain())
    blob = report.to_dict()
    assert blob["resolution_schema"] == RESOLUTION_SCHEMA
    assert {r["failure_id"] for r in blob["rows"]} == set(rec.KERNEL)
    for row in blob["rows"]:
        assert row["verdict"] in {str(v) for v in Verdict}
        assert row["basis"] in (COMPUTED, DECLARED)


def test_advisories_are_carried_through(manifest):
    advisory = chain_mod.Advisory("performed_but_unbound", "criteria", "msg", {})
    report = resolve(manifest, make_probe(), make_chain(), advisories=[advisory])
    assert report.advisories[0]["kind"] == "performed_but_unbound"
    assert report.advisories[0]["advisory"] is True
