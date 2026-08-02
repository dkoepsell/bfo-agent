"""Digestibility repairs: make the file loadable without changing what it claims.

This module deliberately does very little. The extraction-fidelity principle in
this codebase says an audit produces evidence and never edits the ontology, and
the faithful fidelity mode says incoherence is evidence rather than a defect to
fix. Neither is being overturned here. What this does is narrower and separable:
it makes a file that a reasoner or an editor would refuse to load into one they
will load, without asserting anything the file did not already imply.

Every repair here satisfies three conditions:

1. It adds no claim about the domain. Declaring that something used as a class is
   a class states what its usage already entailed.
2. It removes no axiom. Nothing the artifact asserted stops being asserted.
3. It is recorded, with a count and a sample, so the change is auditable and the
   diff against the delivered artifact can be read.

What is deliberately **not** repaired, because it cannot be done without
inventing content or making a judgment the reviewer owns:

* a missing definition. Nothing can supply one.
* a dependent entity with no bearer. Nothing can supply one.
* a circular definition. Breaking the cycle means choosing which edge to cut.
* an entity under two disjoint top-level kinds. Resolving it means choosing which
  parent is wrong.
* a category defined only as a residue, or a term doing double duty. Both are
  readings of what the vocabulary means.

Those are reported in :attr:`RepairResult.not_repaired` so the bundle says what
it left alone and why, rather than implying the file is now correct.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import rdflib
from rdflib import OWL, RDF, RDFS, BNode, Literal, URIRef

# Predicates whose subject must be a class for the axiom to be well formed.
_CLASS_SUBJECT_PREDICATES = (
    RDFS.subClassOf, OWL.equivalentClass, OWL.disjointWith, OWL.disjointUnionOf,
)
# Predicates whose object must be a class.
_CLASS_OBJECT_PREDICATES = (
    RDFS.subClassOf, OWL.equivalentClass, OWL.disjointWith,
    OWL.someValuesFrom, OWL.allValuesFrom, OWL.onClass,
)
_PROPERTY_SUBJECT_PREDICATES = (
    RDFS.subPropertyOf, OWL.inverseOf, OWL.equivalentProperty,
    OWL.propertyDisjointWith, RDFS.domain, RDFS.range,
)

_PROPERTY_TYPES = (
    OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
)

# Kernel primitives this module will not touch, and why. Keyed by the code the
# audits use so the bundle can line them up against the findings.
NOT_REPAIRED: dict[str, str] = {
    "K-A1": "an entity under jointly unsatisfiable types: which assertion is "
            "wrong is a judgment about the domain",
    "K-A2": "a category nothing can belong to: correcting it means deciding "
            "which of its conditions to relax",
    "K-A3": "an undecided boundary: supplying the missing constraint requires "
            "domain knowledge this tool does not have",
    "K-B1": "a circular definition: breaking the cycle means choosing which "
            "edge to cut",
    "K-B2": "a term doing double duty: this is a reading of what the term "
            "means, not a pattern in the axioms",
    "K-B3": "a category defined only as a residue: giving it positive content "
            "is authoring, not repair",
    "K-C1": "an entity under two disjoint top-level kinds: resolving it means "
            "deciding which parent is wrong",
    "K-C2": "a kind and an instance sharing a name: OWL 2 DL permits this, and "
            "separating them would rename an entity",
    "K-C3": "a dependent entity with no bearer: nothing can supply the bearer",
    "K-D1": "a mismatch with the world: needs evidence the artifact does not "
            "contain",
    "K-D2": "a self-defeating requirement: correcting it is a change of policy",
    "K-D3": "a capacity blocked in practice: the blockage is outside the file",
}


@dataclass
class Repair:
    """One class of change, with enough detail to audit it."""

    id: str
    description: str
    count: int = 0
    sample: list[str] = field(default_factory=list)

    def note(self, subject: str) -> None:
        self.count += 1
        if len(self.sample) < 25:
            self.sample.append(subject)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "count": self.count,
            "sample": list(self.sample),
        }


@dataclass
class RepairResult:
    graph: rdflib.Graph
    repairs: list[Repair]
    triples_before: int
    triples_after: int
    ontology_iri: Optional[str] = None

    @property
    def total(self) -> int:
        return sum(r.count for r in self.repairs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repairs": [r.to_dict() for r in self.repairs if r.count],
            "repairs_attempted": [r.to_dict() for r in self.repairs],
            "total_changes": self.total,
            "triples_before": self.triples_before,
            "triples_after": self.triples_after,
            "ontology_iri": self.ontology_iri,
            "not_repaired": NOT_REPAIRED,
            "guarantee": (
                "Every change here was already entailed by the file's own usage. "
                "No axiom was removed and no claim about the domain was added. "
                "This variant is a separate artifact: no audit result in this "
                "bundle was computed from it."
            ),
        }


def _local(iri: str) -> str:
    for sep in ("#", "/"):
        if sep in iri:
            iri = iri.rsplit(sep, 1)[-1]
    return iri


def _declared(g: rdflib.Graph, rdf_type: URIRef) -> set[URIRef]:
    return {s for s in g.subjects(RDF.type, rdf_type) if isinstance(s, URIRef)}


def _is_builtin(iri: URIRef) -> bool:
    """Never declare something that belongs to OWL, RDF, RDFS or XSD."""
    s = str(iri)
    return s.startswith((
        str(OWL), str(RDF), str(RDFS),
        "http://www.w3.org/2001/XMLSchema#",
    ))


def _repair_declarations(g: rdflib.Graph) -> list[Repair]:
    """Declare entities the file uses but never declares.

    This is the most common reason a file fails OWL 2 DL validation and the most
    common reason an editor refuses to load one. The declaration states what the
    usage already entailed, so it adds nothing.
    """
    classes = Repair("declare-class",
                     "declared an IRI used as a class but never typed as one")
    properties = Repair("declare-object-property",
                        "declared an IRI used as a property but never typed as one")
    individuals = Repair("declare-individual",
                         "declared an IRI used as an individual but never typed as one")

    known_classes = _declared(g, OWL.Class) | _declared(g, RDFS.Class)
    known_properties = set()
    for t in _PROPERTY_TYPES:
        known_properties |= _declared(g, t)
    known_individuals = _declared(g, OWL.NamedIndividual)
    datatypes = _declared(g, RDFS.Datatype)

    # Anything used in a class position.
    used_as_class: set[URIRef] = set()
    for pred in _CLASS_SUBJECT_PREDICATES:
        used_as_class |= {s for s in g.subjects(pred, None) if isinstance(s, URIRef)}
    for pred in _CLASS_OBJECT_PREDICATES:
        used_as_class |= {o for o in g.objects(None, pred) if isinstance(o, URIRef)}

    # Anything used in a property position.
    used_as_property: set[URIRef] = set()
    for pred in _PROPERTY_SUBJECT_PREDICATES:
        used_as_property |= {s for s in g.subjects(pred, None) if isinstance(s, URIRef)}
    used_as_property |= {o for o in g.objects(None, OWL.onProperty)
                         if isinstance(o, URIRef)}

    for iri in sorted(used_as_class - known_classes - datatypes, key=str):
        if _is_builtin(iri) or iri in used_as_property:
            continue
        g.add((iri, RDF.type, OWL.Class))
        classes.note(str(iri))
        known_classes.add(iri)

    for iri in sorted(used_as_property - known_properties, key=str):
        if _is_builtin(iri):
            continue
        # Object property unless every use has a literal object, which makes it
        # a data property. Guessing wrong here would be a claim, so the literal
        # evidence has to be unanimous.
        objects = list(g.objects(iri, None))
        uses = [o for _s, _p, o in g.triples((None, iri, None))]
        as_data = bool(uses) and all(isinstance(o, Literal) for o in uses)
        g.add((iri, RDF.type,
               OWL.DatatypeProperty if as_data else OWL.ObjectProperty))
        properties.note(str(iri))
        known_properties.add(iri)
        del objects

    # An IRI asserted as an instance of a declared class, but never typed as an
    # individual. Protege and several toolchains want the explicit typing.
    for subject, _p, obj in g.triples((None, RDF.type, None)):
        if not isinstance(subject, URIRef) or not isinstance(obj, URIRef):
            continue
        if obj in known_classes and obj not in (OWL.Class, RDFS.Class):
            if subject not in known_individuals and subject not in known_classes \
                    and subject not in known_properties and not _is_builtin(subject):
                g.add((subject, RDF.type, OWL.NamedIndividual))
                individuals.note(str(subject))
                known_individuals.add(subject)

    return [classes, properties, individuals]


def _repair_iri_forms(g: rdflib.Graph, base: Optional[str]) -> list[Repair]:
    """Reconcile a file:// serialisation artifact with the real namespace.

    Some serialisers write entity IRIs relative to the file path, so a file can
    end up declaring ``file:///.../working.owl#Alpha`` while its axioms refer to
    ``http://example.org/working#Alpha``. RDF treats those as two entities and
    every audit then undercounts. This rewrites the file:// form to the base
    namespace, but only where an entity of the same local name already exists
    there, so the rewrite reconciles rather than invents.
    """
    repair = Repair("canonicalise-iri",
                    "rewrote a file:// serialisation artifact onto the "
                    "ontology's own namespace")
    if not base:
        return [repair]

    file_iris = {s for s in g.all_nodes()
                 if isinstance(s, URIRef) and str(s).startswith("file://")}
    if not file_iris:
        return [repair]

    base_locals = {
        _local(str(n)): n for n in g.all_nodes()
        if isinstance(n, URIRef) and str(n).startswith(base)
    }

    mapping: dict[URIRef, URIRef] = {}
    for iri in file_iris:
        local = _local(str(iri))
        target = base_locals.get(local)
        if target is not None and target != iri:
            mapping[iri] = target

    if not mapping:
        return [repair]

    for old, new in mapping.items():
        for s, p, o in list(g.triples((old, None, None))):
            g.remove((s, p, o))
            g.add((new, p, o))
        for s, p, o in list(g.triples((None, None, old))):
            g.remove((s, p, o))
            g.add((s, p, new))
        for s, p, o in list(g.triples((None, old, None))):
            g.remove((s, p, o))
            g.add((s, new, o))
        repair.note(f"{old} -> {new}")

    return [repair]


def _repair_header(g: rdflib.Graph, base: Optional[str]) -> tuple[list[Repair], Optional[str]]:
    """Ensure there is exactly one ontology header, so tools know what they loaded."""
    repair = Repair("add-ontology-header",
                    "added an owl:Ontology declaration where the file had none")
    existing = [s for s in g.subjects(RDF.type, OWL.Ontology) if isinstance(s, URIRef)]
    if existing:
        return [repair], str(existing[0])
    iri = (base or "http://example.org/ontology").rstrip("#/")
    g.add((URIRef(iri), RDF.type, OWL.Ontology))
    repair.note(iri)
    return [repair], iri


def _repair_dangling_bnode_types(g: rdflib.Graph) -> list[Repair]:
    """Type anonymous class expressions that were left untyped.

    A restriction written without ``rdf:type owl:Restriction`` parses but is not
    a legal class expression. The type is entailed by ``owl:onProperty``.
    """
    repair = Repair("type-anonymous-restriction",
                    "typed an anonymous class expression that carried "
                    "owl:onProperty but no rdf:type")
    for node in set(g.subjects(OWL.onProperty, None)):
        if isinstance(node, BNode) and (node, RDF.type, OWL.Restriction) not in g:
            g.add((node, RDF.type, OWL.Restriction))
            repair.note(str(node))
    return [repair]


def _base_namespace(g: rdflib.Graph) -> Optional[str]:
    """The namespace the ontology actually uses, preferring its own header."""
    for s in g.subjects(RDF.type, OWL.Ontology):
        if isinstance(s, URIRef) and not str(s).startswith("file://"):
            text = str(s)
            return text if text.endswith(("#", "/")) else text + "#"
    counts: dict[str, int] = {}
    for node in g.all_nodes():
        if not isinstance(node, URIRef):
            continue
        text = str(node)
        if text.startswith("file://") or _is_builtin(node):
            continue
        for sep in ("#", "/"):
            if sep in text:
                counts[text.rsplit(sep, 1)[0] + sep] = \
                    counts.get(text.rsplit(sep, 1)[0] + sep, 0) + 1
                break
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: kv[1])[0]


def repair_for_digestibility(source, bind_prefixes: bool = True) -> RepairResult:
    """Produce a loadable variant of ``source`` without changing what it claims.

    ``source`` is a path or an :class:`rdflib.Graph`. The input graph is never
    mutated: the work is done on a copy, so the delivered artifact stays exactly
    as delivered.
    """
    original = source if isinstance(source, rdflib.Graph) else None
    if original is None:
        original = rdflib.Graph()
        original.parse(str(source))

    g = rdflib.Graph()
    for triple in original:
        g.add(triple)
    for prefix, namespace in original.namespaces():
        g.bind(prefix, namespace)

    before = len(g)
    base = _base_namespace(g)

    repairs: list[Repair] = []
    repairs += _repair_iri_forms(g, base)
    header_repairs, ontology_iri = _repair_header(g, base)
    repairs += header_repairs
    repairs += _repair_dangling_bnode_types(g)
    repairs += _repair_declarations(g)

    if bind_prefixes:
        g.bind("owl", OWL)
        g.bind("rdfs", RDFS)
        g.bind("obo", "http://purl.obolibrary.org/obo/")
        if base:
            g.bind("", base)

    return RepairResult(
        graph=g,
        repairs=repairs,
        triples_before=before,
        triples_after=len(g),
        ontology_iri=ontology_iri,
    )


def serialise(result: RepairResult) -> str:
    """The repaired variant as RDF/XML."""
    return result.graph.serialize(format="pretty-xml")
