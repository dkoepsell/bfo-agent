"""bfo-agent-spec.md acceptance regressions (§10) + determinism (MC-3).

Two halves:

* FIXTURE-BACKED (§10.5/§10.6): the validators MUST flag the defects in the two
  known-bad artifacts, proving the gate would have blocked them. Drop the
  fixtures in ``tests/corpus/`` to activate:
      - SOOL_autofixed.owl     (6,881 flat classes, the disaster the spec exists
                                to prevent) -> privation/anti-pattern terms caught
      - AristotleCategories.owl (refurbished agent output) -> expression-IRIs caught
  Absent fixtures skip (never silently pass).

* FIXTURE-FREE: determinism (MC-3) and self-lint == gate parity, which need no
  external artifacts and always run.
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app import construction_linter, gate_client, owl_checks
from app import stable_iri as si

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "tests" / "corpus"
SOOL_AUTOFIXED = CORPUS / "SOOL_autofixed.owl"
ARISTOTLE = CORPUS / "AristotleCategories.owl"


# --- §10.5: the disaster artifact's prohibited constructions are caught ----
def test_sool_autofixed_antipatterns_are_flagged():
    if not SOOL_AUTOFIXED.exists():
        pytest.skip(f"fixture absent: drop SOOL_autofixed.owl in {CORPUS}")
    raw = SOOL_AUTOFIXED.read_text(encoding="utf-8")
    rep = owl_checks.check_antipatterns_all(raw)
    # The bad artifact is full of AbsenceOf*/Lack*/Failure* terms; the validator
    # must flag them so they can never ship through the gate.
    assert not rep.passed, "expected privation anti-patterns in SOOL_autofixed.owl"


# --- §10.6: expression-IRIs in the refurbished output are caught -----------
def test_aristotle_expression_iris_are_flagged():
    if not ARISTOTLE.exists():
        pytest.skip(f"fixture absent: drop AristotleCategories.owl in {CORPUS}")
    raw = ARISTOTLE.read_text(encoding="utf-8")
    rep = owl_checks.check_expression_iris(raw)
    assert not rep.passed, "expected class-expression IRIs in AristotleCategories.owl"


# --- MC-3: byte-for-byte determinism --------------------------------------
def _malformed_clean_proposal():
    ent = NS(kind="individual", existing_iri="", label="Acme",
             bfo_type="BFO_0000040", parent_class="", iri_suggestion="working:Acme")
    rel = NS(s="working:Acme", p="working:memberOf", o="working:Org")
    return NS(entities=[ent], relations=[rel], utterance="a fixed case")


def test_serialized_fragment_is_byte_identical_across_runs():
    p = _malformed_clean_proposal()
    a = gate_client.serialize_fragment(p)
    b = gate_client.serialize_fragment(p)
    assert a == b  # no timestamps / randomness in the emit path


def test_stable_iris_are_reproducible():
    n1 = si.stable_local_name("Acme", "BFO_0000040", "a fixed case")
    n2 = si.stable_local_name("Acme", "BFO_0000040", "a fixed case")
    assert n1 == n2


# --- self-lint == gate parity ---------------------------------------------
def test_self_lint_equals_gate_on_malformed_iri():
    # The proposal-level linter (PC-7/PC-8) and the gate client's file-level
    # checks must agree: both flag a baked-in expression IRI + privation operand.
    ent = NS(kind="class", existing_iri="", label="Quantity", bfo_type="BFO_0000019",
             parent_class="",
             iri_suggestion="working#[ Quantity and not ( p some working:NonQuantity ) ]")
    prop = NS(entities=[ent], relations=[], utterance="x")

    self_lint = {v.rule for v in construction_linter.lint(prop).violations}
    file_level = {f["rule"] for f in
                  gate_client._file_level_findings(gate_client.serialize_fragment(prop))}

    assert "PC-7" in self_lint and "PC-7" in file_level
    assert "PC-8" in self_lint and "PC-8" in file_level
