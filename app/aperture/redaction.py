"""Patterns that must never reach a client-facing surface.

Used in two places, and it matters that it is the same list in both:

* :mod:`app.aperture.schema` validates ``client_label`` against it at manifest
  *load* time, so a leaky label fails the load rather than the render.
* the client-profile leak tests run it over every rendered fixture.

The list covers framework internals only. It deliberately does not try to catch
ordinary English words that happen to name an instrument, such as "structural",
because a client label is allowed to describe what kind of checking was done.
"""
from __future__ import annotations

import re

# (compiled pattern, what it would disclose)
LEAK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"K-[A-D]\d"), "kernel primitive code"),
    (re.compile(r"\bLC-\d+"), "legal contradiction code"),
    (re.compile(r"\bCT-\d+"), "classificatory type code"),
    (re.compile(r"(?i)\bstrat(?:um|a)\s*[A-D]\b"), "stratum letter"),
    (re.compile(r"(?i)\b(?:app|aperture|recognition)\.[a-z_][a-z0-9_.]*"),
     "dotted module path"),
    (re.compile(r"(?i)\b(?:dl_reasoner|world_evidence|process_model)\b"),
     "instrument identifier"),
)


def find_leaks(text: str) -> list[tuple[str, str]]:
    """Return ``(matched_text, what_it_discloses)`` for every leak in ``text``.

    An empty list means the text is safe for a client surface.
    """
    hits: list[tuple[str, str]] = []
    for pattern, disclosure in LEAK_PATTERNS:
        for match in pattern.finditer(text or ""):
            hits.append((match.group(0), disclosure))
    return hits
