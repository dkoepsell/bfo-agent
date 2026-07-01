#!/usr/bin/env python3
"""Repair dangling ``#_:...`` blank-node axioms in a working ontology.

Background: the old commit path serialized proposer restriction placeholders
(``_:x BFO_0000197 BFO_0000040`` or bare ``_:someLabel``) as named IRIs with a
``_:`` fragment. Each is an invalid, semantically-inert subClassOf axiom. This
script repairs a working.owl in place:

  * ``_:<bnode> <PROP> <FILLER>`` (3 tokens) -> a real
    ``[ owl:Restriction ; owl:onProperty PROP ; owl:someValuesFrom FILLER ]``
  * anything else with a ``#_:`` subject or object -> removed

A timestamped ``.bak`` is written first. Run with ``--dry-run`` to only report.

Usage:
    python scripts/repair_bnode_axioms.py path/to/working.owl [--dry-run]
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from rdflib import BNode, Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS

from app.ontology_manager import _resolve_iri, WORKING_IRI


def _frag(iri: str) -> str:
    return iri.split("#", 1)[1] if "#" in iri else iri


def _is_bnode_iri(node) -> bool:
    return isinstance(node, URIRef) and "#_:" in str(node)


def _is_kernel_iri(iri: str) -> bool:
    """BFO/RO/IAO imported-kernel IRI — never mutate it (anchor, don't rewrite)."""
    return "obolibrary.org/obo/" in iri


def repair(path: Path, dry_run: bool = False) -> dict:
    g = Graph()
    g.parse(str(path))

    materialized: list[str] = []
    dropped: list[str] = []

    # collect every triple touching a #_: IRI (as subject or object)
    bad = [
        (s, p, o) for s, p, o in g
        if _is_bnode_iri(s) or _is_bnode_iri(o)
    ]

    for s, p, o in bad:
        g.remove((s, p, o))
        # only subClassOf edges with a bnode OBJECT can carry a restriction,
        # and we never re-attach one onto a BFO/RO/IAO kernel class
        if (p == RDFS.subClassOf and _is_bnode_iri(o)
                and not _is_bnode_iri(s) and not _is_kernel_iri(str(s))):
            frag = _frag(str(o))
            body = frag[2:] if frag.startswith("_:") else frag  # strip "_:"
            tokens = body.split()
            if len(tokens) == 3:
                _, prop_tok, filler_tok = tokens
                prop = URIRef(_resolve_iri(prop_tok, WORKING_IRI))
                filler = URIRef(_resolve_iri(filler_tok, WORKING_IRI))
                r = BNode()
                g.add((r, RDF.type, OWL.Restriction))
                g.add((r, OWL.onProperty, prop))
                g.add((r, OWL.someValuesFrom, filler))
                g.add((s, RDFS.subClassOf, r))
                materialized.append(f"{_frag(str(s))} subClassOf ({prop_tok} some {filler_tok})")
                continue
        dropped.append(f"{_frag(str(s))} {_frag(str(p))} {_frag(str(o))}")

    summary = {
        "bad_triples": len(bad),
        "materialized": materialized,
        "dropped": dropped,
    }
    if dry_run or not bad:
        return summary

    bak = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    shutil.copy2(path, bak)
    g.serialize(destination=str(path), format="xml")
    summary["backup"] = str(bak)
    return summary


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(2)
    path = Path(args[0])
    s = repair(path, dry_run=dry)
    print(f"{'DRY-RUN ' if dry else ''}{path}")
    print(f"  malformed #_: triples found: {s['bad_triples']}")
    print(f"  materialized as restrictions: {len(s['materialized'])}")
    for m in s["materialized"]:
        print(f"    + {m}")
    print(f"  dropped (unrecoverable): {len(s['dropped'])}")
    for d in s["dropped"]:
        print(f"    - {d}")
    if s.get("backup"):
        print(f"  backup: {s['backup']}")


if __name__ == "__main__":
    main()
