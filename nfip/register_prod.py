"""Register the NFIP coverage ontology as a prod library corpus.

Writes ontology/library/NFIP_SFIP_v1/ :
  * working.owl   - the full kernel + Dwelling Form core (self-contained on real
                    BFO 2020), so OntologyManager loads it directly with every
                    axiom intact (the minimal seed loader cannot ingest defined
                    classes / restrictions, so working.owl is authoritative).
  * manifest.json - fidelity=faithful, kernel_profile=coverage (opt-in flag).
  * seed/coverage-kernel.ttl - the BFO-subclass + property + disjointness subset
                    the seed loader CAN ingest, for a future coverage feed.

Idempotent and isolated: touches only the NFIP_SFIP_v1 directory. Does not read
or write any other corpus, so it cannot disturb a running feed elsewhere.
"""
from __future__ import annotations

import datetime
import json
import os

import owlready2 as o2

from . import nfip_model

ROOT = os.path.dirname(os.path.dirname(__file__))
BFO_PATH = os.path.join(ROOT, "ontology", "bfo.owl")
CORPUS = os.path.join(ROOT, "ontology", "library", "NFIP_SFIP_v1")
WORKING = os.path.join(CORPUS, "working.owl")
SEED_DIR = os.path.join(CORPUS, "seed")

NAME = "NFIP_SFIP_v1"


def build_working() -> dict:
    """Build the self-contained working.owl on real BFO 2020."""
    w = o2.World()
    # Load real BFO first so kernel classes augment canonical obo BFO classes
    # (same IRIs unify) rather than a private backbone.
    w.get_ontology("file://" + BFO_PATH).load()
    onto, handles = nfip_model.build_nfip_base(w)

    os.makedirs(CORPUS, exist_ok=True)
    # Save the whole world (BFO + kernel + domain) -> self-contained working.owl.
    w.save(file=WORKING, format="rdfxml")

    return {
        "classes": len(list(onto.classes())),
        "object_properties": len(list(onto.object_properties())),
    }


SEED_TTL = """@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix obo:  <http://purl.obolibrary.org/obo/> .
@prefix ck:   <https://sool.tamu.edu/coverage-kernel#> .
@prefix nfip: <https://sool.tamu.edu/nfip/sfip-dwelling#> .

# Coverage kernel: clause typology (BFO generically dependent continuant).
ck:Policy      a owl:Class ; rdfs:subClassOf obo:BFO_0000031 .
ck:Clause      a owl:Class ; rdfs:subClassOf obo:BFO_0000031 .
ck:Grant       a owl:Class ; rdfs:subClassOf obo:BFO_0000031 .
ck:Exclusion   a owl:Class ; rdfs:subClassOf obo:BFO_0000031 .
ck:Carveback   a owl:Class ; rdfs:subClassOf obo:BFO_0000031 .
ck:Condition   a owl:Class ; rdfs:subClassOf obo:BFO_0000031 .
ck:Definition  a owl:Class ; rdfs:subClassOf obo:BFO_0000031 .
ck:Insurer     a owl:Class ; rdfs:subClassOf obo:BFO_0000023 .
ck:Insured     a owl:Class ; rdfs:subClassOf obo:BFO_0000023 .
ck:IndemnityObligation a owl:Class ; rdfs:subClassOf obo:BFO_0000020 .
ck:Loss        a owl:Class ; rdfs:subClassOf obo:BFO_0000015 .

# Kernel relations the coverage feed must use.
ck:modifies  a owl:ObjectProperty ; rdfs:domain ck:Carveback ; rdfs:range ck:Exclusion .
ck:exhibits  a owl:ObjectProperty ; rdfs:domain ck:Loss .
ck:inheresIn a owl:ObjectProperty ; rdfs:domain ck:IndemnityObligation ; rdfs:range ck:Insurer .

# Non-negotiable definitional disjointness (II.C.20): mudflow is not earth movement.
nfip:Mudflow       a owl:Class ; rdfs:subClassOf obo:BFO_0000015 .
nfip:EarthMovement a owl:Class ; rdfs:subClassOf obo:BFO_0000015 .
nfip:Mudflow owl:disjointWith nfip:EarthMovement .
"""


def write_manifest(stats: dict) -> None:
    manifest = {
        "name": NAME,
        "description": "NFIP Standard Flood Insurance Policy, Dwelling Form "
                       "(44 CFR Part 61, Appendix A(1)), encoded against the "
                       "coverage kernel. Public-domain federal regulation.",
        "source_text": "44 CFR Part 61, Appendix A(1) (eCFR; public domain)",
        "author": "SEAL Lab, Texas A&M / SOoL (Koepsell)",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "active",
        "fidelity": "faithful",
        "kernel_profile": "coverage",
        "stats": stats,
    }
    with open(os.path.join(CORPUS, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)


def register() -> dict:
    stats = build_working()
    os.makedirs(SEED_DIR, exist_ok=True)
    with open(os.path.join(SEED_DIR, "coverage-kernel.ttl"), "w", encoding="utf-8") as fh:
        fh.write(SEED_TTL)
    for sub in ("jobs", "sessions"):
        os.makedirs(os.path.join(CORPUS, sub), exist_ok=True)
    write_manifest(stats)
    return stats


def main() -> None:
    stats = register()
    print(f"registered {NAME}: {stats}")
    print(f"  working.owl -> {WORKING}")


if __name__ == "__main__":
    main()
