"""Canonical relation vocabulary and bfoagent: annotation namespace
(SPEC-bfo-agent-quality.md QS-B1, QS-B2, QS-B4).

Decision D-1 (2026-09-28): BFO 2020 relation IRIs are canonical; RO IRIs
that duplicate them are aliased in at resolve time. RO is kept only for
relations BFO 2020 does not provide (has role, located in).

This module is the single source of truth for:
  * which object properties are canonical (and their declared semantics),
  * the alias table applied by ``ontology_manager._resolve_iri`` and by
    ``scripts/migrate_invisible_axioms.py``,
  * the ``bfoagent:`` annotation properties used to make tool decisions
    auditable (quantifierDefaulted, scaffolded, sourceSpan, ...).
"""
from __future__ import annotations

OBO = "http://purl.obolibrary.org/obo/"
BFOAGENT_NS = "http://davidkoepsell.com/bfo-agent/meta#"
IAO_DEFINITION = OBO + "IAO_0000115"

RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS_NS = "http://www.w3.org/2000/01/rdf-schema#"
OWL_NS = "http://www.w3.org/2002/07/owl#"

# QS-B1/B2: canonical object properties.  id -> (label, inverse id | None,
# domain id | None, range id | None, characteristics)
CANONICAL_RELATIONS: dict[str, tuple] = {
    "BFO_0000197": ("inheres in", "BFO_0000196", "BFO_0000020", "BFO_0000004", ("Functional",)),
    "BFO_0000196": ("bearer of", "BFO_0000197", "BFO_0000004", "BFO_0000020", ()),
    "BFO_0000054": ("has realization", "BFO_0000055", "BFO_0000017", "BFO_0000015", ()),
    "BFO_0000055": ("realizes", "BFO_0000054", "BFO_0000015", "BFO_0000017", ()),
    "BFO_0000056": ("participates in", "BFO_0000057", "BFO_0000002", "BFO_0000003", ()),
    "BFO_0000057": ("has participant", "BFO_0000056", "BFO_0000003", "BFO_0000002", ()),
    "BFO_0000058": ("is concretized by", "BFO_0000059", "BFO_0000031", "BFO_0000020", ()),
    "BFO_0000059": ("concretizes", "BFO_0000058", "BFO_0000020", "BFO_0000031", ()),
    "BFO_0000066": ("occurs in", None, "BFO_0000003", "BFO_0000004", ()),
    "BFO_0000108": ("exists at", None, None, "BFO_0000008", ()),
    "BFO_0000176": ("continuant part of", "BFO_0000178", "BFO_0000002", "BFO_0000002", ("Transitive",)),
    "BFO_0000178": ("has continuant part", "BFO_0000176", "BFO_0000002", "BFO_0000002", ("Transitive",)),
    # RO kept only where BFO 2020 has no counterpart.
    "RO_0000087": ("has role", None, "BFO_0000004", "BFO_0000023", ()),
    "RO_0001025": ("located in", None, "BFO_0000004", "BFO_0000004", ("Transitive",)),
}

# QS-B1: non-canonical relation id -> canonical id.
RELATION_ALIASES: dict[str, str] = {
    "RO_0000052": "BFO_0000197",  # inheres in
    "RO_0000053": "BFO_0000196",  # bearer of
    "RO_0000056": "BFO_0000056",  # participates in
    "RO_0000057": "BFO_0000057",  # has participant
    "RO_0000058": "BFO_0000058",  # is concretized by
    "RO_0000059": "BFO_0000059",  # concretizes
    "BFO_0000050": "BFO_0000176",  # BFO 2.0 part of -> continuant part of
    "BFO_0000051": "BFO_0000178",  # BFO 2.0 has part -> has continuant part
}

# QS-B4: bfoagent: annotation properties.
BFOAGENT_ANNOTATIONS: tuple[str, ...] = (
    "quantifierDefaulted", "scaffolded", "sourceSpan", "definitionStatus",
    "migratedFrom", "distinctFrom",
)


def canonical_relation_id(obo_id: str) -> str:
    """Map an OBO relation id through the alias table (identity otherwise)."""
    return RELATION_ALIASES.get(obo_id, obo_id)


def canonicalize_iri(iri: str) -> str:
    """Apply the alias table to a full OBO IRI; other IRIs pass through."""
    if iri.startswith(OBO):
        return OBO + canonical_relation_id(iri[len(OBO):])
    return iri


def canonical_relation_iris() -> set[str]:
    return {OBO + k for k in CANONICAL_RELATIONS}


def bfoagent(name: str) -> str:
    return BFOAGENT_NS + name


def seed_turtle() -> str:
    """Turtle declaring the canonical relations bfo.owl does not already
    declare (QS-B2) and every bfoagent: annotation property (QS-B4).

    The BFO 2020 import declares every BFO_* relation above with its own
    domain/range/inverse; redeclaring them here could only drift from it."""
    lines = [
        f"@prefix obo: <{OBO}> .",
        f"@prefix owl: <{OWL_NS}> .",
        f"@prefix rdfs: <{RDFS_NS}> .",
        f"@prefix bfoagent: <{BFOAGENT_NS}> .",
        "",
    ]
    for pid, (label, inv, dom, rng, chars) in CANONICAL_RELATIONS.items():
        if pid.startswith("BFO_"):
            continue
        types = ["owl:ObjectProperty"] + [f"owl:{c}Property" for c in chars]
        body = [f"a {', '.join(types)}", f'rdfs:label "{label}"@en']
        if inv:
            body.append(f"owl:inverseOf obo:{inv}")
        if dom:
            body.append(f"rdfs:domain obo:{dom}")
        if rng:
            body.append(f"rdfs:range obo:{rng}")
        lines.append(f"obo:{pid} " + " ;\n    ".join(body) + " .")
    lines.append("")
    lines.append("obo:IAO_0000115 a owl:AnnotationProperty ; "
                 'rdfs:label "definition"@en .')
    for name in BFOAGENT_ANNOTATIONS:
        lines.append(f"bfoagent:{name} a owl:AnnotationProperty ; "
                     f'rdfs:label "{name}" .')
    return "\n".join(lines) + "\n"
