"""One place that counts things, so a bundle cannot report two values for one name.

The observed collisions were ``object_properties`` at 11 and at 75, and
``classes_directly_bfo_anchored`` 683 against ``classes_directly_anchored`` 648.
Both pairs were correct: one counted the artifact alone, the other counted the
artifact merged with its imports. Neither name said so.

So every name here carries its scope. ``object_properties_local`` and
``object_properties_with_imports`` are different questions and cannot be
mistaken for the same one. Sections reference names from this provider; no
section counts for itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

BFO = "http://purl.obolibrary.org/obo/BFO_"
IAO_DEFINITION = URIRef("http://purl.obolibrary.org/obo/IAO_0000115")
SKOS_DEFINITION = URIRef("http://www.w3.org/2004/02/skos/core#definition")

_DEFINITION_PREDICATES = (IAO_DEFINITION, SKOS_DEFINITION)


@dataclass(frozen=True)
class Metrics:
    """Every count the report needs, each named with its scope."""

    values: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.values)


def _named_classes(g: rdflib.Graph) -> set[URIRef]:
    return {s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}


def _ancestors(g: rdflib.Graph) -> dict[str, set[str]]:
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
    return closure


def compute_metrics(graph: rdflib.Graph,
                    *,
                    with_imports: Optional[rdflib.Graph] = None) -> Metrics:
    """Count everything once.

    ``graph`` is the artifact alone. ``with_imports`` is the artifact merged with
    the upper ontology, and when supplied the with-imports counts are populated
    alongside the local ones rather than replacing them.
    """
    local_classes = _named_classes(graph)
    individuals = {s for s in graph.subjects(RDF.type, OWL.NamedIndividual)
                   if isinstance(s, URIRef)}

    defined_iao = {s for s in graph.subjects(IAO_DEFINITION, None)
                   if isinstance(s, URIRef)}
    defined_any_prose: set[URIRef] = set()
    for pred in _DEFINITION_PREDICATES:
        defined_any_prose |= {s for s in graph.subjects(pred, None)
                              if isinstance(s, URIRef)}
    equivalent = {s for s in graph.subjects(OWL.equivalentClass, None)
                  if isinstance(s, URIRef)}

    merged = with_imports if with_imports is not None else graph
    ancestors = _ancestors(merged)

    anchored_transitive = {
        c for c in local_classes
        if any(a.startswith(BFO) for a in ancestors.get(str(c), set()))
    }
    anchored_direct = {
        s for s, o in graph.subject_objects(RDFS.subClassOf)
        if isinstance(s, URIRef) and isinstance(o, URIRef) and str(o).startswith(BFO)
    } & local_classes

    values: dict[str, Any] = {
        "triples_local": len(graph),
        "named_classes_local": len(local_classes),
        "named_individuals_local": len(individuals),

        "object_properties_local": len(
            {s for s in graph.subjects(RDF.type, OWL.ObjectProperty)
             if isinstance(s, URIRef)}),
        "datatype_properties_local": len(
            {s for s in graph.subjects(RDF.type, OWL.DatatypeProperty)
             if isinstance(s, URIRef)}),
        "annotation_properties_local": len(
            {s for s in graph.subjects(RDF.type, OWL.AnnotationProperty)
             if isinstance(s, URIRef)}),

        "classes_with_iao_definition_local": len(defined_iao & local_classes),
        "classes_with_any_prose_definition_local": len(
            defined_any_prose & local_classes),
        "classes_with_equivalent_class_local": len(equivalent & local_classes),

        "classes_anchored_direct": len(anchored_direct),
        "classes_anchored_transitive": len(anchored_transitive),

        "disjoint_with_axioms_local": len(
            list(graph.triples((None, OWL.disjointWith, None)))),
        "restrictions_local": len(
            {s for s in graph.subjects(RDF.type, OWL.Restriction)}),
    }

    named = max(1, len(local_classes))
    values["definition_coverage_pct"] = round(
        100 * values["classes_with_iao_definition_local"] / named, 2)
    values["equivalent_class_coverage_pct"] = round(
        100 * values["classes_with_equivalent_class_local"] / named, 2)
    values["anchored_direct_pct"] = round(
        100 * values["classes_anchored_direct"] / named, 2)

    if with_imports is not None:
        merged_classes = _named_classes(with_imports)
        values.update({
            "triples_with_imports": len(with_imports),
            "named_classes_with_imports": len(merged_classes),
            "object_properties_with_imports": len(
                {s for s in with_imports.subjects(RDF.type, OWL.ObjectProperty)
                 if isinstance(s, URIRef)}),
            "imported_classes": len(merged_classes - local_classes),
        })

    return Metrics(values)


def from_paths(artifact: str | Path,
               bfo_path: str | Path | None = None) -> Metrics:
    """Convenience: parse and count in one call."""
    g = rdflib.Graph()
    g.parse(str(artifact))
    merged = None
    if bfo_path and Path(bfo_path).exists():
        merged = rdflib.Graph()
        for triple in g:
            merged.add(triple)
        merged.parse(str(bfo_path))
    return compute_metrics(g, with_imports=merged)


# --------------------------------------------------------------------------
# The duplicate-key gate
# --------------------------------------------------------------------------

def find_duplicate_values(payload: Any) -> dict[str, list[Any]]:
    """Every leaf key that appears with two distinct values anywhere in ``payload``.

    This is the check that would have caught ``object_properties`` reading 11 in
    one section and 75 in another. It walks the emitted JSON rather than the code
    that produced it, so it catches a collision no matter which section
    introduced it.
    """
    seen: dict[str, list[Any]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    walk(value)
                elif isinstance(value, (int, float, str, bool)) and \
                        not isinstance(value, bool):
                    bucket = seen.setdefault(key, [])
                    if value not in bucket:
                        bucket.append(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return {k: v for k, v in seen.items() if len(v) > 1}


# Keys whose repetition with different values is meaningful rather than a
# collision: they are per-record fields of a repeated structure, not metric
# names. A coverage table has one `missing` per row and a section list has one
# `gates_total` per section; neither is two answers to one question.
#
# The distinction the gate exists to catch is a *metric* name meaning two things
# in one bundle, as object_properties did at 11 and 75. Everything below is a
# field of a row, so it is exempt by construction rather than by threshold.
STRUCTURAL_KEYS = frozenset({
    # per-row fields of the coverage table
    "present", "missing", "coverage_pct", "attribute",
    # per-section fields
    "gates_total", "gates_passed", "status_meaning", "skipped_because",
    # per-primitive fields of the debt tables
    "findings", "eligible_population", "links", "bound_to",
    # per-entry fields of the dispersion block
    "bindings_total", "bound_classes", "unbound_classes",
    "bindings_per_class_mean", "bindings_per_class_median",
    "bindings_per_class_max", "exclusivity_rate",
    "triples_before", "triples_after", "total_changes",
    "id", "code", "name", "title", "status", "kind", "stratum", "iri", "detail",
    "reason", "message", "locus", "verdict", "basis", "label", "note", "count",
    "total", "seconds", "weight", "description", "error", "path", "instrument",
    "subject", "produced_by", "reason_detail", "attribute", "parent",
    "failure_id", "sample", "text", "term", "predicate", "length", "why",
    "class", "iri_suggestion", "value", "pct", "requires", "prev", "ts",
    "declared", "usable", "engagement", "profile", "sampled_count",
    "total_count", "subject_count", "fire_rate", "capture_pct", "passed",
})


def duplicate_metric_gate(payload: Any) -> list[str]:
    """Collisions that matter: metric-looking keys carrying two values.

    Returns a list of human-readable problems, empty when the bundle is clean.
    """
    problems: list[str] = []
    for key, values in sorted(find_duplicate_values(payload).items()):
        if key in STRUCTURAL_KEYS:
            continue
        # A collision is one name carrying two different *numbers*. A key that
        # holds a number in one map and prose in another is two different maps
        # keyed alike, which is what kernel codes are: finding_counts is keyed
        # by code and so is not_repaired. That is not a metric collision.
        numeric = [v for v in values if isinstance(v, (int, float))]
        if len(numeric) < 2 or len(numeric) != len(values):
            continue
        problems.append(
            f"{key} appears with {len(values)} distinct values {values!r}; a "
            f"metric name must mean one thing in one bundle. Give each scope its "
            f"own name, as with object_properties_local and "
            f"object_properties_with_imports.")
    return problems
