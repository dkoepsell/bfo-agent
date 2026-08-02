"""The kernel manifest, and the guarantees the loader is supposed to enforce.

The manifest exists to stop drift between the published kernel and what the
resolver actually consumes. Most of what follows is that pinning: if
app/recognition.py and app/aperture/manifest.yaml ever disagree, these fail.
"""
from __future__ import annotations

import textwrap

import pytest
import yaml

from app import recognition as rec
from app.aperture.loader import (
    DEFAULT_MANIFEST_PATH,
    INSTRUMENT_FROM_RECOGNITION,
    ManifestError,
    load_manifest,
)
from app.aperture.redaction import find_leaks
from app.aperture.schema import STRATUM_ORDER


@pytest.fixture(scope="module")
def loaded():
    return load_manifest()


def _write(tmp_path, data: dict):
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.safe_dump(data, sort_keys=False))
    return p


def _minimal(**failure_overrides) -> dict:
    """A one-failure manifest that loads cleanly, for negative tests to spoil."""
    failure = {
        "id": "K-A1",
        "stratum": "A",
        "loci": ["criteria"],
        "instrument": "dl_reasoner",
        "detectors": [{"path": "app.kernel_audit.audit", "instrument": "structural"}],
        "requires": [],
        "client_label": "Rules that cannot all be satisfied at once",
    }
    failure.update(failure_overrides)
    return {
        "version": "1.0.0",
        "strata": [
            {"id": "A", "precondition": "formal_theory", "gated_by_chain": False},
            {"id": "B", "precondition": "definitions", "gated_by_chain": False},
            {"id": "C", "precondition": "upper_ontology", "gated_by_chain": False},
            {"id": "D", "precondition": "acts", "gated_by_chain": True},
        ],
        "failures": [failure],
    }


# --------------------------------------------------------------------------
# The shipped manifest loads and agrees with the published kernel
# --------------------------------------------------------------------------

def test_shipped_manifest_loads(loaded):
    assert loaded.path == DEFAULT_MANIFEST_PATH
    assert loaded.version == "1.0.0"
    assert loaded.digest.startswith("sha256:")
    assert len(loaded.failures) == 12


def test_ids_match_the_published_kernel(loaded):
    assert set(loaded.ids()) == set(rec.KERNEL)


def test_strata_match_the_published_kernel(loaded):
    for f in loaded.failures:
        assert f.stratum == rec.KERNEL[f.id].stratum, f.id


def test_primary_instrument_matches_the_published_kernel(loaded):
    """The manifest's instrument is the principled primary one, so it must be the
    first entry of the kernel's instrument tuple after translation."""
    for f in loaded.failures:
        expected = INSTRUMENT_FROM_RECOGNITION[rec.KERNEL[f.id].instruments[0].value]
        assert f.instrument == expected, f.id


def test_partial_flag_matches_the_published_kernel(loaded):
    for f in loaded.failures:
        assert f.instrument_partial == rec.KERNEL[f.id].partial, f.id


def test_loci_are_real_chain_loci(loaded):
    valid = {locus.value for locus in rec.Locus}
    for f in loaded.failures:
        assert set(f.loci) <= valid, f.id
        assert f.loci, f.id


def test_loci_cover_the_typology_tables(loaded):
    """Every locus a primitive carries in CT_TYPES or FR5_ALIASES must be one the
    manifest admits, otherwise the resolver could gate away a defect the typology
    says can occur there."""
    from_tables: dict[str, set[str]] = {}
    for table in (rec.CT_TYPES, rec.FR5_ALIASES):
        for typed in table.values():
            seen = from_tables.setdefault(typed.kernel, set())
            seen.add(typed.locus.value)
            if typed.locus_to is not None:
                seen.add(typed.locus_to.value)
    for code, loci in from_tables.items():
        assert loci <= set(loaded.by_id(code).loci), code


def test_default_locus_is_admitted(loaded):
    """The locus recognition_audit stamps on a finding must be one the manifest
    admits for that primitive, or a real finding would land out of scope."""
    from app import recognition_audit

    for code, locus in recognition_audit._DEFAULT_LOCUS.items():
        assert locus.value in loaded.by_id(code).loci, code


def test_declared_detectors_are_importable(loaded):
    """A dotted path that does not resolve is a manifest defect, not a runtime
    surprise during a resolution."""
    import importlib

    for f in loaded.failures:
        for det in f.detectors:
            module_path, _, attr = det.path.rpartition(".")
            module = importlib.import_module(module_path)
            assert hasattr(module, attr), det.path
            assert callable(getattr(module, attr)), det.path


def test_detectors_agree_with_recognition_audit_instrumentation(loaded):
    """A primitive this system instruments must declare a detector, and one it
    does not instrument must not. Silent disagreement here is what would let a
    zero count read as a measurement."""
    from app import recognition_audit as ra

    instrumented = (
        set(ra.INSTRUMENTED_STRUCTURAL)
        | set(ra.INSTRUMENTED_REASONER)
        | set(ra.INSTRUMENTED_STRATUM_D)
    )
    for f in loaded.failures:
        assert bool(f.detectors) == (f.id in instrumented), f.id


def test_the_two_human_judgment_rows_are_the_expected_ones(loaded):
    """Equivocation is a reading rather than a pattern; falsification needs
    world-facing evidence the artifact does not contain. Both stay in scope."""
    human = {f.id for f in loaded.failures if f.is_human}
    assert human == {"K-B2", "K-D1"}
    for code in human:
        assert loaded.is_human(code) is True


