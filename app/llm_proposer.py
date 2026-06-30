"""LLM proposer: turns an utterance into a structured BFO-typed proposal.

The design principle: the LLM never commits anything. It proposes structured,
typed JSON that the user reviews and the reasoner validates.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from anthropic import Anthropic

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, CACHE_TTL, require_api_key
from .cached_client import Usage, cache_control, summarize

log = logging.getLogger(__name__)
from .schema import Proposal


# BFO top-level categories the proposer should lean on. Keeping this in the
# prompt means the proposer is forced to classify against something concrete
# even if the full BFO class list is not passed in.
BFO_PRIMER = """
BFO 2020 top-level categories (fragment, label, gloss):

CONTINUANT (BFO_0000002): persists through time, maintains identity
  Independent continuant (BFO_0000004): does not inhere in or depend on another
    Material entity (BFO_0000040): has mass and spatial extent; e.g. a person, a document, a building
    Immaterial entity (BFO_0000141): spatial regions, sites, boundaries
      Site (BFO_0000029): a place like a courtroom, a jurisdiction, a room
  Specifically dependent continuant (BFO_0000020): inheres in exactly one bearer
    Quality (BFO_0000019): a measurable property; e.g. height, color, a specific legal status-as-quality
    Realizable entity (BFO_0000017)
      Role (BFO_0000023): externally grounded; e.g. 'mother', 'judge', 'legal person'
      Disposition (BFO_0000016): internally grounded; e.g. fragility, the capacity to contract
  Generically dependent continuant (BFO_0000031): information/patterns that can be copied;
    e.g. the text of a statute, a contract's content, a software program

OCCURRENT (BFO_0000003): unfolds in time
  Process (BFO_0000015): a happening; e.g. a trial, a contract formation, a lifetime
  Temporal region (BFO_0000008): e.g. 'the year 2026'
  Spatiotemporal region (BFO_0000011)

Key BFO 2020 object properties (use THESE exact predicates; the RO_ aliases
are obsolete under BFO 2020 and are rejected):
  BFO_0000050  part of             (part -> whole)
  BFO_0000051  has part            (whole -> part)
  BFO_0000197  inheres in          (specifically dependent continuant -> bearer)
  BFO_0000196  bearer of           (bearer -> specifically dependent continuant)
  BFO_0000054  realized in         (realizable entity -> process)
  BFO_0000055  realizes            (process -> realizable entity)
  BFO_0000056  participates in     (continuant -> process)
  BFO_0000057  has participant     (process -> continuant)
  BFO_0000058  is concretized by   (GDC -> SDC/process)
  BFO_0000059  concretizes         (SDC/process -> GDC)
  BFO_0000066  occurs in           (process -> site/material entity)
  BFO_0000108  exists at           (entity -> temporal region)
  rdfs:subClassOf                  (class -> class)
  rdf:type                         (individual -> class)
"""


PROMPT_TEMPLATE = """You are a BFO-grounded ontology proposer. You never assert anything without typing it against Basic Formal Ontology (BFO 2020). Your output is strict JSON, no prose.

{bfo_primer}

You are building a persistent, consistency-checked knowledge graph through dialogue. The user will make natural-language statements and questions. For each utterance, you propose candidate additions to the graph. A reasoner (HermiT) will validate your proposal before anything is committed, and a human will confirm or edit.

CORE PRINCIPLE - ANCHOR, DON'T REGENERATE:
You select from BFO's fixed vocabulary; you do not invent ontology. The single
most common failure is minting a flat new class for every noun, producing
thousands of classes and almost no relations. Resist it. Most of what a
sentence asserts is better expressed as INDIVIDUALS typed to a kind plus
OBJECT-PROPERTY ASSERTIONS between them, not as new classes. A turn that emits
only new `subClassOf` edges and no object-property relations is almost always
wrong.

RULES:
1. Every entity MUST have a `bfo_type` that is a real BFO 2020 fragment (e.g.
   BFO_0000040). No untyped entities.
2. Reuse before minting. Search the existing classes/individuals below and set
   `is_new=false` with `existing_iri` when one fits. Mint a new class ONLY when
   the term denotes a genuine repeatable kind that (a) no existing class covers
   and (b) cannot be expressed as a BFO class expression. When in doubt, do not
   mint; prefer an individual or a property assertion.
3. Particulars are individuals; kinds are classes. A specific statute, person,
   court, or event is an individual typed to a class. Only recurring universals
   become classes. Lean toward individuals.
