"""Source-aware ingestion for legal text (SPEC-legal-drafting.md section 2, L1).

A legal source is read into :class:`SourceUnit` records: one per paragraph or
point, each with its act, a structural locator ("art_4/pnt_7") and its
character span in the source. Extraction uses the units to

* chunk on article boundaries, so a chunk never loses its article heading;
* check that every claim's ``source_quote`` occurs verbatim in the text
  (whitespace and typographic quotes normalized, nothing else); and
* attach the locator of the unit the quote came from.

Only the plain-text reader is implemented (heading detection for "Article 4",
"Section 2", "Rule 7", "§ 164", DCFR "II.-6:101", numbered paragraphs and
lettered or numbered points). Structured readers (Formex 4, EUR-Lex XHTML,
Akoma Ntoso) are future work; a text with no detectable structure falls back
to locator "unknown", as the spec requires.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

LEGAL_GENRES = ("legislation", "model_rules", "policy")
GENRES = ("philosophy",) + LEGAL_GENRES


def is_legal_genre(genre: str | None) -> bool:
    return (genre or "").strip().lower() in LEGAL_GENRES


def normalize_genre(genre: str | None) -> str:
    g = (genre or "").strip().lower()
    return g if g in GENRES else "philosophy"


@dataclass
class SourceUnit:
    act_id: str
    act_title: str
    locator: str          # "art_4/par_1/pnt_7", "II.-6:101", "sec_164/par_1"
    kind: str             # definition|scope|establishment|conferral|norm|condition|remedy|other
    text: str             # verbatim, whitespace-normalized only
    offsets: tuple = field(default=(0, 0))   # (start, end) in the source text
    language: str = "en"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["offsets"] = list(self.offsets)
        return d


# --------------------------------------------------------------- act ids

_EU_ACT = re.compile(
    r"\b(Regulation|Directive|Decision)\s*\((EU|EC|EEC|EU,\s*Euratom|EC,\s*Euratom)\)"
    r"\s*(?:No\.?\s*)?(\d{4})/(\d{1,4})\b", re.I)
_EU_ACT_OLD = re.compile(
    r"\b(Regulation|Directive|Decision)\s+(?:\((?:EEC|EC)\)\s*)?(?:No\.?\s*)?"
    r"(\d{1,4})/(\d{2,4})/(EEC|EC|EU)\b", re.I)
_CELEX_TYPE = {"regulation": "R", "directive": "L", "decision": "D"}


def detect_act(text: str) -> tuple[str, str]:
    """Best-effort (act_id, act_title) from the first EU act cited in ``text``.

    Returns CELEX ids ("32016R0679" for Regulation (EU) 2016/679). Returns
    ("", "") when nothing is recognised; the caller then relies on a
    user-supplied id, and definitions without one are flagged unscoped.
    """
    head = text[:4000]
    m = _EU_ACT.search(head)
    if m:
        kind, _, year, num = m.groups()
        return (f"3{year}{_CELEX_TYPE[kind.lower()]}{int(num):04d}",
                " ".join(m.group(0).split()))
    m = _EU_ACT_OLD.search(head)
    if m:
        kind, a, b, _ = m.groups()
        # Old style: "Directive 93/13/EEC" is year/number; "Regulation (EEC)
        # No 1408/71" is number/year.
        year, num = (a, b) if kind.lower() == "directive" or len(a) <= 2 and len(b) > 2 else (b, a)
        if len(year) == 2:
            year = ("19" if int(year) > 50 else "20") + year
        return (f"3{year}{_CELEX_TYPE[kind.lower()]}{int(num):04d}",
                " ".join(m.group(0).split()))
    return "", ""


# ----------------------------------------------------- verbatim matching

_TYPO = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"',
    "–": "-", "—": "-", "‑": "-", " ": " ",
})


def _normalize_with_map(s: str) -> tuple[str, list[int]]:
    """Collapse whitespace runs to one space and unify typographic quotes and
    dashes. Returns the normalized string and, per normalized char, the index
    of the original char it came from."""
    out: list[str] = []
    idx: list[int] = []
    prev_space = True  # strips leading whitespace
    for i, ch in enumerate(s.translate(_TYPO)):
        if ch.isspace():
            if not prev_space:
                out.append(" ")
                idx.append(i)
            prev_space = True
            continue
        out.append(ch)
        idx.append(i)
        prev_space = False
    if out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def normalize(s: str) -> str:
    return _normalize_with_map(s or "")[0]


def find_verbatim(quote: str, text: str) -> tuple[int, int] | None:
    """(start, end) of ``quote`` in ``text`` under whitespace/typography
    normalization, or None. Ellipses and paraphrase do not match: that is the
    point (spec 3.2, check ``paraphrase-provenance``)."""
    q = normalize(quote)
    if not q:
        return None
    t, idx = _normalize_with_map(text or "")
    pos = t.find(q)
    if pos < 0:
        return None
    return idx[pos], idx[pos + len(q) - 1] + 1


def quote_is_verbatim(quote: str, text: str) -> bool:
    return find_verbatim(quote, text) is not None


# ---------------------------------------------------------- the reader

_ARTICLE = re.compile(r"^\s*(?:Article|ARTICLE|Art\.)\s+(\d+[a-z]?)\b\.?\s*(.*)$")
_SECTION = re.compile(r"^\s*(?:Section|SECTION|Sec\.)\s+(\d+[a-z]?)\b\.?\s*(.*)$")
_RULE = re.compile(r"^\s*(?:Rule|RULE)\s+(\d+[a-z]?)\b\.?\s*(.*)$")
_PARA_SIGN = re.compile(r"^\s*§+\s*(\d+[a-z]?)\b\.?\s*(.*)$")
_DCFR = re.compile(r"^\s*([IVX]+\.\s?[-–]\s?\d+:\d+[a-z]?)\b\s*(.*)$")
_PARAGRAPH = re.compile(r"^\s*(\d{1,3})\.\s+\S")
_POINT = re.compile(r"^\s*\((\d{1,3}|[a-z]{1,4})\)\s+\S")


def _heading(line: str) -> tuple[str, str] | None:
    for rx, prefix in ((_ARTICLE, "art_"), (_SECTION, "sec_"), (_RULE, "rule_"),
                       (_PARA_SIGN, "sec_"), (_DCFR, "")):
        m = rx.match(line)
        if m:
            loc = prefix + re.sub(r"\s+", "", m.group(1)).replace("–", "-")
            return loc, m.group(2).strip()
    return None


_KIND_RULES = (
    ("definition", re.compile(r"\bmeans\b|\bshall mean\b|\bshall be understood\b|\bis defined as\b", re.I)),
    ("scope", re.compile(r"\b(?:applies|shall (?:not )?apply) to\b|\bshall not apply\b", re.I)),
    ("establishment", re.compile(r"\bis hereby established\b|\blegal personality\b", re.I)),
    ("remedy", re.compile(r"\blodge a complaint\b|\bjudicial remedy\b|\bshall be liable\b|"
                          r"\badministrative fines?\b|\bcompensation\b|\bpenalt(?:y|ies)\b", re.I)),
    ("conferral", re.compile(r"\bshall (?:designate|appoint)\b|\bshall be appointed\b|"
                             r"\bshall have the power\b|\bis empowered\b", re.I)),
    ("condition", re.compile(r"^\s*(?:\(\w+\)\s*)?(?:where|in the case of|if)\b", re.I)),
    ("norm", re.compile(r"\bshall\b|\bmay\b|\bis entitled to\b|\bhas the right\b|\bmust\b", re.I)),
)


def classify_kind(text: str) -> str:
    for kind, rx in _KIND_RULES:
        if rx.search(text):
            return kind
    return "other"


def split_units(text: str, act_id: str = "", act_title: str = "",
                language: str = "en", base_offset: int = 0,
                initial_locator: str = "") -> list[SourceUnit]:
    """Read plain legal text into paragraph/point units.

    A heading line ("Article 4", "§ 164") opens a new top-level locator; a
    numbered paragraph ("1. ...") and a lettered or numbered point ("(a) ...",
    "(7) ...") open sub-locators. Text before any heading is one unit with
    locator ``initial_locator`` or "unknown".
    """
    units: list[SourceUnit] = []
    top = initial_locator or ""
    par = pnt = ""
    cur_start: int | None = None
    cur_loc = ""

    def flush(end: int):
        nonlocal cur_start
        if cur_start is None:
            return
        raw = text[cur_start:end]
        body = normalize(raw)
        if body:
            units.append(SourceUnit(
                act_id=act_id, act_title=act_title,
                locator=cur_loc or "unknown", kind=classify_kind(body),
                text=body,
                offsets=(base_offset + cur_start, base_offset + end),
                language=language))
        cur_start = None

    pos = 0
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        stripped = line.strip()
        if not stripped:
            continue
        h = _heading(stripped)
        if h and len(stripped) < 200:
            flush(start)
            top, par, pnt = h[0], "", ""
            continue
        m_par = _PARAGRAPH.match(line)
        m_pnt = None if m_par else _POINT.match(line)
        if m_par or m_pnt or cur_start is None:
            flush(start)
            if m_par:
                par, pnt = m_par.group(1), ""
            elif m_pnt:
                pnt = m_pnt.group(1)
            parts = [p for p in (top, f"par_{par}" if par else "",
                                 f"pnt_{pnt}" if pnt else "") if p]
            cur_loc = "/".join(parts)
            cur_start = start
    flush(len(text))
    return units


def has_structure(units: list[SourceUnit]) -> bool:
    return any(u.locator != "unknown" for u in units)


def locate(quote: str, units: list[SourceUnit]) -> SourceUnit | None:
    """The unit whose text contains ``quote`` verbatim; for a quote spanning
    units, the first unit it starts in. None when the quote is not found."""
    q = normalize(quote)
    if not q:
        return None
    for u in units:
        if q in u.text:
            return u
    joined = ""
    starts = []
    for u in units:
        starts.append(len(joined))
        joined += u.text + " "
    pos = joined.find(q)
    if pos < 0:
        return None
    owner = None
    for u, s in zip(units, starts):
        if s <= pos:
            owner = u
    return owner


def chunk_units(text: str, chunk_chars: int = 8000) -> list[dict]:
    """Chunk legal text on top-level heading boundaries.

    Returns ``[{"text": str, "offset": int, "locator": str}]`` where ``offset``
    is the chunk's start in ``text`` and ``locator`` the top-level locator in
    force at that point (so a chunk that begins mid-article still knows its
    article). Every chunk's text is an exact slice of the source.
    """
    text = text.replace("\r\n", "\n")
    # Top-level heading positions.
    marks: list[tuple[int, str]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        h = _heading(line.strip()) if line.strip() and len(line.strip()) < 200 else None
        if h:
            marks.append((pos, h[0]))
        pos += len(line)
    bounds = [0] + [p for p, _ in marks if p > 0] + [len(text)]
    sections = [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)
                if bounds[i + 1] > bounds[i]]

    def loc_at(off: int) -> str:
        loc = ""
        for p, l in marks:
            if p <= off:
                loc = l
        return loc

    chunks: list[dict] = []
    cur_s = cur_e = None
    for s, e in sections:
        if e - s > chunk_chars:
            if cur_s is not None:
                chunks.append((cur_s, cur_e))
                cur_s = None
            # Split an oversize section on blank lines, then hard.
            i = s
            while i < e:
                j = min(e, i + chunk_chars)
                if j < e:
                    cut = text.rfind("\n\n", i + chunk_chars // 2, j)
                    if cut <= i:
                        cut = text.rfind("\n", i + chunk_chars // 2, j)
                    j = cut + 1 if cut > i else j
                chunks.append((i, j))
                i = j
            continue
        if cur_s is None:
            cur_s, cur_e = s, e
        elif e - cur_s <= chunk_chars:
            cur_e = e
        else:
            chunks.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    if cur_s is not None:
        chunks.append((cur_s, cur_e))
    return [{"text": text[s:e], "offset": s, "locator": loc_at(s)}
            for s, e in chunks if text[s:e].strip()]
