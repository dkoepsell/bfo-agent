"""The pinned regression fixture: CFR-DisabilityRegs at sha256 b67f2844...

This is the artifact whose bundle prompted the remediation spec. Its numbers are
the acceptance criteria, so the assertions here are deliberately concrete.

One golden value differs from the spec and it is worth stating plainly. The spec
gives ``cd_core == 11`` as the R1 acceptance, which is 5 K-B3 plus 5 K-C3 plus 1
K-D3. But R4, landed after R1 in the spec's own execution order, establishes that
four or five of those K-B3 hits were the label heuristic matching the substring
"nec" inside Cell Necrosis, Connective Tissue and Felony-Connected. Once that
detector is structural the K-B3 contribution is zero and the artifact reports
``silent_by_principle`` for it, which the spec itself calls "the honest result
for this fixture". So 11 is the correct stage-2 checkpoint and is asserted
against stage-2 inputs in test_kernel_registry.py; the end-to-end figure after
stage 3 is lower, and that drop is the fix working rather than a regression.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cfr_disability"
FIXTURE = FIXTURE_DIR / "working.owl"
FIXTURE_SHA256 = "b67f284415060f4057ed5426f443307871e266a37e38d701549b8ab54fcecde8"
BFO = Path(__file__).parent.parent / "ontology" / "bfo.owl"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="reference fixture not present")


class FakeManager:
    def __init__(self, path: Path):
        self.working_path = str(path)

    def stats(self) -> dict:
        return {}


class FakeRegistry:
    """Mirrors the real registry's resolution rules for the fields under test."""

    def __init__(self, root: Path, name: str):
        self._root = root
        self._name = name
        self._manifest = json.loads((root / name / "manifest.json").read_text())

    def manifest(self, name):
        return dict(self._manifest)

    def recognition_profile(self, name):
        from app import recognition as rec

        block = self._manifest.get("recognition") or {}
        profile = rec.profile_for(block.get("domain"))
        return {
            "domain": profile.key,
            "act_thickness": block.get("act_thickness") or profile.act_thickness,
            "repair": block.get("repair") or profile.repair,
        }

    def chain_block(self, name):
        return dict((self._manifest.get("recognition") or {}).get("chain") or {})

    def chain_status(self, name):
        from app import recognition as rec
        from app.aperture import chain as chain_mod
        from dataclasses import replace

        declared = self.recognition_profile(name)
        profile = replace(rec.profile_for(declared["domain"]),
                          act_thickness=declared["act_thickness"],
                          repair=declared["repair"])
        return chain_mod.status(self.chain_block(name), engagement=name,
                                domain=profile.key, profile=profile)


