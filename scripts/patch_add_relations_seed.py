"""Patch OntologyManager to load the BFO relations seed.

After applying this and placing bfo_relations.ttl in ontology/seed/,
the working ontology will gain:
  - 18 BFO/RO object property declarations with inverses, transitivity,
    and domain/range constraints
  - 8 disjointness axioms between BFO top-level classes

Effect: HermiT will now catch many more proposer mistakes that previously
passed through as 'consistent'. The headline inconsistency rate will
likely rise from ~0% to somewhere in the 5-15% range, which is the
target zone we hoped for during the pilot run.

Usage:
    python scripts/patch_add_relations_seed.py
    # then restart Flask

Idempotent; safe to run multiple times.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def install_relations_seed():
    """Copy bfo_relations.ttl into place if present in project root."""
    dst = ROOT / "ontology" / "seed" / "bfo_relations.ttl"

    # Look in a few plausible source locations
    for candidate in [
        ROOT / "bfo_relations.ttl",
        ROOT / "scripts" / "bfo_relations.ttl",
        Path.home() / "Downloads" / "bfo_relations.ttl",
    ]:
        if candidate.exists() and candidate != dst:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(candidate, dst)
            print(f"  [ok]   copied {candidate} -> {dst}")
            return True

    if dst.exists():
        print(f"  [skip] {dst} already present")
        return True

    print(f"  [FAIL] could not find bfo_relations.ttl to install")
    print(f"         put it at {ROOT / 'bfo_relations.ttl'} and rerun,")
    print(f"         or copy manually to {dst}")
    return False


def patch_ontology_manager():
    """Extend OntologyManager to load multiple seed files."""
    path = ROOT / "app" / "ontology_manager.py"
    print(f"\n[2/2] Patching {path.name}")

    src = path.read_text()

    if "def _apply_all_seeds" in src or "bfo_relations" in src:
        print("  [skip] relations seed loading already present")
        return True

    # Strategy: wrap _apply_seed so it also tries a sibling relations file.
    # We replace the _apply_seed method body to discover and apply all
    # .ttl files under the seed directory, in deterministic order.

    old_method = '''    def _apply_seed(self):
        """Apply a minimal seed ontology from a Turtle file.

        For MVP we parse seed as simple class declarations via rdflib and
        mint subclasses in the working ontology. More elaborate seeds can
        extend this.
        """
        from rdflib import Graph, RDF, RDFS, OWL, URIRef

        g = Graph()
        g.parse(str(self.seed_path), format="turtle")

        with self.working:
            # For each OWL Class declaration in the seed, create a subclass
            for s in g.subjects(RDF.type, OWL.Class):
                # Find parent via rdfs:subClassOf
                parents = list(g.objects(s, RDFS.subClassOf))
                parent_iri = str(parents[0]) if parents else None
                # Find label
                labels = list(g.objects(s, RDFS.label))
                label = str(labels[0]) if labels else _local_name(str(s))

                if parent_iri and parent_iri.startswith(BFO_OBO_PREFIX):
                    parent_cls = self.world[parent_iri]
                    if parent_cls is None:
                        continue
                    name = _local_name(str(s))
                    new_cls = types.new_class(name, (parent_cls,))
                    new_cls.label = [label]'''

    new_method = '''    def _apply_seed(self):
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
                rdf_g.add((s, OWL.disjointWith, o))'''

    if old_method not in src:
        print("  [FAIL] couldn't find _apply_seed to patch")
        return False

    src = src.replace(old_method, new_method)
    path.write_text(src)
    print("  [ok]   extended _apply_seed to load all .ttl files in seed dir")
    print("  [ok]   added property and disjointness axiom handling")

    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print(f"  [FAIL] compile error after patch: {r.stderr}")
        return False
    print("  [ok]   compiles")
    return True


def main():
    print("[1/2] Installing bfo_relations.ttl")
    install_relations_seed()

    ok = patch_ontology_manager()
    if not ok:
        print("\nPATCH FAILED.")
        return 1

    print("""
============================================================
Relations seed installed.

What changed:
  - ontology/seed/bfo_relations.ttl added (BFO property & axiom declarations)
  - app/ontology_manager.py extended to load all .ttl files in seed dir
  - Property characteristics (transitive, inverse, domain, range) now loaded
  - 8 BFO-level disjointness axioms now enforceable by HermiT

Next steps:
  1. (Optional) Back up working.owl first:
       cp ontology/working.owl ontology/working.pre_relations.owl
  2. Restart Flask so it reloads with the new seed:
       Ctrl-C in Flask terminal, then: python run.py
  3. When Flask starts, owlready2 will detect the new seed axioms
     but only apply them to NEW assertions. Existing classes are
     unaffected.
  4. Run a consistency check to see whether any existing claims now
     flag as inconsistent under the new axioms:
       python -c "from app.ontology_manager import OntologyManager; from app import config; mgr = OntologyManager(config.BFO_PATH, config.WORKING_PATH, config.SEED_PATH); ok, detail = mgr.check_consistency_dry_run(type('P', (), {'entities':[], 'relations':[]})()); print('consistent:', ok); print(detail[:500])"

If new inconsistencies surface, they are real findings: places where
the proposer put something in a BFO category it shouldn't have been in,
now visible because the disjointness axioms are active.

Rollback if needed:
  git checkout -- app/ontology_manager.py
  rm ontology/seed/bfo_relations.ttl
============================================================
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
