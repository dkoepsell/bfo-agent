"""Proposal-time content quality (SPEC-bfo-agent-quality.md Workstream D).

  * QS-D1  annotate_new_class: IAO_0000115 definition, bfoagent:sourceSpan,
           bfoagent:definitionStatus, bfoagent:distinctFrom on a new class.
  * QS-D3  norm_key / reuse_candidates: reuse-before-mint retrieval.
  * QS-D5  reduce_redundant_parents: entailment-preserving transitive
           reduction of asserted named parents, recorded for rollback.
  * QS-D6  fanout_report: hubs over FANOUT_LIMIT direct named subclasses.
  * QS-D8  write_examples_module: illustrative individuals kept out of the
           working ontology, in an examples.owl that imports it.

``norm_key`` is the same normalisation ``scripts/ontology_audit.py`` uses for
synonym clustering, so a mint PC-15 rejects is exactly a cluster the audit
would report.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from . import relation_vocab as rv

META_LABEL = re.compile(r"\((?:reuse|instance|class|example[^)]*)\)|^example", re.I)
SUFFIXES = {"process", "disposition", "quality", "role", "function", "legal",
            "the", "of", "class", "act"}
# Category-bearing suffixes: two mints sharing a key but differing in these are
# a realizable/realization (or quality) triad (QS-D4).
CATEGORY_SUFFIXES = ("process", "disposition", "quality", "role", "function")


def norm_key(label: str) -> str:
    """Suffix-stripped, order-insensitive label key (audit synonym key)."""
    lbl = re.sub(r"\(.*?\)", "", label or "")
    lbl = re.sub(r"([a-z])([A-Z])", r"\1 \2", lbl).lower()
    toks = {t for t in re.findall(r"[a-z]+", lbl) if t not in SUFFIXES}
    return " ".join(sorted(toks))


def has_meta_label(label: str) -> bool:
    """QS-D7: '(reuse)', '(instance)', '(class)', '(example ...)', 'example...'."""
    return bool(META_LABEL.search((label or "").strip()))


def _tokens(label: str) -> set[str]:
    return set(norm_key(label).split())


def reuse_candidates(label: str, classes: Iterable[dict], k: int = 5,
                     canonical: Optional[set[str]] = None) -> list[dict]:
    """QS-D3/QS-F2: top-k existing classes a new mint might duplicate.

    ``classes`` are ``{"iri", "label"}`` dicts (``list_working_classes``
    shape). Exact normalized-key matches rank first, then token Jaccard.
    IRIs in ``canonical`` rank ahead of everything at equal key match. The
    codebase has no embedding index, so similarity is lexical.
    """
    key = norm_key(label)
    toks = set(key.split())
    if not toks:
        return []
    canonical = canonical or set()
    scored = []
    for c in classes:
        lbl = c.get("label") or c.get("name") or ""
        ctoks = _tokens(lbl)
        if not ctoks:
            continue
        exact = norm_key(lbl) == key
        jac = len(toks & ctoks) / len(toks | ctoks)
        if not exact and jac < 0.5:
            continue
        scored.append((exact, c.get("iri") in canonical, jac, c))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    return [dict(c, exact_key_match=e, canonical=can, similarity=round(j, 3))
            for e, can, j, c in scored[:k]]


def utterance_candidates(utterance: str, classes: Iterable[dict], k: int = 5,
                         canonical: Optional[set[str]] = None) -> list[dict]:
    """QS-D3 pre-proposal retrieval: existing classes whose whole normalized
    key occurs in the utterance, canonical first, longest key first."""
    utoks = set(norm_key(utterance).split())
    canonical = canonical or set()
    hits = []
    for c in classes:
        ktoks = set(norm_key(c.get("label") or c.get("name") or "").split())
        if ktoks and ktoks <= utoks:
            hits.append((c.get("iri") in canonical, len(ktoks), c))
    hits.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [dict(c, canonical=can) for can, _n, c in hits[:k]]


def format_utterance_candidates(cands: list[dict]) -> str:
    if not cands:
        return ""
    lines = ["REUSE CANDIDATES (existing classes this claim may name -- reuse "
             "the IRI rather than minting a synonym; see rule 18):"]
    for c in cands:
        lines.append(f"- {c.get('iri')} \"{c.get('label')}\""
                     + (" [canonical]" if c.get("canonical") else ""))
    return "\n".join(lines)


def format_candidates_for_prompt(cands: dict[str, list[dict]]) -> str:
    """Render {new_label: candidates} as the proposer's reuse block (QS-D3)."""
    if not cands:
        return ""
    lines = ["EXISTING NEAR-SYNONYMS (reuse one of these IRIs, or give "
             "distinct_from=<IRI> plus a source_span showing the text "
             "distinguishes them):"]
    for new_label, cs in cands.items():
        opts = "; ".join(f"{c.get('iri')} \"{c.get('label')}\""
                         + (" [canonical]" if c.get("canonical") else "")
                         for c in cs)
        lines.append(f"- {new_label}: {opts}")
    return "\n".join(lines)


