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
import types
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

from owlready2 import (
    World,
    Thing,
    ObjectProperty,
    sync_reasoner,
    onto_path,
)

BFO_OBO_PREFIX = "http://purl.obolibrary.org/obo/"
WORKING_IRI = "http://davidkoepsell.com/bfo-agent/working"


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
    if iri_suggestion.startswith("bfo:"):
        return BFO_OBO_PREFIX + iri_suggestion.split(":", 1)[1]
    if iri_suggestion.startswith("working:"):
        return f"{working_base}#{iri_suggestion.split(':', 1)[1]}"
    if iri_suggestion.startswith("BFO_"):
        return BFO_OBO_PREFIX + iri_suggestion
    # Bare name defaults to working namespace
    return f"{working_base}#{iri_suggestion}"


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
        """Add a triple. Handles rdfs:subClassOf, rdf:type, and object properties."""
        from rdflib import URIRef

        s_iri = _resolve_iri(rel.s, WORKING_IRI)
        p_iri = _resolve_iri(rel.p, WORKING_IRI)
        o_iri = _resolve_iri(rel.o, WORKING_IRI)

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
                with self.world:
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

    def commit_proposal(self, proposal) -> list[str]:
        """Apply the proposal for real and save to disk."""
        warnings = self.apply_proposal(proposal)
        self.save()
        return warnings

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

    def summary_for_proposer(self, max_items: int = 40) -> dict:
        """Compact context block for the LLM proposer prompt."""
        working_cls = self.list_working_classes()[:max_items]
        indivs = self.list_individuals()[:max_items]
        return {
            "working_classes": working_cls,
            "known_individuals": indivs,
        }
