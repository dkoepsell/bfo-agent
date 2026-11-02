"""Genre-independent source grounding for extracted claims.

Every claim the extractor returns carries a ``source_quote``. Models often
return a near-quote: a word dropped ("provided by devices" for "provided by
their devices"), a modal smoothed away ("Consent is a clear affirmative act"
for "Consent should be given by a clear affirmative act"), or a sentence
reworded. Provenance built from such a quote is not provenance.

:func:`ground_quote` resolves a quote against the passage it came from:

* an exact match (under the whitespace and typography normalization of
  :mod:`app.legal_source`) is kept as is;
* a close match is *snapped*: the quote is replaced by the exact span of the
  passage it best aligns with, so what is stored is always the source's own
  words. Snapping needs a long common anchor and a high word-level
  similarity, and it never crosses a sentence boundary, so a paraphrase
  cannot be laundered into a quote of something else;
* anything else is reported as unverified.

:func:`detect_genre` picks the extraction profile when the caller did not,
so legal text is no longer run through the philosophy prompt just because
the Genre control was left at its default.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from . import legal_source

# A snapped span must align this well with the model's quote, word by word.
SNAP_MIN_RATIO = 0.85
# ... share a run of at least this many consecutive words with it ...
SNAP_MIN_ANCHOR = 4
# ... and quotes shorter than this are only ever matched exactly.
SNAP_MIN_WORDS = 5

_WORD = re.compile(r"\S+")
_SENT_END = re.compile(r"[.;:!?]$")
# Tokens ending in "." that do not end a sentence: list numbers and the
# abbreviations legal and academic prose uses mid-sentence.
_NOT_A_BOUNDARY = re.compile(
    r"^(?:\(\w{1,4}\)|\d{1,3}\.|[ivxlc]{1,6}\.|No\.|Nos\.|Art\.|Arts\.|para\.|"
    r"e\.g\.|i\.e\.|cf\.|viz\.|vol\.|p\.|pp\.|ch\.|sec\.|Mr\.|Ms\.|Dr\.|St\.)$")


@dataclass
class Grounding:
    quote: str            # the text to store: verbatim source, or the input
    status: str           # "verbatim" | "snapped" | "unverified"
    start: int | None = None
    end: int | None = None
    ratio: float = 1.0

    @property
    def verified(self) -> bool:
        return self.status in ("verbatim", "snapped")


def _key(w: str) -> str:
    return re.sub(r"[^\w]", "", w.translate(legal_source._TYPO)).lower()


def ground_quote(quote: str, passage: str) -> Grounding:
    """Resolve ``quote`` against ``passage`` (see module docstring)."""
    quote = (quote or "").strip().strip('"').strip()
    if not quote or not passage:
        return Grounding(quote, "unverified", ratio=0.0)
    # Ellipses mark omitted text: never verbatim, never snapped.
    if "..." in quote or "…" in quote:
        return Grounding(quote, "unverified", ratio=0.0)
    hit = legal_source.find_verbatim(quote, passage)
    if hit:
        return Grounding(passage[hit[0]:hit[1]], "verbatim", hit[0], hit[1])

    q_words = [_key(w) for w in quote.split()]
    q_words = [w for w in q_words if w]
    if len(q_words) < SNAP_MIN_WORDS:
        return Grounding(quote, "unverified", ratio=0.0)
    tokens = [(m.start(), m.end(), _key(m.group())) for m in _WORD.finditer(passage)]
    tokens = [t for t in tokens if t[2]]
    if not tokens:
        return Grounding(quote, "unverified", ratio=0.0)
    p_words = [t[2] for t in tokens]

    sm = SequenceMatcher(None, p_words, q_words, autojunk=False)
    anchor = sm.find_longest_match(0, len(p_words), 0, len(q_words))
    if anchor.size < SNAP_MIN_ANCHOR:
        return Grounding(quote, "unverified", ratio=0.0)

    # Candidate windows around the anchor, with lengths near the quote's.
    n = len(q_words)
    best = (0.0, None, None)
    lo_start = max(0, anchor.a - anchor.b - 3)
    hi_start = min(len(p_words) - 1, anchor.a)
    for s in range(lo_start, hi_start + 1):
        for length in range(max(1, int(n * 0.8)), int(n * 1.25) + 2):
            e = s + length
            if e > len(p_words) or e <= anchor.a:
                continue
            r = SequenceMatcher(None, p_words[s:e], q_words, autojunk=False).ratio()
            if r > best[0]:
                best = (r, s, e)
    ratio, s, e = best
    if s is None or ratio < SNAP_MIN_RATIO:
        return Grounding(quote, "unverified", ratio=round(ratio, 3))
    # Never span a sentence boundary inside the window.
    for k in range(s, e - 1):
        raw = passage[tokens[k][0]:tokens[k][1]]
        if _SENT_END.search(raw) and not _NOT_A_BOUNDARY.match(raw):
            return Grounding(quote, "unverified", ratio=round(ratio, 3))
    start, end = tokens[s][0], tokens[e - 1][1]
    span = passage[start:end].rstrip(",;:")
    return Grounding(span, "snapped", start, start + len(span), round(ratio, 3))


_BOUNDARY = re.compile(r"(?:[.;:!?](?=\s)|\n\s*\n|\n\s*\(\w{1,4}\)\s)")
SENTENCE_MAX_CHARS = 600


def sentence_around(passage: str, start: int, end: int) -> str:
    """The sentence (or clause list item) of ``passage`` containing
    [start, end), whitespace-collapsed and capped at SENTENCE_MAX_CHARS.
    This is what lets the proposer see the modal verb or condition that
    governs a quoted phrase ("Consent should be given by ...")."""
    lo = max(0, start - SENTENCE_MAX_CHARS)
    head = passage[lo:start]
    cut = None
    for m in _BOUNDARY.finditer(head):
        cut = m.end()
    s = lo + cut if cut is not None else lo
    m = _BOUNDARY.search(passage, end - 1 if end > 0 else 0, end + SENTENCE_MAX_CHARS)
    e = m.end() if m else min(len(passage), end + SENTENCE_MAX_CHARS)
    text = re.sub(r"\s+", " ", passage[s:e]).strip()
    # A leading list or recital number ("(19)", "3.") is not part of it.
    text = re.sub(r"^(?:\(\w{1,4}\)|\d{1,3}\.)\s+", "", text)
    if len(text) > SENTENCE_MAX_CHARS:
        text = text[:SENTENCE_MAX_CHARS].rsplit(" ", 1)[0]
    return text


def ground_claim(claim: dict, passage: str) -> dict:
    """Ground ``claim['source_quote']`` in place; returns the claim.

    Sets ``source_quote`` to the stored text, ``quote_verified`` (bool),
    ``quote_grounding`` ("verbatim" | "snapped" | "unverified") and, for a
    verified quote, ``source_sentence`` (the whole sentence it sits in).
    When the model's quote was snapped, its original wording is kept in
    ``quote_model`` so a reviewer can see what changed."""
    g = ground_quote(claim.get("source_quote") or "", passage)
    if g.status == "snapped":
        claim["quote_model"] = claim.get("source_quote")
    claim["source_quote"] = g.quote
    claim["quote_verified"] = g.verified
    claim["quote_grounding"] = g.status
    if g.verified and g.start is not None:
        sent = sentence_around(passage, g.start, g.end)
        if sent and legal_source.normalize(sent).rstrip(".;:") != \
                legal_source.normalize(g.quote).rstrip(".;:"):
            claim["source_sentence"] = sent
    return claim


# ------------------------------------------------------------ genre

_ARTICLE_HEAD = re.compile(r"(?m)^\s*(?:Article|ARTICLE|Art\.|Section|SECTION|§)\s+\d+[a-z]?\b")
_SHALL = re.compile(r"\bshall\b", re.I)
_LEGAL_MARKERS = re.compile(
    r"\b(?:Whereas:|HAVE ADOPTED THIS|hereby (?:enacted|established)|"
    r"Official Journal of the European Union|BE IT ENACTED|"
    r"for the purposes of this (?:Regulation|Directive|Act|Decision))", re.I)


def detect_genre(text: str) -> str:
    """'legislation' when ``text`` reads as an enacted legal instrument,
    otherwise 'philosophy' (the default profile). Conservative: it needs at
    least two independent signals."""
    if not text:
        return "philosophy"
    sample = text[:200_000]
    words = max(1, len(sample.split()))
    signals = 0
    if legal_source.detect_act(sample)[0]:
        signals += 1
    if len(_ARTICLE_HEAD.findall(sample)) >= 2:
        signals += 1
    if _LEGAL_MARKERS.search(sample):
        signals += 1
    if len(_SHALL.findall(sample)) * 1000 / words >= 3:
        signals += 1
    return "legislation" if signals >= 2 else "philosophy"


def resolve_genre(requested: str | None, text: str) -> str:
    """The genre to extract with: an explicit choice wins; blank or 'auto'
    is detected from the text."""
    req = (requested or "").strip().lower()
    if req and req != "auto" and req in legal_source.GENRES:
        return req
    return detect_genre(text)