# ---------------------------------------------------------------- QS-D1

def annotate_new_class(world_or_graph, iri: str, entity, delta=None,
                       onto=None) -> list[tuple]:
    """Write the QS-D1/D3 annotations of a NEW class; returns triples added.

    Call right after the class is created in ``_add_entity``, inside its
    ``with self.working:`` block (or pass ``onto``: owlready2 refuses rdflib
    adds outside a ``with <ontology>`` block). The class is in
    ``delta.new_entities``, so rollback's destroy sweeps these triples; they
    are not added to ``delta.raw_triples`` (whose rollback assumes IRI
    objects).
    """
    from rdflib import Literal, URIRef

    g = (world_or_graph.as_rdflib_graph()
         if hasattr(world_or_graph, "as_rdflib_graph") else world_or_graph)
    s = URIRef(iri)
    added = []

    def put(p, o):
        t = (s, URIRef(p), o)
        if onto is not None:
            with onto:
                g.add(t)
        else:
            g.add(t)
        added.append(t)

    definition = (getattr(entity, "definition", None) or "").strip()
    span = (getattr(entity, "source_span", None) or "").strip()
    status = getattr(entity, "definition_status", None)
    distinct = (getattr(entity, "distinct_from", None) or "").strip()
    if definition:
        put(rv.IAO_DEFINITION, Literal(definition, lang="en"))
    elif status:
        put(rv.bfoagent("definitionStatus"), Literal(status))
    if span:
        put(rv.bfoagent("sourceSpan"), Literal(span))
    if distinct:
        put(rv.bfoagent("distinctFrom"), URIRef(_expand(distinct)))
    return added


def _expand(ref: str) -> str:
    if ref.startswith("http"):
        return ref
    from .ontology_manager import WORKING_IRI, _resolve_iri
    return _resolve_iri(ref, WORKING_IRI)


# ---------------------------------------------------------------- QS-D2

def mark_scaffolded(manager, class_ref: str, prop_ref: str) -> int:
    """QS-D2: annotate every ``class ⊑ prop some F`` restriction on the class
    ``bfoagent:scaffolded true`` via an owl:Axiom reification, so scaffolded
    axioms can be filtered or removed later. Returns annotations written."""
    from rdflib import BNode, Literal, OWL, RDF, RDFS, URIRef
    from rdflib.namespace import XSD
    from .ontology_manager import WORKING_IRI, _resolve_iri

    g = manager.world.as_rdflib_graph()
    s = URIRef(_resolve_iri(class_ref, WORKING_IRI))
    p = URIRef(rv.canonicalize_iri(_resolve_iri(prop_ref, WORKING_IRI)))
    flag = URIRef(rv.bfoagent("scaffolded"))
    n = 0
    with manager.working:
        n = _flag_restrictions(g, s, p, flag)
    return n