4. EMIT RELATIONS. For every entity you introduce, assert how it connects to
   the others using the BFO object properties above (inheres in, bearer of,
   realized in, participates in, part of, ...). Prefer a relation triple over a
   new class. This is mandatory, not optional.
5. NEVER name a class for an absence, lack, failure, or negation
   (no `AbsenceOf...`, `LackOf...`, `Non...`, `Invalid...`, `Failure...`).
   Model absence as the relevant contradiction/defect asserted on an
   individual, or as a restriction (e.g. `not (BFO_0000196 some X)` /
   cardinality 0), never as a primitive class.
6. NEVER bake a relation into a class name (no `NormAlignmentWithRule`,
   `XDependencyRelation`, `PartOfY`). The meaning is a triple: emit
   `X someProperty Y` using a BFO object property. Class names are simple genus
   terms, not sentences.
7. A person bearing a social function (mother, judge, CEO) is the person (a
   material entity, BFO_0000040) PLUS a role (BFO_0000023) they bear. Emit the
   role as an entity and connect it with `BFO_0000196` (bearer of) /
   `BFO_0000197` (inheres in) and `BFO_0000054` (realized in) to the process.
8. Be conservative. If the utterance asserts no new ontological commitment,
   return empty `entities`/`relations` and explain in `rationale_summary`.
9. When minting IRIs, use `working:LocalName`, CamelCase, a SINGLE simple genus
   token where possible (`Norm`, not `LegalNormConcept`).
10. A dependent continuant must CONSTRAIN, not just classify, and the
    constraint is an emitted relation - not prose. When you type something as a
    quality, emit `BFO_0000197` (inheres in) to the independent continuant that
    bears it. When you type something as a role/disposition/function, emit
    `BFO_0000054` (realized in) to the process it is realized in (and for
    function vs disposition note in the rationale whether the bearer was
    engineered/selected for it). Put residual uncertainty in `open_questions`.
11. If you genuinely need a primitive BFO cannot express and no class
    expression covers, DO NOT mint it. Note it in `open_questions` prefixed
    with "KEXT:" (a kernel-extension request for human review) and proceed
    without it.

CURRENT WORKING ONTOLOGY CONTEXT:

Existing working classes:
{working_classes}

Known individuals:
{known_individuals}

USER UTTERANCE:
\"\"\"{utterance}\"\"\"

Respond with ONLY a JSON object matching this schema:

{{
  "entities": [
    {{
      "label": "...",
      "iri_suggestion": "working:Name or bfo:BFO_00000xx",
      "bfo_type": "BFO_00000xx",
      "bfo_label": "...",
      "kind": "individual" or "class",
      "parent_class": null or "working:..." or "bfo:BFO_...",
      "rationale": "...",
      "is_new": true or false,
      "existing_iri": null or "full IRI if reusing"
    }}
  ],
  "relations": [
    {{
      "s": "working:...",
      "p": "bfo:BFO_... or rdfs:subClassOf or rdf:type",
      "o": "working:... or bfo:BFO_...",
      "rationale": "..."
    }}
  ],
  "open_questions": ["clarifying questions for the user, if any"],
  "rationale_summary": "one-paragraph explanation of your analysis"
}}

