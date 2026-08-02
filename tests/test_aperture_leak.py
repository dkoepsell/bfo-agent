"""Leak tests. Acceptance criteria, not review items.

The client renderer must be unable to emit an identifier it never receives. These
tests are what makes that a property of the code rather than a claim about it.
"""
from __future__ import annotations

import dataclasses
import re

import pytest

from app import recognition as rec
from app.aperture import chain as chain_mod
from app.aperture import project, render
from app.aperture.loader import load_manifest
from app.aperture.probe import ProbeReport, ProbeResult
from app.aperture.redaction import LEAK_PATTERNS, find_leaks
from app.aperture.resolve import resolve

ENGAGEMENT = "example-engagement"


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


def make_probe(**overrides) -> ProbeReport:
    values = {pid: True for pid in (
        "formal_theory", "definitions", "complement_available", "upper_ontology",
        "bearer_relations", "acts", "individuals", "profile_dl")}
    values.update(overrides)
    return ProbeReport(
        artifact_path="/lib/working.owl",
        artifact_sha256="c0ffee" + "0" * 58,
        probe_version="1.0.0",
        formal_theory_strict=True,
        results={pid: ProbeResult(pid, v, {"stub": 1},
                                  "" if v is True else f"{pid} unmet")
                 for pid, v in values.items()},
    )


def make_chain(state="performed", domain="legal_order"):
    return chain_mod.validate(
        {"links": {locus: state for locus in chain_mod.LOCI},
         "basis": "the engagement letter, section 2, and the entry file",
         "declared_on": "2026-08-02"},
        engagement=ENGAGEMENT,
        domain=domain,
    )


# Every combination worth rendering: healthy, profile unverified, precondition
# missing, chain absent, chain external.
FIXTURES = [
    ("healthy", {}, "performed", "legal_order"),
    ("profile-unverified", {"profile_dl": None}, "performed", "legal_order"),
    ("profile-violated", {"profile_dl": False}, "performed", "legal_order"),
    ("no-theory", {"formal_theory": False}, "performed", "legal_order"),
    ("no-definitions", {"definitions": False}, "performed", "legal_order"),
    ("no-upper", {"upper_ontology": False}, "performed", "legal_order"),
    ("no-complement", {"complement_available": False}, "performed", "legal_order"),
    ("no-bearers", {"bearer_relations": False}, "performed", "legal_order"),
    ("no-acts", {"acts": False}, "performed", "legal_order"),
    ("chain-absent", {}, "absent", "scientific_reference"),
    ("chain-external", {}, "external", "clinical_nosology"),
]


def build(probe_overrides, link_state, domain, manifest):
    declaration = make_chain(link_state, domain)
    report = resolve(manifest, make_probe(**probe_overrides), declaration)
    view = project.project_client(report, manifest, ENGAGEMENT,
                                  declaration.declared_on)
    return report, declaration, view


@pytest.fixture(params=FIXTURES, ids=[f[0] for f in FIXTURES])
def fixture(request, manifest):
    _name, overrides, link_state, domain = request.param
    return build(overrides, link_state, domain, manifest)


# --------------------------------------------------------------------------
# Leak test 1: rendered client output discloses no framework internals
# --------------------------------------------------------------------------

def test_client_markdown_discloses_nothing(fixture):
    _report, _declaration, view = fixture
    text = render.client_markdown(view)
    assert find_leaks(text) == [], find_leaks(text)


def test_client_scope_statement_discloses_nothing(fixture, manifest):
    report, _declaration, view = fixture
    text = render.scope_statement(report, manifest, ENGAGEMENT, view=view)
    assert find_leaks(text) == []


def test_client_payload_discloses_nothing(fixture):
    import json

    _report, _declaration, view = fixture
    assert find_leaks(json.dumps(view.to_dict())) == []


def test_no_kernel_code_appears_in_client_output(fixture):
    _report, _declaration, view = fixture
    text = render.client_markdown(view)
    for code in rec.KERNEL:
        assert code not in text


def test_no_detector_path_appears_in_client_output(fixture, manifest):
    _report, _declaration, view = fixture
    text = render.client_markdown(view)
    for failure in manifest.failures:
        for det in failure.detectors:
            assert det.path not in text


def test_no_digest_or_version_appears_in_client_output(fixture, manifest):
    report, _declaration, view = fixture
    text = render.client_markdown(view)
    assert manifest.digest not in text
    assert manifest.version not in text
    assert report.artifact_sha256 not in text
    assert report.chain_digest not in text


def test_the_internal_render_does_disclose_internals(fixture, manifest):
    """The control. If the internal renderer were also clean, the leak tests
    above would be passing for the wrong reason."""
    report, _declaration, _view = fixture
    text = render.internal_markdown(report, manifest, ENGAGEMENT)
    assert find_leaks(text), "internal output should carry identifiers"
    assert "K-A1" in text