@pytest.fixture(scope="module")
def report():
    from app.report.bundle import build_report

    tmp = Path(tempfile.mkdtemp())
    try:
        lib = tmp / "CFR-DisabilityRegs"
        lib.mkdir(parents=True)
        shutil.copy(FIXTURE, lib / "working.owl")
        shutil.copy(FIXTURE_DIR / "manifest.json", lib / "manifest.json")
        yield build_report(
            "CFR-DisabilityRegs",
            FakeRegistry(tmp, "CFR-DisabilityRegs"),
            FakeManager(lib / "working.owl"),
            run_reasoner=False,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def debt(report):
    return (report.data("contradiction_debt") or {}).get("contradiction_debt") or {}


# --------------------------------------------------------------------------

def test_the_fixture_is_pinned():
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256


def test_schema_is_two_point_zero(report):
    assert report.report_schema == "2.0"
    assert report.kernel_registry_version
    assert report.kernel_registry_digest.startswith("sha256:")


# --- R1: debt no longer counts the ontology's size --------------------------

def test_the_coverage_predicate_is_excluded_at_full_population(debt):
    """1009, not the 25 that happened to be sampled."""
    assert debt["excluded_coverage"]["K-A3b"] == 1009


def test_the_coverage_predicate_is_not_in_the_debt(debt):
    assert "K-A3b" not in debt["per_type"]


def test_debt_is_no_longer_the_class_count(debt):
    """It was 1020 on an artifact with 1009 classes."""
    assert debt["cd_core"] < 20
    assert debt["cd_core"] != 1020.0


def test_debt_and_the_reasoner_no_longer_contradict_each_other(debt):
    """The reference bundle reported 1020 debt beside a consistent, coherent,
    zero-unsatisfiable reasoner result."""
    assert debt["cd_core"] == float(sum(debt["per_type"].values()))


def test_the_valuable_dependence_findings_survive(debt):
    """The five quantitative thresholds are the substantively useful result."""
    assert debt["per_type"].get("K-C3") == 5


def test_cd_per_class_is_emitted(debt):
    assert debt["cd_per_class"] == round(debt["cd_core"] / 1009, 4)


def test_weights_are_declared_uncalibrated(debt):
    assert debt["calibrated"] is False
    assert debt["weights_source"] == "uniform_fallback"


def test_the_debt_is_never_rendered_bare(report):
    line = (report.data("contradiction_debt") or {}).get("render_line", "")
    assert "typed count" in line and "uncalibrated" in line


# --- R2: coverage is its own section ----------------------------------------

def test_coverage_is_reported_as_coverage(report):
    rows = (report.data("coverage") or {}).get("rows") or []
    iao = next(r for r in rows if r["attribute"] == "IAO definition")
    assert iao["missing"] == 1009
    assert iao["coverage_pct"] == 0.0


def test_coverage_renders_above_the_debt(report):
    from app.report.bundle import report_markdown

    text = report_markdown(report)
    assert text.index("## Coverage") < text.index("## Contradiction debt")


def test_the_coverage_line_follows_the_debt_figure(report):
    from app.report.bundle import report_markdown

    text = report_markdown(report)
    assert "not counted as debt" in text


# --- R4: the residual detector ----------------------------------------------

def test_the_residual_detector_yields_zero_or_one_never_five(debt):
    assert debt["per_type"].get("K-B3", 0) in (0, 1)


# --- R3 and R7: binding gates and the status vocabulary ---------------------

def test_the_binding_section_is_unreliable(report):
    assert report.section("binding").status == "unreliable"


def test_all_four_binding_gates_fail(report):
    section = report.section("binding")
    assert len(section.gates) == 4
    assert len(section.gates_failed) == 4


def test_the_dispersion_figures_are_present(report):
    d = (report.data("binding") or {}).get("dispersion") or {}
    assert d["bindings_per_class_mean"] > 1.5
    assert d["jaccard"]
    assert d["top_multiplicity"]


def test_the_matrix_is_rendered_when_a_gate_trips(report):
    from app.report.bundle import report_markdown

    text = report_markdown(report)
    assert "Link overlap (Jaccard)" in text
    assert "Classes bound to the most links" in text


def test_the_scope_section_records_its_cause(report):
    section = report.section("scope")
    assert section.status == "skipped"
    assert section.skipped_because["section"] == "chain"
    assert section.skipped_because["reason"] == "thickness_contradiction"


def test_the_causal_chain_is_printed(report):
    from app.report.bundle import report_markdown

    text = report_markdown(report)
    assert "Why a section did not run" in text
    assert "downstream consequence" in text


# --- R5, R6, R8: hygiene ----------------------------------------------------

def test_the_duplicate_metric_check_passes(report):
    from app.report.metrics import duplicate_metric_gate

    assert duplicate_metric_gate(report.to_dict()) == []


def test_metrics_are_emitted_once_at_top_level(report):
    assert report.metrics["named_classes_local"] == 1009
    assert report.metrics["object_properties_local"] == 11
    assert "object_properties" not in report.metrics


def test_the_readme_matches_the_write_manifest(report):
    import io
    import zipfile

    from app.report.bundle import write_bundle

    with zipfile.ZipFile(io.BytesIO(write_bundle(report, "<rdf:RDF/>"))) as z:
        names = set(z.namelist())
        readme = z.read("README.txt").decode()
    assert "scope-client.md" not in readme
    for name in names:
        assert name in readme


def test_provenance_is_stated_above_the_fold(report):
    from app.report.bundle import report_markdown

    assert report.provenance.derivation == "seeded_bootstrap"
    assert report.provenance.seeded_from == "GeometryofTheGood"
    assert report.provenance.source_text is None
    text = report_markdown(report)
    assert text.index("**Provenance:**") < text.index("## Sections")
    assert "nothing in this artifact was derived from that text" in text


def test_the_name_implies_a_source_that_was_never_read(report):
    assert report.provenance.name_implies_source is True
    assert "CFR" in report.provenance.implied_source


# --- the bundle as a whole --------------------------------------------------

def test_no_section_reports_ok_while_its_gates_fail(report):
    for section in report.sections:
        if section.gates_failed:
            assert section.status != "ok", section.id


def test_every_finding_code_is_registered(report):
    from app.kernel import load_registry

    registry = load_registry()
    audit = report.data("recognition_audit") or {}
    registry.validate_codes(f.get("kernel_code") for f in audit.get("findings") or ())
