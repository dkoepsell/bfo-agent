"""Parsing and normalization for the norm extractor — no API calls.

The prompt's own worked example is used as the fixture response: if the parser
cannot read what the prompt asks for, the prompt and the parser have drifted.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chainlab.model import Modality  # noqa: E402
from chainlab.norm_extractor import (  # noqa: E402
    NORM_EXTRACTION_SYSTEM,
    NormExtractor,
    _extract_json_array,
)

RESPONSE = """[
  {
    "bearer": "the plaintiff",
    "bearer_role": "plaintiff",
    "modality": "permission",
    "action": "present a summons to the clerk for signature and seal",
    "conditions": ["on or after filing the complaint"],
    "deadline": null,
    "counterparty": "the clerk",
    "source_locator": "Rule 4(b)",
    "source_quote": "On or after filing the complaint, the plaintiff may present a summons to the clerk for signature and seal.",
    "external_reference": false,
    "external_targets": [],
    "chain_link": "presenting_facts",
    "confidence": "high",
    "extraction_note": null
  },
  {
    "bearer": "the clerk",
    "bearer_role": "clerk",
    "modality": "duty",
    "action": "sign, seal, and issue the summons to the plaintiff for service on the defendant",
    "conditions": ["the summons is properly completed"],
    "deadline": null,
    "counterparty": "the plaintiff",
    "source_locator": "Rule 4(b)",
    "source_quote": "If the summons is properly completed, the clerk must sign, seal, and issue it to the plaintiff for service on the defendant.",
    "external_reference": false,
    "external_targets": [],
    "chain_link": "recognition_act",
    "confidence": "high",
    "extraction_note": null
  }
]"""


def coerce_all(raw: list, locator: str = "") -> list:
    stub = object.__new__(NormExtractor)
    out = [NormExtractor._coerce(stub, r, "frcp", 0, locator) for r in raw]
    return [t for t in out if t is not None]


def test_parses_the_prompts_own_example():
    tuples = coerce_all(_extract_json_array(RESPONSE))
    assert len(tuples) == 2
    a, b = tuples
    assert a.modality is Modality.PERMISSION
    assert a.chain_link == "presenting_facts"
    assert a.conditions == ["on or after filing the complaint"]
    assert a.deadline is None
    assert b.modality is Modality.DUTY
    assert b.counterparty == "the plaintiff"
    assert {t.source_locator for t in tuples} == {"Rule 4(b)"}


def test_empty_array_is_a_valid_result():
    assert _extract_json_array("[]") == []
    assert coerce_all([]) == []


def test_tolerates_fences_and_preamble():
    fenced = "Here you go:\n```json\n" + RESPONSE + "\n```"
    assert len(coerce_all(_extract_json_array(fenced))) == 2


def test_rejects_output_with_no_array():
    with pytest.raises(ValueError):
        _extract_json_array("I could not find any norms in this passage.")


def test_drops_entries_missing_a_bearer_modality_or_action():
    raw = [
        {"bearer": "", "modality": "duty", "action": "do the thing"},
        {"bearer": "the court", "modality": "", "action": "do the thing"},
        {"bearer": "the court", "modality": "duty", "action": ""},
        {"bearer": "the court", "modality": "wishful", "action": "do the thing"},
        {"bearer": "the court", "modality": "duty", "action": "do the thing"},
    ]
    assert len(coerce_all(raw)) == 1


def test_unknown_confidence_falls_back_to_low():
    t = coerce_all([{"bearer": "the court", "modality": "duty",
                     "action": "act", "confidence": "very high"}])[0]
    assert t.confidence == "low"


def test_source_quote_is_capped_at_400_chars():
    t = coerce_all([{"bearer": "the court", "modality": "duty", "action": "act",
                     "source_quote": "x" * 900}])[0]
    assert len(t.source_quote) == 400


def test_default_locator_fills_in_when_the_model_omits_one():
    t = coerce_all([{"bearer": "the court", "modality": "duty", "action": "act"}],
                   locator="Rule 26(c)(1)")[0]
    assert t.source_locator == "Rule 26(c)(1)"


def test_conditions_accepts_a_bare_string():
    t = coerce_all([{"bearer": "the court", "modality": "duty", "action": "act",
                     "conditions": "good cause is shown"}])[0]
    assert t.conditions == ["good cause is shown"]


def test_prompt_still_documents_every_modality():
    for m in Modality:
        assert m.value in NORM_EXTRACTION_SYSTEM


def test_prompt_examples_are_valid_json():
    """Each `Output:` block in the prompt must parse, or the model is being
    shown malformed targets."""
    blocks = [b.split("Input:")[0] for b in NORM_EXTRACTION_SYSTEM.split("Output:")[1:]]
    assert len(blocks) == 3
    for block in blocks:
        start = block.find("[")
        end = block.rfind("]")
        assert start != -1 and end != -1
        json.loads(block[start : end + 1])
