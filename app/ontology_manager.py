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
import time
import types
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from owlready2 import (
    World,
    Thing,
    Nothing,
    ObjectProperty,
    PropertyClass,
    destroy_entity,
    sync_reasoner,
    onto_path,
)

from . import bfo_catalog
from . import config
from . import owl_checks
from . import stable_iri
from . import timing

log = logging.getLogger(__name__)

# Each sync_reasoner() run spawns a HermiT java process that can take 0.5-1GB+;
# on the 4GB host, concurrent requests stacking reasoner runs can freeze the
# whole box (incident 2026-07-02). Serialize them process-wide.
_REASONER_LOCK = threading.Lock()

BFO_OBO_PREFIX = "http://purl.obolibrary.org/obo/"
WORKING_IRI = "http://davidkoepsell.com/bfo-agent/working"

# owlready2's Nothing is a per-World object; comparing scratch-world classes
# against the module-level Nothing by identity silently fails. Compare by IRI.
_NOTHING_IRI = "http://www.w3.org/2002/07/owl#Nothing"
_OWL_DISJOINT_IRI = "http://www.w3.org/2002/07/owl#disjointWith"
_RDFS_SUBCLASSOF_IRI = "http://www.w3.org/2000/01/rdf-schema#subClassOf"


class CommitCoherenceError(Exception):
    """A commit was rolled back because it would leave the persisted ontology
    inconsistent or introduce an unsatisfiable class."""


