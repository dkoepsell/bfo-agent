"""K-D3 validation fixtures (norm_extractor_spec_v1.md §4).

The must-not-fire cases are the binding constraint: each is a real capacity in
FRCP whose invocation pathway is spelled out in the same rule. If any of them
fires, the pathway search is too narrow and nothing downstream is trustworthy.

The candidate positives are *test candidates*, not findings. They are here to
show the detector reaches a no_pathway verdict on that shape, not to assert
that FRCP actually contains those defects — that is a hand adjudication.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chainlab.capacity import project_capacities, rule_root  # noqa: E402
from chainlab.detectors.stratum_d import detect_kd3  # noqa: E402
from chainlab.model import Modality, NormTuple  # noqa: E402

CORPUS = "frcp"


def nt(locator, bearer_role, modality, action, **kw) -> NormTuple:
    return NormTuple(
        corpus_id=CORPUS,
        source_locator=locator,
        chunk_index=kw.pop("chunk_index", 0),
        bearer=kw.pop("bearer", f"the {bearer_role}"),
        bearer_role=bearer_role,
        modality=modality,
        action=action,
        confidence=kw.pop("confidence", "high"),
        **kw,
    )


# ------------------------------------------------------------------ fixtures
def rule_27_tuples() -> list[NormTuple]:
    """Perpetuating testimony before an action is filed. Pathway: verified
    petition, required contents, 21-day notice, service under Rule 4, court order."""
    return [
        nt("Rule 27(a)(1)", "party", Modality.PERMISSION,
           "file a verified petition to perpetuate testimony before an action is filed",
           chain_link="presenting_facts",
           source_quote="A person who wants to perpetuate testimony about any matter "
                        "cognizable in a United States court may file a verified petition "
                        "in the district court for the district where any expected adverse "
                        "party resides.",
           counterparty="the district court"),
        nt("Rule 27(a)(1)", "party", Modality.DUTY,
           "state in the petition the subject matter of the expected action and the "
           "petitioner's interest, and the substance of the testimony to be perpetuated",
           chain_link="presenting_facts",
           source_quote="The petition must ask for an order authorizing the petitioner to "
                        "depose the named persons in order to perpetuate their testimony."),
        nt("Rule 27(a)(2)", "party", Modality.DUTY,
           "serve notice of the petition and hearing on each expected adverse party",
           chain_link="presenting_facts",
           deadline="at least 21 days before the hearing date",
           counterparty="each expected adverse party",
           source_quote="At least 21 days before the hearing date, the petitioner must serve "
                        "each expected adverse party with a copy of the petition and a notice "
                        "stating the time and place of the hearing."),
        nt("Rule 27(a)(3)", "court", Modality.POWER,
           "issue an order authorizing depositions to perpetuate testimony",
           chain_link="recognition_act",
           counterparty="the petitioner",
           source_quote="If satisfied that perpetuating the testimony may prevent a failure "
                        "or delay of justice, the court must issue an order that designates "
                        "or describes the persons whose depositions may be taken."),
    ]


def rule_26c_tuples() -> list[NormTuple]:
    """Moving for a protective order. Pathway: motion, meet-and-confer
    certification, named court."""
    return [
        nt("Rule 26(c)(1)", "party", Modality.PERMISSION,
           "move for a protective order",
           chain_link="presenting_facts",
           counterparty="the court where the action is pending",
           source_quote="A party or any person from whom discovery is sought may move for a "
                        "protective order in the court where the action is pending."),
        nt("Rule 26(c)(1)", "party", Modality.DUTY,
           "include in the motion a certification that the movant has in good faith "
           "conferred or attempted to confer with the other affected parties",
           chain_link="presenting_facts",
           counterparty="the other affected parties",
           source_quote="The motion must include a certification that the movant has in good "
                        "faith conferred or attempted to confer with other affected parties "
                        "in an effort to resolve the dispute without court action."),
        nt("Rule 26(c)(1)", "court", Modality.POWER,
           "issue a protective order to protect a party from annoyance, embarrassment, "
           "oppression, or undue burden or expense",
           chain_link="recognition_act",
           counterparty="the moving party",
           source_quote="The court may, for good cause, issue an order to protect a party or "
                        "person from annoyance, embarrassment, oppression, or undue burden."),
    ]


def rule_65b_tuples() -> list[NormTuple]:
    """Issuing a TRO without notice. Pathway: affidavit, attorney certification,
    14-day expiration, filing in the clerk's office."""
    return [
        nt("Rule 65(b)(1)", "court", Modality.POWER,
           "issue a temporary restraining order without written notice to the adverse party",
           chain_link="recognition_act",
           conditions=[
               "specific facts in an affidavit or a verified complaint clearly show that "
               "immediate and irreparable injury will result before the adverse party can "
               "be heard in opposition",
               "the movant's attorney certifies in writing any efforts made to give notice "
               "and the reasons why it should not be required",
           ],
           counterparty="the movant",
           source_quote="The court may issue a temporary restraining order without written or "
                        "oral notice to the adverse party or its attorney only if: (A) specific "
                        "facts in an affidavit or a verified complaint clearly show that "
                        "immediate and irreparable injury will result."),
        nt("Rule 65(b)(2)", "clerk", Modality.DUTY,
           "file the temporary restraining order in the clerk's office and enter it in the record",
           chain_link="recognition_act",
           deadline="promptly",
           source_quote="Every temporary restraining order issued without notice must be filed "
                        "in the clerk's office and entered in the record."),
        nt("Rule 65(b)(2)", "court", Modality.DUTY,
           "set the temporary restraining order to expire at the time set, not to exceed 14 days",
           chain_link="effect",
           deadline="not to exceed 14 days after entry",
           source_quote="The order expires at the time after entry — not to exceed 14 days — "
                        "that the court sets, unless before that time the court, for good cause, "
                        "extends it."),
    ]


