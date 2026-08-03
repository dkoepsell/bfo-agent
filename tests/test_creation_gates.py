"""Creation-pipeline gates: what stops a defective artifact being finalized.

The reference artifact reached ``status: finalized`` on 2026-07-30 carrying a
live thickness contradiction, zero definition coverage, thirteen unbound classes
and thirty-one undeclared IRIs. The golden expectation here is that it now fails,
and fails on the five specific gates the spec names.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest
import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

from app.library.finalize import GATES, GateReport, GateResult, Waiver, check_finalizable
from app.library.lint import lint_capacity_realization, lint_definitions
from app.mlc_anchor import LINK_IRIS, MLC_LINK, read_asserted_links, suggest, verify

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "cfr_disability"
FIXTURE = FIXTURE_DIR / "working.owl"

HEADER = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:sool="http://davidkoepsell.com/sool#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
  <owl:Ontology rdf:about="http://example.org/t"/>
"""


def graph(body: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=HEADER + body + "</rdf:RDF>\n", format="xml")
    return g


# --------------------------------------------------------------------------
# C1: the gate vocabulary
# --------------------------------------------------------------------------

def test_four_gates_cannot_be_waived():
    """An artifact failing these is not internally coherent, and no written
    reason makes it so."""
    assert {g for g, waivable in GATES.items() if not waivable} == {
        "chain.consistent", "declarations.complete",
        "imports.resolve", "reasoner.coherent"}


def test_two_gates_are_waivable():
    assert {g for g, waivable in GATES.items() if waivable} == {
        "definitions.coverage", "mlc.anchoring"}


def test_a_waiver_without_a_reason_is_not_a_waiver():
    result = GateResult("definitions.coverage", False, "", waivable=True,
                        waiver=Waiver("definitions.coverage", "  ", "drk", "2026-08-03"))
    assert result.waived is False
    assert result.blocking is True


def test_a_waiver_with_a_reason_unblocks_a_waivable_gate():
    result = GateResult("definitions.coverage", False, "", waivable=True,
                        waiver=Waiver("definitions.coverage", "frozen for review",
                                      "drk", "2026-08-03"))
    assert result.waived is True
    assert result.blocking is False


def test_a_waiver_cannot_unblock_an_unwaivable_gate():
    result = GateResult("chain.consistent", False, "", waivable=False,
                        waiver=Waiver("chain.consistent", "we disagree", "drk", "x"))
    assert result.waived is False
    assert result.blocking is True


def test_a_gate_that_could_not_run_is_not_a_gate_that_passed():
    report = GateReport("x", gates=[GateResult("chain.consistent", True)],
                        errors=["reasoner.coherent could not be checked"])
    assert report.finalizable is False


def test_the_report_reproduces_the_waiver_verbatim():
    report = GateReport("x", gates=[GateResult(
        "definitions.coverage", False, "0%", waivable=True,
        waiver=Waiver("definitions.coverage", "seeded scaffold, definitions to follow",
                      "drk", "2026-08-03"))])
    text = "\n".join(report.render())
    assert "seeded scaffold, definitions to follow" in text
    assert "drk" in text


# --------------------------------------------------------------------------
# C3 and C6: the lints
# --------------------------------------------------------------------------

def test_definition_lint_accepts_an_explicit_deferral():
    g = graph("""
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <sool:definitionDeferred>pending clinical review</sool:definitionDeferred>
      </owl:Class>
    """)
    result = lint_definitions(g)
    assert result.offenders == []
    assert result.deferred == ["http://example.org/t#Alpha"]


def test_definition_lint_flags_a_bare_class():
    result = lint_definitions(graph(
        '<owl:Class rdf:about="http://example.org/t#Alpha"/>'))
    assert result.offenders == ["http://example.org/t#Alpha"]
    assert result.passed is False


def test_definition_lint_counts_an_equivalent_class_as_defined():
    g = graph("""
      <owl:Class rdf:about="http://example.org/t#Beta"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <owl:equivalentClass rdf:resource="http://example.org/t#Beta"/>
      </owl:Class>
    """)
    assert "http://example.org/t#Alpha" not in lint_definitions(g).offenders


def test_capacity_lint_flags_an_unrealized_disposition():
    g = graph("""
      <owl:Class rdf:about="http://example.org/t#Capacity">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000016"/>
      </owl:Class>
    """)
    result = lint_capacity_realization(g)
    assert result.offenders == ["http://example.org/t#Capacity"]


def test_capacity_lint_accepts_a_realization_link():
    g = graph("""
      <owl:Class rdf:about="http://example.org/t#Process">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000015"/>
      </owl:Class>
      <owl:Class rdf:about="http://example.org/t#Capacity">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000016"/>
        <rdfs:subClassOf>
          <owl:Restriction>
            <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/BFO_0000054"/>
            <owl:someValuesFrom rdf:resource="http://example.org/t#Process"/>
          </owl:Restriction>
        </rdfs:subClassOf>
      </owl:Class>
    """)
    assert lint_capacity_realization(g).offenders == []