Return JSON only. No markdown fences, no commentary."""


class LLMProposer:
    def __init__(self, model: str = ANTHROPIC_MODEL, api_key: str | None = None,
                 on_usage=None):
        """Build a proposer.

        api_key: when None, falls back to the owner key in config (the legacy
            singleton/CLI/test path). When provided (BYOK), all calls bill to it.
        on_usage: optional callback(model, input_tokens, output_tokens) invoked
            after each LLM call so the caller can meter token usage. Resample
            calls go through the same client, so they are captured too.
        """
        if api_key is None:
            require_api_key()
            api_key = ANTHROPIC_API_KEY
        self.client = Anthropic(api_key=api_key)
        self.model = model
        self._on_usage = on_usage
        self.ttl = CACHE_TTL
        # Session token accumulator: tracks cache_read / cache_write so the
        # prompt-cache savings are measurable (§9). Same Usage/cost model as the
        # batch CachedAnthropic client, so both report identical numbers.
        self.usage = Usage()

    def _record_usage(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        if usage is None:
            return
        self.usage.add(usage)
        if self._on_usage:
            self._on_usage(
                self.model,
                getattr(usage, "input_tokens", 0) or 0,
                getattr(usage, "output_tokens", 0) or 0,
            )

    def stats(self) -> dict:
        """Cache-hit ratio and $ saved across this proposer's calls (§9)."""
        return summarize(self.usage, self.model, self.ttl)

    def report(self) -> dict:
        """Log the cache savings; returns the stats dict."""
        s = self.stats()
        log.info(
            "prompt-cache: %d calls, hit=%.1f%%, cost=$%.4f (uncached $%.4f), "
            "saved $%.4f (%.1f%%)",
            s["calls"], s["cache_hit_ratio"] * 100, s["cost_usd"],
            s["cost_without_cache_usd"], s["saved_usd"], s["saved_pct"],
        )
        return s

    def propose(
        self,
        utterance: str,
        session_id: str,
        working_classes: list[dict],
        known_individuals: list[dict],
    ) -> Proposal:
        prompt = PROMPT_TEMPLATE.format(
            bfo_primer=BFO_PRIMER,
            working_classes=json.dumps(working_classes, indent=2),
            known_individuals=json.dumps(known_individuals, indent=2),
            utterance=utterance,
        )

        # Split prompt into static (cached) and dynamic parts. The
        # BFO primer, rules, and schema are identical on every call and
        # account for ~80% of input tokens, so caching them drops cost
        # dramatically for a full-book feed.
        static_system, dynamic_user = _split_for_caching(prompt)

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=[{
                "type": "text",
                "text": static_system,
                "cache_control": cache_control(self.ttl),
            }],
            messages=[{"role": "user", "content": dynamic_user}],
        )
        self._record_usage(resp)

        text = "".join(
            block.text for block in resp.content if getattr(block, "text", None)
        )
        data = _extract_json(text)

        return Proposal(
            session_id=session_id,
            utterance=utterance,
            entities=data.get("entities", []),
            relations=data.get("relations", []),
            open_questions=data.get("open_questions", []),
            rationale_summary=data.get("rationale_summary", ""),
        )

    def answer_grounded(
        self,
        question: str,
        graph_context: str,
    ) -> dict:
        """Answer a question using ONLY the supplied graph context.

        Returns a dict with 'answer' and 'referenced_iris'. The orchestrator
        cross-checks referenced_iris against the actual graph to tag
        grounded vs ungrounded.
        """
        prompt = f"""You are answering a question using only a BFO-grounded knowledge graph. You must NOT use background knowledge. If the graph does not contain enough information to answer, say so explicitly.

GRAPH CONTEXT:
{graph_context}

QUESTION:
{question}

Respond with ONLY a JSON object:
{{
  "answer": "your answer or an explicit refusal",
  "referenced_iris": ["list of IRIs you drew on from the graph"],
  "grounded": true or false
}}

Return JSON only."""

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp)
        text = "".join(
            block.text for block in resp.content if getattr(block, "text", None)
        )
        return _extract_json(text)


def _extract_json(text: str) -> dict:
    """Robustly extract a JSON object from LLM output.

    Handles markdown fences and attempts a best-effort recovery when the
    JSON is truncated (e.g. because the model hit max_tokens mid-output).
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in LLM output:\n{text[:500]}")
    end = text.rfind("}")
    if end != -1:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass  # fall through to truncation recovery

    # Truncation recovery: try to parse what we can by closing open
    # brackets/braces and stripping trailing garbage. Works for the common
    # case where max_tokens cut off a long answer string.
    import re
    snippet = text[start:]
    # Close an unterminated string (odd number of unescaped quotes)
    quotes = len(re.findall(r'(?<!\\)"', snippet))
    if quotes % 2 == 1:
        snippet = snippet + '"'
    # Balance braces and brackets
    snippet += "}" * max(0, snippet.count("{") - snippet.count("}"))
    snippet = snippet.rsplit(",", 1)[0]  # drop trailing partial field
    snippet += "}" * max(0, snippet.count("{") - snippet.count("}"))
    try:
        return json.loads(snippet)
    except json.JSONDecodeError:
        raise ValueError(
            f"No parseable JSON object in LLM output (possibly truncated):\n{text[:500]}"
        )


def _split_for_caching(full_prompt: str) -> tuple[str, str]:
    """Split the fully-rendered proposer prompt into (static_system, dynamic_user).

    The static part contains everything up to and including the
    "CURRENT WORKING ONTOLOGY CONTEXT:" header boundary. The dynamic
    part contains the current graph summary and the user utterance,
    which changes every call and cannot be cached.
    """
    marker = "CURRENT WORKING ONTOLOGY CONTEXT:"
    idx = full_prompt.find(marker)
    if idx == -1:
        # Fallback: don't split, whole thing becomes user message.
        return "", full_prompt
    static = full_prompt[:idx].rstrip()
    dynamic = full_prompt[idx:]
    return static, dynamic