def rule_60b_tuples() -> list[NormTuple]:
    """Relief from a final judgment. Pathway: motion, enumerated grounds,
    one-year limit for grounds (1)-(3)."""
    return [
        nt("Rule 60(b)", "court", Modality.POWER,
           "relieve a party from a final judgment, order, or proceeding on motion and just terms",
           chain_link="remedy",
           counterparty="a party or its legal representative",
           source_quote="On motion and just terms, the court may relieve a party or its legal "
                        "representative from a final judgment, order, or proceeding for the "
                        "following reasons: (1) mistake, inadvertence, surprise, or excusable "
                        "neglect."),
        nt("Rule 60(c)(1)", "party", Modality.DUTY,
           "make a motion for relief from a final judgment within a reasonable time",
           chain_link="remedy",
           deadline="no more than a year after entry of the judgment for reasons (1), (2), and (3)",
           source_quote="A motion under Rule 60(b) must be made within a reasonable time — and "
                        "for reasons (1), (2), and (3) no more than a year after the entry of "
                        "the judgment or order."),
    ]


MUST_NOT_FIRE = {
    "Rule 27(a)": (rule_27_tuples, "perpetuate testimony"),
    "Rule 26(c)(1)": (rule_26c_tuples, "protective order"),
    "Rule 65(b)(1)": (rule_65b_tuples, "temporary restraining order"),
    "Rule 60(b)": (rule_60b_tuples, "final judgment"),
}


# -------------------------------------------------------------- must not fire
@pytest.mark.parametrize("label", sorted(MUST_NOT_FIRE))
def test_capacity_with_pathway_does_not_fire(label):
    build, _ = MUST_NOT_FIRE[label]
    tuples = build()
    result = detect_kd3(tuples, corpus_id=CORPUS, run_id="fixture")
    offenders = [c.capacity for c in result.capacities if c.verdict == "no_pathway"]
    assert not offenders, (
        f"{label}: pathway search too narrow, no_pathway on {offenders}"
    )
    assert result.findings == []


