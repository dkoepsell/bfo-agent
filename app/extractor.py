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

An atomic ontological claim does ONE of the following:
- asserts that some kind of entity exists
- asserts that one kind is a subkind of another (X is a Y)
- asserts that some entity bears a relation to another (X is part of Y, X realizes Y, X participates in Y, etc.)
- asserts that some entity has a property, quality, disposition, role, or function
- asserts that something is an individual of a particular kind (Socrates is a man)

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

For each claim, produce:
{{
  "claim": "A single short declarative sentence, 25 words or fewer, in the author's voice. Rephrase for clarity but do not change the commitment.",
  "source_quote": "The exact sentence(s) from the passage that motivate this claim, up to 40 words.",
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
    def __init__(self, model: str = ANTHROPIC_EXTRACTOR_MODEL):
        require_api_key()
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = model

    def extract_chunk(self, passage: str, section: str = "") -> list[dict]:
        prompt = EXTRACTION_PROMPT.format(section=section or "unknown", passage=passage)
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "text", None)).strip()
        data = _extract_json(text)
        claims = data.get("claims", [])
        # Normalize shape and attach section
        out = []
        for c in claims:
            out.append(
                {
                    "claim": (c.get("claim") or "").strip(),
                    "source_quote": (c.get("source_quote") or "").strip(),
                    "confidence": (c.get("confidence") or "low").strip().lower(),
                    "note": (c.get("note") or "").strip(),
                    "section": section,
                }
            )
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