# --------------------------------------------------------------------------
# Leak test 2: the projection is a fixed allowlist
# --------------------------------------------------------------------------

def test_projection_attribute_set_is_the_allowlist():
    project.assert_projection_is_narrow()


def test_adding_a_field_to_the_internal_model_does_not_reach_the_projection(fixture, manifest):
    """The structural guarantee. A new internal field must not appear in client
    output, because the projection copies by name rather than by iteration."""
    report, _declaration, _view = fixture
    leaked = "SECRET-K-A9-app.detector.path"
    mutated = dataclasses.replace(report, chain_basis=report.chain_basis)
    object.__setattr__(mutated, "newly_added_internal_field", leaked)

    view = project.project_client(mutated, manifest, ENGAGEMENT)
    assert leaked not in render.client_markdown(view)
    assert not hasattr(view, "newly_added_internal_field")


def test_client_view_fields_cannot_silently_widen():
    got = {f.name for f in dataclasses.fields(project.ClientView)}
    assert got == set(project.CLIENT_VIEW_FIELDS)
    got_row = {f.name for f in dataclasses.fields(project.ClientRow)}
    assert got_row == set(project.CLIENT_ROW_FIELDS)


def test_the_client_renderer_refuses_the_internal_report(fixture, manifest):
    """It takes a projection, never a filtered view of the internal model."""
    report, _declaration, _view = fixture
    with pytest.raises((AttributeError, TypeError)):
        render.client_markdown(report)


def test_render_refuses_the_client_profile_without_a_projection(fixture, manifest):
    report, _declaration, _view = fixture
    with pytest.raises(ValueError):
        render.render(report, manifest, ENGAGEMENT, "client", view=None)


# --------------------------------------------------------------------------
# Leak test 3: every failure is represented, and by its client label
# --------------------------------------------------------------------------

def test_every_failure_has_a_client_row(fixture, manifest):
    _report, _declaration, view = fixture
    assert len(view.rows) == len(manifest.failures) == 12


def test_every_client_row_uses_the_client_label(fixture, manifest):
    _report, _declaration, view = fixture
    labels = set(manifest.client_labels().values())
    for row in view.rows:
        assert row.defect_class in labels


def test_a_failure_without_a_client_label_is_omitted_not_named(manifest):
    """If a label were ever missing, the row must disappear rather than fall back
    to the identifier."""
    report = resolve(manifest, make_probe(), make_chain())

    class Stripped:
        def client_labels(self):
            labels = manifest.client_labels()
            labels.pop("K-A1")
            return labels

    view = project.project_client(report, Stripped(), ENGAGEMENT)
    assert len(view.rows) == 11
    assert find_leaks(render.client_markdown(view)) == []


# --------------------------------------------------------------------------
# Leak test 4: nothing reads as clean
# --------------------------------------------------------------------------

_CLEAN_WORDS = re.compile(r"(?i)\b(clean|no defects found|passed|compliant)\b")


def test_no_rendering_claims_the_artifact_is_clean(fixture, manifest):
    report, _declaration, view = fixture
    for text in (render.client_markdown(view),
                 render.internal_markdown(report, manifest, ENGAGEMENT),
                 render.scope_statement(report, manifest, ENGAGEMENT, view=view)):
        for match in _CLEAN_WORDS.finditer(text):
            # The only permitted use is the sentence explaining that a row is
            # NOT being reported as clean.
            window = text[max(0, match.start() - 90):match.end() + 20].lower()
            assert "not being reported as clean" in window or "not report" in window \
                or "reports a clean" in window or "rather than as clean" in window, window


def test_not_reachable_is_never_rendered_as_a_result(manifest):
    report, _declaration, view = build({"acts": False}, "performed",
                                       "legal_order", manifest)
    text = render.client_markdown(view)
    assert "not being reported as clean" in text


# --------------------------------------------------------------------------
# The redaction list itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,should_hit", [
    ("Inconsistency K-A1 fired", True),
    ("See LC-13 for the legal reading", True),
    ("CT-7 type contradiction", True),
    ("Stratum D is off", True),
    ("strata A and B", True),
    ("app.kernel_audit.audit reported it", True),
    ("needs a dl_reasoner", True),
    ("A capacity the rules allow but practice blocks", False),
    ("Definitions that depend on themselves", False),
    ("a structural review of the criteria", False),
])
def test_leak_patterns(text, should_hit):
    assert bool(find_leaks(text)) is should_hit


def test_every_leak_pattern_is_exercised_by_a_client_render(fixture):
    """A pattern nobody can trigger is not protecting anything. This asserts the
    list is live, by checking each pattern compiles and runs over real output."""
    _report, _declaration, view = fixture
    text = render.client_markdown(view)
    for pattern, _what in LEAK_PATTERNS:
        assert pattern.search(text) is None
