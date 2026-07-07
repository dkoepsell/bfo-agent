"""Ontology manager wrapping owlready2.

Responsibilities:
- Load BFO (read-only) plus a growing working ontology that imports BFO.
- List classes/individuals for the proposer's context.
- Apply candidate additions (entities, triples) in a rollback-safe way.
- Run HermiT (via owlready2) for consistency checks before commit.
- Persist the working ontology to disk.

Design choice: the 'propose' path uses a dry-run world built fresh from disk,
so no failed proposal can corrupt the committed graph. Commit writes to disk
and then optionally git-commits in storage.py.
"""
from __future__ import annotations

import io
import logging
import threading
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

from owlready2 import (
    World,
    Thing,
    Nothing,
    ObjectProperty,
    sync_reasoner,
    onto_path,
)

from . import bfo_catalog
from . import config
from . import owl_checks
from . import stable_iri

log = logging.getLogger(__name__)

# Each sync_reasoner() run spawns a HermiT java process that can take 0.5-1GB+;
# on the 4GB host, concurrent requests stacking reasoner runs can freeze the
# whole box (incident 2026-07-02). Serialize them process-wide.
_REASONER_LOCK = threading.Lock()

BFO_OBO_PREFIX = "http://purl.obolibrary.org/obo/"
WORKING_IRI = "http://davidkoepsell.com/bfo-agent/working"


class CommitCoherenceError(Exception):
    """A commit was rolled back because it would leave the persisted ontology
    inconsistent or introduce an unsatisfiable class."""


def _resolve_iri(iri_suggestion: str, working_base: str) -> str:
    """Expand a prefixed IRI suggestion to a full IRI.

    Accepts forms:
      'working:Vanessa'   -> {working_base}#Vanessa
      'bfo:BFO_0000040'   -> http://purl.obolibrary.org/obo/BFO_0000040
      'BFO_0000040'       -> http://purl.obolibrary.org/obo/BFO_0000040
      'http://...'        -> returned unchanged
    """
    if iri_suggestion.startswith(("http", "file:")):
        return iri_suggestion
    # Standard RDF/RDFS/OWL vocabulary prefixes. Without these, predicates like
    # 'rdfs:subClassOf' fall through to the working namespace and silently
    # corrupt the triple.
    _STD = {
        "rdf:": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs:": "http://www.w3.org/2000/01/rdf-schema#",
        "owl:": "http://www.w3.org/2002/07/owl#",
        "obo:": BFO_OBO_PREFIX,
    }
    for prefix, base in _STD.items():
        if iri_suggestion.startswith(prefix):
            return base + iri_suggestion.split(":", 1)[1]
    if iri_suggestion.startswith("bfo:"):
        return BFO_OBO_PREFIX + iri_suggestion.split(":", 1)[1]
    if iri_suggestion.startswith("working:"):
        return f"{working_base}#{_working_fragment(iri_suggestion.split(':', 1)[1])}"
    if iri_suggestion.startswith("BFO_"):
        return BFO_OBO_PREFIX + iri_suggestion
    # Bare name defaults to working namespace
    return f"{working_base}#{_working_fragment(iri_suggestion)}"


_STOPWORDS = frozenset(
    "the and with that this from have has for are was were will would other "
    "such into over more most some each which their there been being does "
    "disorder disorders symptom symptoms criteria criterion".split()
)


def _rank_by_overlap(classes: list[dict], utterance: str,
                     limit: int = 50) -> list[dict]:
    """Rank classes by token overlap between the utterance and the class's
    name + label (CamelCase split). Returns only classes with a nonzero
    score, best first, capped at ``limit``."""
    import re as _re

    def tokens(text: str) -> set[str]:
        return {
            t.lower()
            for t in _re.findall(r"[A-Za-z][a-z0-9]+|[A-Z]+(?![a-z])", text or "")
            if len(t) > 3 and t.lower() not in _STOPWORDS
        }

    utt = tokens(utterance)
    if not utt:
        return []
    scored = []
    for c in classes:
        name = (c.get("iri") or "").rsplit("#", 1)[-1]
        score = len(utt & tokens(name + " " + (c.get("label") or "")))
        if score:
            scored.append((score, c))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]


def _working_fragment(frag: str) -> str:
    """H-1: slugify a label-like working fragment ('Premenstrual Dysphoric
    Disorder' -> 'PremenstrualDysphoricDisorder') so display text never leaks
    into the IRI. A fragment that is neither valid nor label-like is returned
    unchanged; the write-path guard in ``_add_entity`` / ``_add_relation``
    refuses it (read paths must stay non-raising)."""
    slug = owl_checks.slugify_fragment(frag)
    return slug if slug is not None else frag


