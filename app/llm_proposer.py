"""LLM proposer: turns an utterance into a structured BFO-typed proposal.

The design principle: the LLM never commits anything. It proposes structured,
typed JSON that the user reviews and the reasoner validates.
"""
from __future__ import annotations

import json
from typing import Optional

from anthropic import Anthropic

from .config import ANTHROPIC_API_KEY, ANTHROPIC_MODEL, require_api_key
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

Key BFO relations (use these predicates when possible):
  BFO_0000050  part of
  BFO_0000051  has part
  RO_0000052   inheres in          (SDC -> bearer)
  RO_0000053   bearer of           (bearer -> SDC)
  BFO_0000054  realized in         (realizable -> process)
  BFO_0000055  realizes            (process -> realizable)
  RO_0000056   participates in     (continuant -> process)
  RO_0000057   has participant     (process -> continuant)
  rdfs:subClassOf                  (class -> class)
  rdf:type                         (individual -> class)
"""


PROMPT_TEMPLATE = """You are a BFO-grounded ontology proposer. You never assert anything without typing it against Basic Formal Ontology (BFO 2020). Your output is strict JSON, no prose.

{bfo_primer}

You are building a persistent, consistency-checked knowledge graph through dialogue. The user will make natural-language statements and questions. For each utterance, you propose candidate additions to the graph. A reasoner (HermiT) will validate your proposal before anything is committed, and a human will confirm or edit.

RULES:
1. Every entity must have a BFO type (fragment like BFO_0000040).
2. Prefer to reuse existing classes and individuals listed below over minting new ones.
3. When unsure about typing, list the choice in `open_questions` for the user.
4. Proper names (people, specific documents, specific places) are individuals, not classes.
5. Common nouns denoting kinds (Mother, Contract, Jurisdiction) are classes.
6. A person bearing a social function (mother, judge, CEO) is modeled as the person plus a role they bear; the role is an instance of the role class. Use `RO_0000053` (bearer of) or `RO_0000052` (inheres in).
7. Be conservative. If the utterance does not actually assert new ontological commitments, return empty `entities` and `relations` and explain in `rationale_summary`.
8. When minting IRIs, use `working:LocalName` for new items. Use CamelCase for classes, UpperCamel for individuals.

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
    def __init__(self, model: str = ANTHROPIC_MODEL):
        require_api_key()
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
        self.model = model

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
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": dynamic_user}],
        )

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

