"""LLM proposer: turns an utterance into a structured BFO-typed proposal.

The design principle: the LLM never commits anything. It proposes structured,
typed JSON that the user reviews and the reasoner validates.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from anthropic import Anthropic

from .config import (
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    CACHE_TTL,
    LLM_CALL_TIMEOUT_SECONDS,
    PROPOSER_TEMPERATURE,
    require_api_key,
)
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
12. DIFFERENTIAL EXCLUSION. When the source asserts that A is not a B, that A
    excludes B, or that A is "not better explained by" B, emit
    `{{"s": "working:A", "p": "rdfs:subClassOf", "o": "not working:B"}}`.
    When the exclusion is mutual, also emit
    `{{"s": "working:A", "p": "owl:disjointWith", "o": "working:B"}}`.
    The `o` value `"not working:B"` is the ONLY sanctioned negation syntax:
    NEVER write `owl:complementOf`, brackets, or any boolean operator as part
    of an IRI or class name.
13. SHARED CRITERIA. A symptom, criterion, or manifestation that can occur in
    more than one condition (fatigue, insomnia, poor concentration, sleep
    disturbance, psychomotor change, ...) is ONE class: mint it once with a
    general name and REUSE it (`is_new=false` + `existing_iri`) in every later
    condition that references it -- check REUSE CANDIDATES below first. A
    condition is NEVER `subClassOf` its symptom (that says the condition IS
    the symptom). Type the manifestation as a process (BFO_0000015) and link
    the condition to it with an existential restriction:
    `{{"s": "working:MajorDepressiveDisorder", "p": "rdfs:subClassOf",
    "o": "bfo:BFO_0000054 some working:Fatigue"}}` (the disposition is
    realized in such a process). The `o` form `"PROP some FILLER"` is the
    sanctioned restriction syntax.
14. DISEASE / DISORDER / CONDITION ANCHORING (OGMS-under-BFO). A disease,
    disorder, syndrome, or pathological condition is a DISPOSITION
    (BFO_0000016) borne by an organism, realized in pathological processes.
    Type it `BFO_0000016` and NEVER also as a material entity (BFO_0000040)
    or a process (BFO_0000015) -- those BFO categories are pairwise disjoint,
    so a class carrying two of them is rejected outright. One class, one
    top-level BFO category. Separate the three senses a disease term blurs,
    each its OWN class with its OWN single type:
      - the CONDITION itself ("hepatitis E", "tuberculosis" as the disorder)
        -> disposition (BFO_0000016);
      - the pathological/infectious PROCESS ("the infection", "the acute
        episode", inflammation) -> process (BFO_0000015);
      - the causal AGENT / pathogen ("Mycobacterium tuberculosis", a virus,
        a parasite) -> material entity (BFO_0000040).
    If you relate them, use ONLY the restriction syntax from rule 13
    (`"o": "bfo:BFO_0000054 some working:TheProcess"`) -- NEVER subclass a
    class directly to a bare property id like `bfo:BFO_0000054` (that is
    rejected). Relating the three is optional; typing each with one correct
    category is what matters. An anatomical structure or whole organism is a
    material entity (BFO_0000040).
15. CLASS NAMES ARE SHORT ATOMS. A class name (the IRI local part) is a
    single concept in CamelCase, ideally 2 and at most 3 meaningful tokens:
    `Smallpox`, `HepatitisE`, `VariolaVirus`, `CommonWart`. Put the full
    verbatim source term in `rdfs:label`, NOT in the name. NEVER fuse a
    description into the name: no `HumanPapillomavirusInfectionOfEpidermis`,
    no `AcuteHepatitisEVirusInfection` -- names with 4+ fused tokens are
    rejected. NEVER bake a relation word (Of, In, By, Due, Caused, Associated,
    Related) or a numeric/type qualifier suffix (`Type2`, `SubtypeB`) into the
    name; express those as separate classes or property assertions.

CURRENT WORKING ONTOLOGY CONTEXT:

Existing working classes:
{working_classes}

Known individuals:
{known_individuals}

USER UTTERANCE:
\"\"\"{utterance}\"\"\"

REUSE CANDIDATES (existing classes lexically matching this utterance; reuse
these with is_new=false instead of minting a near-duplicate):
{relevant_classes}

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
      "p": "bfo:BFO_... or rdfs:subClassOf or rdf:type or owl:disjointWith",
      "o": "working:... or bfo:BFO_... -- or, on a subClassOf edge only, 'not working:X' or 'bfo:BFO_xxx some working:X'",
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
        self.client = Anthropic(
            api_key=api_key,
            timeout=(LLM_CALL_TIMEOUT_SECONDS
                     if LLM_CALL_TIMEOUT_SECONDS > 0 else None),
        )
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
        relevant_classes: list[dict] | None = None,
    ) -> Proposal:
        system, user_message = build_prompt_blocks(
            utterance=utterance,
            working_classes=working_classes,
            known_individuals=known_individuals,
            relevant_classes=relevant_classes,
            ttl=self.ttl,
        )

        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            temperature=PROPOSER_TEMPERATURE,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        self._record_usage(resp)

        text = "".join(
            block.text for block in resp.content if getattr(block, "text", None)
        )
        return parse_proposal_response(text, session_id, utterance)

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
            temperature=PROPOSER_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp)
        text = "".join(
            block.text for block in resp.content if getattr(block, "text", None)
        )
        return _extract_json(text)


def build_prompt_blocks(
    utterance: str,
    working_classes: list[dict],
    known_individuals: list[dict],
    relevant_classes: list[dict] | None,
    ttl: str,
) -> tuple[list[dict], str]:
    """Render the proposer prompt into ``(system_blocks, user_message)``.

    Two cache breakpoints + compact context. The ontology snapshot (one
    compact line per term, ~3-4x fewer tokens than indented JSON) is the
    dominant per-call input cost on a feed, so we (a) shrink it and (b) give
    it its own breakpoint so it is a cache READ between commits. The static
    block is instructions + the JSON schema (lifted out of the dynamic tail,
    where it was being re-sent uncached every call). Only the per-claim
    utterance is uncached.

    Shared by the live :meth:`LLMProposer.propose` call and the batch prepare
    pass (``app/batch_propose.py``, SPEC-bfo-agent-speed.md change 5) so both
    produce byte-identical blocks -- batched entries then share the live
    path's prompt-cache prefix (best-effort).
    """
    prompt = PROMPT_TEMPLATE.format(
        bfo_primer=BFO_PRIMER,
        working_classes=_compact_lines(working_classes),
        known_individuals=_compact_lines(known_individuals),
        utterance=utterance,
        relevant_classes=_compact_lines(relevant_classes or []),
    )
    static_system, ontology_block, claim = _split_for_breakpoints(prompt)
    system = [{
        "type": "text",
        "text": static_system,
        "cache_control": cache_control(ttl),
    }]
    if ontology_block:
        system.append({
            "type": "text",
            "text": ontology_block,
            "cache_control": cache_control(ttl),
        })
    return system, (claim or prompt)


def parse_proposal_response(text: str, session_id: str, utterance: str) -> Proposal:
    """Parse a proposer completion into a :class:`Proposal`.

    Factored out of :meth:`LLMProposer.propose` so the batch prepare pass
    (``app/batch_propose.py``) parses batched results through the exact same
    path as live calls."""
    data = _extract_json(text)
    return Proposal(
        session_id=session_id,
        utterance=utterance,
        entities=data.get("entities", []),
        relations=data.get("relations", []),
        open_questions=data.get("open_questions", []),
        rationale_summary=data.get("rationale_summary", ""),
    )


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


def _local_name(ref: str) -> str:
    s = (ref or "").split("#")[-1]
    return s.split("/")[-1]


def _compact_lines(items: list[dict]) -> str:
    """Render classes/individuals as one compact line each instead of indented
    JSON, dropping the repeated namespace.

    On a feed this ontology snapshot is the dominant per-call *uncached* input
    (40 classes + 40 individuals as full ``{"iri","label","parents"}`` dicts is
    ~4k tokens). The same information as ``Label [Name] <: Parents`` is ~3-4x
    smaller, which directly cuts the per-call input bill -- the genuine win, more
    than caching, since this content changes whenever a claim commits.
    """
    if not items:
        return "(none)"
    lines = []
    for it in items:
        name = _local_name(it.get("iri") or it.get("label") or "")
        label = it.get("label") or name
        rel = it.get("parents") or it.get("types") or []
        rel = ", ".join(_local_name(r) for r in rel)
        head = name if label == name else f'{name} "{label}"'
        lines.append(f"- {head}" + (f" <: {rel}" if rel else ""))
    return "\n".join(lines)


def _split_for_breakpoints(full_prompt: str) -> tuple[str, str, str]:
    """Split the rendered prompt into (static, ontology, claim) for two cache
    breakpoints.

    The JSON schema sits at the END of the template, so today it is re-sent
    uncached on every call. We lift it into the static head, so the static block
    is instructions + schema (one stable cached prefix), the ontology snapshot is
    a second cached block (changes only when a claim commits -> a cache READ in
    between), and only the per-claim utterance is uncached.
    """
    onto_m = "CURRENT WORKING ONTOLOGY CONTEXT:"
    claim_m = "USER UTTERANCE:"
    schema_m = "Respond with ONLY a JSON object matching this schema:"
    i, j, k = (full_prompt.find(onto_m), full_prompt.find(claim_m),
               full_prompt.find(schema_m))
    if not (0 <= i < j < k):
        # Markers missing/reordered: degrade to the old single-breakpoint split.
        static, dynamic = _split_for_caching(full_prompt)
        return static, "", dynamic
    head = full_prompt[:i].rstrip()
    ontology = full_prompt[i:j].rstrip()
    claim = full_prompt[j:k].rstrip()
    schema = full_prompt[k:].rstrip()
    return head + "\n\n" + schema, ontology, claim


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

