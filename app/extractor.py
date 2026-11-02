"""Extraction logic for pulling atomic ontological claims from text.

Shared between the CLI script (`scripts/extract_claims.py`) and the
orchestrator's `/extract/*` endpoints so both paths use identical prompting
and chunking behavior.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from anthropic import Anthropic

from .config import ANTHROPIC_API_KEY, ANTHROPIC_EXTRACTOR_MODEL, require_api_key


EXTRACTION_SYSTEM = (
    "You are extracting atomic ontological claims from a work of philosophy. "
    "Your output is strict JSON, no prose."
)

EXTRACTION_PROMPT = """I will give you a passage from a philosophy text. Extract the atomic ontological claims the AUTHOR IS ASSERTING IN HIS OWN VOICE.

An atomic ontological claim does EXACTLY ONE of the following:
- asserts that some kind of entity exists
- asserts that one kind is a subkind of another (X is a Y)
- asserts that some entity bears a relation to another (X is part of Y, X realizes Y, X participates in Y, etc.)
- asserts that some entity has a property, quality, disposition, role, or function
- asserts that something is an individual of a particular kind (Socrates is a man)

ATOMICITY — one commitment per claim. A claim must make a SINGLE ontological
assertion. If the source sentence bundles two or more, SPLIT it into separate
claims (or drop the ones the author does not clearly commit to). In particular:
- Do NOT join two commitments with "but", "yet", "while", "although", "however",
  "once established", or a semicolon. "X depends on Y but keeps its integrity"
  is TWO claims — emit them separately, or omit whichever is not asserted.
- Do NOT assert both that something is dependent (needs a bearer / needs
  recognition / is generically dependent) AND that it is independent
  (self-standing, persists on its own, maintains integrity by itself) in the
  same claim. In basic ontology these are mutually exclusive; pick the one the
  author actually asserts.

COHERENCE — stay in plain ontological vocabulary. Every claim must classify its
subject using ONE ordinary category word: continuant, occurrent, process,
object, quality, role, disposition, function, relation, part, or participant.
This is a HARD rule. A metaphorical or invented noun ("repair node",
"relational field", "attractor", "scaffold", "malformed structure") is NOT a
category word. If the only predicate a passage offers is such a metaphor, DROP
the claim entirely — do not restate the metaphor and do not guess a category
the author did not state. Emitting a coined category is worse than emitting
nothing; the downstream reasoner rejects it and the work is wasted.
Example: "courts act as repair nodes that stabilize relational fields" yields NO
claim (its only predicate is metaphor), not "courts are repair nodes".

INCLUDE only:
- the author's own positive assertions
- claims stated with endorsement, not merely reported

EXCLUDE:
- rhetorical questions
- views of OTHER philosophers being described (unless the author is endorsing them)
- methodological remarks ("in what follows I will argue...")
- transitions and summaries
- illustrative examples presented only for exposition, without ontological commitment
- hedged or tentative formulations ("one might say that...", "it could be argued...")
- quotations from source material being analyzed, not asserted

QUANTIFIER: record what the passage says about HOW MANY, never what is typical:
- "definitional": "X is (by definition) a Y that ..." (necessary and sufficient)
- "every": the passage says every / each / all X are or have it, or states it unconditionally of the kind
- "some": the passage says some, many, most or certain X
- "can": the passage says X can, may, might or is able to have it (a possibility, NOT a universal)
- "none": the passage says no X is or has it
- "individual": the claim is about a named particular (Socrates, this statute)
"Persons can have expertise" is "can": it does NOT say every person has expertise.

ALTERNATIVES: when the claim names several kinds as alternatives or as examples
("devices, applications, tools or protocols", "such as A, B and C"), list them in
"alternatives". A list of examples does not say each one is always involved.

SOURCE QUOTE: copy the words of the passage EXACTLY, character for character, as
one continuous span of at most 40 words: no ellipses, no paraphrase, no reordering,
no added words, and keep the modal verbs ("should", "may", "can") that govern it.
A quote that is not found in the passage marks the claim unverified.

For each claim, produce:
{{
  "claim": "A single short declarative sentence, 25 words or fewer, asserting ONE ontological commitment, in the author's voice. No 'but'/'yet'/'while'/'although' clauses and no semicolons joining two assertions. Rephrase for clarity but do not change the commitment, and keep its quantifier ('can', 'some') in the sentence.",
  "source_quote": "Verbatim continuous span of the passage, up to 40 words.",
  "subject": "the term the claim is about, as the passage names it",
  "quantifier": "definitional | every | some | can | none | individual",
  "alternatives": ["terms the claim lists as alternatives or examples, else empty"],
  "confidence": "high" | "medium" | "low",
  "note": "Brief rationale: why this counts as an atomic ontological claim (and not, e.g., a methodological remark)."
}}

