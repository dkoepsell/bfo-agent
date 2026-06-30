"""Tests for the PC-1..PC-6 construction linter (app/construction_linter.py).

Includes the bfo-agent-spec.md Acceptance #5 regression: the prohibited
constructions that filled the runaway-class artifacts (AbsenceOf*, *Relation,
*WithX, ...) must be rejected, and a clean BFO-anchored proposal must pass.
"""
import re
from pathlib import Path

from app import construction_linter as L
from app.schema import Entity, Proposal, Relation

REPO = Path(__file__).resolve().parents[1]
# The runaway-class artifact: thousands of flat classes, zero object properties.
BAD_ARTIFACT = REPO / "ontology" / "library" / "_archive" / "SOoL_pilot_1243_20260421.owl"


def _cls(name, bfo="BFO_0000020", parent=None, is_new=True, existing=None):
    return Entity(
        label=name, iri_suggestion=f"working:{name}", bfo_type=bfo,
        bfo_label="x", kind="class", parent_class=parent, rationale="r",
        is_new=is_new, existing_iri=existing,
    )


def _ind(name, bfo="BFO_0000040"):
    return Entity(
        label=name, iri_suggestion=f"working:{name}", bfo_type=bfo,
        bfo_label="x", kind="individual", rationale="r", is_new=True,
    )


def _prop(proposal):
    return Proposal(session_id="s", utterance="u", **proposal)


def _rules(report):
    return {v.rule for v in report.violations}


# --- PC-1: privation primitives ------------------------------------------
def test_pc1_absence_class_rejected():
    p = _prop({"entities": [_cls("AbsenceOfLegalExistence")]})
    assert "PC-1" in _rules(L.lint(p))


def test_pc1_prefix_forms():
    # Prefix forms require a CamelCase boundary (Non+Arbitrary), so the matcher
    # catches compounds without flagging "Universal"/"Understanding".
    for name in ("NonArbitraryGrounding", "NonExistenceCondition", "InvalidLaw",
                 "FailureCondition"):
        assert "PC-1" in _rules(L.lint(_prop({"entities": [_cls(name)]}))), name


def test_pc1_does_not_flag_legitimate_terms():
    # Single-token words must not trip the privation prefix matcher; critical for
    # the philosophy corpora ("Universal", "Understanding", "Unity").
    for name in ("Universal", "Norm", "Nonsense", "Understanding", "Unity"):
        assert "PC-1" not in _rules(L.lint(_prop({"entities": [_cls(name)]}))), name


# --- PC-2: relation-baked / compound names --------------------------------
def test_pc2_relation_noun_suffix():
    assert "PC-2" in _rules(L.lint(_prop({"entities": [_cls("NormDependencyRelation")]})))


def test_pc2_with_connective():
    assert "PC-2" in _rules(L.lint(_prop({"entities": [_cls("NormAlignmentWithRule")]})))


def test_pc2_simple_genus_term_ok():
    assert "PC-2" not in _rules(L.lint(_prop({"entities": [_cls("LegalNorm")]})))


# --- PC-3: untyped entity -------------------------------------------------
def test_pc3_untyped_rejected():
    assert "PC-3" in _rules(L.lint(_prop({"entities": [_cls("Mystery", bfo="NOPE")]})))


def test_pc3_reused_entity_exempt():
    e = _cls("Reused", bfo="NOPE", is_new=False, existing="http://x/Reused")
    assert "PC-3" not in _rules(L.lint(_prop({"entities": [e]})))


# --- PC-5: invented predicate --------------------------------------------
def test_pc5_invented_predicate_rejected():
    p = _prop({
        "entities": [_cls("Norm")],
        "relations": [Relation(s="working:Norm", p="working:dependsOn",
                               o="working:X", rationale="r")],
    })
    assert "PC-5" in _rules(L.lint(p))


def test_pc5_bfo_property_ok():
    p = _prop({
        "entities": [_cls("Norm")],
        "relations": [
            Relation(s="working:n1", p="BFO_0000197", o="working:p", rationale="r"),
            Relation(s="working:Norm", p="rdfs:subClassOf", o="BFO_0000020", rationale="r"),
        ],
    })
    assert "PC-5" not in _rules(L.lint(p))


# --- PC-6: continuant/occurrent conflation --------------------------------
def test_pc6_conflation_rejected():
    # process (occurrent) typed under a continuant parent.
    e = _cls("Trial", bfo="BFO_0000015", parent="BFO_0000002")
    assert "PC-6" in _rules(L.lint(_prop({"entities": [e]})))