@pytest.mark.parametrize("label", sorted(MUST_NOT_FIRE))
def test_pathway_kinds_are_recorded(label):
    """A pathway_found verdict with no recorded kind would mean the verdict
    came from somewhere other than the five signals."""
    build, _ = MUST_NOT_FIRE[label]
    result = detect_kd3(build(), corpus_id=CORPUS, run_id="fixture")
    for cap in result.capacities:
        if cap.verdict == "pathway_found":
            assert cap.pathway_kind, f"{label}: {cap.capacity} has no pathway_kind"
            assert cap.pathway_loci, f"{label}: {cap.capacity} has no pathway_loci"


def test_all_fixtures_together_still_do_not_fire():
    """Run the four rules as one corpus: cross-rule tuples must not turn a
    pathway_found into a no_pathway, and must not introduce spurious findings."""
    tuples = []
    for build, _ in MUST_NOT_FIRE.values():
        tuples.extend(build())
    result = detect_kd3(tuples, corpus_id=CORPUS, run_id="fixture")
    assert result.n_no_pathway == 0, [c.capacity for c in result.capacities
                                      if c.verdict == "no_pathway"]


# ------------------------------------------------------------------ externals
def test_external_capacity_is_excluded_not_scored():
    """28 U.S.C. 2072(c): no procedure in this document, but the reference is
    external, so it must come out `external` and emit no finding."""
    tuples = [
        nt("28 U.S.C. 2072(c)", "Supreme Court", Modality.POWER,
           "define when a ruling of a district court is final for purposes of appeal",
           bearer="Such rules",
           chain_link="authority",
           counterparty="district court",
           external_reference=True,
           external_targets=["28 U.S.C. 1291"],
           source_quote="Such rules may define when a ruling of a district court is final for "
                        "the purposes of appeal under section 1291 of this title."),
    ]
    result = detect_kd3(tuples, corpus_id=CORPUS, run_id="fixture")
    assert [c.verdict for c in result.capacities] == ["external"]
    assert result.findings == []
    assert result.n_external == 1


def test_external_targets_imply_external_even_if_flag_missing():
    """The flag is load-bearing; a populated target list without it is a model
    slip, not a source-level clash."""
    from chainlab.norm_extractor import NormExtractor

    coerced = NormExtractor._coerce(
        object.__new__(NormExtractor),
        {
            "bearer": "Such rules", "bearer_role": "Supreme Court", "modality": "power",
            "action": "define finality for appeal",
            "external_reference": False, "external_targets": ["28 U.S.C. 1291"],
        },
        CORPUS, 0, "28 U.S.C. 2072(c)",
    )
    assert coerced.external_reference is True


# --------------------------------------------------------- candidate positives
def test_bare_capacity_yields_no_pathway_and_a_finding():
    """Rule 83(b) shape: a broad capacity with no invocation procedure, no form,
    no deadline. A *candidate*, to be adjudicated by hand against the full text."""
    tuples = [
        nt("Rule 83(b)", "judge", Modality.POWER,
           "regulate practice in any manner consistent with federal law and the "
           "district's local rules",
           chain_link="authority",
           source_quote="A judge may regulate practice in any manner consistent with federal "
                        "law, rules adopted under 28 U.S.C. 2072 and 2075, and the district's "
                        "local rules."),
    ]
    result = detect_kd3(tuples, corpus_id=CORPUS, run_id="fixture")
    assert [c.verdict for c in result.capacities] == ["no_pathway"]
    assert len(result.findings) == 1
    f = result.findings[0]
    assert f.kernel_id == "K-D3"
    assert f.stratum == "D"
    assert f.detection_method == "structural"
    assert f.provenance == "source"
    assert f.locus == "authority"
    assert "no invocation pathway" in f.justification
    assert f.source_locators == ["Rule 83(b)"]