@dataclass
class AppliedDelta:
    """Exact record of every live-world mutation one apply performed, so
    :meth:`OntologyManager.rollback` can undo it without a disk reload
    (SPEC-bfo-agent-speed.md change 2). Rollback exactness is the safety
    contract: a missed record silently corrupts the persistent ontology."""

    # Full IRIs of entities CREATED by this apply (classes or individuals).
    new_entities: list[str] = field(default_factory=list)
    # (entity_full_iri, is_a member object) appended to a PRE-EXISTING entity.
    is_a_added: list[tuple[str, object]] = field(default_factory=list)
    # (entity_full_iri, label str) appended to a PRE-EXISTING entity.
    label_added: list[tuple[str, str]] = field(default_factory=list)
    # (entity_full_iri, label str) REMOVED from a pre-existing entity: reuse
    # assigns ``.label = [new]``, which drops the old label; rollback must
    # put it back or a rejected dry-run erases committed labels.
    label_removed: list[tuple[str, str]] = field(default_factory=list)
    # Plain (s, p, o) IRI triples added via the rdflib graph.
    raw_triples: list[tuple[str, str, str]] = field(default_factory=list)
    # (class_full_iri, parent_full_iri) subClassOf edges removed by
    # _retract_axiom_triples (faithful-mode FM-9 exclusions) to restore.
    retracted: list[tuple[str, str]] = field(default_factory=list)


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

        # Commits applied with verify=False since the last full-graph
        # certificate (SPEC-bfo-agent-speed.md change 6). Incremented by
        # commit_proposal(verify=False), reset by verify_full(); the finalize
        # guard and the job-completion pass consult it.
        self.commits_since_full_verify = 0

        # Commits applied without a disk write (SPEC-bfo-agent-speed.md
        # change 7, SAVE_EVERY_COMMIT=false). Incremented by commit_proposal
        # when it skips save(), reset by save(); the orchestrator's flush
        # points (checkpoint, final pass, pause/runner exit, shutdown) and
        # the crash-recovery reset consult it.
        self.unsaved_commits = 0

        self._load()

    # ------------------------------------------------------------------ load
    def _load(self):
        """(Re)load BFO and working ontology from disk into a fresh World."""
        with timing.phase("reload"):
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

            # Reduced-world indexes (SPEC-bfo-agent-speed.md change 1): built
            # eagerly only when the feature is live, lazily otherwise
            # (_ensure_reduced_indexes), so the flag-off path pays nothing.
            self._individual_iris: Optional[set[str]] = None
            self._tbox_mirror = None
            if config.REDUCED_REASONING_WORLD and config.INMEM_DRY_RUN:
                self._rebuild_reduced_indexes()

            # Commit-time IRI reservations (SPEC-bfo-agent-speed.md change 5):
            # canonical label key -> existing IRI, so batched proposals that
            # could not see classes minted by immediately-preceding commits
            # reuse them instead of minting near-duplicates. Seeded lazily
            # (None until first use) so the flag-off path pays nothing.
            self._iri_reservations: Optional[dict[str, str]] = None
            if config.IRI_RESERVATION_ENABLED:
                self._seed_iri_reservations()

    def _strip_subclass_of_property(self, world: Optional[World] = None):
        """Drop any ``rdfs:subClassOf`` whose object is a BFO/RO *property*.

        A class cannot be a subclass of a relation. owlready2 tries to build a
        Python class whose bases include a property's metaclass and raises
        ``TypeError: metaclass conflict``, which crashes class enumeration and
        takes down the whole feed. The classic case was
        ``PropertyRight subClassOf BFO_0000054`` ("realized in" is a relation).
        We strip such axioms in-memory on every load so one bad committed axiom
        cannot brick the ontology; the gate also rejects them up front.

        ``world`` defaults to the live ``self.world``; the in-memory verify
        path passes its scratch world so the verdict matches a full reload.
        """
        from rdflib import RDF, RDFS, OWL, URIRef
        g = (world or self.world).as_rdflib_graph()
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

    def _sanitize_bfo_disjointness(self, world: Optional[World] = None):
        """Drop any owl:disjointWith between two BFO classes in a subclass
        relationship (BFO-correctness guard).

        A class disjoint with its own ancestor/descendant is unsatisfiable: the
        classic case is ``Disposition (BFO_0000016) disjointWith Function
        (BFO_0000034)`` -- but Function is a subclass of Disposition, so that
        axiom makes Function (and every individual under it) inconsistent. This
        ran the whole feed to "inconsistent". We strip such axioms in-memory on
        every load, so no ontology -- however it was seeded or hand-edited -- can
        carry a self-contradicting BFO disjointness. Operates on the live world
        (or on ``world`` when given -- the in-memory verify path passes its
        scratch world); the file on disk is untouched unless it is saved later.
        """
        from rdflib import OWL
        g = (world or self.world).as_rdflib_graph()
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

    # ------------------------- IRI reservations (speed-spec change 5)
    def _reservation_key(self, kind: str, label: str) -> Optional[str]:
        body = stable_iri.canonical_key(label)
        return f"{kind}|{body}" if body else None

    def _seed_iri_reservations(self) -> None:
        """Build the canonical-label -> IRI reservation map from the working
        ontology's classes and individuals (first entity wins a collision)."""
        res: dict[str, str] = {}
        for kind, entities in (("class", self.working.classes()),
                               ("individual", self.working.individuals())):
            for ent in entities:
                label = (str(ent.label.first()) if ent.label else "") or ent.name
                key = self._reservation_key(kind, label)
                if key is not None:
                    res.setdefault(key, ent.iri)
        self._iri_reservations = res

    def _update_iri_reservations(self, delta: Optional[AppliedDelta]) -> None:
        """Fold a successful commit's NEW entities into the reservation map,
        so the next claim's :meth:`_reserve_iris` sees them. No-op unless
        IRI_RESERVATION_ENABLED."""
        if not config.IRI_RESERVATION_ENABLED:
            return
        if self._iri_reservations is None or delta is None:
            # Legacy (no-delta) commits carry no new-entity record: re-walk.
            # Also seeds on first use when the flag was flipped after load.
            self._seed_iri_reservations()
            return
        for iri in delta.new_entities:
            ent = self.world[iri]
            if ent is None:
                continue
            kind = "class" if isinstance(ent, type) else "individual"
            label = (str(ent.label.first()) if ent.label else "") or ent.name
            key = self._reservation_key(kind, label)
            if key is not None:
                self._iri_reservations.setdefault(key, ent.iri)

    def _reserve_iris(self, proposal):
        """Rewrite NEW entities whose canonical label (stable_iri.canonical_key)
        already names an EXISTING entity of the same kind to reference that IRI
        instead of minting a near-duplicate, remapping relation endpoints in
        lockstep (the same name_map mechanism as stable_iri.remap_proposal).

        Pure: works on a copy; entities with an empty label, reused entities
        (existing_iri set), and is_new=False entities are left untouched.
        Batched proposals (SPEC-bfo-agent-speed.md change 5) cannot see classes
        minted by claims committed just before them; this closes that gap at
        apply time.
        """
        if self._iri_reservations is None:
            self._seed_iri_reservations()
        name_map: dict[str, str] = {}
        changed = False
        new_entities = []
        for ent in proposal.entities:
            label = (getattr(ent, "label", "") or "").strip()
            reused = bool(getattr(ent, "existing_iri", None))
            if not label or reused or not getattr(ent, "is_new", True):
                new_entities.append(ent)
                continue
            kind = getattr(ent, "kind", "individual")
            key = self._reservation_key(kind, label)
            hit = self._iri_reservations.get(key) if key else None
            if not hit or not self.iri_exists(hit):
                new_entities.append(ent)
                continue
            old_ref = getattr(ent, "iri_suggestion", "") or label
            old_local = _local_name(old_ref).split(":")[-1]
            new_local = _local_name(hit)
            if old_local and old_local != new_local:
                name_map[old_local] = new_local
            new_entities.append(ent.model_copy(update={
                "iri_suggestion": f"working:{new_local}",
                "is_new": False,
                "existing_iri": hit,
            }))
            changed = True
        if not changed:
            return proposal

        # Remap relation endpoints token-wise so compound objects
        # ("not working:X", "PROP some working:X") keep their operators.
        new_relations = []
        for rel in proposal.relations:
            upd = {}
            for f in ("s", "o"):
                val = getattr(rel, f, "") or ""
                toks = val.split(" ")
                hit_any = False
                out_toks = []
                for t in toks:
                    loc = _local_name(t).split(":")[-1]
                    if loc in name_map:
                        out_toks.append(f"working:{name_map[loc]}")
                        hit_any = True
                    else:
                        out_toks.append(t)
                if hit_any:
                    upd[f] = " ".join(out_toks)
            new_relations.append(rel.model_copy(update=upd) if upd else rel)

        return proposal.model_copy(
            update={"entities": new_entities, "relations": new_relations}
        )

    # -------------------------------------------------- apply a proposal
    def apply_proposal(
        self, proposal, delta: Optional[AppliedDelta] = None
    ) -> list[str]:
        """Mutate the current world with entities/relations from a proposal.

        Returns a list of warning strings; raises on hard failures.
        Does NOT save to disk. Caller decides whether to save or reload.

        ``delta`` (SPEC-bfo-agent-speed.md change 2): optional out-param that
        records every mutation performed, so :meth:`rollback` can undo the
        apply exactly without reloading from disk.
        """
        warnings: list[str] = []

        # Commit-time IRI reservation (speed-spec change 5): rewrite new
        # entities whose canonical label already names a committed entity of
        # the same kind to reuse that IRI -- BEFORE the stable-IRI remap so
        # reserved (now reused) entities are never re-hashed.
        if config.IRI_RESERVATION_ENABLED:
            proposal = self._reserve_iris(proposal)

        # FR-6: deterministic, diffable individual IRIs (opt-in). Relation
        # endpoints are remapped in lockstep, so this is verdict-neutral.
        if config.STABLE_INDIVIDUAL_IRIS:
            proposal = stable_iri.remap_proposal(
                proposal, source=getattr(proposal, "utterance", "") or ""
            )

        with timing.phase("apply"), self.working:
            # Entities first so relations can reference them
            for ent in proposal.entities:
                try:
                    self._add_entity(ent, delta=delta)
                except Exception as e:
                    warnings.append(f"Entity '{ent.label}' failed: {e}")

            for rel in proposal.relations:
                try:
                    self._add_relation(rel, delta=delta)
                except Exception as e:
                    warnings.append(f"Relation {rel.s} {rel.p} {rel.o} failed: {e}")

        return warnings

    def _resolve_entity(self, iri: str, local: str):
        """Resolve an existing entity by full IRI, then by local name over the
        working ontology (same discipline as :meth:`iri_exists`: owlready2
        sometimes serializes under a file:// base, so two IRIs can name the
        same entity without being string-equal). Returns None when absent."""
        ent = self.world[iri]
        if ent is not None:
            return ent
        if not local:
            return None
        for e in list(self.working.classes()) + list(self.working.individuals()):
            if e.name == local:
                return e
        return None

    def _add_entity(self, ent, delta: Optional[AppliedDelta] = None):
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

        # Delta capture: detect existence BEFORE creating. types.new_class /
        # type_cls(name) REOPEN an existing entity with the same name rather
        # than create one, so we must know up front whether this apply is a
        # create (rollback: destroy) or a reuse (rollback: remove the diff).
        existing = None
        before_is_a: list = []
        before_labels: list = []
        if delta is not None:
            existing = self._resolve_entity(iri, name)
            if existing is not None:
                before_is_a = list(existing.is_a)
                before_labels = [str(l) for l in existing.label]

        if ent.kind == "class":
            parent_full = (
                _resolve_iri(ent.parent_class, WORKING_IRI)
                if ent.parent_class
                else type_full
            )
            parent_cls = self.world[parent_full] or type_cls
            obj = types.new_class(name, (parent_cls,))
            obj.label = [ent.label]
        else:
            obj = type_cls(name, namespace=self.working)
            obj.label = [ent.label]

        if delta is not None:
            if existing is not None and obj is existing:
                # Reused/reopened entity: record exactly what changed.
                for m in obj.is_a:
                    if not any(m is b for b in before_is_a):
                        delta.is_a_added.append((obj.iri, m))
                after_labels = [str(l) for l in obj.label]
                for l in after_labels:
                    if l not in before_labels:
                        delta.label_added.append((obj.iri, l))
                for l in before_labels:
                    if l not in after_labels:
                        delta.label_removed.append((obj.iri, l))
            else:
                # Genuinely new entity. Use the created object's .iri: it can
                # differ from the resolved string (file:// serialization base).
                delta.new_entities.append(obj.iri)

    def _add_relation(self, rel, delta: Optional[AppliedDelta] = None):
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
            if self.add_class_expression(rel.s, expr, delta=delta):
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
                if self.add_existential_restriction(
                    rel.s, prop_tok, filler_tok, delta=delta
                ):
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
        triple = (URIRef(s_iri), URIRef(p_iri), URIRef(o_iri))
        # Record only triples that were genuinely NEW: rolling back a triple
        # that already existed before this apply would erase committed content.
        preexisting = delta is not None and triple in g
        g.add(triple)
        if delta is not None and not preexisting:
            delta.raw_triples.append((s_iri, p_iri, o_iri))

    # --------------------------------------------------- consistency check
    def check_consistency_dry_run(self, proposal) -> tuple[bool, str]:
        """Apply the proposal, reason, then discard. Returns (is_consistent,
        message).

        With INMEM_DRY_RUN (SPEC-bfo-agent-speed.md change 2): apply to the
        live world under an :class:`AppliedDelta`, roll back exactly, and
        reason in a disposable scratch world -- zero disk reads of
        working.owl. Flag off: the legacy load-apply-reason-reload path,
        byte-identical to before.
        """
        if not config.INMEM_DRY_RUN:
            return self._check_consistency_dry_run_legacy(proposal)

        delta = AppliedDelta()
        try:
            warnings = self.apply_proposal(proposal, delta=delta)
            try:
                self._proposal_guards(delta)
            except ValueError as e:
                return False, f"proposal guard: {e}"
            # Snapshot INCLUDES the delta. Reduced world (speed change 1):
            # BFO + TBox + touched individuals only; else the full graph.
            if config.REDUCED_REASONING_WORLD:
                w, _onto = self._reduced_world(proposal, delta)
            else:
                w, _onto = self._scratch_world()
        finally:
            self.rollback(delta)

        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                with timing.phase("reason_dry_run"):
                    with _REASONER_LOCK, w:
                        sync_reasoner(w, infer_property_values=False)
        except Exception as e:
            return False, f"Reasoner error: {e}\n{buf.getvalue()}"

        output = buf.getvalue()
        inconsistent_markers = ["Inconsistent", "inconsistent", "UnsatisfiableClass"]
        if any(m in output for m in inconsistent_markers):
            return False, output
        warn_prefix = ("Warnings: " + "; ".join(warnings)) if warnings else ""
        return True, (warn_prefix + "\n" + output).strip()

    def _check_consistency_dry_run_legacy(self, proposal) -> tuple[bool, str]:
        """Apply the proposal to a fresh copy, reason, then discard.

        Returns (is_consistent, message).
        """
        # Reload cleanly so no prior dry-run residue exists
        self._load()
        warnings = self.apply_proposal(proposal)

        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                with timing.phase("reason_dry_run"):
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

    def _retract_axiom_triples(
        self,
        triples: Optional[list[dict]],
        delta: Optional[AppliedDelta] = None,
        world: Optional[World] = None,
        ontology=None,
    ) -> int:
        """Remove named-parent subClassOf edges from the IN-MEMORY world only.

        Faithful-extraction support (fidelity-mode-spec.md FM-9): the ledgered
        clash axioms are retracted on the dry-run/verify copy so reasoning
        evaluates the coherent view. The persisted file is never touched --
        legacy callers reload via :meth:`_load` afterwards; the in-memory
        dry-run path passes ``delta`` so :meth:`rollback` restores the edges.
        ``world``/``ontology`` (in-memory verify path) resolve classes in a
        scratch world instead of the live one. Returns the number of edges
        actually removed.
        """
        world = world if world is not None else self.world
        ontology = ontology if ontology is not None else self.working
        removed = 0
        for t in triples or []:
            if "subClassOf" not in (t.get("p") or ""):
                continue
            cls = world[_resolve_iri(t["s"], WORKING_IRI)]
            if cls is None:
                local = _local_name(t["s"])
                cls = next(
                    (c for c in ontology.classes() if c.name == local),
                    None,
                )
            parent = world[_resolve_iri(t["o"], WORKING_IRI)]
            if cls is None or parent is None:
                continue
            try:
                if parent in cls.is_a:
                    cls.is_a.remove(parent)
                    removed += 1
                    if delta is not None:
                        delta.retracted.append((cls.iri, parent.iri))
            except Exception:
                continue
        return removed

    # -------------------------------------- in-memory dry-run (speed change 2)
    def rollback(self, delta: AppliedDelta) -> None:
        """Undo exactly the mutations recorded in ``delta`` on the live world.

        Best-effort per item: a rollback must remove as much as it can and
        never abort halfway, so each step is individually guarded and logged.
        Order matters: destroying new entities first sweeps every triple that
        references them (making later per-triple removals harmless no-ops),
        and FM-9 retractions are restored last.
        """
        from rdflib import URIRef

        with self.working:
            # 1. New entities, reverse creation order. destroy_entity removes
            #    ALL triples referencing the entity, including raw triples that
            #    touch it -- later removals are then no-ops.
            for iri in reversed(delta.new_entities):
                try:
                    ent = self._resolve_entity(iri, _local_name(iri))
                    if ent is not None:
                        destroy_entity(ent)
                except Exception:
                    log.warning("rollback: could not destroy %s", iri,
                                exc_info=True)

            # 2. is_a members appended to pre-existing entities.
            for iri, obj in delta.is_a_added:
                try:
                    ent = self._resolve_entity(iri, _local_name(iri))
                    if ent is not None and obj in ent.is_a:
                        ent.is_a.remove(obj)
                except Exception:
                    log.warning("rollback: could not remove is_a %r from %s",
                                obj, iri, exc_info=True)

            # 3. Labels appended to / dropped from pre-existing entities.
            for iri, lbl in delta.label_added:
                try:
                    ent = self._resolve_entity(iri, _local_name(iri))
                    if ent is not None and lbl in ent.label:
                        ent.label.remove(lbl)
                except Exception:
                    log.warning("rollback: could not remove label %r from %s",
                                lbl, iri, exc_info=True)
            for iri, lbl in delta.label_removed:
                try:
                    ent = self._resolve_entity(iri, _local_name(iri))
                    if ent is not None and lbl not in ent.label:
                        ent.label.append(lbl)
                except Exception:
                    log.warning("rollback: could not restore label %r on %s",
                                lbl, iri, exc_info=True)

            # 4. Raw triples (no-op if already swept by destroy_entity).
            g = self.world.as_rdflib_graph()
            for s, p, o in delta.raw_triples:
                try:
                    g.remove((URIRef(s), URIRef(p), URIRef(o)))
                except Exception:
                    log.warning("rollback: could not remove triple %s %s %s",
                                s, p, o, exc_info=True)

            # 5. Restore FM-9 exclusions retracted for this dry-run.
            for cls_iri, parent_iri in delta.retracted:
                try:
                    cls = self._resolve_entity(cls_iri, _local_name(cls_iri))
                    parent = self.world[parent_iri]
                    if (cls is not None and parent is not None
                            and parent not in cls.is_a):
                        cls.is_a.append(parent)
                except Exception:
                    log.warning("rollback: could not restore %s subClassOf %s",
                                cls_iri, parent_iri, exc_info=True)

        # 6. The memoized maps may have been built while the delta was applied.
        for attr in ("_bfo_depth_cache", "_bfo_anchor_cache",
                     "_working_depth_cache"):
            self.__dict__.pop(attr, None)

    def _scratch_world(self) -> tuple:
        """Disposable World for reasoning: BFO from disk + the CURRENT live
        working graph serialized to an in-memory buffer. The live world is
        already sanitized (startup _load); no re-sanitize, no disk read of
        working.owl. HermiT inferences land in this world and die with it,
        so the persistent world can never be polluted by reasoning."""
        with timing.phase("scratch_build"):
            w = World()
            w.get_ontology(str(self.bfo_path)).load()
            buf = io.BytesIO()
            self.working.save(file=buf, format="rdfxml")
            buf.seek(0)
            onto = w.get_ontology(self.working_path.as_uri()).load(fileobj=buf)
        return w, onto

    def _proposal_guards(self, delta: AppliedDelta) -> None:
        """Targeted per-proposal re-run of the two load-time sanitizer guards
        over ONLY the delta (bounded by proposal size). The full-graph sweeps
        still run at startup (:meth:`_load`) and on every scratch verify
        world; this closes the gap for the in-memory dry-run path, which no
        longer reloads. Raises ValueError on a violation.
        """
        for s, p, o in delta.raw_triples:
            if p == _OWL_DISJOINT_IRI:
                sf = bfo_catalog.normalize_fragment(s)
                of = bfo_catalog.normalize_fragment(o)
                if (sf in bfo_catalog.KERNEL_CLASSES
                        and of in bfo_catalog.KERNEL_CLASSES
                        and (bfo_catalog.is_descendant_of(sf, of)
                             or bfo_catalog.is_descendant_of(of, sf))):
                    raise ValueError(
                        f"invalid BFO subclass-pair disjointness axiom "
                        f"({sf} disjointWith {of}): a class disjoint with its "
                        f"own ancestor/descendant is unsatisfiable"
                    )
            elif p == _RDFS_SUBCLASSOF_IRI:
                if isinstance(self.world[o], PropertyClass):
                    raise ValueError(
                        f"subClassOf-a-property axiom ({s} subClassOf {o}): "
                        f"a class cannot be a subclass of a relation"
                    )
        for iri, obj in delta.is_a_added:
            if isinstance(obj, PropertyClass):
                raise ValueError(
                    f"subClassOf-a-property axiom ({iri} subClassOf "
                    f"{getattr(obj, 'iri', obj)}): a class cannot be a "
                    f"subclass of a relation"
                )

    # ------------------------------------ reduced world (speed change 1)
    def _working_context(self):
        """rdflib view restricted to EXACTLY the working ontology's triples.

        owlready2's rdflib store exposes each loaded ontology as a named
        context; ``get_context(self.working)`` therefore yields the working
        ontology's content without any of BFO's triples (verified empirically
        on owlready2 0.50: a fresh working ontology shows only its header +
        imports triples while the world graph also holds BFO's ~1400).
        Subject-keyed lookups on it are SQL-indexed, not full scans.
        """
        return self.world.as_rdflib_graph().get_context(self.working)

    def _rebuild_reduced_indexes(self) -> None:
        """(Re)build the persistent TBox mirror + individual index.

        ``_individual_iris``: full IRIs of every working individual.
        ``_tbox_mirror``: every working-ontology triple whose subject is NOT
        an individual -- named classes, properties, blank-node restriction
        clusters, and the ontology header (owl:Ontology + imports), which the
        reduced buffer must carry so owlready2 recognizes the ontology.
        Called from _load (feature on), lazily on first use, and at every
        verify_full checkpoint (drift guard for the incremental appends).
        """
        import rdflib
        from rdflib import URIRef

        self._individual_iris = {i.iri for i in self.working.individuals()}
        mirror = rdflib.Graph()
        inds = self._individual_iris
        for s, p, o in self._working_context():
            if isinstance(s, URIRef) and str(s) in inds:
                continue
            mirror.add((s, p, o))
        self._tbox_mirror = mirror

    def _ensure_reduced_indexes(self) -> None:
        if getattr(self, "_tbox_mirror", None) is None:
            self._rebuild_reduced_indexes()

    @staticmethod
    def _copy_subject_closure(src, dst, subj) -> None:
        """Copy all of ``subj``'s subject-triples from ``src`` to ``dst``,
        following blank-node objects transitively so restriction/complement
        clusters (and any rdf:List inside them) arrive whole. A dangling
        bnode reference would silently drop the constraint on reparse."""
        from rdflib import BNode

        stack, seen = [subj], set()
        while stack:
            s = stack.pop()
            if s in seen:
                continue
            seen.add(s)
            for t in src.triples((s, None, None)):
                dst.add(t)
                if isinstance(t[2], BNode):
                    stack.append(t[2])

    def _refresh_mirror_subject(self, iri: str) -> None:
        """Re-copy one non-individual subject from the live working graph
        into the TBox mirror (incremental maintenance after a commit or a
        scaffolding write). Old bnode clusters may be orphaned in the mirror
        until the next checkpoint rebuild; a detached restriction bnode is
        semantically inert, so that is drift-free for reasoning."""
        from rdflib import URIRef

        subj = URIRef(iri)
        self._tbox_mirror.remove((subj, None, None))
        self._copy_subject_closure(self._working_context(), self._tbox_mirror, subj)

    def _delta_new_individuals(self, delta: AppliedDelta) -> set[str]:
        """IRIs among delta.new_entities that resolve to individuals. Must be
        called while the delta is still applied (entities exist in the world)."""
        out: set[str] = set()
        for iri in delta.new_entities:
            ent = self._resolve_entity(iri, _local_name(iri))
            if ent is not None and not isinstance(ent, type):
                out.add(iri)
        return out

    def _delta_class_subjects(
        self, delta: AppliedDelta, new_individuals: set[str]
    ) -> set[str]:
        """Non-individual subjects the delta touched: the class-level content
        that is NOT in the TBox mirror yet (the mirror updates on commit)."""
        inds = (self._individual_iris or set()) | new_individuals
        subjects: set[str] = set()
        for iri in delta.new_entities:
            if iri not in new_individuals:
                subjects.add(iri)
        for iri, _obj in delta.is_a_added:
            if iri not in inds:
                subjects.add(iri)
        for iri, _lbl in delta.label_added:
            if iri not in inds:
                subjects.add(iri)
        for iri, _lbl in delta.label_removed:
            if iri not in inds:
                subjects.add(iri)
        for s, _p, _o in delta.raw_triples:
            if s not in inds:
                subjects.add(s)
        for cls_iri, _parent in delta.retracted:
            if cls_iri not in inds:
                subjects.add(cls_iri)
        return subjects

    def _update_reduced_indexes(self, delta: Optional[AppliedDelta]) -> None:
        """Incrementally fold a COMMITTED delta into the persistent indexes.

        New individuals extend ``_individual_iris``; every non-individual
        subject the delta touched is re-copied from the live graph into the
        TBox mirror (bnode closure included) -- copy-from-live rather than
        manual triple construction, so restriction clusters and owlready2's
        auto-materialized companions can never drift from the real graph.
        Dry-run deltas never reach here (they roll back before commit).
        """
        if delta is None or getattr(self, "_tbox_mirror", None) is None:
            return
        new_inds = self._delta_new_individuals(delta)
        subjects = self._delta_class_subjects(delta, new_inds)
        self._individual_iris |= new_inds
        for iri in subjects:
            self._refresh_mirror_subject(iri)

    def _touched_individual_iris(self, proposal, delta: AppliedDelta) -> set[str]:
        """Full IRIs of every working individual this proposal touches.

        Union of: delta-created individuals; delta mutations whose subject or
        raw-triple endpoint is an individual (these carry the ACTUAL IRIs,
        surviving stable-IRI remaps); proposal entities with kind != "class"
        (existing_iri reuse included) and relation endpoints, resolved full-IRI
        first then by local name. Must run while the delta is applied.
        """
        self._ensure_reduced_indexes()
        new_inds = self._delta_new_individuals(delta)
        known = self._individual_iris | new_inds
        by_local = {_local_name(i): i for i in known}
        touched: set[str] = set(new_inds)

        def resolve(ref: Optional[str]) -> Optional[str]:
            if not ref:
                return None
            full = _resolve_iri(ref, WORKING_IRI)
            if full in known:
                return full
            return by_local.get(_local_name(ref))

        for ent in proposal.entities:
            if ent.kind != "class":
                for ref in (getattr(ent, "existing_iri", None),
                            ent.iri_suggestion):
                    r = resolve(ref)
                    if r:
                        touched.add(r)
        for rel in proposal.relations:
            for ref in (rel.s, rel.o):
                r = resolve(ref)
                if r:
                    touched.add(r)
        for iri, _obj in delta.is_a_added:
            if iri in known:
                touched.add(iri)
        for iri, _lbl in delta.label_added:
            if iri in known:
                touched.add(iri)
        for iri, _lbl in delta.label_removed:
            if iri in known:
                touched.add(iri)
        for s, _p, o in delta.raw_triples:
            if s in known:
                touched.add(s)
            if o in known:
                touched.add(o)
        return touched

    def _reduced_world(self, proposal, delta: AppliedDelta) -> tuple:
        """Fresh World for dry-run reasoning: BFO + working TBox mirror + only
        the individuals touched by this proposal (SPEC change 1). Sound for
        class satisfiability and the proposal's own assertions; may miss a
        new-axiom x distant-individual interaction -- the checkpoint/final
        verify_full (change 6) closes that gap.

        Must be called while the delta is still applied to the live world:
        the proposal's own triples are then picked up naturally from the live
        graph (the same content commit would write). Class-level delta content
        is refreshed from the live graph explicitly because the mirror only
        updates on commit; FM-9 retractions are dropped from the buffer (the
        persistent mirror still carries them until the next rebuild).
        """
        import rdflib
        from rdflib import RDFS, URIRef

        with timing.phase("scratch_build"):
            self._ensure_reduced_indexes()
            live = self._working_context()
            g = rdflib.Graph()
            for t in self._tbox_mirror:
                g.add(t)

            # Delta's class-level content (new classes, restriction bnode
            # clusters, raw class-level triples): refresh each touched
            # non-individual subject from the live graph, dropping the
            # mirror's (possibly stale, e.g. replaced-label) version first.
            new_inds = self._delta_new_individuals(delta)
            for iri in self._delta_class_subjects(delta, new_inds):
                subj = URIRef(iri)
                g.remove((subj, None, None))
                self._copy_subject_closure(live, g, subj)
            # FM-9 exclusions: retracted from the live graph already, but the
            # persistent mirror may still carry the edge -- drop it here.
            for cls_iri, parent_iri in delta.retracted:
                g.remove((URIRef(cls_iri), RDFS.subClassOf, URIRef(parent_iri)))

            # Touched individuals' assertions, plus depth-1 pull-in: any
            # individual a touched one references gets its own subject
            # triples too (one level, no recursion beyond that). Inverse
            # assertions owlready2 auto-materializes ride along as the
            # neighbour's subject-triples.
            known = self._individual_iris | new_inds
            touched = self._touched_individual_iris(proposal, delta)
            neighbours: set[str] = set()
            for iri in touched:
                for _s, _p, o in live.triples((URIRef(iri), None, None)):
                    if isinstance(o, URIRef) and str(o) in known:
                        neighbours.add(str(o))
                self._copy_subject_closure(live, g, URIRef(iri))
            for iri in neighbours - touched:
                self._copy_subject_closure(live, g, URIRef(iri))

            # The delta's raw triples verbatim (idempotent for those already
            # copied above; covers e.g. an assertion onto a BFO-side subject).
            for s, p, o in delta.raw_triples:
                g.add((URIRef(s), URIRef(p), URIRef(o)))

            data = g.serialize(format="xml")
            if isinstance(data, str):
                data = data.encode("utf-8")
            w = World()
            w.get_ontology(str(self.bfo_path)).load()
            onto = w.get_ontology(self.working_path.as_uri()).load(
                fileobj=io.BytesIO(data)
            )
        return w, onto

    def proposal_touched_iris(self, proposal) -> set[str]:
        """Local-name fragments of every class a proposal ASSERTS an axiom
        about: created/typed entities (and their parents) plus the subject and
        object of every relation. Used by the reasoner tier to tell a class the
        proposal made unsatisfiable from a pre-existing (already-committed)
        unsatisfiable class it merely inherited into the world -- the latter
        must not poison every later proposal (the incoherence-cascade bug).
        Fragments, not full IRIs, because owlready2 may serialize under a
        file:// base so two IRIs can name the same class without string-equal.
        """
        frags: set[str] = set()

        def _add(ref: Optional[str]) -> None:
            if not ref:
                return
            try:
                full = _resolve_iri(ref, WORKING_IRI)
            except Exception:
                return
            frags.add(_local_name(full))

        for ent in getattr(proposal, "entities", None) or []:
            _add(getattr(ent, "iri_suggestion", None))
            _add(getattr(ent, "parent_class", None))
        for rel in getattr(proposal, "relations", None) or []:
            _add(getattr(rel, "s", None))
            o_raw = (getattr(rel, "o", None) or "").strip()
            # A class-expression / restriction object names its filler class;
            # count that filler as touched too.
            expr = owl_checks.parse_class_expression(o_raw)
            if expr is not None:
                _add(expr.get("filler"))
            elif not o_raw.startswith("_:"):
                _add(o_raw)
        return frags

    def check_coherence_dry_run(
        self, proposal, exclude_axioms: Optional[list[dict]] = None
    ) -> tuple[bool, list[str], str]:
        """Apply the proposal, reason, and report COHERENCE.

        Returns (is_coherent, unsatisfiable_class_iris, detail).

        With INMEM_DRY_RUN (SPEC-bfo-agent-speed.md change 2): apply to the
        live world under an :class:`AppliedDelta` (FM-9 exclusions retracted
        under the same delta), run the targeted per-proposal sanitizer guards,
        serialize the live graph to a disposable scratch world, roll the live
        world back exactly, then reason in the scratch world only -- zero disk
        reads of working.owl and no possibility of inference pollution. Flag
        off: the legacy load-apply-reason-reload path, byte-identical.
        """
        if not config.INMEM_DRY_RUN:
            return self._check_coherence_dry_run_legacy(proposal, exclude_axioms)

        delta = AppliedDelta()
        try:
            self.apply_proposal(proposal, delta=delta)
            if exclude_axioms:
                self._retract_axiom_triples(exclude_axioms, delta=delta)
            try:
                self._proposal_guards(delta)
            except ValueError as e:
                return False, [], f"proposal guard: {e}"
            # Snapshot INCLUDES the delta. Reduced world (speed change 1):
            # BFO + TBox + touched individuals only; else the full graph.
            if config.REDUCED_REASONING_WORLD:
                w, _onto = self._reduced_world(proposal, delta)
            else:
                w, _onto = self._scratch_world()
        finally:
            self.rollback(delta)

        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                with timing.phase("reason_dry_run"):
                    with _REASONER_LOCK, w:
                        sync_reasoner(w, infer_property_values=False)
        except Exception as e:
            # Reasoner exception == outright inconsistent (strictly worse than
            # incoherent). Surface it as incoherent so the gate rejects.
            return False, [], (
                f"Reasoner error (inconsistent): {e}\n{buf.getvalue()}".strip()
            )

        # Nothing is per-world in owlready2: compare by IRI, not identity.
        unsat = [
            c.iri for c in w.inconsistent_classes() if c.iri != _NOTHING_IRI
        ]
        output = buf.getvalue().strip()
        if unsat:
            detail = "Unsatisfiable classes: " + ", ".join(unsat)
            if output:
                detail += "\n" + output
            return False, unsat, detail
        return True, [], output

    def _check_coherence_dry_run_legacy(
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
                with timing.phase("reason_dry_run"):
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

    def committed_individual_anchors(self, ref: str) -> set[str]:
        """Return all BFO category fragments among an existing INDIVIDUAL's types.

        Mirrors :meth:`committed_bfo_anchors` for the ABox
        (SPEC-bfo-agent-speed.md change 3): resolve ``ref`` to an individual
        (full IRI first, then local-name fallback), then collect the BFO
        fragments among the ancestors of every asserted type. Used by the
        gate's lint tier to fold an individual's already-committed types into
        the straddle test. Returns an empty set for a reference that does not
        resolve to an existing individual.
        """
        full = _resolve_iri(ref, WORKING_IRI)
        ind = self.world[full]
        if ind is None or isinstance(ind, type):
            local = _local_name(ref)
            try:
                ind = next(
                    (i for i in self.working.individuals() if i.name == local),
                    None,
                )
            except Exception:
                ind = None
        if ind is None or isinstance(ind, type) or not hasattr(ind, "is_a"):
            return set()
        anchors: set[str] = set()
        try:
            for t in ind.is_a:
                if not hasattr(t, "ancestors"):
                    continue
                for anc in t.ancestors():
                    if hasattr(anc, "iri"):
                        frag = _local_name(anc.iri)
                        if frag.startswith("BFO_"):
                            anchors.add(frag)
        except Exception:
            pass
        return anchors

    def add_existential_restriction(
        self, class_ref: str, prop_frag: str, filler_frag: str,
        delta: Optional[AppliedDelta] = None,
    ) -> bool:
        """Add `class_ref SubClassOf (prop some filler)` to the live world.

        Used by Task 4 relation-aware scaffolding to turn a bare placement
        under a dependent BFO category into an actual constraint (e.g. a
        quality that inheres_in some independent continuant). Returns True if
        applied, False if the class or property could not be resolved. Does
        NOT save; the caller decides. ``delta`` records the appended is_a
        member for exact rollback (scaffolding callers pass none).
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
                restriction = prop.some(filler)
                cls.is_a.append(restriction)
            except Exception:
                return False
        if delta is not None:
            delta.is_a_added.append((cls.iri, restriction))
        elif getattr(self, "_tbox_mirror", None) is not None:
            # Delta-less caller (scaffolding) writes for keeps: keep the
            # reduced-world TBox mirror in step (speed change 1).
            self._refresh_mirror_subject(cls.iri)
        return True

    def add_class_expression(
        self, class_ref: str, expr: dict,
        delta: Optional[AppliedDelta] = None,
    ) -> bool:
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
                class_ref, expr["prop"], expr["filler"], delta=delta
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
        if delta is not None:
            delta.is_a_added.append((cls.iri, target))
        elif getattr(self, "_tbox_mirror", None) is not None:
            # Delta-less caller (scaffolding) writes for keeps: keep the
            # reduced-world TBox mirror in step (speed change 1).
            self._refresh_mirror_subject(cls.iri)
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
        inmem = config.INMEM_DRY_RUN
        delta = AppliedDelta() if inmem else None

        backup = None
        if verify and self.working_path.exists():
            backup = self.working_path.with_suffix(
                self.working_path.suffix + ".precommit"
            )
            shutil.copy2(self.working_path, backup)

        # Amortized save (SPEC-bfo-agent-speed.md change 7): with
        # SAVE_EVERY_COMMIT off (config guarantees verify-per-commit is off
        # and INMEM_DRY_RUN is on), skip the O(N) per-claim serialization;
        # the orchestrator's flush points write the file and the in-memory
        # world stays the source of truth in between. The verify path always
        # saves: _verify_saved_coherent judges what is on disk.
        save_now = verify or config.SAVE_EVERY_COMMIT

        try:
            warnings = self.apply_proposal(proposal, delta=delta)
            if save_now:
                self.save()
            else:
                self.unsaved_commits += 1
        except Exception:
            # Mechanical failure: roll back in every mode (FM-6).
            if backup is not None and backup.exists():
                shutil.copy2(backup, self.working_path)
                backup.unlink()
            if inmem:
                # In-memory rollback restores memory; the backup restore above
                # fixed disk. No disk-heavy _load on this path.
                self.rollback(delta)
            else:
                self._load()
            raise

        if inmem:
            # Legacy commits resync the memo caches via the verify path's
            # _load(); the in-memory path never reloads, so drop the caches
            # that predate this apply.
            for attr in ("_bfo_depth_cache", "_bfo_anchor_cache",
                         "_working_depth_cache"):
                self.__dict__.pop(attr, None)

        if verify:
            ok, detail = self._verify_saved_coherent(
                exclude_axioms=exclude_axioms if faithful else None
            )
            if not ok and faithful:
                # Evidence, not correction: keep the file, record the verdict.
                # The commit stands, so the delta is NOT rolled back.
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
                if inmem:
                    # save() already wrote; the file restore above fixed disk
                    # and this puts memory back to the pre-commit state.
                    self.rollback(delta)
                else:
                    self._load()  # resync in-memory state with restored file
                raise CommitCoherenceError(detail)
            elif backup is not None and backup.exists():
                backup.unlink()
        else:
            # No full-graph pass covered this commit; the checkpoint /
            # final-pass machinery (verify_full) owes it a certificate.
            self.commits_since_full_verify += 1

        # The commit stands (every failure path above raised): fold it into
        # the reduced-world indexes (speed change 1). No-op unless built.
        self._update_reduced_indexes(delta)

        # Reserve the committed entities' canonical labels so later claims
        # reuse them (speed change 5). No-op unless IRI_RESERVATION_ENABLED.
        self._update_iri_reservations(delta)

        return warnings

    def _verify_saved_coherent(
        self, exclude_axioms: Optional[list[dict]] = None
    ) -> tuple[bool, str]:
        """Reason over the just-saved working file; return ``(ok, detail)``.

        With INMEM_DRY_RUN (SPEC-bfo-agent-speed.md change 2): build a scratch
        world FROM THE SAVED FILE (the point is to verify what is on disk),
        run the same two sanitizers :meth:`_load` runs so the verdict is
        identical to a full reload, retract FM-9 exclusions in the scratch
        world, and reason there. The live world is never touched -- no _load
        calls at all. Flag off: the legacy triple-reload path, byte-identical.
        """
        if not config.INMEM_DRY_RUN:
            return self._verify_saved_coherent_legacy(exclude_axioms)

        ok, detail, _unsat = self._scratch_verify_saved(exclude_axioms)
        return ok, detail

    def _scratch_verify_saved(
        self,
        exclude_axioms: Optional[list[dict]] = None,
        phase: str = "reason_verify",
    ) -> tuple[bool, str, list[str]]:
        """Reason over the SAVED working file in a disposable scratch world.

        Shared machinery for the in-memory post-commit verify and for
        :meth:`verify_full` (which uses it regardless of INMEM_DRY_RUN).
        Applies the same two sanitizers :meth:`_load` runs so the verdict is
        identical to a full reload, retracts FM-9 exclusions in the scratch
        world, and reasons there. The live world is never touched. Returns
        ``(ok, detail, unsat_class_iris)`` -- detail capped as legacy does,
        ``unsat_class_iris`` the full list.
        """
        with timing.phase("scratch_build"):
            w = World()
            w.get_ontology(str(self.bfo_path)).load()
            onto = w.get_ontology(self.working_path.as_uri()).load()
        # Same sanitizers _load runs, applied to the scratch verify world so
        # the verdict matches what the app would run with after a reload.
        self._sanitize_bfo_disjointness(world=w)
        self._strip_subclass_of_property(world=w)
        if exclude_axioms:
            self._retract_axiom_triples(exclude_axioms, world=w, ontology=onto)
        buf = io.StringIO()
        try:
            with redirect_stdout(buf), redirect_stderr(buf):
                with timing.phase(phase):
                    with _REASONER_LOCK, w:
                        sync_reasoner(w, infer_property_values=False)
        except Exception as e:  # noqa: BLE001 — reasoner raises on inconsistency
            return False, f"inconsistent ontology: {str(e)[:200]}", []
        # Nothing is per-world in owlready2: compare by IRI, not identity.
        unsat = [
            c.iri for c in w.inconsistent_classes() if c.iri != _NOTHING_IRI
        ]
        if unsat:
            return False, "unsatisfiable classes: " + ", ".join(unsat[:12]), unsat
        return True, "", []

    def verify_full(self, exclude_axioms: Optional[list[dict]] = None) -> dict:
        """Full-graph HermiT certificate over the SAVED working file.

        SPEC-bfo-agent-speed.md change 6: the per-claim gate is a sound
        over-approximation; this is the periodic/final reconciliation that
        certifies the artifact. Runs in a disposable scratch world (live world
        untouched). Saves the live world first so the certificate covers every
        in-memory commit. Returns {"ok", "unsat_classes", "detail",
        "duration_ms", "classes", "individuals"}.
        """
        t0 = time.perf_counter()
        self.save()
        ok, detail, unsat = self._scratch_verify_saved(
            exclude_axioms=exclude_axioms, phase="reason_full_verify"
        )
        self.commits_since_full_verify = 0
        # Checkpoint boundary: rebuild the reduced-world indexes from scratch
        # so any drift the incremental appends missed is bounded to one
        # checkpoint window (speed change 1). Cheap at this frequency.
        if getattr(self, "_tbox_mirror", None) is not None:
            self._rebuild_reduced_indexes()
        return {
            "ok": ok,
            "unsat_classes": unsat,
            "detail": detail,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
            "classes": len(list(self.working.classes())),
            "individuals": len(list(self.working.individuals())),
        }

    def quarantine_classes(self, iris: list[str]) -> list[str]:
        """Destroy the named classes -- and every triple that references them
        -- from the live working world, then save.

        Used by the self-healing checkpoint to remove reduced-world
        false-coherent commits: classes the per-claim gate admitted but that
        the full-graph HermiT certificate proves unsatisfiable. Mirrors
        :meth:`rollback` step 1 (``destroy_entity`` sweeps all referencing
        triples). Returns the IRIs actually removed; a no-op (empty return)
        when none resolve. Best-effort per IRI -- one failure never aborts the
        sweep, so a partial quarantine still makes progress.
        """
        removed: list[str] = []
        with self.working:
            for iri in iris:
                try:
                    ent = self._resolve_entity(iri, _local_name(iri))
                    if ent is not None:
                        destroy_entity(ent)
                        removed.append(iri)
                except Exception:
                    log.warning("quarantine: could not destroy %s", iri,
                                exc_info=True)
        if removed:
            for attr in ("_bfo_depth_cache", "_bfo_anchor_cache",
                         "_working_depth_cache"):
                self.__dict__.pop(attr, None)
            self.save()
        return removed

    def _verify_saved_coherent_legacy(
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
                with timing.phase("reason_verify"):
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
        with timing.phase("save"):
            self.working_path.parent.mkdir(parents=True, exist_ok=True)
            self.working.save(file=str(self.working_path), format="rdfxml")
            self.unsaved_commits = 0

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