def _local_name(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[-1]
    return iri.rsplit("/", 1)[-1]


class OntologyManager:
    def __init__(
        self,
        bfo_path: Path,
        working_path: Path,
        seed_path: Optional[Path] = None,
    ):
        self.bfo_path = Path(bfo_path)
        self.working_path = Path(working_path)
        self.seed_path = Path(seed_path) if seed_path else None

        if not self.bfo_path.exists():
            raise FileNotFoundError(
                f"BFO ontology not found at {self.bfo_path}. "
                f"Run: python scripts/download_bfo.py"
            )

        # onto_path tells owlready2 where to find imported ontologies locally
        onto_path.append(str(self.bfo_path.parent))

        self._load()

    # ------------------------------------------------------------------ load
    def _load(self):
        """(Re)load BFO and working ontology from disk into a fresh World."""
        for attr in ("_bfo_depth_cache", "_bfo_anchor_cache", "_working_depth_cache"):
            self.__dict__.pop(attr, None)
        self.world = World()
        self.bfo = self.world.get_ontology(str(self.bfo_path)).load()

        if self.working_path.exists():
            self.working = self.world.get_ontology(
                self.working_path.as_uri()
            ).load()
        else:
            self.working = self.world.get_ontology(WORKING_IRI)
            # Import BFO by adding it to the imported_ontologies list
            self.working.imported_ontologies.append(self.bfo)
            if self.seed_path and self.seed_path.exists():
                self._apply_seed()
            self.save()

        self._sanitize_bfo_disjointness()
        self._strip_subclass_of_property()

    def _strip_subclass_of_property(self):
        """Drop any ``rdfs:subClassOf`` whose object is a BFO/RO *property*.

        A class cannot be a subclass of a relation. owlready2 tries to build a
        Python class whose bases include a property's metaclass and raises
        ``TypeError: metaclass conflict``, which crashes class enumeration and
        takes down the whole feed. The classic case was
        ``PropertyRight subClassOf BFO_0000054`` ("realized in" is a relation).
        We strip such axioms in-memory on every load so one bad committed axiom
        cannot brick the ontology; the gate also rejects them up front.
        """
        from rdflib import RDF, RDFS, OWL, URIRef
        g = self.world.as_rdflib_graph()
        # Every IRI typed as a property anywhere in the loaded world (BFO closure
        # included), so we catch part_of/realized_in etc. that the curated K_P
        # omits. Detecting from rdf:type is what makes this bulletproof.
        prop_types = [
            OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
            OWL.TransitiveProperty, OWL.FunctionalProperty, OWL.SymmetricProperty,
            URIRef("http://www.w3.org/2002/07/owl#InverseFunctionalProperty"),
        ]
        props = set()
        for pt in prop_types:
            props |= set(g.subjects(RDF.type, pt))
        removed = 0
        for s, o in list(g.subject_objects(RDFS.subClassOf)):
            if o in props:
                g.remove((s, RDFS.subClassOf, o))
                removed += 1
        if removed:
            log.warning(
                "stripped %d subClassOf-a-property axiom(s) on load "
                "(e.g. PropertyRight subClassOf BFO_0000054)", removed
            )

    def _sanitize_bfo_disjointness(self):
        """Drop any owl:disjointWith between two BFO classes in a subclass
        relationship (BFO-correctness guard).

        A class disjoint with its own ancestor/descendant is unsatisfiable: the
        classic case is ``Disposition (BFO_0000016) disjointWith Function
        (BFO_0000034)`` -- but Function is a subclass of Disposition, so that
        axiom makes Function (and every individual under it) inconsistent. This
        ran the whole feed to "inconsistent". We strip such axioms in-memory on
        every load, so no ontology -- however it was seeded or hand-edited -- can
        carry a self-contradicting BFO disjointness. Operates on the live world;
        the file on disk is untouched unless it is saved later.
        """
        from rdflib import OWL
        g = self.world.as_rdflib_graph()
        removed = 0
        for s, o in list(g.subject_objects(OWL.disjointWith)):
            sf = bfo_catalog.normalize_fragment(str(s))
            of = bfo_catalog.normalize_fragment(str(o))
            if sf not in bfo_catalog.KERNEL_CLASSES or of not in bfo_catalog.KERNEL_CLASSES:
                continue
            if bfo_catalog.is_descendant_of(sf, of) or bfo_catalog.is_descendant_of(of, sf):
                g.remove((s, OWL.disjointWith, o))
                g.remove((o, OWL.disjointWith, s))
                removed += 1
        if removed:
            log.warning(
                "stripped %d invalid BFO subclass-pair disjointness axiom(s) "
                "on load (e.g. Disposition/Function)", removed
            )

    def _apply_seed(self):
        """Apply all seed files from the seed directory.

        Discovers every .ttl file in the same directory as `seed_path`
        and applies them in alphabetical order. This lets us keep
        separate seed files for class declarations (legal_seed.ttl) and
        BFO property/disjointness declarations (bfo_relations.ttl)
        without conflating them.
        """
        seed_dir = self.seed_path.parent
        seed_files = sorted(seed_dir.glob("*.ttl"))
        for seed_file in seed_files:
            self._apply_one_seed(seed_file)

    def _apply_one_seed(self, seed_path):
        """Apply a single seed file: classes, properties, and axioms.

        Handles: class subsumption, object property declarations with
        characteristics (transitive, inverse, domain, range), and
        direct disjointness axioms.
        """
        from rdflib import Graph, RDF, RDFS, OWL, URIRef

        g = Graph()
        try:
            g.parse(str(seed_path), format="turtle")
        except Exception as e:
            print(f"[seed] could not parse {seed_path.name}: {e}")
            return

        with self.working:
            # --- Class declarations ---
            for s in g.subjects(RDF.type, OWL.Class):
                parents = list(g.objects(s, RDFS.subClassOf))
                parent_iri = str(parents[0]) if parents else None
                labels = list(g.objects(s, RDFS.label))
                label = str(labels[0]) if labels else _local_name(str(s))

                if parent_iri and parent_iri.startswith(BFO_OBO_PREFIX):
                    parent_cls = self.world[parent_iri]
                    if parent_cls is None:
                        continue
                    name = _local_name(str(s))
                    if self.world[str(s)] is not None:
                        continue  # already exists
                    new_cls = types.new_class(name, (parent_cls,))
                    new_cls.label = [label]

            # --- Object property declarations ---
            # We assert these directly into the rdflib graph backing the
            # world, so that owlready2 picks up the property axioms
            # without us needing to construct Python classes for them.
            rdf_g = self.world.as_rdflib_graph()
            for s, p, o in g.triples((None, RDF.type, OWL.ObjectProperty)):
                rdf_g.add((s, RDF.type, OWL.ObjectProperty))
            for s, p, o in g.triples((None, RDF.type, OWL.TransitiveProperty)):
                rdf_g.add((s, RDF.type, OWL.TransitiveProperty))
            for s, p, o in g.triples((None, OWL.inverseOf, None)):
                rdf_g.add((s, OWL.inverseOf, o))
            for s, p, o in g.triples((None, RDFS.domain, None)):
                rdf_g.add((s, RDFS.domain, o))
            for s, p, o in g.triples((None, RDFS.range, None)):
                rdf_g.add((s, RDFS.range, o))
            for s, p, o in g.triples((None, RDFS.label, None)):
                rdf_g.add((s, RDFS.label, o))
            for s, p, o in g.triples((None, RDFS.comment, None)):
                rdf_g.add((s, RDFS.comment, o))

            # --- Disjointness axioms ---
            for s, p, o in g.triples((None, OWL.disjointWith, None)):
                rdf_g.add((s, OWL.disjointWith, o))

    # ------------------------------------------------------------- inventory
    def list_bfo_classes(self) -> list[dict]:
        """Return a labeled list of BFO classes for the proposer prompt."""
        out = []
        for cls in self.bfo.classes():
            if cls.iri.startswith(BFO_OBO_PREFIX + "BFO_"):
                fragment = cls.iri.split("/")[-1]
                label = (cls.label.first() if cls.label else cls.name) or cls.name
                out.append({"fragment": fragment, "label": str(label), "iri": cls.iri})
        return sorted(out, key=lambda x: x["fragment"])

    def list_working_classes(self) -> list[dict]:
        out = []
        for cls in self.working.classes():
            label = (cls.label.first() if cls.label else cls.name) or cls.name
            parents = [p.name for p in cls.is_a if hasattr(p, "name")]
            out.append({"iri": cls.iri, "label": str(label), "parents": parents})
        return out

    def list_individuals(self) -> list[dict]:
        out = []
        for ind in self.working.individuals():
            label = (ind.label.first() if ind.label else ind.name) or ind.name
            types_ = [c.name for c in ind.is_a if hasattr(c, "name")]
            out.append({"iri": ind.iri, "label": str(label), "types": types_})
        return out

    def get_class_triples(self, iri: str) -> list[dict]:
        """Return all explicit triples with the given IRI as subject."""
        from rdflib import URIRef

        full_iri = _resolve_iri(iri, WORKING_IRI)
        g = self.world.as_rdflib_graph()

        _PREFIXES = [
            ("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:"),
            ("http://www.w3.org/2000/01/rdf-schema#", "rdfs:"),
            ("http://www.w3.org/2002/07/owl#", "owl:"),
            ("http://purl.obolibrary.org/obo/", "obo:"),
            (WORKING_IRI + "#", ""),
        ]

        def shorten(term):
            s = str(term)
            for prefix, short in _PREFIXES:
                if s.startswith(prefix):
                    return short + s[len(prefix):]
            if "#" in s:
                return s.rsplit("#", 1)[-1]
            return s

        return [
            {"s": shorten(s), "p": shorten(p), "o": shorten(o)}
            for s, p, o in g.triples((URIRef(full_iri), None, None))
        ]

    # ------------------------------------------------------------------ argmap

    def _bfo_depth_map(self) -> dict:
        if hasattr(self, "_bfo_depth_cache"):
            return self._bfo_depth_cache
        from collections import deque
        parents_map = {}
        for cls in self.bfo.classes():
            frag = _local_name(cls.iri)
            bfo_parents = [
                _local_name(p.iri) for p in cls.is_a
                if hasattr(p, "iri") and "BFO_" in _local_name(p.iri)
            ]
            parents_map[frag] = bfo_parents
        children_map = {f: [] for f in parents_map}
        for frag, parents in parents_map.items():
            for p in parents:
                if p in children_map:
                    children_map[p].append(frag)
        depths = {}
        queue = deque()
        for frag, parents in parents_map.items():
            if not parents:
                depths[frag] = 0
                queue.append(frag)
        while queue:
            frag = queue.popleft()
            for child in children_map.get(frag, []):
                if child not in depths:
                    depths[child] = depths[frag] + 1
                    queue.append(child)
        self._bfo_depth_cache = depths
        return depths

    def _bfo_anchor_map(self) -> dict:
        if hasattr(self, "_bfo_anchor_cache"):
            return self._bfo_anchor_cache
        bfo_frags = {_local_name(cls.iri) for cls in self.bfo.classes()}
        w_parents: dict[str, list[str]] = {}
        for cls in self.working.classes():
            frag = _local_name(cls.iri)
            w_parents[frag] = [_local_name(p.iri) for p in cls.is_a if hasattr(p, "iri")]
        anchor_cache: dict[str, str | None] = {}

        def get_anchor(frag: str, visiting: frozenset = frozenset()) -> str | None:
            if frag in anchor_cache:
                return anchor_cache[frag]
            if frag in bfo_frags:
                return frag
            if frag in visiting:
                return None
            visiting = visiting | {frag}
            for p in w_parents.get(frag, []):
                a = get_anchor(p, visiting)
                if a:
                    anchor_cache[frag] = a
                    return a
            anchor_cache[frag] = None
            return None

        for frag in w_parents:
            get_anchor(frag)
        self._bfo_anchor_cache = anchor_cache
        return anchor_cache

    def _working_depth_map(self) -> dict:
        if hasattr(self, "_working_depth_cache"):
            return self._working_depth_cache
        bfo_depths = self._bfo_depth_map()
        w_parents: dict[str, list[str]] = {}
        for cls in self.working.classes():
            frag = _local_name(cls.iri)
            w_parents[frag] = [_local_name(p.iri) for p in cls.is_a if hasattr(p, "iri")]
        cache: dict[str, int] = {}

        def get_depth(frag: str, visiting: frozenset = frozenset()) -> int:
            if frag in cache:
                return cache[frag]
            if frag in bfo_depths:
                return bfo_depths[frag]
            if frag in visiting:
                cache[frag] = 8
                return 8
            visiting = visiting | {frag}
            parents = w_parents.get(frag, [])
            d = (min(get_depth(p, visiting) for p in parents) + 1) if parents else 6
            cache[frag] = d
            return d

        for frag in w_parents:
            get_depth(frag)
        self._working_depth_cache = cache
        return cache

    def _working_counts_by_bfo_anchor(self) -> dict:
        anchors = self._bfo_anchor_map()
        counts: dict[str, int] = {}
        for anchor in anchors.values():
            if anchor:
                counts[anchor] = counts.get(anchor, 0) + 1
        return counts

    def _individual_counts_by_bfo_anchor(self) -> dict:
        bfo_frags = {_local_name(cls.iri) for cls in self.bfo.classes()}
        counts: dict[str, int] = {}
        for ind in self.working.individuals():
            for t in ind.is_a:
                if hasattr(t, "iri"):
                    frag = _local_name(t.iri)
                    if frag in bfo_frags:
                        counts[frag] = counts.get(frag, 0) + 1
                        break
        return counts

    def build_argmap_spine(self) -> dict:
        """Return the 36-node BFO skeleton with class/individual counts per node."""
        depth_map = self._bfo_depth_map()
        class_counts = self._working_counts_by_bfo_anchor()
        ind_counts = self._individual_counts_by_bfo_anchor()
        nodes, edges = [], []
        for cls in self.bfo.classes():
            frag = _local_name(cls.iri)
            label = (cls.label.first() if cls.label else cls.name) or cls.name
            parents = [
                _local_name(p.iri) for p in cls.is_a
                if hasattr(p, "iri") and "BFO_" in _local_name(p.iri)
            ]
            nodes.append({
                "id": frag, "label": str(label), "kind": "bfo",
                "depth": depth_map.get(frag, 0), "iri": cls.iri,
                "parents": parents,
                "class_count": class_counts.get(frag, 0),
                "individual_count": ind_counts.get(frag, 0),
            })
            for p in parents:
                edges.append({"s": frag, "p": "subClassOf", "o": p})
        return {"nodes": nodes, "edges": edges}

    def expand_argmap_node(self, bfo_fragment: str) -> dict:
        """Return working classes and individuals whose BFO anchor is bfo_fragment."""
        depth_map = self._bfo_depth_map()
        wdepth = self._working_depth_map()
        anchor_map = self._bfo_anchor_map()
        bfo_depth = depth_map.get(bfo_fragment, 0)
        nodes, edges = [], []

        for cls in self.working.classes():
            frag = _local_name(cls.iri)
            if anchor_map.get(frag) != bfo_fragment:
                continue
            label = (cls.label.first() if cls.label else cls.name) or cls.name
            all_parents = [_local_name(p.iri) for p in cls.is_a if hasattr(p, "iri")]
            nodes.append({
                "id": frag, "label": str(label), "kind": "class",
                "depth": wdepth.get(frag, bfo_depth + 1),
                "iri": cls.iri, "parents": all_parents,
            })
            edges.append({"s": frag, "p": "subClassOf", "o": bfo_fragment})

        for ind in self.working.individuals():
            for t in ind.is_a:
                if hasattr(t, "iri") and _local_name(t.iri) == bfo_fragment:
                    label = (ind.label.first() if ind.label else ind.name) or ind.name
                    type_frags = [_local_name(t2.iri) for t2 in ind.is_a if hasattr(t2, "iri")]
                    nodes.append({
                        "id": ind.name, "label": str(label), "kind": "individual",
                        "depth": bfo_depth + 2, "iri": ind.iri, "types": type_frags,
                    })
                    edges.append({"s": ind.name, "p": "type", "o": bfo_fragment})
                    break

        return {"bfo_parent": bfo_fragment, "nodes": nodes, "edges": edges}

    def list_object_properties(self) -> list[dict]:
        out = []
        # Include BFO relations
        for prop in self.bfo.object_properties():
            label = (prop.label.first() if prop.label else prop.name) or prop.name
            out.append({"iri": prop.iri, "label": str(label)})
        for prop in self.working.object_properties():
            label = (prop.label.first() if prop.label else prop.name) or prop.name
            out.append({"iri": prop.iri, "label": str(label)})
        return out

    def iri_exists(self, iri: str) -> bool:
        # Try the given IRI as-is (handles full URIs including file:// form)
        if self.world[iri] is not None:
            return True
        # Try prefix-resolved form (working:Foo, bfo:BFO_..., etc.)
        full = _resolve_iri(iri, WORKING_IRI)
        if self.world[full] is not None:
            return True
        # Fall back to local-name match. owlready2 sometimes serializes the
        # working ontology with a file:// base URI instead of the canonical
        # WORKING_IRI, so two IRIs can refer to the same class while not
        # being string-equal. Matching on fragment/local name catches this.
        local = _local_name(iri)
        if not local:
            return False
        for entity in list(self.working.classes()) + list(self.working.individuals()):
            if entity.name == local:
                return True
        return False

    # -------------------------------------------------- apply a proposal
    def apply_proposal(self, proposal) -> list[str]:
        """Mutate the current world with entities/relations from a proposal.

        Returns a list of warning strings; raises on hard failures.
        Does NOT save to disk. Caller decides whether to save or reload.
        """
        warnings: list[str] = []

        # FR-6: deterministic, diffable individual IRIs (opt-in). Relation
        # endpoints are remapped in lockstep, so this is verdict-neutral.
        if config.STABLE_INDIVIDUAL_IRIS:
            proposal = stable_iri.remap_proposal(
                proposal, source=getattr(proposal, "utterance", "") or ""
            )

        with self.working:
            # Entities first so relations can reference them
            for ent in proposal.entities:
                try:
                    self._add_entity(ent)
                except Exception as e:
                    warnings.append(f"Entity '{ent.label}' failed: {e}")

            for rel in proposal.relations:
                try:
                    self._add_relation(rel)
                except Exception as e:
                    warnings.append(f"Relation {rel.s} {rel.p} {rel.o} failed: {e}")

        return warnings

    def _add_entity(self, ent):
        iri = _resolve_iri(ent.iri_suggestion, WORKING_IRI)
        if owl_checks.iri_is_malformed(iri):
            raise ValueError(
                f"malformed IRI {iri!r} (H-1: fragment must be a valid token; "
                f"put display text in the label)"
            )
        name = _local_name(iri)

        # Resolve the BFO (or working) type class
        type_full = _resolve_iri(ent.bfo_type, WORKING_IRI)
        type_cls = self.world[type_full]
        if type_cls is None:
            raise ValueError(f"Type class not found: {ent.bfo_type} -> {type_full}")

        if ent.kind == "class":
            parent_full = (
                _resolve_iri(ent.parent_class, WORKING_IRI)
                if ent.parent_class
                else type_full
            )
            parent_cls = self.world[parent_full] or type_cls
            new_cls = types.new_class(name, (parent_cls,))
            new_cls.label = [ent.label]
        else:
            ind = type_cls(name, namespace=self.working)
            ind.label = [ent.label]

    def _add_relation(self, rel):
        """Add a triple. Handles rdfs:subClassOf, rdf:type, and object properties.

        The proposer sometimes expresses an existential restriction as the
        object of a subClassOf edge using a blank-node placeholder rather than
        a real ``owl:Restriction``, e.g.::

            o = "_:x BFO_0000197 BFO_0000040"   # (has-participant some material entity)
            o = "_:legalRoleInheresInPerson"    # a bare descriptive placeholder

        Writing such an object verbatim minted a dangling ``#_:...`` IRI: an
        invalid, semantically-inert axiom that also spammed the serializer with
        "does not look like a valid URI" warnings. We now materialise the
        parseable ``_:bnode PROP FILLER`` form as a real restriction and refuse
        the rest, so no ``#_:`` IRI is ever persisted.
        """
        from rdflib import URIRef

        o_raw = (rel.o or "").strip()

        # Sanctioned class-expression object on a subClassOf edge (FIX-1 /
        # X-1..X-3): materialise via owlready2 as a real anonymous construct.
        # This also recovers the 'owl:complementOf working:X' form the DSM run
        # baked into fabricated IRIs.
        expr = owl_checks.parse_class_expression(o_raw)
        if expr is not None and "subClassOf" in (rel.p or ""):
            # never mutate an imported BFO/RO/IAO kernel class
            if "obolibrary.org/obo/" in _resolve_iri(rel.s, WORKING_IRI):
                raise ValueError(
                    f"refusing to add a class expression to kernel class "
                    f"{rel.s}; anchor a SOoL subclass instead"
                )
            if self.add_class_expression(rel.s, expr):
                return
            if expr["op"] == "some":
                raise ValueError(
                    f"unresolvable restriction ({expr['prop']} some "
                    f"{expr['filler']}) on {rel.s}; skipped rather than mint "
                    f"a dangling IRI"
                )
            raise ValueError(
                f"unresolvable class expression {o_raw!r} on {rel.s}; "
                f"skipped rather than mint a malformed IRI"
            )

        if o_raw.startswith("_:"):
            parts = o_raw.split()
            # "_:bnode PROP FILLER" on a subClassOf edge -> existential restriction
            if len(parts) == 3 and "subClassOf" in rel.p:
                _, prop_tok, filler_tok = parts
                # never mutate an imported BFO/RO/IAO kernel class
                if "obolibrary.org/obo/" in _resolve_iri(rel.s, WORKING_IRI):
                    raise ValueError(
                        f"refusing to add a restriction to kernel class {rel.s}; "
                        f"anchor a SOoL subclass instead"
                    )
                if self.add_existential_restriction(rel.s, prop_tok, filler_tok):
                    return
                raise ValueError(
                    f"unresolvable restriction ({prop_tok} some {filler_tok}) "
                    f"on {rel.s}; skipped rather than mint a dangling IRI"
                )
            # bare placeholder (not machine-parseable) -> refuse to persist
            raise ValueError(
                f"blank-node placeholder object {o_raw!r} is not a valid "
                f"restriction; skipped to avoid a dangling '#_:' axiom"
            )

        s_iri = _resolve_iri(rel.s, WORKING_IRI)
        p_iri = _resolve_iri(rel.p, WORKING_IRI)
        o_iri = _resolve_iri(rel.o, WORKING_IRI)

        # H-1 / X-1 write-path guard: never persist an IRI carrying whitespace
        # or a templated OWL construct name. Reasoners silently drop such
        # axioms, so writing them loses content while looking successful.
        for iri in (s_iri, p_iri, o_iri):
            if owl_checks.iri_is_malformed(iri):
                raise ValueError(
                    f"malformed IRI {iri!r}; skipped rather than persist an "
                    f"axiom every reasoner would drop"
                )

        g = self.world.as_rdflib_graph()
        g.add((URIRef(s_iri), URIRef(p_iri), URIRef(o_iri)))

    # --------------------------------------------------- consistency check
    def check_consistency_dry_run(self, proposal) -> tuple[bool, str]:
        """Apply the proposal to a fresh copy, reason, then discard.

        Returns (is_consistent, message).
        """
        # Reload cleanly so no prior dry-run residue exists
        self._load()
        warnings = self.apply_proposal(proposal)

        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                with _REASONER_LOCK, self.world:
                    sync_reasoner(self.world, infer_property_values=False)
        except Exception as e:
            # HermiT raises on inconsistency in some versions; in others it
            # just prints. We try to surface both.
            msg = f"Reasoner error: {e}\n{buf.getvalue()}"
            self._load()  # discard dry-run state
            return False, msg

        output = buf.getvalue()
        self._load()  # discard dry-run state unconditionally

        inconsistent_markers = ["Inconsistent", "inconsistent", "UnsatisfiableClass"]
        if any(m in output for m in inconsistent_markers):
            return False, output
        warn_prefix = ("Warnings: " + "; ".join(warnings)) if warnings else ""
        return True, (warn_prefix + "\n" + output).strip()

    def _retract_axiom_triples(self, triples: Optional[list[dict]]) -> int:
        """Remove named-parent subClassOf edges from the IN-MEMORY world only.

        Faithful-extraction support (fidelity-mode-spec.md FM-9): the ledgered
        clash axioms are retracted on the dry-run/verify copy so reasoning
        evaluates the coherent view. The persisted file is never touched --
        every caller reloads via :meth:`_load` afterwards. Returns the number
        of edges actually removed.
        """
        removed = 0
        for t in triples or []:
            if "subClassOf" not in (t.get("p") or ""):
                continue
            cls = self.world[_resolve_iri(t["s"], WORKING_IRI)]
            if cls is None:
                local = _local_name(t["s"])
                cls = next(
                    (c for c in self.working.classes() if c.name == local),
                    None,
                )
            parent = self.world[_resolve_iri(t["o"], WORKING_IRI)]
            if cls is None or parent is None:
                continue
            try:
                if parent in cls.is_a:
                    cls.is_a.remove(parent)
                    removed += 1
            except Exception:
                continue
        return removed

    def check_coherence_dry_run(
        self, proposal, exclude_axioms: Optional[list[dict]] = None
    ) -> tuple[bool, list[str], str]:
        """Apply the proposal to a fresh copy, reason, and report COHERENCE.

        Coherence is distinct from consistency. HermiT does not raise or print
        for an ontology that is consistent yet has an unsatisfiable class (a
        class equivalent to owl:Nothing that no individual instantiates). That
        is exactly the disjoint-parent straddle defect we must catch, so we
        ask owlready2 for the inferred unsatisfiable classes directly rather
        than grepping reasoner stdout.

        ``exclude_axioms`` (faithful mode, FM-9): ledgered clash axioms to
        retract from the dry-run copy so the candidate is judged against the
        coherent view rather than against previously flagged incoherence.

        Returns (is_coherent, unsatisfiable_class_iris, detail).
        """
        self._load()
        self.apply_proposal(proposal)
        if exclude_axioms:
            self._retract_axiom_triples(exclude_axioms)

        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                with _REASONER_LOCK, self.world:
                    sync_reasoner(self.world, infer_property_values=False)
        except Exception as e:
            # A reasoner exception means the ontology is outright inconsistent,
            # which is strictly worse than incoherent. Surface it as incoherent
            # so the gate rejects.
            detail = f"Reasoner error (inconsistent): {e}\n{buf.getvalue()}".strip()
            self._load()
            return False, [], detail

        unsat = [
            c.iri for c in self.world.inconsistent_classes()
            if c is not Nothing
        ]
        output = buf.getvalue().strip()
        self._load()  # discard dry-run state unconditionally

        if unsat:
            detail = "Unsatisfiable classes: " + ", ".join(unsat)
            if output:
                detail += "\n" + output
            return False, unsat, detail
        return True, [], output

    def committed_bfo_anchors(self, ref: str) -> set[str]:
        """Return all BFO category fragments among an existing class's ancestors.

        `ref` may be a local name, a prefixed IRI (working:Foo, bfo:BFO_...),
        or a full IRI. Used by the gate's lint tier to fold a class's already
        committed BFO parents into the straddle test. Returns an empty set for
        a class that does not yet exist in the working ontology.
        """
        full = _resolve_iri(ref, WORKING_IRI)
        cls = self.world[full]
        if cls is None:
            local = _local_name(ref)
            cls = next(
                (c for c in self.working.classes() if c.name == local), None
            )
        if cls is None or not hasattr(cls, "ancestors"):
            return set()
        anchors: set[str] = set()
        try:
            for anc in cls.ancestors():
                if hasattr(anc, "iri"):
                    frag = _local_name(anc.iri)
                    if frag.startswith("BFO_"):
                        anchors.add(frag)
        except Exception:
            pass
        return anchors

    def add_existential_restriction(
        self, class_ref: str, prop_frag: str, filler_frag: str
    ) -> bool:
        """Add `class_ref SubClassOf (prop some filler)` to the live world.

        Used by Task 4 relation-aware scaffolding to turn a bare placement
        under a dependent BFO category into an actual constraint (e.g. a
        quality that inheres_in some independent continuant). Returns True if
        applied, False if the class or property could not be resolved. Does
        NOT save; the caller decides.
        """
        cls = self.world[_resolve_iri(class_ref, WORKING_IRI)]
        if cls is None:
            local = _local_name(class_ref)
            cls = next((c for c in self.working.classes() if c.name == local), None)
        prop = self.world[_resolve_iri(prop_frag, WORKING_IRI)]
        filler = self.world[_resolve_iri(filler_frag, WORKING_IRI)]
        if cls is None or prop is None or filler is None:
            return False
        with self.working:
            try:
                cls.is_a.append(prop.some(filler))
            except Exception:
                return False
        return True

    def add_class_expression(self, class_ref: str, expr: dict) -> bool:
        """Materialise a parsed sanctioned expression (owl_checks.
        parse_class_expression) as a real anonymous construct on class_ref:

            {"op": "some", ...}      -> class_ref SubClassOf (prop some filler)
            {"op": "not", ...}       -> class_ref SubClassOf Not(cls)
            {"op": "not_some", ...}  -> class_ref SubClassOf Not(prop some filler)

        Returns True if applied, False if any operand failed to resolve.
        Does NOT save; the caller decides."""
        from owlready2 import Not

        op = expr.get("op")
        if op == "some":
            return self.add_existential_restriction(
                class_ref, expr["prop"], expr["filler"]
            )

        cls = self.world[_resolve_iri(class_ref, WORKING_IRI)]
        if cls is None:
            local = _local_name(class_ref)
            cls = next((c for c in self.working.classes() if c.name == local), None)
        if cls is None:
            return False

        if op == "not":
            comp = self.world[_resolve_iri(expr["cls"], WORKING_IRI)]
            if comp is None:
                local = _local_name(expr["cls"])
                comp = next(
                    (c for c in self.working.classes() if c.name == local), None
                )
            if comp is None:
                return False
            target = Not(comp)
        elif op == "not_some":
            prop = self.world[_resolve_iri(expr["prop"], WORKING_IRI)]
            filler = self.world[_resolve_iri(expr["filler"], WORKING_IRI)]
            if prop is None or filler is None:
                return False
            target = Not(prop.some(filler))
        else:
            return False

        with self.working:
            try:
                cls.is_a.append(target)
            except Exception:
                return False
        return True

    def annotate_incoherence(self, class_ref: str, text: str) -> bool:
        """Attach a ``working:incoherenceEvidence`` annotation to a class.

        Fidelity-mode-spec.md FM-8: annotations are OWL-DL semantics-free, so
        this is the one permitted category of write in faithful mode -- the
        ontology's logical content (the text's content) is untouched. Full
        detail lives in the incoherence ledger; the annotation carries the
        human summary + ledger entry id. Does NOT save; the caller decides.
        """
        from owlready2 import AnnotationProperty

        cls = self.world[_resolve_iri(class_ref, WORKING_IRI)]
        if cls is None:
            local = _local_name(class_ref)
            cls = next(
                (c for c in self.working.classes() if c.name == local), None
            )
        if cls is None:
            return False
        with self.working:
            prop = self.world[WORKING_IRI + "#incoherenceEvidence"]
            if prop is None:
                import types as _types

                prop = _types.new_class(
                    "incoherenceEvidence", (AnnotationProperty,)
                )
            try:
                getattr(cls, "incoherenceEvidence").append(text)
            except Exception:
                return False
        return True

    def has_restriction_on(self, class_ref: str, prop_frag: str) -> bool:
        """True if the class already carries an existential restriction on prop."""
        cls = self.world[_resolve_iri(class_ref, WORKING_IRI)]
        if cls is None:
            local = _local_name(class_ref)
            cls = next((c for c in self.working.classes() if c.name == local), None)
        if cls is None:
            return False
        prop = self.world[_resolve_iri(prop_frag, WORKING_IRI)]
        for parent in cls.is_a:
            if hasattr(parent, "property") and parent.property is prop:
                return True
        return False

    # Set by commit_proposal in faithful mode when the post-commit verify
    # found (new) incoherence that was recorded instead of rolled back
    # (fidelity-mode-spec.md FM-6). None after every coherent commit.
    last_commit_incoherence: Optional[dict] = None

    def commit_proposal(
        self,
        proposal,
        verify: bool = True,
        faithful: bool = False,
        exclude_axioms: Optional[list[dict]] = None,
    ) -> list[str]:
        """Apply the proposal for real and save to disk.

        When ``verify`` is set (the default), the persisted ontology is
        re-checked for global consistency AND coherence after the write; if the
        commit would leave the ontology inconsistent or introduce an
        unsatisfiable class, the working file is rolled back to its pre-commit
        state and ``CommitCoherenceError`` is raised. This is the backstop that
        guarantees a malformed ontology can never accumulate on disk even if an
        upstream gate tier was skipped, errored, or a scaffolding/ABox change
        clashed retroactively with previously-committed content.

        ``faithful`` (fidelity-mode-spec.md FM-6): the extracted ontology must
        stay true to the source text including its errors, so a coherence
        failure is NOT rolled back -- the axioms stay committed as-asserted and
        the verdict is recorded in :attr:`last_commit_incoherence` for the
        caller to ledger. Rollback still applies to mechanical failures
        (apply/serialization exceptions), which are pipeline bugs, not text
        content. ``exclude_axioms`` makes the verify judge the file against
        the coherent view (minus already-ledgered clashes) so only NEW
        incoherence is reported.
        """
        import shutil

        self.last_commit_incoherence = None

        backup = None
        if verify and self.working_path.exists():
            backup = self.working_path.with_suffix(
                self.working_path.suffix + ".precommit"
            )
            shutil.copy2(self.working_path, backup)

        try:
            warnings = self.apply_proposal(proposal)
            self.save()
        except Exception:
            # Mechanical failure: roll back in every mode (FM-6).
            if backup is not None and backup.exists():
                shutil.copy2(backup, self.working_path)
                backup.unlink()
            self._load()
            raise

        if verify:
            ok, detail = self._verify_saved_coherent(
                exclude_axioms=exclude_axioms if faithful else None
            )
            if not ok and faithful:
                # Evidence, not correction: keep the file, record the verdict.
                self.last_commit_incoherence = {"detail": detail}
                if backup is not None and backup.exists():
                    backup.unlink()
            elif not ok:
                # roll the persisted file back to its pre-commit state
                if backup is not None and backup.exists():
                    shutil.copy2(backup, self.working_path)
                    backup.unlink()
                elif backup is None:
                    # first-ever commit: no prior file to restore
                    self.working_path.unlink(missing_ok=True)
                self._load()  # resync in-memory state with the restored file
                raise CommitCoherenceError(detail)
            elif backup is not None and backup.exists():
                backup.unlink()

        return warnings

    def _verify_saved_coherent(
        self, exclude_axioms: Optional[list[dict]] = None
    ) -> tuple[bool, str]:
        """Reason over the just-saved working file; return ``(ok, detail)``.

        ``ok`` is False if the reasoner reports the ontology inconsistent (it
        raises) or if any named class is unsatisfiable. We reload through the
        normal :meth:`_load` path so the check sees exactly the sanitized BFO
        disjointness and property-stripping the app runs with, then reload once
        more so reasoner-inferred axioms never leak into the state the caller
        (scaffolding) keeps working on.

        ``exclude_axioms`` (faithful mode, FM-9): retract the ledgered clash
        axioms from the in-memory copy before reasoning, so the check reports
        only incoherence NEW to this commit. The persisted file is untouched.
        """
        self._load()  # clean, sanitized load of the committed file
        if exclude_axioms:
            self._retract_axiom_triples(exclude_axioms)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                with _REASONER_LOCK, self.world:
                    sync_reasoner(self.world, infer_property_values=False)
        except Exception as e:  # noqa: BLE001 — reasoner raises on inconsistency
            self._load()  # discard partial inference state
            return False, f"inconsistent ontology: {str(e)[:200]}"
        unsat = [
            c.iri for c in self.world.inconsistent_classes() if c is not Nothing
        ]
        self._load()  # drop reasoner-inferred axioms before returning
        if unsat:
            return False, "unsatisfiable classes: " + ", ".join(unsat[:12])
        return True, ""

    # ------------------------------------------------------- persistence
    def save(self):
        self.working_path.parent.mkdir(parents=True, exist_ok=True)
        self.working.save(file=str(self.working_path), format="rdfxml")

    def stats(self) -> dict:
        return {
            "num_classes": len(list(self.working.classes())),
            "num_individuals": len(list(self.working.individuals())),
            "num_object_properties": len(list(self.working.object_properties())),
            "bfo_loaded": self.bfo is not None,
        }

    def summary_for_proposer(self, max_items: int = 40,
                             utterance: str | None = None) -> dict:
        """Compact context block for the LLM proposer prompt.

        With ``utterance``, also returns ``relevant_classes``: existing working
        classes ranked by lexical overlap with the utterance (FIX-2 / S-1 —
        the running vocabulary that lets the proposer reuse an already-minted
        criterion class instead of re-minting it per disorder). Kept separate
        from ``working_classes`` because it changes per claim and must land in
        the UNCACHED prompt segment, not the cached ontology block."""
        all_cls = self.list_working_classes()
        indivs = self.list_individuals()[:max_items]
        out = {
            "working_classes": all_cls[:max_items],
            "known_individuals": indivs,
        }
        if utterance:
            head_iris = {c["iri"] for c in all_cls[:max_items]}
            out["relevant_classes"] = _rank_by_overlap(
                [c for c in all_cls if c["iri"] not in head_iris],
                utterance,
            )
        return out
