"""FRCP front-matter trim (norm_extractor_spec_v1.md §6).

The trim must drop the foreword, committee roster, historical note and table of
contents while retaining 28 U.S.C. §§ 2072-2074 — those sections are the
authority link and hold two of the four K-D3 candidate positives.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chainlab.corpora.legal_frcp import preprocess_frcp  # noqa: E402
from chainlab.corpora.routing import extractors_for  # noqa: E402


FRONT_MATTER = """\
FEDERAL RULES OF CIVIL PROCEDURE

COMMITTEE ON RULES OF PRACTICE AND PROCEDURE
Hon. John D. Bates, Chair
Hon. Jane Q. Judge

FOREWORD

This document contains the Federal Rules of Civil Procedure, as amended.

TABLE OF CONTENTS

Rule 1. Scope and Purpose
Rule 2. One Form of Action
"""

AUTHORITY = """\
AUTHORITY FOR PROMULGATION OF RULES

§ 2072. Rules of procedure and evidence; power to prescribe
(a) The Supreme Court shall have the power to prescribe general rules of
practice and procedure.
(c) Such rules may define when a ruling of a district court is final for the
purposes of appeal under section 1291 of this title.

§ 2073. Rules of procedure and evidence; method of prescribing
(e) Failure to comply with this section does not invalidate a rule.

§ 2074. Rules of procedure and evidence; submission to Congress
(a) The Supreme Court shall transmit to the Congress not later than May 1.

§ 2075. Bankruptcy rules
The Supreme Court shall have the power to prescribe by general rules the
forms of process in cases under title 11.
"""

HISTORICAL = """\
HISTORICAL NOTE

The Federal Rules of Civil Procedure were adopted by order of the Supreme
Court on Dec. 20, 1937.
"""

OPERATIVE = """\
RULES OF CIVIL PROCEDURE
FOR THE
UNITED STATES DISTRICT COURTS

Rule 1. Scope and Purpose
These rules govern the procedure in all civil actions.

Rule 4. Summons
(b) Issuance. On or after filing the complaint, the plaintiff may present a
summons to the clerk for signature and seal.
"""

FULL = FRONT_MATTER + "\n" + AUTHORITY + "\n" + HISTORICAL + "\n" + OPERATIVE


def test_retains_authority_and_operative_text():
    retained, span = preprocess_frcp(FULL)
    assert "§ 2072." in retained
    assert "§ 2073." in retained
    assert "§ 2074." in retained
    assert "Rule 4. Summons" in retained
    assert "the plaintiff may present a" in retained


def test_drops_front_matter_and_historical_note():
    retained, _ = preprocess_frcp(FULL)
    assert "Hon. John D. Bates" not in retained
    assert "FOREWORD" not in retained
    assert "TABLE OF CONTENTS" not in retained
    assert "HISTORICAL NOTE" not in retained


def test_drops_bankruptcy_rules_section():
    """§ 2075 is out of scope; the retained authority block ends at § 2074."""
    retained, _ = preprocess_frcp(FULL)
    assert "Bankruptcy rules" not in retained


def test_span_is_recorded_for_the_denominator():
    retained, span = preprocess_frcp(FULL)
    assert span.total_chars == len(FULL)
    assert span.retained_chars == len(retained)
    assert span.dropped_chars == len(FULL) - len(retained)
    assert span.dropped_chars > 0
    assert span.to_dict()["operative_start"] is not None


def test_heading_on_a_title_page_does_not_defeat_the_trim():
    """The heading also appears on the title page and in the table of contents.
    Picking the first occurrence would retain all the front matter while
    reporting a successful trim — worse than not trimming at all."""
    title_page = ("RULES OF CIVIL PROCEDURE\nFOR THE\nUNITED STATES DISTRICT COURTS\n\n"
                  "December 1, 2024\n\n")
    text = title_page + FRONT_MATTER + "\n" + AUTHORITY + "\n" + OPERATIVE
    retained, span = preprocess_frcp(text)
    assert "Hon. John D. Bates" not in retained
    assert "TABLE OF CONTENTS" not in retained
    assert "Rule 4. Summons" in retained
    assert span.dropped_chars > len(title_page)


def test_body_is_found_when_the_copy_has_no_rules_heading():
    """Not every printing carries the three-line heading. Rule 1 still appears
    twice — once in the contents, once at the top of the body — and the body is
    the one with a whole rule's text before Rule 2."""
    toc = ("TABLE OF CONTENTS\n\nRule 1. Scope and Purpose\nRule 2. One Form of Action\n"
           "Rule 3. Commencing an Action\n")
    body = ("Rule 1. Scope and Purpose\nThese rules govern the procedure in all civil "
            "actions and proceedings in the United States district courts. They should "
            "be construed, administered, and employed by the court and the parties to "
            "secure the just, speedy, and inexpensive determination of every action.\n\n"
            "Rule 2. One Form of Action\nThere is one form of action — the civil action.\n")
    text = FRONT_MATTER + "\n" + AUTHORITY + "\n" + toc + "\n" + body
    retained, span = preprocess_frcp(text)
    assert "TABLE OF CONTENTS" not in retained
    assert "Hon. John D. Bates" not in retained
    assert "These rules govern the procedure" in retained
    assert "§ 2072." in retained          # authority link still kept


def test_authority_block_stops_at_front_matter_when_2075_is_absent():
    """Without a § 2075 to stop at, the block would run to the body and drag the
    contents list back in behind it."""
    text = ("COMMITTEE ON RULES\nHon. John D. Bates\n\n"
            "AUTHORITY FOR PROMULGATION OF RULES\n\n"
            "§ 2072. Rules of procedure\n(a) The Supreme Court shall have the power.\n\n"
            "TABLE OF CONTENTS\nRule 1. Scope\nRule 2. One Form\n\n"
            "Rule 1. Scope and Purpose\nThese rules govern the procedure in all civil "
            "actions and proceedings in the United States district courts, and should "
            "be construed to secure the just, speedy, and inexpensive determination of "
            "every action.\n\nRule 2. One Form of Action\nThere is one form of action.\n")
    retained, span = preprocess_frcp(text)
    assert "§ 2072." in retained
    assert "These rules govern" in retained
    assert "TABLE OF CONTENTS" not in retained
    assert "Hon. John D. Bates" not in retained


def test_contents_alone_is_not_mistaken_for_the_body():
    """A file that is only front matter must not have its contents list treated
    as operative text."""
    text = FRONT_MATTER + "\nRule 1. Scope\nRule 2. One Form\nRule 3. Commencing\n"
    retained, span = preprocess_frcp(text)
    assert span.dropped_chars == 0
    assert "no front matter dropped" in span.notes


def test_missing_landmarks_keep_everything_and_say_so():
    text = "Some unrelated document with no FRCP landmarks in it at all."
    retained, span = preprocess_frcp(text)
    assert retained == text
    assert span.dropped_chars == 0
    assert "no front matter dropped" in span.notes


def test_routing():
    assert extractors_for("frcp") == ("entity_relation", "norm")
    assert extractors_for("nop_7cfr205") == ("entity_relation", "norm")
    assert extractors_for("icd11") == ("entity_relation",)
    assert extractors_for("dsm") == ("entity_relation",)
    assert extractors_for("go") == ("entity_relation",)
    assert extractors_for("something_else") == ("entity_relation",)
