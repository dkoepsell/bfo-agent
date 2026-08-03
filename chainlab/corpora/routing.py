"""Which extractor(s) each corpus gets (norm_extractor_spec_v1.md §0).

Act-thick corpora need both: FRCP is mostly deontic, but it still contains
genuine ontological content — Rule 7(a)'s closed list of pleadings, Rule 54(a)'s
definition of judgment — and the entity-relation extractor is the only thing
that sees it.
"""
from __future__ import annotations

ENTITY_RELATION = "entity_relation"
NORM = "norm"

EXTRACTOR_ROUTES: dict[str, tuple[str, ...]] = {
    "icd11": (ENTITY_RELATION,),
    "dsm": (ENTITY_RELATION,),
    "go": (ENTITY_RELATION,),
    "frcp": (ENTITY_RELATION, NORM),
    "nop_7cfr205": (ENTITY_RELATION, NORM),
}

# Unrecognized corpora keep the historical behaviour rather than silently
# gaining a second extractor.
DEFAULT_ROUTE: tuple[str, ...] = (ENTITY_RELATION,)


def extractors_for(corpus_id: str) -> tuple[str, ...]:
    return EXTRACTOR_ROUTES.get((corpus_id or "").strip().lower(), DEFAULT_ROUTE)
