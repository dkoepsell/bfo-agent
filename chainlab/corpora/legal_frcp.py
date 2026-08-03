"""FRCP corpus preparation (norm_extractor_spec_v1.md §6).

Roughly the first third of the document is front matter — committee rosters,
foreword, historical note, table of contents — which correctly yields `[]` and
costs a model call per chunk to say so. Drop it, but keep 28 U.S.C. §§ 2072-2074:
those sections are the authority link of the chain and hold two of the four
K-D3 candidate positives.

The retained span is recorded so the denominator for any rate is reproducible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

# The heading that opens the operative text, allowing for line breaks between
# its three lines and for varying whitespace.
_RULES_HEADING = re.compile(
    r"RULES\s+OF\s+CIVIL\s+PROCEDURE\s+FOR\s+THE\s+UNITED\s+STATES\s+DISTRICT\s+COURTS",
    re.I,
)

# "Authority for Promulgation of Rules" — the §§ 2072-2074 block.
_AUTHORITY_HEADING = re.compile(
    r"(AUTHORITY\s+FOR\s+PROMULGATION\s+OF\s+RULES"
    r"|§?\s*2072\.\s*Rules\s+of\s+procedure\s+and\s+evidence)",
    re.I,
)

# End of the retained authority block: § 2075 is bankruptcy rules, out of scope.
_AUTHORITY_END = re.compile(r"§?\s*2075\.", re.I)

# Rule 1 is the first operative rule, so a heading followed closely by it is the
# real start of the body rather than a title page or a table-of-contents entry.
_RULE_ONE = re.compile(r"^\s*Rule\s+1\.", re.I | re.M)
_RULE_ONE_WINDOW = 2000


def _operative_start(text: str) -> int | None:
    """Where the body begins.

    The heading appears on the title page and again above the rules themselves,
    and Rule 1 follows both — once as a table-of-contents line, once for real.
    The body is the later one. Taking the last qualifying heading also survives
    the phrase being used as a running page header, since a header printed above
    Rule 70 has no Rule 1 after it.

    Falls back to the first heading when none qualifies: keeping too much is
    recoverable, silently dropping the corpus is not.
    """
    matches = list(_RULES_HEADING.finditer(text))
    if not matches:
        return None
    qualifying = [m.start() for m in matches
                  if _RULE_ONE.search(text[m.end() : m.end() + _RULE_ONE_WINDOW])]
    return qualifying[-1] if qualifying else matches[0].start()


@dataclass
class RetainedSpan:
    """Provenance for the corpus denominator. Write this into source.json."""

    total_chars: int
    authority_start: int | None
    authority_end: int | None
    operative_start: int | None
    retained_chars: int
    dropped_chars: int
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def preprocess_frcp(text: str) -> tuple[str, RetainedSpan]:
    """Return (retained_text, span). Retains §§ 2072-2074 plus everything from
    the rules heading onward; drops foreword, roster, historical note, and TOC.

    If neither landmark is found the text is returned untouched, with a note —
    silently keeping everything is recoverable, silently dropping is not.
    """
    total = len(text)

    auth_match = _AUTHORITY_HEADING.search(text)
    op_start = _operative_start(text)

    if op_start is None and auth_match is None:
        return text, RetainedSpan(
            total_chars=total, authority_start=None, authority_end=None,
            operative_start=None, retained_chars=total, dropped_chars=0,
            notes="Neither the rules heading nor the authority block was found; "
                  "no front matter dropped.",
        )

    parts: list[str] = []
    auth_start = auth_end = None
    if auth_match is not None:
        auth_start = auth_match.start()
        end_match = _AUTHORITY_END.search(text, auth_start)
        # The authority block ends at § 2075, at the rules heading, or at EOF.
        candidates = [p for p in (
            end_match.start() if end_match else None,
            op_start if op_start is not None and op_start > auth_start else None,
        ) if p is not None]
        auth_end = min(candidates) if candidates else total
        parts.append(text[auth_start:auth_end].strip())

    if op_start is not None:
        parts.append(text[op_start:].strip())

    retained = "\n\n".join(p for p in parts if p)
    notes = ""
    if auth_match is None:
        notes = "Authority block (28 U.S.C. 2072-2074) not found; retained operative text only."
    elif op_start is None:
        notes = "Rules heading not found; retained the authority block only."

    return retained, RetainedSpan(
        total_chars=total,
        authority_start=auth_start,
        authority_end=auth_end,
        operative_start=op_start,
        retained_chars=len(retained),
        dropped_chars=total - len(retained),
        notes=notes,
    )