def test_capacity_lint_accepts_an_explicit_deferral():
    g = graph("""
      <owl:Class rdf:about="http://example.org/t#Capacity">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000016"/>
        <sool:realizationDeferred>no process modelled yet</sool:realizationDeferred>
      </owl:Class>
    """)
    assert lint_capacity_realization(g).offenders == []


def test_a_continuant_is_not_expected_to_be_realized():
    g = graph("""
      <owl:Class rdf:about="http://example.org/t#Aorta">
        <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000040"/>
      </owl:Class>
    """)
    result = lint_capacity_realization(g)
    assert result.total == 0
    assert result.passed is True


# --------------------------------------------------------------------------
# C4: anchoring is asserted, never inferred
# --------------------------------------------------------------------------

def anchored(link: str, cls: str) -> str:
    return (f'<owl:Class rdf:about="http://example.org/t#{cls}">'
            f'<sool:mlcLink rdf:resource="{LINK_IRIS[link]}"/></owl:Class>')


def test_asserted_links_are_read_not_guessed():
    g = graph(anchored("authority", "Agency") + anchored("act", "Determination"))
    links = read_asserted_links(g)
    assert links["authority"] == ("http://example.org/t#Agency",)
    assert links["act"] == ("http://example.org/t#Determination",)
    assert links["remedy"] == ()


def test_an_unasserted_link_is_reported_absent_not_inferred():
    """The auditor never manufactures domain structure."""
    g = graph(anchored("authority", "Agency"))
    v = verify(g)
    assert "remedy" in v.empty_links
    assert v.total_assertions == 1
    assert "Nothing here is inferred" in v.to_dict()["note"]


def test_a_class_on_two_links_without_justification_is_reported():
    g = graph(
        '<owl:Class rdf:about="http://example.org/t#Thing">'
        f'<sool:mlcLink rdf:resource="{LINK_IRIS["criteria"]}"/>'
        f'<sool:mlcLink rdf:resource="{LINK_IRIS["effect"]}"/>'
        '</owl:Class>')
    v = verify(g)
    assert v.multi_linked["http://example.org/t#Thing"] == ("criteria", "effect")
    assert v.unjustified_multi == ("http://example.org/t#Thing",)


def test_a_justified_multi_link_is_accepted():
    g = graph(
        '<owl:Class rdf:about="http://example.org/t#Thing">'
        f'<sool:mlcLink rdf:resource="{LINK_IRIS["criteria"]}"/>'
        f'<sool:mlcLink rdf:resource="{LINK_IRIS["effect"]}"/>'
        '<sool:mlcLinkJustification>the norm and the status it confers'
        '</sool:mlcLinkJustification></owl:Class>')
    assert verify(g).unjustified_multi == ()


def test_a_suggestion_is_a_proposal_not_a_binding():
    proposal = suggest({"authority": ["A"], "criteria": ["B", "C"]}, "abc")
    assert proposal["kind"] == "mlc_link_proposal"
    assert proposal["proposal_count"] == 3
    assert all(p["accepted"] is False for p in proposal["proposals"])
    assert "never a binding" in proposal["note"].lower() or \
        "not a binding" in proposal["note"].lower()


@pytest.mark.skipif(not FIXTURE.exists(), reason="reference fixture not present")
def test_the_fixture_asserts_no_links_at_all():
    """Which is why the adapter was smearing: it was asked an unanswerable
    question and answered anyway."""
    v = verify(str(FIXTURE))
    assert v.total_assertions == 0
    assert len(v.empty_links) == 7


@pytest.mark.skipif(not FIXTURE.exists(), reason="reference fixture not present")
def test_the_fixture_has_no_definitions():
    assert lint_definitions(str(FIXTURE)).coverage_pct == 0.0


# --------------------------------------------------------------------------
# The golden expectation: the reference artifact is not finalizable
# --------------------------------------------------------------------------

class FakeRegistry:
    def __init__(self, root: Path, name: str):
        self._root, self._name = root, name
        self._manifest = json.loads((root / name / "manifest.json").read_text())

    def manifest(self, name):
        return dict(self._manifest)

    def get(self, name):
        class M:
            working_path = str(self._root / self._name / "working.owl")
        return M()

    def recognition_profile(self, name):
        from app import recognition as rec

        block = self._manifest.get("recognition") or {}
        profile = rec.profile_for(block.get("domain"))
        return {"domain": profile.key,
                "act_thickness": block.get("act_thickness") or profile.act_thickness,
                "repair": block.get("repair") or profile.repair}

    def chain_block(self, name):
        return dict((self._manifest.get("recognition") or {}).get("chain") or {})

    def chain_status(self, name):
        from dataclasses import replace

        from app import recognition as rec
        from app.aperture import chain as chain_mod

        declared = self.recognition_profile(name)
        profile = replace(rec.profile_for(declared["domain"]),
                          act_thickness=declared["act_thickness"],
                          repair=declared["repair"])
        return chain_mod.status(self.chain_block(name), engagement=name,
                                domain=profile.key, profile=profile)


