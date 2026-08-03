"""Projection of norm tuples onto capacities, and the pathway search K-D3 runs.

`CapacityRecord`s are derived, not extracted: every POWER or PERMISSION tuple
projects to one (norm_extractor_spec_v1.md §1.2). Nothing here calls a model —
this is a search over tuples already in hand.
"""
from __future__ import annotations

import re

from .model import CapacityRecord, Modality, NormTuple


CAPACITY_MODALITIES = {Modality.POWER, Modality.PERMISSION}

# Coverage of the capacity's content words that a candidate must reach to count
# as referring to the same capacity on wording alone.
COVERAGE_STRONG = 0.5

_STOPWORDS = {
    "a", "an", "and", "any", "are", "as", "at", "be", "been", "by", "for", "from",
    "has", "have", "if", "in", "is", "it", "its", "may", "must", "no", "not", "of",
    "on", "or", "shall", "such", "that", "the", "their", "them", "there", "this",
    "to", "under", "upon", "was", "were", "which", "who", "will", "with", "within",
}

# motion/movant/moving all denote the same act; the stemmer alone will not join them.
_SYNONYMS = {
    "motion": "move", "motions": "move", "movant": "move", "movants": "move",
    "moved": "move", "moves": "move", "moving": "move",
    "application": "apply", "applications": "apply", "applicant": "apply",
    "petition": "petit", "petitions": "petit", "petitioner": "petit",
}

# Compared after the leading article is stripped.
_VAGUE_COUNTERPARTIES = {
    "", "any person", "any party", "anyone", "others", "other parties", "public",
    "parties", "all parties", "someone", "persons", "person", "people", "n/a", "none",
}

# Signals that a tuple supplies an invocation pathway (spec §3 step 2).
_PATHWAY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("motion", re.compile(r"\b(motion|move[sd]?|moving|movant|appl(y|ies|ication)|petition|request)\w*\b", re.I)),
    ("filing", re.compile(r"\b(fil(e|es|ed|ing)|lodge[sd]?|submit\w*|serve[sd]?|service)\b", re.I)),
    ("notice", re.compile(r"\bnotice\b|\bnotif\w*", re.I)),
    ("hearing", re.compile(r"\bhearing\b|\bconference\b|\bbe heard\b", re.I)),
    ("form", re.compile(r"\b(affidavit|declaration|certif\w*|verified|in writing|written|"
                        r"must (state|include|contain|specify|set out|name)|form)\b", re.I)),
    ("order", re.compile(r"\border\b|\bdirect\w*\b", re.I)),
]