Confidence rules:
- "high": the author plainly asserts this
- "medium": the author asserts it but with qualification, or it is an implication so direct that the author clearly commits to it
- "low": it is plausibly the author's view but interpretation is involved; flag these for human review

PASSAGE (section: {section}):
\"\"\"
{passage}
\"\"\"

Return ONLY a JSON object of the form:
{{"claims": [ {{...}}, {{...}}, ... ]}}

If the passage contains no atomic ontological claims (e.g. it is entirely methodological or historical), return {{"claims": []}}.
"""


# SPEC-legal-drafting.md 3.1: genre profiles. "philosophy" is the prompt
# above, unchanged. The legal genres replace "the author's own voice" with
# clause types and keep modal verbs, which are norms in law, not hedges.
LEGAL_GENRE_SOURCE = {
    "legislation": "legislation (a statute, regulation or directive)",
    "model_rules": "a set of model rules (a restatement or a draft common frame of reference)",
    "policy": "a policy or code of conduct",
}

LEGAL_EXTRACTION_SYSTEM = (
    "You are extracting atomic ontological and normative claims from {source}. "
    "You copy the legal text exactly. Your output is strict JSON, no prose."
)

LEGAL_EXTRACTION_PROMPT = """I will give you a passage of {source}. Extract the atomic claims the TEXT ITSELF makes. Legal text is not an author's opinion: every operative provision counts, and modal verbs ("may", "shall", "is entitled to") are norms, not hedges.

Classify each claim by CLAUSE TYPE:
- definition: "'X' means ...", "X shall be understood as ..."
- scope: "This Regulation applies to ...", "shall not apply to ..."
- establishment: "is hereby established", "shall have legal personality"
- conferral: "shall designate", "shall be appointed", "shall have the power to"
- norm: "shall", "shall not", "may", "is entitled to", "has the right to"
- condition: "where", "in the case of", "after having become aware" (a trigger of a norm)
- time_limit: "within 72 hours", "without undue delay"
- remedy: "has the right to lodge a complaint", "shall be liable", "administrative fines"
- other: any other ontological content (an entity, a part-whole relation, a kind)

ATOMICITY: one commitment per claim. A definition with several limbs ("natural or legal person, public authority, agency or other body") is ONE definition claim; a norm with two duties is two claims.

QUANTIFIER: record what the text says about HOW MANY, never what is typical:
- "definitional": "X means Y" (necessary and sufficient)
- "every": the text says every / each / all / any X has it, or states it unconditionally of the kind
- "some": the text says some or certain X
- "can": the text says X can, may, might, or is able to have it (a possibility, NOT a universal)
- "at_most_one": the text limits it to one
- "none": the text says no X has it ("shall not", "no ... may")
A conditional ("where ...", "if ...") is never "every" for the unconditioned kind.

MODALITY (deontic force of a norm; "none" for definitions and descriptions):
obligation ("shall", "must"), prohibition ("shall not", "may not"), permission ("may"), right ("is entitled to", "has the right to"), power ("shall have the power to", "may adopt").

SOURCE QUOTE: copy the words of the passage EXACTLY, character for character, as one continuous span of at most 60 words: no ellipses, no paraphrase, no reordering, no added words. A claim whose quote is not found verbatim in the passage is discarded.

For each claim, produce:
{{
  "claim": "Short restatement, 25 words or fewer, used for prompting only",
  "source_quote": "Verbatim continuous span of the passage",
  "clause_type": "definition | scope | establishment | conferral | norm | condition | time_limit | remedy | other",
  "subject": "the term the claim is about, as the text names it (e.g. 'controller')",
  "quantifier": "definitional | every | some | can | at_most_one | none",
  "modality": "obligation | prohibition | permission | right | power | none",
  "alternatives": ["terms the text lists as alternatives or examples ('devices, applications, tools and protocols'), else empty"],
  "confidence": "high" | "medium" | "low",
  "note": "Brief rationale"
}}

Confidence rules:
- "high": the text states it plainly
- "medium": the text states it with a qualification or cross-reference
- "low": interpretation is involved; flag for human review

PASSAGE (act: {act}; location: {locator}):
\"\"\"
{passage}
\"\"\"

Return ONLY a JSON object of the form:
{{"claims": [ {{...}}, {{...}}, ... ]}}

