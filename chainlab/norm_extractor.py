"""Norm-tuple extraction from act-thick legal and regulatory text.

The claim extractor in `app/extractor.py` looks for ontological assertions —
*X is a kind of Y*, *X has part Z*. FRCP contains almost none of those and
returned 93 chunks / 0 claims. That is a schema mismatch, not a bug, so this
is a *second* extractor that runs alongside it. See `chainlab.corpora.routing`
for which corpora get which.

Prompting and JSON handling mirror `app.extractor` so the two are comparable
in Study C: same model default, same client, same usage metering hook.
"""
from __future__ import annotations

import json
import time

from anthropic import Anthropic

from app.config import ANTHROPIC_API_KEY, ANTHROPIC_EXTRACTOR_MODEL, require_api_key
from app.extractor import chunk_text

from .model import Modality, NormTuple


NORM_EXTRACTION_SYSTEM = """\
You extract normative content from legal and regulatory text.

Your output is a JSON array of norm tuples. Each tuple records ONE normative
statement: a single thing that one actor must, must not, may, or is empowered
to do. Return ONLY the JSON array. No preamble, no markdown fences, no commentary.

## What counts as a norm tuple

Extract a tuple whenever the text states that some actor is required, forbidden,
permitted, or empowered to do something. Typical signals:

  duty         must, shall, is required to, has a duty to
  prohibition  must not, may not, shall not, is not permitted to
  permission   may, is permitted to, need not, is not required to
  power        shall have the power to, is authorized to, may order,
               may issue, may grant, may appoint, may certify
  liability    is subject to, is bound by, waives, is liable for
  exemption    does not apply to, is exempt from, except as provided

## Duty vs. power

This distinction matters most. Ask: does performing the action change someone's
normative position, or does it merely comply with a requirement?

  "A summons must name the court and the parties"        -> duty (compliance)
  "The court may permit a summons to be amended"         -> power (changes positions)
  "The clerk must sign, seal, and issue it"              -> duty
  "the court may order that service be made by a marshal" -> power

When "may" grants discretion to an official to alter what others must or may do,
it is a power, not a permission. When "may" merely licenses a party's own optional
conduct, it is a permission.

## Fields

bearer         The actor, verbatim from the text.
bearer_role    Normalized role: court, judge, clerk, party, plaintiff, defendant,
               attorney, marshal, deponent, nonparty, Supreme Court,
               Judicial Conference, Congress, or other. One word or short phrase.
modality       One of: duty, prohibition, permission, power, liability, exemption.
action         What is to be done, verbatim or lightly normalized.
conditions     List of antecedents. "If the summons is properly completed" ->
               ["the summons is properly completed"]. Empty list if unconditional.
deadline       Any stated time limit, verbatim: "within 21 days after being served".
               null if none.
counterparty   Who benefits from or is affected by the action. null if none.
source_locator The rule or section number: "Rule 4(b)", "Rule 26(a)(1)(A)",
               "28 U.S.C. 2072(c)". Use the most specific subdivision available.
source_quote   The verbatim sentence(s), at most 400 characters.
external_reference
               true if the action or conditions depend on an instrument outside
               this document: state law, another U.S. Code title, a local rule,
               a treaty, a foreign country's law, another rule set.
external_targets
               List of those instruments, e.g. ["state law"], ["28 U.S.C. 1915"].
               Empty list if external_reference is false.
chain_link     Which link of the recognition chain this norm sits at:
                 authority         who is empowered to make the rules
                 criteria          the substantive standard applied
                 assessor_in_role  who applies the standard, and their qualification
                 presenting_facts  what must be shown, pleaded, served, or proved
                 recognition_act   the decision, order, judgment, or conferral itself
                 effect            what follows from the decision
                 remedy            appeal, review, reconsideration, sanction, relief
               null if it fits none.
confidence     high | medium | low.
extraction_note
               Free text for anything ambiguous. null if nothing to note.

## Rules

1. ONE norm per tuple. "The clerk must sign, seal, and issue it" is three actions
   in one duty; keep it as one tuple with action "sign, seal, and issue the summons".
   But "A party must serve an answer and may move to dismiss" is two tuples.

2. Extract from the operative text only. SKIP: tables of contents, committee
   rosters, forewords, historical notes, amendment histories, parenthetical
   citation blocks like "(As amended Apr. 30, 2007, eff. Dec. 1, 2007.)".
   If a chunk contains only such material, return [].

3. Preserve the bearer as written, but fill bearer_role with the normalized form.
   "the party to whom the request is directed" -> bearer_role "party".

4. Do not infer norms that are not stated. If the text describes what happens
   without directing anyone ("Service is complete upon mailing"), that is not a
   norm tuple. Skip it.

5. Returning [] is correct and expected for non-operative chunks. Do not invent
   content to avoid an empty result.

6. Set confidence low rather than skipping when a passage is normative but its
   bearer, modality, or scope is unclear. Low-confidence tuples are useful; silent
   omissions are not.

## Examples

Input: "On or after filing the complaint, the plaintiff may present a summons to
the clerk for signature and seal. If the summons is properly completed, the clerk
must sign, seal, and issue it to the plaintiff for service on the defendant."
(Rule 4(b))

Output:
[
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
]

Input: "Such rules may define when a ruling of a district court is final for the
purposes of appeal under section 1291 of this title." (28 U.S.C. 2072(c))

Output:
[
  {
    "bearer": "Such rules",
    "bearer_role": "Supreme Court",
    "modality": "power",
    "action": "define when a ruling of a district court is final for purposes of appeal",
    "conditions": [],
    "deadline": null,
    "counterparty": "district court",
    "source_locator": "28 U.S.C. 2072(c)",
    "source_quote": "Such rules may define when a ruling of a district court is final for the purposes of appeal under section 1291 of this title.",
    "external_reference": true,
    "external_targets": ["28 U.S.C. 1291"],
    "chain_link": "authority",
    "confidence": "high",
    "extraction_note": "Bearer is the rules themselves; attributed to the Supreme Court as the promulgating authority under 2072(a)."
  }
]

Input: "Rule 74. [Abrogated.]"

Output: []
"""

