"""Authoring-time lints, so gaps are caught singly rather than accumulating.

Both of these report at creation time on purpose. Seventy-four conferred
capacities with no realization link is not seventy-four decisions; it is one
decision made seventy-four times because nothing said anything the first time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

from .. import bfo_catalog
from ..mlc_anchor import SOOL

IAO_DEFINITION = URIRef("http://purl.obolibrary.org/obo/IAO_0000115")
DEFINITION_DEFERRED = URIRef(SOOL + "definitionDeferred")
REALIZATION_DEFERRED = URIRef(SOOL + "realizationDeferred")

# Realizable dependent continuants: things that must be realizable in a process.
REALIZABLE_ANCHORS = ("BFO_0000017", "BFO_0000023", "BFO_0000016", "BFO_0000034")
OCCURRENT_ANCHORS = ("BFO_0000003", "BFO_0000015")

REALIZATION_PROPERTIES = {
    "BFO_0000054",  # realized in
    "BFO_0000055",  # realizes
    "RO_0000056",   # participates in
    "RO_0000057",   # has participant
}


@dataclass
class LintResult:
    id: str
    total: int
    offenders: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    coverage_pct: float = 0.0
    detail: str = ""

    @property
    def passed(self) -> bool:
        return not self.offenders

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "total": self.total,
            "offenders": self.offenders[:50],
            "offender_count": len(self.offenders),
            "deferred": self.deferred[:50],
            "deferred_count": len(self.deferred),
            "coverage_pct": self.coverage_pct,
            "detail": self.detail,
        }


def _named(g: rdflib.Graph) -> set[URIRef]:
    return {s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}


def _ancestor_fragments(g: rdflib.Graph) -> dict[str, set[str]]:
    parents: dict[str, set[str]] = {}
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            parents.setdefault(str(s), set()).add(str(o))
    closure: dict[str, set[str]] = {}

    def walk(node: str, seen: frozenset[str]) -> set[str]:
        if node in closure:
            return closure[node]
        acc: set[str] = set()
        for parent in parents.get(node, ()):
            if parent in seen:
                continue
            acc.add(parent)
            acc |= walk(parent, seen | {parent})
        closure[node] = acc
        return acc

    for node in list(parents):
        walk(node, frozenset({node}))
    return {k: {bfo_catalog.normalize_fragment(a) for a in v}
            for k, v in closure.items()}


def lint_definitions(source) -> LintResult:
    """C3. Every class carries a definition, or an explicit deferral with a reason.

    The reference artifact had 1,009 classes, 1,200 constructs, and not one
    definition. It was axiomatically rich and semantically undocumented, and
    nothing at authoring time said so.
    """
    g = source if isinstance(source, rdflib.Graph) else rdflib.Graph()
    if not isinstance(source, rdflib.Graph):
        g.parse(str(source))

    named = _named(g)
    defined = {s for s in g.subjects(IAO_DEFINITION, None) if isinstance(s, URIRef)}
    defined |= {s for s in g.subjects(OWL.equivalentClass, None)
                if isinstance(s, URIRef)}
    deferred = {s for s in g.subjects(DEFINITION_DEFERRED, None)
                if isinstance(s, URIRef)}

    offenders = sorted(str(c) for c in named - defined - deferred)
    covered = len(named & defined)
    pct = round(100 * covered / len(named), 2) if named else 0.0

    return LintResult(
        id="definitions.coverage",
        total=len(named),
        offenders=offenders,
        deferred=sorted(str(c) for c in deferred & named),
        coverage_pct=pct,
        detail=(f"{covered} of {len(named)} classes carry a definition ({pct}%). "
                f"{len(offenders)} carry neither a definition nor an explicit "
                f"deferral."),
    )


def lint_capacity_realization(source) -> LintResult:
    """C6. A conferred capacity has somewhere to be realized.

    Seventy-four capacities with no realization link across 440 occurrent
    classes is the accumulated form of this lint never having run.
    """
    g = source if isinstance(source, rdflib.Graph) else rdflib.Graph()
    if not isinstance(source, rdflib.Graph):
        g.parse(str(source))

    named = _named(g)
    ancestors = _ancestor_fragments(g)
    deferred = {s for s in g.subjects(REALIZATION_DEFERRED, None)
                if isinstance(s, URIRef)}

    realizable: list[URIRef] = []
    for c in named:
        frags = ancestors.get(str(c), set()) | {bfo_catalog.normalize_fragment(str(c))}
        if frags & set(REALIZABLE_ANCHORS):
            realizable.append(c)

    def has_realization(c: URIRef) -> bool:
        for pred in g.predicates(c, None):
            if bfo_catalog.normalize_fragment(str(pred)) in REALIZATION_PROPERTIES:
                return True
        for pred in g.predicates(None, c):
            if bfo_catalog.normalize_fragment(str(pred)) in REALIZATION_PROPERTIES:
                return True
        for expression in g.objects(c, RDFS.subClassOf):
            if isinstance(expression, URIRef):
                continue
            for prop in g.objects(expression, OWL.onProperty):
                if bfo_catalog.normalize_fragment(str(prop)) in REALIZATION_PROPERTIES:
                    return True
        return False

    offenders = sorted(str(c) for c in realizable
                       if c not in deferred and not has_realization(c))
    linked = len(realizable) - len(offenders)
    pct = round(100 * linked / len(realizable), 2) if realizable else 100.0

    return LintResult(
        id="capacity.realization",
        total=len(realizable),
        offenders=offenders,
        deferred=sorted(str(c) for c in deferred),
        coverage_pct=pct,
        detail=(f"{linked} of {len(realizable)} realizable entities carry a "
                f"realization link ({pct}%). A disposition, role or function "
                f"with nowhere to be realized is a capacity the artifact confers "
                f"and never uses."),
    )


def lint_all(source) -> dict[str, Any]:
    results = [lint_definitions(source), lint_capacity_realization(source)]
    return {
        "passed": all(r.passed for r in results),
        "lints": [r.to_dict() for r in results],
    }
