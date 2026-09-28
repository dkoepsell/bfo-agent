"""Parsers for the legacy text forms that earlier runs baked into IRIs
(SPEC-bfo-agent-quality.md QS-C1 steps 4, 5, 7).

Everything here is a pure function over strings, so the migration script and
its tests share one definition of what is recoverable.  A restriction is
recovered only when the text consists of exactly a property, an optional
quantifier, an optional negation and a filler; anything with leftover words
is not guessed at and falls through to the comment path.
"""
from __future__ import annotations

import re

OBO = "http://purl.obolibrary.org/obo/"

# A term: an OBO id (optionally prefixed), a working:/prefixed local name, or
# a bare CamelCase local name.
_OBO_ID = r"(?:(?:bfo|ro|iao|obo|BFO|RO|IAO|OBO):)?(?:BFO|RO|IAO|OBI)_\d+"
_LOCAL = r"(?:working:|:)?[A-Za-z_][\w.\-]*"

_QUANT = {
    "some": "some", "somevaluesfrom": "some", "owl:somevaluesfrom": "some",
    "only": "only", "allvaluesfrom": "only", "owl:allvaluesfrom": "only",
    "value": "value", "hasvalue": "value", "owl:hasvalue": "value",
}
_NOISE = {
    "owl:restriction", "owl:restriction:", "restriction", "rdf:type", "a",
    "on", "owl:onproperty", "onproperty", "property", ":",
}
_NEG = {"not", "owl:complementof", "complementof"}

_OBO_ID_RE = re.compile(rf"^{_OBO_ID}$")
_LOCAL_RE = re.compile(rf"^{_LOCAL}$")
_UPPER_ID = re.compile(r"^(?:BFO|RO|IAO|OBI)_\d+$")

# Hints that a text blob was *meant* as a restriction (step 4) rather than a
# gloss (step 5): unparseable text with one of these is ledgered too.
RESTRICTION_HINT = re.compile(
    r"_:|\[|\]|\bsome\b|\bonly\b|onProperty|someValuesFrom|allValuesFrom|"
    r"owl:Restriction|\b(?:BFO|RO|IAO)_\d+", re.I)


def tokenize(text: str) -> list[str]:
    """Split restriction-ish text into tokens, dropping punctuation noise."""
    t = text.strip()
    # 'BFO_0000052some bfo:BFO_0000040' -- a quantifier glued to an id.
    t = re.sub(r"(_\d+)(some|only|value)\b", r"\1 \2", t)
    t = re.sub(r"^_:[^\s\[\(]*", " ", t)  # leading bnode label
    t = re.sub(r"_:restriction", " ", t)  # '_:restriction(BFO_0000054 ...'
    t = re.sub(r"[\[\]\(\);,]", " ", t)
    return [x for x in t.split() if x]


def parse_restriction_text(text: str) -> dict | None:
    """``{"prop", "quant", "neg", "filler", "defaulted"}`` or None.

    ``prop`` and ``filler`` are raw tokens (resolve with :func:`resolve_term`).
    ``defaulted`` is True when the text gave no quantifier and ``some`` was
    assumed (annotated ``bfoagent:quantifierDefaulted``)."""
    toks = [x for x in tokenize(text) if x.lower() not in _NOISE]
    if len(toks) < 2:
        return None
    prop = toks[0]
    if not (_OBO_ID_RE.match(prop) or _LOCAL_RE.match(prop)):
        return None
    rest = toks[1:]
    quant, defaulted = "some", True
    if rest and rest[0].lower() in _QUANT:
        quant, defaulted = _QUANT[rest[0].lower()], False
        rest = rest[1:]
    neg = False
    if rest and rest[0].lower() in _NEG:
        neg, rest = True, rest[1:]
    if len(rest) != 1:
        return None
    filler = rest[0]
    if not (_OBO_ID_RE.match(filler) or _LOCAL_RE.match(filler)):
        return None
    # A bare two-word phrase ('Theoretical Framework') is prose, not a
    # restriction: without an explicit quantifier the property must be an id.
    if defaulted and not _OBO_ID_RE.match(prop):
        return None
    if neg and quant == "value":
        return None
    return {"prop": prop, "quant": quant, "neg": neg, "filler": filler,
            "defaulted": defaulted}


def resolve_term(tok: str, working_ns: str) -> str | None:
    """Resolve a restriction-text token to a full IRI, or None if it is not a
    clean name (never mint a malformed IRI)."""
    tok = tok.strip()
    if tok.startswith(("http://", "https://")):
        return tok
    if ":" in tok:
        pre, name = tok.split(":", 1)
        if pre.lower() in ("bfo", "ro", "iao", "obo"):
            return OBO + name if _UPPER_ID.match(name) else None
        if pre not in ("working", ""):
            return None
        tok = name
    if _UPPER_ID.match(tok):
        return OBO + tok
    if re.match(r"^[A-Za-z_][\w.\-]*$", tok):
        return working_ns + tok
    return None


# Step 7: '_:restriction_inheres_in_InformationArtifact' style labels.
_LABEL_RELATIONS = {
    "inheres_in": "BFO_0000197", "inheresin": "BFO_0000197",
    "bearer_of": "BFO_0000196", "bearerof": "BFO_0000196",
    "participates_in": "BFO_0000056", "participatesin": "BFO_0000056",
    "has_participant": "BFO_0000057", "hasparticipant": "BFO_0000057",
    "realizes": "BFO_0000055", "has_realization": "BFO_0000054",
    "hasrealization": "BFO_0000054", "concretizes": "BFO_0000059",
    "is_concretized_by": "BFO_0000058", "occurs_in": "BFO_0000066",
}


def parse_bnode_label(label: str, known_fragments: set[str]) -> tuple[str, str] | None:
    """Recover ``(relation_id, filler_fragment)`` from a descriptive bnode
    label when both halves are unambiguous: the relation matches a canonical
    relation name and the filler is an existing local class fragment."""
    body = label[2:] if label.startswith("_:") else label
    body = re.sub(r"^restriction_?", "", body, flags=re.I)
    low = body.lower()
    for key in sorted(_LABEL_RELATIONS, key=len, reverse=True):
        if low.startswith(key):
            filler = body[len(key):].lstrip("_")
            if filler and filler[0].islower():
                filler = filler[0].upper() + filler[1:]
            if filler in known_fragments:
                return _LABEL_RELATIONS[key], filler
    return None
