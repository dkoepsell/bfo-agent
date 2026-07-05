"""Regression tests for dsm-extraction-fix-spec.md (FIX-1/FIX-2/FIX-3, G-1..G-4).

The golden-bad fixture ``fixtures/DSM52022__1_.owl`` is the real half-done DSM
extraction; it MUST fail the gate on the three defects the spec names.
"""
from pathlib import Path

import pytest

from app import owl_checks as oc

FIXTURE = Path(__file__).parent / "fixtures" / "DSM52022__1_.owl"


# --------------------------------------------------------------- parser (X-1..X-3)
@pytest.mark.parametrize("text, expected", [
    ("not working:NeurocognitiveDisorder", {"op": "not", "cls": "working:NeurocognitiveDisorder"}),
    ("owl:complementOf working:X", {"op": "not", "cls": "working:X"}),
    ("bfo:BFO_0000054 some working:Fatigue", {"op": "some", "prop": "bfo:BFO_0000054", "filler": "working:Fatigue"}),
    ("not (BFO_0000196 some working:Contrary)", {"op": "not_some", "prop": "BFO_0000196", "filler": "working:Contrary"}),
    ("_:x BFO_0000197 BFO_0000040", {"op": "some", "prop": "BFO_0000197", "filler": "BFO_0000040"}),
    ("working:PlainClass", None),
    ("Premenstrual Dysphoric Disorder", None),
])
def test_parse_class_expression(text, expected):
    assert oc.parse_class_expression(text) == expected


# --------------------------------------------------------------- slugify (H-1)
def test_slugify_fragment():
    assert oc.slugify_fragment("Premenstrual Dysphoric Disorder") == "PremenstrualDysphoricDisorder"
    assert oc.slugify_fragment("Fatigue") == "Fatigue"
    assert oc.slugify_fragment("complementOf working:X") is None


# --------------------------------------------------------------- G-1 / G-2
def test_g1_construct_name_in_iri_flagged():
    bad = ('<rdfs:subClassOf rdf:resource="http://www.w3.org/2002/07/owl#'
           'complementOf working:NeurocognitiveDisorder"/>')
    assert any(f.code == "E_EXPR_IRI" for f in oc.check_expression_iris(bad).findings)


def test_g1_owl_vocab_not_flagged():
    assert not oc.check_expression_iris(
        '<x rdf:resource="http://www.w3.org/2002/07/owl#disjointWith"/>').findings
    assert not oc.check_expression_iris(
        '<x rdf:about="http://purl.obolibrary.org/obo/BFO_0000016"/>').findings


def test_g2_whitespace_iri_flagged():
    bad = '<owl:Class rdf:about="http://x/working#PremenstrualDysphoric Disorder"/>'
    assert any(f.code == "E_BAD_IRI" for f in oc.check_bad_iris(bad).findings)


# --------------------------------------------------------------- self-lint parity
def test_check_iri_strings_accepts_sanctioned_expressions():
    r = oc.check_iri_strings(["not working:B", "bfo:BFO_0000054 some working:Fatigue"])
    assert r.passed


def test_check_iri_strings_rejects_baked_expression():
    assert not oc.check_iri_strings(["working:[ A and not B ]"]).passed
    assert not oc.check_iri_strings(["owl:complementOf"]).passed


# --------------------------------------------------------------- WARN/INFO advisory
def test_warn_info_do_not_fail_report():
    rep = oc.Report()
    rep.add("WARN_LOST_COMPLEMENT", "x")
    rep.add("INFO_SHARED_FILLERS", "0 shared")
    assert rep.passed
    rep.add("E_BAD_IRI", "y")
    assert not rep.passed


# --------------------------------------------------------------- acceptance §6.1
@pytest.mark.skipif(not FIXTURE.exists(), reason="golden-bad fixture absent")
def test_golden_bad_fixture_fails_gate():
    rep = oc.run_all(str(FIXTURE))
    codes = {f.code for f in rep.findings}
    assert not rep.passed
    assert "E_EXPR_IRI" in codes          # the complementOf IRIs
    assert "E_BAD_IRI" in codes           # 'PremenstrualDysphoric Disorder'
    assert "WARN_LOST_COMPLEMENT" in codes  # negation lost in serialization
    assert any(f.code == "INFO_SHARED_FILLERS" for f in rep.findings)  # G-4 reported