If the passage has no operative content (recitals of history, a table of contents, signatures), return {{"claims": []}}.
"""

_QUANTIFIERS = {"definitional", "every", "some", "can", "at_most_one", "none"}
_PHIL_QUANTIFIERS = {"definitional", "every", "some", "can", "none", "individual"}
_MODALITIES = {"obligation", "prohibition", "permission", "right", "power", "none"}
_CLAUSE_TYPES = {"definition", "scope", "establishment", "conferral", "norm",
                 "condition", "time_limit", "remedy", "other"}


def _pick(value, allowed: set, default: str) -> str:
    v = (value or "").strip().lower().replace(" ", "_") if isinstance(value, str) else ""
    return v if v in allowed else default


def _alternatives(value) -> list[str]:
    if isinstance(value, str):
        value = [v for v in re.split(r",|\bor\b|\band\b", value)]
    if not isinstance(value, list):
        return []
    out = [str(v).strip() for v in value if str(v).strip()]
    return out if len(out) >= 2 else []


# ----------------------------------------------------------------- chunking
def chunk_text(text: str, chunk_chars: int = 8000, overlap: int = 400) -> list[str]:
    """Split text into overlapping chunks no larger than `chunk_chars`.

    Prefers paragraph boundaries (blank lines), falls back to sentence
    splits, then hard character splits. Final output is guaranteed to be
    bounded by chunk_chars, so no single chunk blows through context.
    """
    text = re.sub(r"\r\n", "\n", text)
    if len(text) <= chunk_chars:
        return [text]

    def hard_split(s: str) -> list[str]:
        step = max(1, chunk_chars - overlap)
        return [s[i : i + chunk_chars] for i in range(0, len(s), step)]

    def sentence_split(s: str) -> list[str]:
        sentences = re.split(r"(?<=[.!?])\s+", s)
        out, cur = [], ""
        for sent in sentences:
            cand = (cur + " " + sent).strip() if cur else sent
            if len(cand) > chunk_chars and cur:
                out.append(cur)
                cur = sent
            else:
                cur = cand
        if cur:
            out.append(cur)
        final = []
        for piece in out:
            final.extend(hard_split(piece) if len(piece) > chunk_chars else [piece])
        return final

    paragraphs = re.split(r"\n\s*\n", text)
    expanded: list[str] = []
    for p in paragraphs:
        if len(p) > chunk_chars:
            expanded.extend(sentence_split(p))
        else:
            expanded.append(p)

    chunks: list[str] = []
    cur = ""
    for p in expanded:
        candidate = (cur + "\n\n" + p).strip() if cur else p
        if len(candidate) > chunk_chars and cur:
            chunks.append(cur)
            tail = cur[-overlap:] if overlap and len(cur) > overlap else ""
            cur = (tail + "\n\n" + p).strip()
        else:
            cur = candidate
    if cur:
        chunks.append(cur)

    safe: list[str] = []
    for c in chunks:
        if len(c) <= chunk_chars:
            safe.append(c)
        else:
            safe.extend(hard_split(c))
    return safe


# -------------------------------------------------------------- extraction
def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = [l for l in text.split("\n") if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in extraction output:\n{text[:400]}")
    return json.loads(text[start : end + 1])


class ClaimExtractor:
    def __init__(self, model: str = ANTHROPIC_EXTRACTOR_MODEL,
                 api_key: str | None = None, on_usage=None):
        """api_key=None falls back to the owner key (legacy/CLI/test path);
        when provided (BYOK), calls bill to it. on_usage(model, in_tok, out_tok)
        is invoked after each LLM call for token metering."""
        if api_key is None:
            require_api_key()
            api_key = ANTHROPIC_API_KEY
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self._on_usage = on_usage

    def _record_usage(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        if self._on_usage and usage is not None:
            self._on_usage(
                self.model,
                getattr(usage, "input_tokens", 0) or 0,
                getattr(usage, "output_tokens", 0) or 0,
            )

    def extract_chunk(self, passage: str, section: str = "") -> list[dict]:
        prompt = EXTRACTION_PROMPT.format(section=section or "unknown", passage=passage)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp)
        text = "".join(b.text for b in resp.content if getattr(b, "text", None)).strip()
        data = _extract_json(text)
        claims = data.get("claims", [])
        # Normalize shape and attach section
        from .grounding import ground_claim

        out = []
        for c in claims:
            claim = {
                "claim": (c.get("claim") or "").strip(),
                "source_quote": (c.get("source_quote") or "").strip(),
                "confidence": (c.get("confidence") or "low").strip().lower(),
                "note": (c.get("note") or "").strip(),
                "section": section,
            }
            if c.get("quantifier"):
                claim["quantifier"] = _pick(c.get("quantifier"), _PHIL_QUANTIFIERS, "some")
            if (c.get("subject") or "").strip():
                claim["subject"] = c["subject"].strip()
            alts = _alternatives(c.get("alternatives"))
            if alts:
                claim["alternatives"] = alts
            # Provenance is the passage's own words: snap a near-quote to the
            # exact span, or mark the claim unverified. Unverified claims are
            # kept (philosophy has no article structure to fall back on) but
            # never auto-approved.
            ground_claim(claim, passage)
            if not claim["quote_verified"]:
                claim["confidence"] = "low"
                claim["note"] = (claim["note"] + " [quote not found in source]").strip()
            out.append(claim)
        return out

    def extract_legal_chunk(self, passage: str, genre: str = "legislation",
                            act_id: str = "", act_title: str = "",
                            locator: str = "", section: str = "",
                            rejected_out: list | None = None) -> list[dict]:
        """Legal genre profile (SPEC-legal-drafting.md 3.1-3.2).

        Every returned claim carries a ``source_quote`` found verbatim in the
        passage, its ``locator`` and ``act_id``, and the clause type,
        quantifier and modality. A claim whose quote is not verbatim is
        dropped (check ``paraphrase-provenance``, both fidelity modes) and
        appended to ``rejected_out`` when given.
        """
        from . import legal_source as ls
        from .grounding import ground_claim

        src = LEGAL_GENRE_SOURCE.get(genre, LEGAL_GENRE_SOURCE["legislation"])
        if not act_id:
            act_id, detected_title = ls.detect_act(passage)
            act_title = act_title or detected_title
        prompt = LEGAL_EXTRACTION_PROMPT.format(
            source=src, act=act_title or act_id or "unknown",
            locator=locator or "unknown", passage=passage)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=8000,
            system=LEGAL_EXTRACTION_SYSTEM.format(source=src),
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp)
        text = "".join(b.text for b in resp.content if getattr(b, "text", None)).strip()
        data = _extract_json(text)
        units = ls.split_units(passage, act_id=act_id, act_title=act_title,
                               initial_locator=locator)
        out = []
        for c in data.get("claims", []):
            quote = (c.get("source_quote") or "").strip()
            claim = {
                "claim": (c.get("claim") or "").strip(),
                "source_quote": quote,
                "confidence": (c.get("confidence") or "low").strip().lower(),
                "note": (c.get("note") or "").strip(),
                "section": section,
                "genre": genre,
                "act_id": act_id,
                "act_title": act_title,
                "clause_type": _pick(c.get("clause_type"), _CLAUSE_TYPES, "other"),
                "subject": (c.get("subject") or "").strip(),
                "quantifier": _pick(c.get("quantifier"), _QUANTIFIERS, "some"),
                "modality": _pick(c.get("modality"), _MODALITIES, "none"),
            }
            alts = _alternatives(c.get("alternatives"))
            if alts:
                claim["alternatives"] = alts
            # A near-quote is snapped to the act's exact words (grounding.py);
            # only a quote that cannot be grounded is rejected.
            ground_claim(claim, passage)
            if not claim["quote_verified"]:
                claim["rejected"] = "paraphrase-provenance"
                if rejected_out is not None:
                    rejected_out.append(claim)
                continue
            quote = claim["source_quote"]
            unit = ls.locate(quote, units)
            claim["locator"] = unit.locator if unit else (locator or "unknown")
            if claim["clause_type"] == "definition":
                # Spec 5.3: "For the purposes of this Regulation" stays with
                # the definition so a reader can see its limit.
                from .legal_kernel import detect_scope
                scope = detect_scope(quote) or detect_scope(passage)
                if scope:
                    claim["scope"] = scope
            out.append(claim)
        return out

    def extract_text(
        self,
        text: str,
        section: str = "",
        chunk_chars: int = 8000,
        overlap: int = 400,
        delay: float = 0.3,
        min_confidence: str = "low",
    ) -> list[dict]:
        """Full-text extraction. Used by the CLI; the web UI prefers
        per-chunk streaming via extract_chunk for progress feedback."""
        rank = {"low": 0, "medium": 1, "high": 2}
        threshold = rank.get(min_confidence, 0)
        chunks = chunk_text(text, chunk_chars, overlap)
        all_claims: list[dict] = []
        for i, chunk in enumerate(chunks):
            claims = self.extract_chunk(chunk, section=section)
            for c in claims:
                c["chunk_index"] = i
                if rank.get(c.get("confidence", "low"), 0) >= threshold:
                    all_claims.append(c)
            time.sleep(delay)
        return all_claims
