"""General, on-demand coherence reasoning for ANY ontology.

Two capabilities, both usable across every ontology workflow (not just the
coverage profile):

  * coherence_check(working_path)     -- run HermiT over an ontology's
        working.owl and report whether it is consistent, and every
        unsatisfiable class WITH ITS MECHANISM (the asserted superclasses and
        the disjoint ancestors that collide). This is the live reasoner run the
        stored /incoherence and /fol_audit reports do not provide.

  * fact_pattern_check(working_path, classes)  -- assert one individual that is
        an instance of the chosen classes, reason, and report whether that fact
        pattern is consistent, what the reasoner infers it to be, and which
        asserted classes collide when it is not. The general form of the
        coverage claim-checker.

Both run under ontology_manager._REASONER_LOCK via _sync_reasoner_guarded, so an
on-demand check serialises behind any in-progress feed reasoning (and behind the
watchdog) rather than colliding with it or freezing the box.
"""
from __future__ import annotations

import io
import os
from contextlib import redirect_stderr, redirect_stdout

from owlready2 import World, Nothing, OwlReadyInconsistentOntologyError

from .ontology_manager import _REASONER_LOCK, _sync_reasoner_guarded

REASONER = "HermiT via owlready2"


def _load_world(working_path: str) -> World:
    w = World()
    w.get_ontology("file://" + os.path.abspath(working_path)).load()
    return w


def _disjoint_index(world: World) -> list[frozenset]:
    """All declared disjoint class pairs, as frozensets of class entities."""
    pairs: list[frozenset] = []
    seen: set = set()
    for cls in world.classes():
        for d in cls.disjoints():
            ents = [e for e in d.entities]
            for i in range(len(ents)):
                for j in range(i + 1, len(ents)):
                    key = frozenset((ents[i], ents[j]))
                    if key not in seen:
                        seen.add(key)
                        pairs.append(key)
    return pairs


def _name(entity) -> str:
    return getattr(entity, "name", None) or str(entity)


def _why_unsat(cls, disjoint_pairs: list[frozenset]) -> dict:
    """Cheap justification for one unsatisfiable class: its asserted parents and
    any disjoint pair both of whose members are ancestors of the class."""
    supers = [_name(p) for p in cls.is_a
              if getattr(p, "name", None) and _name(p) != "Thing"]
    try:
        ancestors = set(cls.ancestors())
    except Exception:
        ancestors = set()
    collisions = []
    for pair in disjoint_pairs:
        a, b = tuple(pair)
        if a in ancestors and b in ancestors:
            collisions.append([_name(a), _name(b)])
    return {
        "name": cls.name,
        "iri": cls.iri,
        "asserted_superclasses": supers,
        "disjoint_collisions": collisions,
    }


def coherence_check(working_path: str) -> dict:
    """Live reasoner run over an ontology. Reports consistency and every
    unsatisfiable class with the disjointness/subsumption that makes it empty."""
    w = _load_world(working_path)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            with _REASONER_LOCK, w:
                _sync_reasoner_guarded(w)
    except OwlReadyInconsistentOntologyError:
        return {
            "reasoner": REASONER,
            "consistent": False,
            "inconsistent_ontology": True,
            "detail": "The ontology is inconsistent: it entails a contradiction, "
                      "so it has no model. Individual unsatisfiable classes cannot "
                      "be enumerated until the contradiction is removed.",
            "unsatisfiable_classes": [],
            "unsat_count": None,
        }
    except Exception as e:
        return {"reasoner": REASONER, "error": f"reasoner failed: {e}"}

    disjoint_pairs = _disjoint_index(w)
    unsat = [c for c in w.inconsistent_classes()
             if c is not Nothing and getattr(c, "name", None) != "Nothing"]
    findings = [_why_unsat(c, disjoint_pairs) for c in unsat]
    return {
        "reasoner": REASONER,
        "consistent": True,
        "inconsistent_ontology": False,
        "coherent": not findings,
        "unsat_count": len(findings),
        "unsatisfiable_classes": findings,
    }


def _class_names(world: World) -> list[str]:
    return sorted({c.name for c in world.classes() if getattr(c, "name", None)})


def _resolve(world: World, name: str):
    hits = [c for c in world.classes() if c.name == name]
    return hits[0] if hits else None


def fact_pattern_check(working_path: str, classes: list[str],
                       individual_id: str = "fact_pattern_individual") -> dict:
    """Assert one individual as an instance of `classes`, reason, and report.

    consistent -> the fact pattern is admissible; returns what the reasoner
    infers the individual to be. inconsistent -> the pattern is self-
    contradictory under the ontology; returns the colliding asserted classes."""
    if not isinstance(classes, list) or not classes:
        return {"well_formed": False,
                "errors": ["provide a non-empty 'classes' list"]}
    w = _load_world(working_path)
    known = set(_class_names(w))
    unknown = [c for c in classes if c not in known]
    if unknown:
        sample = _class_names(w)
        return {"well_formed": False,
                "errors": [f"unknown class(es): {sorted(unknown)}"],
                "known_class_count": len(sample),
                "known_class_sample": sample[:200]}

    resolved = [_resolve(w, c) for c in classes]
    onto = w.get_ontology("https://sool.tamu.edu/fact-pattern-probe")
    with onto:
        ind = resolved[0](individual_id)
        for extra in resolved[1:]:
            ind.is_a.append(extra)

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            with _REASONER_LOCK, w:
                _sync_reasoner_guarded(w)
    except OwlReadyInconsistentOntologyError:
        # The pattern is contradictory. Report which asserted classes are
        # pairwise disjoint (the collision), sourced from the ontology's axioms.
        disjoint_pairs = _disjoint_index(w)
        collisions = []
        rset = set(resolved)
        for pair in disjoint_pairs:
            if pair <= rset:
                a, b = tuple(pair)
                collisions.append([_name(a), _name(b)])
        return {
            "well_formed": True,
            "consistent": False,
            "verdict": "inconsistent",
            "asserted_classes": classes,
            "colliding_classes": collisions,
            "detail": "This fact pattern is contradictory under the ontology: an "
                      "individual cannot be all of these at once.",
        }
    except Exception as e:
        return {"well_formed": True, "error": f"reasoner failed: {e}"}

    inferred = [_name(c) for c in ind.INDIRECT_is_a
                if getattr(c, "name", None) and _name(c) not in ("Thing",)]
    entailed = [n for n in inferred if n not in set(classes)]
    return {
        "well_formed": True,
        "consistent": True,
        "verdict": "consistent",
        "asserted_classes": classes,
        "inferred_classes": sorted(set(inferred)),
        "newly_entailed_classes": sorted(set(entailed)),
        "detail": "This fact pattern is admissible under the ontology.",
    }