@pytest.fixture(scope="module")
def fixture_gates():
    if not FIXTURE.exists():
        pytest.skip("reference fixture not present")
    tmp = Path(tempfile.mkdtemp())
    try:
        lib = tmp / "CFR-DisabilityRegs"
        lib.mkdir(parents=True)
        shutil.copy(FIXTURE, lib / "working.owl")
        shutil.copy(FIXTURE_DIR / "manifest.json", lib / "manifest.json")
        yield check_finalizable(FakeRegistry(tmp, "CFR-DisabilityRegs"),
                                "CFR-DisabilityRegs", run_reasoner=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_reference_artifact_is_not_finalizable(fixture_gates):
    assert fixture_gates.finalizable is False


def test_it_fails_the_five_gates_the_spec_names(fixture_gates):
    failing = {g.id for g in fixture_gates.gates if not g.passed}
    expected = {"chain.consistent", "declarations.complete", "imports.resolve",
                "definitions.coverage", "mlc.anchoring"}
    assert expected <= failing, f"expected {expected}, failing {failing}"


def test_the_chain_failure_names_the_thickness_contradiction(fixture_gates):
    gate = next(g for g in fixture_gates.gates if g.id == "chain.consistent")
    assert "thickness_contradiction" in gate.detail


def test_the_definition_failure_names_the_coverage(fixture_gates):
    gate = next(g for g in fixture_gates.gates if g.id == "definitions.coverage")
    assert "0 of 1009" in gate.detail


def test_the_anchoring_failure_says_links_are_asserted_not_inferred(fixture_gates):
    gate = next(g for g in fixture_gates.gates if g.id == "mlc.anchoring")
    assert "never inferred" in gate.detail


def test_every_blocking_gate_is_unwaivable_or_unwaived(fixture_gates):
    for gate in fixture_gates.blocking:
        assert not gate.waived


# --------------------------------------------------------------------------
# C5: patterns that cannot be instantiated incomplete
# --------------------------------------------------------------------------

def test_a_threshold_cannot_be_authored_without_its_bearer():
    """The shape that produced all five dependence findings."""
    from app.patterns import PatternError, instantiate

    with pytest.raises(PatternError) as e:
        instantiate("quantitative_threshold", value="20/200", unit="ratio")
    assert "bearer_class" in str(e.value)
    assert "depending on nothing" in str(e.value)


def test_a_complete_threshold_instantiates():
    from app.patterns import instantiate

    out = instantiate("quantitative_threshold", bearer_class="Eye",
                      measured_quality="VisualAcuity", value="20/200",
                      unit="ratio", norm_class="Listing2_02")
    assert out["pattern"] == "quantitative_threshold"
    assert out["fields"]["bearer_class"] == "Eye"


@pytest.mark.parametrize("pattern_id", [
    "quantitative_threshold", "duration_requirement", "exclusion_clause",
    "role", "evidentiary_source",
])
def test_every_pattern_refuses_an_empty_instantiation(pattern_id):
    from app.patterns import PatternError, instantiate

    with pytest.raises(PatternError):
        instantiate(pattern_id)


def test_an_unknown_pattern_is_refused():
    from app.patterns import PatternError, instantiate

    with pytest.raises(PatternError):
        instantiate("vibes")


def test_the_pattern_lint_names_the_pattern():
    from app.patterns import lint_patterns

    g = graph("""
      <owl:Class rdf:about="http://example.org/t#TwentyPoundWeightLimit">
        <rdfs:label>20-pound weight limit</rdfs:label>
      </owl:Class>
    """)
    result = lint_patterns(g)
    assert result["smell_count"] == 1
    assert result["smells"][0]["pattern"] == "quantitative_threshold"
    assert "bearer_class" in result["smells"][0]["why"]


# --------------------------------------------------------------------------
# C8: provenance is captured at creation
# --------------------------------------------------------------------------

def test_a_source_text_digest_is_recorded():
    from app.registry import _source_text_sha256

    assert _source_text_sha256(None) is None
    assert _source_text_sha256("some text").startswith("sha256:")


def test_a_regulatory_name_without_a_source_text_is_flagged():
    from app.report.bundle import _implied_source

    assert "20 CFR 404" in _implied_source("CFR-DisabilityRegs", "20CFR404")
    assert _implied_source("GeometryofTheGood", "ethics") == ""
