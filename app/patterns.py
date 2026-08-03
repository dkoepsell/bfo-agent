"""Templates for recurring regulatory furniture, which cannot be instantiated incomplete.

The five dependence findings on the reference artifact are the substantively
valuable result in the whole bundle: 20/200 visual acuity, a 20-degree visual
field, a 12-month duration, and 10-pound and 20-pound weight limits. Each is a
specifically dependent continuant depending on nothing. These are exactly the
quantitative thresholds where Part 404 does its legal work, and each was modeled
as a dependent entity with no bearer.

A pattern is a required-field list plus the axioms that follow from it. The point
is not convenience: it is that ``instantiate`` raises on a missing field, so the
shape that produced those five findings cannot be authored in the first place.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

BFO = "http://purl.obolibrary.org/obo/"
RO_INHERES_IN = BFO + "RO_0000052"
BFO_REALIZED_IN = BFO + "BFO_0000054"


class PatternError(Exception):
    """A pattern was instantiated without something it requires.

    Raised rather than warned. A threshold with no bearer is the defect this
    module exists to prevent, so producing one has to be impossible rather than
    discouraged.
    """


@dataclass(frozen=True)
class Pattern:
    id: str
    name: str
    gloss: str
    required: tuple[str, ...]
    # What the finding looks like when the pattern is not used.
    prevents: str
    # A shape that suggests the pattern was wanted. Used by the lint.
    smell_anchors: tuple[str, ...] = ()

    def instantiate(self, **fields: Any) -> dict[str, Any]:
        missing = [f for f in self.required
                   if fields.get(f) in (None, "", [], {})]
        if missing:
            raise PatternError(
                f"{self.id}: cannot instantiate without {missing}. "
                f"{self.prevents}")
        return {
            "pattern": self.id,
            "fields": {k: fields[k] for k in self.required},
            "extra": {k: v for k, v in fields.items() if k not in self.required},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "gloss": self.gloss,
            "required": list(self.required), "prevents": self.prevents,
        }


PATTERNS: dict[str, Pattern] = {p.id: p for p in (
    Pattern(
        "quantitative_threshold", "Quantitative threshold",
        "a numeric limit that a norm applies to a measured quality of a bearer",
        ("bearer_class", "measured_quality", "value", "unit", "norm_class"),
        prevents=("A threshold authored without its bearer is a specifically "
                  "dependent continuant depending on nothing, which is what "
                  "20/200 visual acuity and the 10-pound weight limit each were."),
        smell_anchors=("BFO_0000019", "BFO_0000020"),
    ),
    Pattern(
        "duration_requirement", "Duration requirement",
        "a temporal minimum that qualifies a process",
        ("temporal_region", "process_class", "value", "unit"),
        prevents=("A duration with no process to qualify is a quality with no "
                  "bearer, which is what the 12-month duration was."),
        smell_anchors=("BFO_0000008", "BFO_0000019"),
    ),
    Pattern(
        "exclusion_clause", "Exclusion clause",
        "a carve-out from a positive class, with the condition that triggers it",
        ("excluded_from_class", "triggering_condition"),
        prevents=("An exclusion with no positive class to exclude from is a "
                  "category defined only as a residue."),
        smell_anchors=("BFO_0000020",),
    ),
    Pattern(
        "role", "Role",
        "a role, its bearer, and the institution that confers it",
        ("bearer_class", "conferring_institution"),
        prevents=("A role with no conferring institution is a status nothing "
                  "grants, and a role with no bearer inheres in nothing."),
        smell_anchors=("BFO_0000023",),
    ),
    Pattern(
        "evidentiary_source", "Evidentiary source",
        "a source of evidence and the assessor role licensed to credit it",
        ("assessor_role", "evidence_kind"),
        prevents=("An evidentiary source with no assessor to credit it is "
                  "evidence for nobody."),
        smell_anchors=("BFO_0000031",),
    ),
)}


def get(pattern_id: str) -> Pattern:
    try:
        return PATTERNS[pattern_id]
    except KeyError:
        raise PatternError(
            f"unknown pattern {pattern_id!r}; expected one of "
            f"{sorted(PATTERNS)}") from None


def instantiate(pattern_id: str, **fields: Any) -> dict[str, Any]:
    return get(pattern_id).instantiate(**fields)


@dataclass
class PatternSmell:
    cls: str
    pattern: str
    why: str

    def to_dict(self) -> dict[str, Any]:
        return {"class": self.cls, "pattern": self.pattern, "why": self.why}


# Words that suggest a class is one of these shapes. Lexical, and deliberately
# only used to raise a warning naming a pattern, never to classify anything.
_SMELL_WORDS = {
    "quantitative_threshold": ("threshold", "limit", "acuity", "pound", "degree",
                               "minimum", "maximum", "cutoff"),
    "duration_requirement": ("duration", "month", "period", "continuous"),
    "exclusion_clause": ("exclusion", "excluded", "carveback", "carve"),
    "role": ("role", "claimant", "examiner", "adjudicator"),
    "evidentiary_source": ("source", "evidence", "report", "opinion"),
}


def lint_patterns(source, limit: int = 50) -> dict[str, Any]:
    """Warn where a class has a pattern's shape but was authored without it.

    A warning, not an error, and it names the pattern rather than describing the
    problem abstractly, because the useful thing to say is which template would
    have carried the missing field.
    """
    import rdflib
    from rdflib import OWL, RDF, RDFS, URIRef

    from . import bfo_catalog

    g = source if isinstance(source, rdflib.Graph) else rdflib.Graph()
    if not isinstance(source, rdflib.Graph):
        g.parse(str(source))

    labels = {str(s): str(o) for s, o in g.subject_objects(RDFS.label)}
    named = [s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)]

    smells: list[PatternSmell] = []
    for cls in named:
        text = (labels.get(str(cls)) or str(cls).rsplit("#", 1)[-1]).lower()
        for pattern_id, words in _SMELL_WORDS.items():
            if not any(w in text for w in words):
                continue
            pattern = PATTERNS[pattern_id]
            smells.append(PatternSmell(
                cls=str(cls), pattern=pattern_id,
                why=(f"the name suggests a {pattern.name.lower()}. Authoring it "
                     f"through the {pattern_id} pattern would require "
                     f"{list(pattern.required)}.")))
            break

    return {
        "id": "patterns.usage",
        "smell_count": len(smells),
        "smells": [s.to_dict() for s in smells[:limit]],
        "patterns": [p.to_dict() for p in PATTERNS.values()],
        "note": ("A warning naming the pattern that would have carried the "
                 "missing fields. Nothing here is a defect on its own."),
    }