NORM_EXTRACTION_PROMPT = "Corpus: {corpus_id}\n\nPassage:\n\n{passage}"

_VALID_MODALITIES = {m.value for m in Modality}
_VALID_CONFIDENCE = {"high", "medium", "low"}
_QUOTE_MAX = 400


def _extract_json_array(text: str) -> list:
    """Pull the JSON array out of a model response.

    The prompt forbids fences and preamble, but a model that ignores that
    should not cost us a chunk, so strip fences and take the outermost
    brackets — same tolerance as `app.extractor._extract_json`.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON array found in norm extraction output:\n{text[:400]}")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("Norm extraction output was not a JSON array")
    return data


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(v).strip() for v in value if str(v).strip()]


def _clean_optional(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


class NormExtractor:
    def __init__(self, model: str = ANTHROPIC_EXTRACTOR_MODEL,
                 api_key: str | None = None, on_usage=None):
        """api_key=None falls back to the owner key (CLI/test path); when
        provided (BYOK), calls bill to it. on_usage(model, in_tok, out_tok)
        is invoked after each LLM call for token metering."""
        if api_key is None:
            require_api_key()
            api_key = ANTHROPIC_API_KEY
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self._on_usage = on_usage
        self.last_raw_response: str = ""

    def _record_usage(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        if self._on_usage and usage is not None:
            self._on_usage(
                self.model,
                getattr(usage, "input_tokens", 0) or 0,
                getattr(usage, "output_tokens", 0) or 0,
            )

    def extract_chunk(self, passage: str, corpus_id: str, chunk_index: int = 0,
                      default_locator: str = "") -> list[NormTuple]:
        prompt = NORM_EXTRACTION_PROMPT.format(corpus_id=corpus_id, passage=passage)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=NORM_EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp)
        text = "".join(b.text for b in resp.content if getattr(b, "text", None)).strip()
        self.last_raw_response = text
        raw = _extract_json_array(text)
        return [
            t for t in (
                self._coerce(r, corpus_id, chunk_index, default_locator) for r in raw
            ) if t is not None
        ]

    def _coerce(self, r: dict, corpus_id: str, chunk_index: int,
                default_locator: str) -> NormTuple | None:
        """Normalize one raw tuple. Returns None for entries too malformed to
        keep — a missing bearer or action means there is no norm here."""
        if not isinstance(r, dict):
            return None
        modality = str(r.get("modality") or "").strip().lower()
        bearer = str(r.get("bearer") or "").strip()
        action = str(r.get("action") or "").strip()
        if modality not in _VALID_MODALITIES or not bearer or not action:
            return None

        confidence = str(r.get("confidence") or "low").strip().lower()
        if confidence not in _VALID_CONFIDENCE:
            confidence = "low"

        targets = _as_str_list(r.get("external_targets"))
        external = bool(r.get("external_reference")) or bool(targets)

        return NormTuple(
            corpus_id=corpus_id,
            source_locator=str(r.get("source_locator") or default_locator or "").strip(),
            chunk_index=chunk_index,
            bearer=bearer,
            bearer_role=_clean_optional(r.get("bearer_role")),
            modality=Modality(modality),
            action=action,
            conditions=_as_str_list(r.get("conditions")),
            deadline=_clean_optional(r.get("deadline")),
            counterparty=_clean_optional(r.get("counterparty")),
            source_quote=str(r.get("source_quote") or "").strip()[:_QUOTE_MAX],
            chain_link=_clean_optional(r.get("chain_link")),
            external_reference=external,
            external_targets=targets,
            confidence=confidence,
            extraction_note=_clean_optional(r.get("extraction_note")),
        )

    def extract_text(self, text: str, corpus_id: str, chunk_chars: int = 8000,
                     overlap: int = 400, on_progress=None,
                     pause: float = 0.4) -> list[NormTuple]:
        chunks = chunk_text(text, chunk_chars, overlap)
        out: list[NormTuple] = []
        for i, chunk in enumerate(chunks):
            tuples = self.extract_chunk(chunk, corpus_id=corpus_id, chunk_index=i)
            out.extend(tuples)
            if on_progress:
                on_progress(i, len(chunks), len(tuples))
            if pause:
                time.sleep(pause)
        return out