# --------------------------------------------------------------------------
# Derived preconditions
# --------------------------------------------------------------------------

def test_preconditions_are_cumulative_and_ordered(loaded):
    expected = {
        "A": ("formal_theory",),
        "B": ("formal_theory", "definitions"),
        "C": ("formal_theory", "definitions", "upper_ontology"),
        "D": ("formal_theory", "definitions", "upper_ontology", "acts"),
    }
    for f in loaded.failures:
        base = expected[f.stratum]
        got = loaded.preconditions_for(f.id)
        assert got[: len(base)] == base, f.id
        assert set(got) == set(base) | set(f.requires), f.id


def test_only_the_two_stratum_free_preconditions_appear_as_extras(loaded):
    """complement_available and bearer_relations are the probe values with no
    stratum of their own, so they are the only legitimate per-failure extras."""
    extras = {r for f in loaded.failures for r in f.requires}
    assert extras == {"complement_available", "bearer_relations"}
    assert loaded.by_id("K-B3").requires == ("complement_available",)
    assert loaded.by_id("K-C3").requires == ("bearer_relations",)


def test_only_stratum_d_is_gated_by_chain(loaded):
    for f in loaded.failures:
        assert loaded.gated_by_chain(f.id) == (f.stratum == "D"), f.id


# --------------------------------------------------------------------------
# client_label is validated at load, not at render
# --------------------------------------------------------------------------

def test_shipped_client_labels_disclose_nothing(loaded):
    for f in loaded.failures:
        assert find_leaks(f.client_label) == [], f.id


def test_client_labels_are_present_and_distinct(loaded):
    labels = loaded.client_labels()
    assert set(labels) == set(loaded.ids())
    assert len(set(labels.values())) == len(labels)


@pytest.mark.parametrize(
    "label",
    [
        "Inconsistency (K-A1)",
        "Authority Inflation, LC-1",
        "Disjointness failure CT-1",
        "A stratum A defect",
        "Reported by app.kernel_audit.audit",
        "Needs a dl_reasoner",
    ],
)
def test_leaky_client_label_fails_the_load(tmp_path, label):
    p = _write(tmp_path, _minimal(client_label=label))
    with pytest.raises(ManifestError) as e:
        load_manifest(p)
    assert "client_label" in str(e.value)


def test_empty_client_label_fails_the_load(tmp_path):
    p = _write(tmp_path, _minimal(client_label="   "))
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_ordinary_english_starting_with_a_stratum_letter_is_allowed(tmp_path):
    """The regex targets stratum references, not any word beginning with A."""
    p = _write(tmp_path, _minimal(
        client_label="A capacity the rules allow but practice blocks"))
    assert load_manifest(p).failures[0].client_label.startswith("A capacity")


# --------------------------------------------------------------------------
# Other load-time defects
# --------------------------------------------------------------------------

def test_missing_file_raises_manifest_error(tmp_path):
    with pytest.raises(ManifestError) as e:
        load_manifest(tmp_path / "nope.yaml")
    assert "cannot read manifest" in str(e.value)


def test_malformed_yaml_raises_manifest_error(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(textwrap.dedent("""
        version: "1.0.0"
        strata: [
    """))
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_strata_out_of_order_fails_the_load(tmp_path):
    data = _minimal()
    data["strata"].reverse()
    with pytest.raises(ManifestError) as e:
        load_manifest(_write(tmp_path, data))
    assert "strata must be exactly" in str(e.value)


def test_duplicate_failure_id_fails_the_load(tmp_path):
    data = _minimal()
    data["failures"].append(dict(data["failures"][0]))
    with pytest.raises(ManifestError) as e:
        load_manifest(_write(tmp_path, data))
    assert "duplicate failure id" in str(e.value)


def test_unknown_precondition_fails_the_load(tmp_path):
    p = _write(tmp_path, _minimal(requires=["vibes"]))
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_unknown_locus_fails_the_load(tmp_path):
    p = _write(tmp_path, _minimal(loci=["adjudicator"]))
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_empty_loci_fails_the_load(tmp_path):
    p = _write(tmp_path, _minimal(loci=[]))
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_non_dotted_detector_path_fails_the_load(tmp_path):
    p = _write(tmp_path, _minimal(
        detectors=[{"path": "kernel_audit", "instrument": "structural"}]))
    with pytest.raises(ManifestError) as e:
        load_manifest(p)
    assert "dotted callable" in str(e.value)


def test_unknown_field_fails_the_load(tmp_path):
    """extra=forbid, so a typo in the manifest is caught rather than ignored."""
    p = _write(tmp_path, _minimal(detecters=[]))
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_manifest_is_frozen(loaded):
    """The resolver must not be able to edit the manifest it was handed."""
    with pytest.raises(Exception):
        loaded.failures[0].loci = ("act",)


def test_digest_changes_with_content(tmp_path):
    a = load_manifest(_write(tmp_path, _minimal()))
    b = load_manifest(_write(tmp_path, _minimal(client_label="Something else entirely")))
    assert a.digest != b.digest


def test_strata_constant_matches_the_manifest(loaded):
    assert tuple(s.id for s in loaded.manifest.strata) == STRATUM_ORDER
