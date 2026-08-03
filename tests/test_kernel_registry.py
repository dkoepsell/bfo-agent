"""The primitive registry and the debt calculation, against the pinned fixture.

The fixture is CFR-DisabilityRegs at sha256 b67f2844..., the artifact whose
bundle produced a debt figure equal to its class count. Its numbers are the
acceptance criteria, so most assertions here are against it rather than against
synthetic data.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from app import recognition as rec
from app.kernel import COVERAGE, DEFECT, RegistryError, load_registry
from app.kernel.debt import compute_coverage, compute_debt
from app.kernel.registry import DEFAULT_REGISTRY_PATH, base_code
from app.report import metrics as metrics_mod

FIXTURE = Path(__file__).parent / "fixtures" / "cfr_disability" / "working.owl"
FIXTURE_SHA256 = "b67f284415060f4057ed5426f443307871e266a37e38d701549b8ab54fcecde8"


@pytest.fixture(scope="module")
def registry():
    return load_registry()


@pytest.fixture(scope="module")
def fixture_metrics():
    if not FIXTURE.exists():
        pytest.skip("reference fixture not present")
    return metrics_mod.from_paths(FIXTURE)


def write_registry(tmp_path, data) -> Path:
    p = tmp_path / "primitives.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def minimal(**overrides):
    entry = {
        "iri": "http://example.org/k#K-A1", "code": "K-A1", "name": "Inconsistency",
        "stratum": "A", "kind": "defect", "weight": 1.0, "calibrated": False,
        "requires": ["reasoner"], "aggregates_subjects": False,
    }
    entry.update(overrides)
    return {"version": "1.0.0", "weights_source": "uniform_fallback",
            "namespace": "http://example.org/k#", "primitives": [entry]}


# --------------------------------------------------------------------------
# The fixture is the one the spec pins
# --------------------------------------------------------------------------

def test_fixture_is_the_pinned_artifact():
    if not FIXTURE.exists():
        pytest.skip("reference fixture not present")
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_fixture_has_the_numbers_the_spec_describes(fixture_metrics):
    assert fixture_metrics["named_classes_local"] == 1009
    assert fixture_metrics["classes_with_iao_definition_local"] == 0
    assert fixture_metrics["classes_with_equivalent_class_local"] == 0
    assert fixture_metrics["definition_coverage_pct"] == 0.0
    assert fixture_metrics["object_properties_local"] == 11


# --------------------------------------------------------------------------
# F1: the registry
# --------------------------------------------------------------------------

def test_registry_loads_and_is_versioned(registry):
    assert registry.version
    assert registry.digest.startswith("sha256:")
    assert len(registry.top_level()) == 12


def test_registry_covers_every_published_primitive(registry):
    assert {p.code for p in registry.top_level()} == set(rec.KERNEL)


def test_strata_match_the_published_kernel(registry):
    for p in registry.top_level():
        assert p.stratum == rec.KERNEL[p.code].stratum, p.code


def test_the_undefined_class_subtype_is_coverage_not_defect(registry):
    """The entry the whole spec turns on."""
    a3b = registry.get("K-A3b")
    assert a3b.kind == COVERAGE
    assert a3b.weight == 0.0
    assert a3b.parent == "K-A3"
    assert registry.get("K-A3").kind == DEFECT


def test_subtypes_are_registry_entries_in_their_own_right(registry):
    """The overlap that let K-A3b appear in the findings table while the
    completeness table listed only the twelve."""
    assert "K-A3b" in registry.codes()
    assert len(registry) == len(registry.top_level()) + 1


def test_a_coverage_predicate_may_not_carry_weight(tmp_path):
    data = minimal(kind="coverage", weight=1.0)
    with pytest.raises(RegistryError) as e:
        load_registry(write_registry(tmp_path, data))
    assert "weight 0.0" in str(e.value)


def test_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(RegistryError):
        load_registry(write_registry(tmp_path, minimal(kind="warning")))


def test_an_unknown_requirement_is_refused(tmp_path):
    with pytest.raises(RegistryError) as e:
        load_registry(write_registry(tmp_path, minimal(requires=["vibes"])))
    assert "unknown requirement" in str(e.value)


def test_a_duplicate_code_is_refused(tmp_path):
    data = minimal()
    data["primitives"].append(dict(data["primitives"][0]))
    with pytest.raises(RegistryError):
        load_registry(write_registry(tmp_path, data))


def test_a_missing_version_is_refused(tmp_path):
    data = minimal()
    del data["version"]
    with pytest.raises(RegistryError):
        load_registry(write_registry(tmp_path, data))


def test_an_unregistered_code_is_a_build_error(registry):
    with pytest.raises(RegistryError) as e:
        registry.validate_codes(["K-A1", "K-Z9"])
    assert "K-Z9" in str(e.value)
    assert "build error" in str(e.value)


def test_every_finding_code_must_be_in_the_registry(registry):
    """The acceptance criterion: codes in findings are a subset of the registry."""
    from app import recognition_audit as ra

    emitted = (set(ra.INSTRUMENTED_STRUCTURAL) | set(ra.INSTRUMENTED_REASONER)
               | set(ra.INSTRUMENTED_STRATUM_D) | {"K-A3b"})
    assert emitted <= set(registry.codes())


def test_base_code_maps_a_subtype_to_its_primitive():
    assert base_code("K-A3b") == "K-A3"
    assert base_code("K-A3") == "K-A3"


def test_requirements_are_declared_for_every_entry(registry):
    for p in registry:
        assert p.requires, p.code


def test_the_two_aggregating_primitives_are_the_set_valued_ones(registry):
    """One finding naming 74 capacities is not the size of one missing annotation."""
    assert {p.code for p in registry if p.aggregates_subjects} == {"K-D2", "K-D3"}


# --------------------------------------------------------------------------
# R1: debt stops counting the ontology's size
# --------------------------------------------------------------------------

def fixture_findings():
    """The finding shape the reference bundle produced."""
    findings = [{"kernel_code": "K-A3b", "subjects": [f"C{i}"], "locus": "criteria",
                 "attribution": "undetermined"} for i in range(1009)]
    findings += [{"kernel_code": "K-B3", "subjects": [f"R{i}"], "locus": "criteria",
                  "attribution": "undetermined"} for i in range(5)]
    findings += [{"kernel_code": "K-C3", "subjects": [f"D{i}"], "locus": "effect",
                  "attribution": "undetermined"} for i in range(5)]
    findings += [{"kernel_code": "K-D3", "subjects": [f"c{i}" for i in range(74)],
                  "subject_count": 74, "locus": "effect",
                  "attribution": "undetermined"}]
    return findings


def test_fixture_debt_is_eleven_not_one_thousand_and_twenty(registry):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    assert debt.cd_core == 11.0


def test_the_coverage_predicate_is_excluded_and_counted_separately(registry):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    assert debt.excluded_coverage["K-A3b"] == 1009
    assert "K-A3b" not in debt.per_type


def test_per_type_matches_the_spec(registry):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    assert debt.per_type == {"K-B3": 5, "K-C3": 5, "K-D3": 1}


def test_subject_aggregation_distinguishes_the_capacity_finding(registry):
    """The inversion: one K-D3 finding over 74 capacities counted the same as one
    class missing an annotation."""
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    assert debt.per_type_weighted["K-D3"] == 74.0
    assert debt.per_type_weighted["K-C3"] == 5.0


def test_cd_per_class_is_emitted(registry):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    assert debt.cd_per_class == round(11.0 / 1009, 4)


def test_debt_is_never_rendered_as_a_bare_number_while_uncalibrated(registry):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    line = debt.render_line()
    assert debt.calibrated is False
    assert "typed count" in line and "uncalibrated" in line and "lower bound" in line
    assert line.strip() != "11"


def test_weights_source_is_recorded(registry):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    assert debt.weights_source == "uniform_fallback"
    assert debt.registry_version == registry.version


# --------------------------------------------------------------------------
# The saturation guard
# --------------------------------------------------------------------------

def test_a_defect_firing_on_most_of_its_population_is_marked_saturated(registry):
    findings = [{"kernel_code": "K-C3", "subjects": [f"D{i}"]} for i in range(900)]
    debt = compute_debt(findings, registry=registry, named_classes=1000)
    assert [s.code for s in debt.saturated_primitives] == ["K-C3"]
    assert debt.saturated_primitives[0].fire_rate == 0.9
    assert debt.cd_core == 0.0


def test_a_defect_below_the_threshold_is_not_saturated(registry):
    findings = [{"kernel_code": "K-C3", "subjects": [f"D{i}"]} for i in range(100)]
    debt = compute_debt(findings, registry=registry, named_classes=1000)
    assert debt.saturated_primitives == []
    assert debt.cd_core == 100.0


def test_the_threshold_is_configurable(registry):
    findings = [{"kernel_code": "K-C3", "subjects": [f"D{i}"]} for i in range(300)]
    assert compute_debt(findings, registry=registry, named_classes=1000,
                        saturation_tau=0.2).cd_core == 0.0
    assert compute_debt(findings, registry=registry, named_classes=1000,
                        saturation_tau=0.5).cd_core == 300.0


def test_translation_attributed_findings_never_enter_the_debt(registry):
    findings = [{"kernel_code": "K-C3", "attribution": "translation"},
                {"kernel_code": "K-C3", "attribution": "undetermined"}]
    debt = compute_debt(findings, registry=registry, named_classes=10)
    assert debt.cd_core == 1.0
    assert debt.attributed_to_translation == 1


def test_an_unregistered_finding_code_raises(registry):
    with pytest.raises(RegistryError):
        compute_debt([{"kernel_code": "K-Q7"}], registry=registry, named_classes=1)


# --------------------------------------------------------------------------
# R2: the coverage section
# --------------------------------------------------------------------------

def test_coverage_rows_report_the_gap_as_coverage(registry, fixture_metrics):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    rows = compute_coverage(debt, fixture_metrics, registry)
    by_attribute = {r.attribute: r for r in rows}
    iao = by_attribute["IAO definition"]
    assert iao.present == 0
    assert iao.missing == 1009
    assert iao.pct == 0.0


def test_coverage_is_one_row_not_a_thousand_findings(registry, fixture_metrics):
    debt = compute_debt(fixture_findings(), registry=registry, named_classes=1009)
    rows = compute_coverage(debt, fixture_metrics, registry)
    assert len(rows) <= 5


# --------------------------------------------------------------------------
# F2: one name, one value
# --------------------------------------------------------------------------

def test_metric_names_carry_their_scope(fixture_metrics):
    values = fixture_metrics.to_dict()
    assert "object_properties_local" in values
    assert "object_properties" not in values
    assert "classes_anchored_direct" in values
    assert "classes_anchored_transitive" in values
    assert "classes_directly_bfo_anchored" not in values


def test_the_duplicate_gate_catches_a_collision():
    payload = {"a": {"object_properties": 11}, "b": {"object_properties": 75}}
    problems = metrics_mod.duplicate_metric_gate(payload)
    assert len(problems) == 1
    assert "object_properties" in problems[0]


def test_the_duplicate_gate_passes_a_clean_bundle():
    payload = {"metrics": {"object_properties_local": 11,
                           "object_properties_with_imports": 75},
               "sections": [{"id": "a"}, {"id": "b"}]}
    assert metrics_mod.duplicate_metric_gate(payload) == []


def test_structural_keys_repeating_is_not_a_collision():
    payload = {"sections": [{"id": "a", "count": 1}, {"id": "b", "count": 2}]}
    assert metrics_mod.duplicate_metric_gate(payload) == []


def test_the_registry_file_is_the_one_shipped():
    assert DEFAULT_REGISTRY_PATH.exists()
    assert load_registry(DEFAULT_REGISTRY_PATH).version == load_registry().version