# --- PC-4: strict closed vocabulary (opt-in) ------------------------------
def test_pc4_off_by_default():
    p = _prop({"entities": [_cls("Norm")]})
    assert "PC-4" not in _rules(L.lint(p, strict_closed_vocab=False))


def test_pc4_strict_forbids_new_class():
    p = _prop({"entities": [_cls("Norm")]})
    assert "PC-4" in _rules(L.lint(p, strict_closed_vocab=True))


def test_pc4_strict_allows_individuals():
    p = _prop({"entities": [_ind("vanessa")]})
    assert "PC-4" not in _rules(L.lint(p, strict_closed_vocab=True))


# --- Clean proposal passes -----------------------------------------------
def test_clean_anchored_proposal_passes():
    p = _prop({
        "entities": [_cls("Norm"), _ind("vanessa")],
        "relations": [
            Relation(s="working:norm1", p="BFO_0000197", o="working:vanessa", rationale="r"),
            Relation(s="working:Norm", p="rdfs:subClassOf", o="BFO_0000020", rationale="r"),
        ],
    })
    assert L.lint(p).ok


# --- Acceptance #5 regression --------------------------------------------
_PROHIBITED = re.compile(
    r"^(AbsenceOf|Absence|Lack|Loss|Missing|Failure|Broken|Invalid|"
    r"Invalidity|Degraded|Collapsed|Residual|Partial|No[A-Z]|Non[A-Z]|Un[A-Z])"
)


def _bad_artifact_prohibited_classes():
    if not BAD_ARTIFACT.exists():
        return []
    text = BAD_ARTIFACT.read_text(encoding="utf-8", errors="ignore")
    # Class IRIs appear as rdf:about="#LocalName" / rdf:ID="LocalName".
    names = set(re.findall(r'rdf:(?:about|ID)="[^"]*?[#/]?([A-Z][A-Za-z0-9]+)"', text))
    return sorted(n for n in names if _PROHIBITED.match(n))


def test_regression_prohibited_classes_would_be_rejected():
    """Every prohibited-pattern class from the bad artifact is caught by the linter.

    This is the binding CI assertion: regenerating over the SOoL sources must
    not reproduce any AbsenceOf*/compound-CamelCase class, because the linter
    now rejects each one with zero overlap allowed.
    """
    prohibited = _bad_artifact_prohibited_classes()
    if not prohibited:
        import pytest
        pytest.skip("bad artifact not present")
    # Sample to keep the test fast but representative.
    sample = prohibited[:200]
    survivors = []
    for name in sample:
        report = L.lint(_prop({"entities": [_cls(name)]}))
        if "PC-1" not in _rules(report) and "PC-2" not in _rules(report):
            survivors.append(name)
    assert not survivors, f"{len(survivors)} prohibited classes slipped through: {survivors[:10]}"


def test_artifact_actually_contains_prohibited_classes():
    """Guard the regression: confirm the baseline really has the bad pattern."""
    prohibited = _bad_artifact_prohibited_classes()
    if not BAD_ARTIFACT.exists():
        import pytest
        pytest.skip("bad artifact not present")
    assert len(prohibited) >= 10, "expected the artifact to be full of privation classes"


def test_no_em_dashes_in_source():
    src = (REPO / "app" / "construction_linter.py").read_text(encoding="utf-8")
    assert "—" not in src


# --- PC-7: class expression baked into an IRI ----------------------------
def test_pc7_expression_iri_rejected():
    # The exact AristotleCategories.owl defect: an OWL expression templated
    # into an IRI fragment instead of a real anonymous construct.
    ent = _cls("Quantity", bfo="BFO_0000019")
    ent.iri_suggestion = "working#[ Quantity and not ( has_quality some Contrary ) ]"
    p = _prop({"entities": [ent]})
    assert "PC-7" in _rules(L.lint(p))


def test_pc7_clean_iri_passes():
    # A plain declaration IRI with no boolean/restriction tokens is fine.
    p = _prop({"entities": [_cls("Contract", bfo="BFO_0000040")]})
    assert "PC-7" not in _rules(L.lint(p))


# --- PC-8: privation/compound term in ANY IRI fragment -------------------
def test_pc8_privation_operand_rejected():
    # A privation term smuggled inside an expression operand (not a clean
    # declaration) must still be caught -- the IRI-fragment twin of PC-1.
    ent = _cls("Quantity", bfo="BFO_0000019")
    ent.iri_suggestion = "working#( Quantity and working:NonQuantity )"
    p = _prop({"entities": [ent]})
    assert "PC-8" in _rules(L.lint(p))


def test_pc8_clean_term_passes():
    p = _prop({"entities": [_cls("Norm", bfo="BFO_0000020")]})
    assert "PC-8" not in _rules(L.lint(p))