def _stem(word: str) -> str:
    """Cheap suffix stripper. Good enough to join perpetuate/perpetuating and
    testimony/testimonies; no linguistic claim beyond that."""
    w = _SYNONYMS.get(word, word)
    if w != word:
        return w
    for suffix in ("ations", "ation", "ements", "ement", "ingly", "ing", "ies", "ied",
                   "ers", "er", "ed", "es", "s", "e"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def content_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {_stem(w) for w in words if w not in _STOPWORDS and len(w) > 2}


def rule_root(locator: str) -> str:
    """"Rule 27(a)(1)" -> "rule 27"; "28 U.S.C. 2073(e)" -> "28 u.s.c. 2073"."""
    loc = (locator or "").strip().lower()
    if not loc:
        return ""
    # Drop the subdivision parentheticals; what remains is the rule or section.
    return re.sub(r"\(.*$", "", loc).strip(" .,;")


def _searchable(t: NormTuple) -> str:
    return " ".join([t.action, t.source_quote, " ".join(t.conditions)])


def _coverage(capacity_tokens: set[str], candidate_text: str) -> float:
    if not capacity_tokens:
        return 0.0
    cand = content_tokens(candidate_text)
    return len(capacity_tokens & cand) / len(capacity_tokens)


def _has_specific_counterparty(t: NormTuple) -> bool:
    cp = (t.counterparty or "").strip().lower()
    cp = re.sub(r"^(the|a|an)\s+", "", cp)
    return bool(cp) and cp not in _VAGUE_COUNTERPARTIES


# A capacity's own tuple can specify its invocation pathway inline — Rule
# 65(b)(1) does exactly that — but two of the signals do not survive that move.
# A power's named counterparty is the object it acts on, not a route for
# invoking it, and "may issue an order" is the power restated.
_NOT_SELF_SUPPLIED = {"recipient", "order"}


def pathway_kinds(t: NormTuple, is_self: bool = False) -> list[str]:
    """Which of the five pathway signals this tuple supplies, if any."""
    kinds: list[str] = []
    text = _searchable(t)
    for kind, pattern in _PATHWAY_PATTERNS:
        if pattern.search(text):
            kinds.append(kind)
    if t.deadline:
        kinds.append("deadline")
    if _has_specific_counterparty(t):
        kinds.append("recipient")
    # A form/content specification counts when the tuple is a duty, or when the
    # requirement sits in an antecedent — a power that merely happens to mention
    # writing is not a route for invoking itself.
    if "form" in kinds and t.modality != Modality.DUTY:
        _, form_pattern = next(k for k in _PATHWAY_PATTERNS if k[0] == "form")
        if not form_pattern.search(" ".join(t.conditions)):
            kinds.remove("form")
    if is_self:
        kinds = [k for k in kinds if k not in _NOT_SELF_SUPPLIED]
    return sorted(set(kinds))


def _addressed_to(candidate: NormTuple, bearer_role: str) -> bool:
    """Is this tuple's counterparty the actor who holds the capacity?"""
    role_tokens = content_tokens(bearer_role)
    if not role_tokens:
        return False
    return role_tokens <= content_tokens(candidate.counterparty or "")


def _refers_to(capacity_tokens: set[str], cap_tuple: NormTuple,
               candidate: NormTuple, candidate_tokens: set[str] | None = None) -> bool:
    if candidate.tuple_id == cap_tuple.tuple_id:
        return True     # the capacity's own tuple may specify its pathway inline
    if candidate_tokens is None:
        candidate_tokens = content_tokens(_searchable(candidate))
    if capacity_tokens and (
            len(capacity_tokens & candidate_tokens) / len(capacity_tokens)) >= COVERAGE_STRONG:
        return True
    # Sitting under the same rule is evidence of reference, but only when the
    # procedure involves the same actor — otherwise Rule 83(a)'s
    # notice-and-comment procedure for the district court would be read as a
    # route for invoking Rule 83(b)'s power of an individual judge.
    same_rule = (bool(rule_root(cap_tuple.source_locator))
                 and rule_root(candidate.source_locator) == rule_root(cap_tuple.source_locator))
    if not same_rule or not cap_tuple.bearer_role:
        return False
    # Same actor, or a procedure addressed to that actor: invoking an official's
    # power is normally somebody else's move, so Rule 26(c)(1)'s party motion
    # "in the court where the action is pending" is the route to the court's
    # power to issue a protective order.
    return (cap_tuple.bearer_role == candidate.bearer_role
            or _addressed_to(candidate, cap_tuple.bearer_role))


def _index(corpus: list[NormTuple]) -> dict[str, tuple[set[str], list[str]]]:
    """Tokens and pathway kinds per tuple, computed once.

    The search compares every capacity against every tuple, so anything derived
    from a tuple's own text must not be recomputed inside that loop — on a
    corpus the size of the Federal Rules it is the difference between two
    minutes and a few seconds.
    """
    return {t.tuple_id: (content_tokens(_searchable(t)), pathway_kinds(t)) for t in corpus}


def find_pathways(cap_tuple: NormTuple, corpus: list[NormTuple],
                  index: dict[str, tuple[set[str], list[str]]] | None = None,
                  ) -> tuple[list[str], list[str]]:
    """Return (pathway_loci, pathway_kinds) for one capacity tuple."""
    if index is None:
        index = _index(corpus)
    capacity_tokens = content_tokens(cap_tuple.action)
    loci: list[str] = []
    kinds: set[str] = set()
    for candidate in corpus:
        cand_tokens, cand_kinds = index[candidate.tuple_id]
        if not _refers_to(capacity_tokens, cap_tuple, candidate, cand_tokens):
            continue
        if candidate.tuple_id == cap_tuple.tuple_id:
            cand_kinds = [k for k in cand_kinds if k not in _NOT_SELF_SUPPLIED]
        if not cand_kinds:
            continue
        kinds.update(cand_kinds)
        if candidate.source_locator and candidate.source_locator not in loci:
            loci.append(candidate.source_locator)
    return loci, sorted(kinds)


def project_capacities(tuples: list[NormTuple]) -> list[CapacityRecord]:
    """Derive one CapacityRecord per POWER/PERMISSION tuple, verdict filled in.

    Externals are settled first and never searched: a capacity whose invocation
    procedure lives in a document we did not extract must not be scored as a
    source-level modal clash.
    """
    corpus = list(tuples)
    index = _index(corpus)
    records: list[CapacityRecord] = []
    for t in corpus:
        if t.modality not in CAPACITY_MODALITIES:
            continue
        if t.external_reference:
            records.append(CapacityRecord(
                tuple_id=t.tuple_id,
                bearer_role=t.bearer_role or t.bearer,
                capacity=t.action,
                external_reference=True,
                verdict="external",
            ))
            continue
        loci, kinds = find_pathways(t, corpus, index)
        records.append(CapacityRecord(
            tuple_id=t.tuple_id,
            bearer_role=t.bearer_role or t.bearer,
            capacity=t.action,
            pathway_loci=loci,
            pathway_kind=kinds,
            external_reference=False,
            verdict="pathway_found" if kinds else "no_pathway",
        ))
    return records