def test_sibling_procedure_for_a_different_actor_is_not_a_pathway():
    """Rule 83(a)'s notice-and-comment procedure binds the district court's
    rulemaking, not a single judge's Rule 83(b) power. Same rule root, different
    bearer, weak overlap — must not be read as an invocation pathway."""
    tuples = [
        nt("Rule 83(a)(1)", "court", Modality.DUTY,
           "give public notice and an opportunity for comment before adopting a local rule",
           chain_link="authority",
           counterparty="the public",
           source_quote="A district court, acting by a majority of its district judges, may "
                        "adopt and amend rules governing its practice after giving public "
                        "notice and an opportunity for comment."),
        nt("Rule 83(b)", "judge", Modality.POWER,
           "regulate practice in any manner consistent with federal law and the "
           "district's local rules",
           chain_link="authority",
           source_quote="A judge may regulate practice in any manner consistent with federal "
                        "law and the district's local rules."),
    ]
    result = detect_kd3(tuples, corpus_id=CORPUS, run_id="fixture")
    verdicts = {c.capacity[:20]: c.verdict for c in result.capacities}
    assert verdicts == {"regulate practice in": "no_pathway"}


def test_a_powers_own_counterparty_is_not_an_invocation_pathway():
    """"The court may order X against the defendant" names a recipient, but that
    recipient is what the power acts on, not a route for invoking it."""
    tuples = [
        nt("Rule 99(a)", "court", Modality.POWER,
           "impose a sanction on its own initiative",
           chain_link="remedy",
           counterparty="the offending attorney",
           source_quote="The court may impose a sanction on the offending attorney."),
    ]
    result = detect_kd3(tuples, corpus_id=CORPUS, run_id="fixture")
    assert [c.verdict for c in result.capacities] == ["no_pathway"]


def test_a_procedure_addressed_to_the_power_holder_is_a_pathway():
    """Invoking an official's power is normally somebody else's move, so a
    party's motion filed *in that court* counts even though the bearers differ."""
    tuples = [
        nt("Rule 98(a)", "court", Modality.POWER,
           "grant leave to amend a pleading",
           chain_link="recognition_act",
           source_quote="The court may grant leave to amend a pleading."),
        nt("Rule 98(b)", "party", Modality.PERMISSION,
           "file a motion for leave to amend in the court where the action is pending",
           chain_link="presenting_facts",
           counterparty="the court where the action is pending",
           deadline="within 21 days after service of a responsive pleading",
           source_quote="A party may file a motion for leave to amend in the court where "
                        "the action is pending within 21 days after service."),
    ]
    result = detect_kd3(tuples, corpus_id=CORPUS, run_id="fixture")
    by_capacity = {c.capacity: c for c in result.capacities}
    granting = by_capacity["grant leave to amend a pleading"]
    assert granting.verdict == "pathway_found"
    assert granting.pathway_loci == ["Rule 98(b)"]
    assert result.findings == []


# ------------------------------------------------------------------- plumbing
def test_only_powers_and_permissions_project_to_capacities():
    tuples = [
        nt("Rule 8(a)", "party", Modality.DUTY, "state the claim showing entitlement to relief"),
        nt("Rule 11(b)", "attorney", Modality.PROHIBITION, "present a paper for an improper purpose"),
        nt("Rule 6(b)", "court", Modality.POWER, "extend the time for good cause",
           deadline="before the original time expires"),
    ]
    caps = project_capacities(tuples)
    assert [c.capacity for c in caps] == ["extend the time for good cause"]


@pytest.mark.parametrize("locator,expected", [
    ("Rule 27(a)(1)", "rule 27"),
    ("Rule 27", "rule 27"),
    ("28 U.S.C. 2073(e)", "28 u.s.c. 2073"),
    ("", ""),
])
def test_rule_root(locator, expected):
    assert rule_root(locator) == expected