def _flag_restrictions(g, s, p, flag) -> int:
    from rdflib import BNode, Literal, OWL, RDF, RDFS
    from rdflib.namespace import XSD

    n = 0
    for r in list(g.objects(s, RDFS.subClassOf)):
        if (r, OWL.onProperty, p) not in g:
            continue
        if any(True for ax in g.subjects(OWL.annotatedTarget, r)
               if (ax, flag, None) in g):
            continue
        ax = BNode()
        for t in ((ax, RDF.type, OWL.Axiom), (ax, OWL.annotatedSource, s),
                  (ax, OWL.annotatedProperty, RDFS.subClassOf),
                  (ax, OWL.annotatedTarget, r),
                  (ax, flag, Literal(True, datatype=XSD.boolean))):
            g.add(t)
        n += 1
    return n


# ---------------------------------------------------------------- QS-D5

def _named_parents(cls) -> list:
    from owlready2 import ThingClass
    return [p for p in cls.is_a if isinstance(p, ThingClass)]


def reduce_redundant_parents(cls, delta=None) -> list[tuple[str, str]]:
    """QS-D5 [ENTAILMENT-PRESERVING]: drop asserted named parent P of ``cls``
    when another asserted named parent Q already has P as an ancestor.

    Each removal is appended to ``delta.retracted`` as (class, parent), which
    ``OntologyManager.rollback`` already restores. Returns the removals (the
    job log / delta report). A removed parent is always still entailed.
    """
    removed = []
    for p in _named_parents(cls):
        # Re-read the live parents each time so two mutually-subsuming
        # parents can't both be dropped.
        others = [q for q in _named_parents(cls) if q is not p]
        if others and any(p in q.ancestors(include_self=False) for q in others):
            cls.is_a.remove(p)
            removed.append((cls.iri, p.iri))
            if delta is not None:
                delta.retracted.append((cls.iri, p.iri))
    return removed


# ---------------------------------------------------------------- QS-D6

def fanout_report(classes: Iterable, limit: int = 40) -> list[dict]:
    """QS-D6 [CONTENT, report-only]: classes with > ``limit`` direct named
    subclasses, largest first. ``classes`` are owlready2 classes."""
    hubs = []
    for c in classes:
        try:
            n = len(list(c.subclasses()))
        except Exception:
            continue
        if n > limit:
            label = (c.label[0] if getattr(c, "label", None) else c.name)
            hubs.append({"iri": c.iri, "label": str(label), "direct_subclasses": n})
    return sorted(hubs, key=lambda h: -h["direct_subclasses"])


def hub_children_summary(cls, max_children: int = 60) -> str:
    """Curated-mode proposer hint: a hub's current children, so new classes
    can be placed under an intermediate class (QS-D6)."""
    kids = sorted((k.label[0] if k.label else k.name) for k in cls.subclasses())
    more = f" (+{len(kids) - max_children} more)" if len(kids) > max_children else ""
    return ", ".join(map(str, kids[:max_children])) + more


# ---------------------------------------------------------------- QS-D8

EXAMPLES_IRI_SUFFIX = "/examples"


def write_examples_module(individuals: Iterable[tuple[str, str, Optional[str]]],
                          working_iri: str, out_path: Path) -> Path:
    """QS-D8: write illustrative individuals to ``examples.owl`` importing the
    working ontology. ``individuals`` are (iri, class_iri, label) triples;
    an existing module at ``out_path`` is extended, never truncated."""
    from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef

    out_path = Path(out_path)
    g = Graph()
    if out_path.exists():
        g.parse(out_path)
    onto = URIRef(working_iri + EXAMPLES_IRI_SUFFIX)
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, OWL.imports, URIRef(working_iri)))
    for iri, cls_iri, label in individuals:
        s = URIRef(iri)
        g.add((s, RDF.type, OWL.NamedIndividual))
        if cls_iri:
            g.add((s, RDF.type, URIRef(cls_iri)))
        if label:
            g.add((s, RDFS.label, Literal(label)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(out_path), format="xml")
    return out_path
