#!/usr/bin/env python3
"""Phase 2 + Phase 6 vocabulary: writes ``kernel/kernel-vocab.owl``.

Two things live here:

* the provenance annotation properties every cfr404 term must carry, and
* the contradiction-kernel audit vocabulary (loci, primitives, flag shape).

The audit vocabulary sits in its own namespace ``.../cfr404/kernel#`` (prefix
``ck``) rather than in ``cfr:``. Keeping the audit of the artifact out of the
artifact is the same artifact-versus-source discipline §10 asks for: a kernel flag
is the detector's suspicion about a class, not a fact the regulation states, and it
must not show up in the artifact's class census or need a BFO anchor.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, SKOS, XSD

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import CFR, INSTRUMENT, LOCI, LOCUS_NAME, PRIMITIVES, ROOT, STRATUM  # noqa: E402

CK = Namespace("http://davidkoepsell.com/cfr404/kernel#")
CK_IRI = URIRef("http://davidkoepsell.com/cfr404/kernel")
OUT = ROOT / "kernel" / "kernel-vocab.owl"

# (name, rdfs:comment, range hint, what it is required on)
PROVENANCE = [
    ("sourceSection", "CFR section or appendix sub-unit this term is traced to, e.g. "
     "'404.1546' or 'App1-1.00'. The sentinel 'UNATTRIBUTED' means no verbatim trace was "
     "found; it is not a section and must be excluded from any locus count.",
     XSD.string, "every class and axiom"),
    ("sourceQuote", "Verbatim regulation text supporting the term, under 40 words.",
     XSD.string, "every extracted class"),
    ("chunkId", "Stable corpus chunk id the term was extracted from.",
     XSD.string, "every extracted class"),
    ("extractionConfidence", "One of high | medium | low.", XSD.string,
     "every extracted class"),
    ("approved", "Whether the assertion passed review. Untraced terms are false.",
     XSD.boolean, "every extracted class"),
    ("chainLocus", "One of L1..L7, or L0 for terms outside the recognition chain.",
     XSD.string, "every institutional class"),
    ("reviewNote", "Free-text adjudication note.", XSD.string, "optional"),
]

# Kernel-side annotation properties, ck: namespace.
KERNEL_PROPS = [
    ("hasContradictionCandidate",
     "Links a flagged term to a ck:ContradictionCandidate individual. A candidate is the "
     "detector's suspicion, NOT a reasoner-confirmed contradiction."),
    ("primitive", "Kernel primitive id, K-A1 .. K-D3."),
    ("stratum", "Kernel stratum, A | B | C | D."),
    ("locus", "Recognition-chain locus the flag is attributed to, L1..L7."),
    ("instrument", "Detecting instrument, per Table 7."),
    ("detector", "Detector module that raised the flag."),
    ("evidence", "What the detector actually observed."),
    ("blanketFlag", "True when the flag was applied to a whole class family rather than "
     "term by term. Blanket flags must be reported and removable separately."),
    ("edge", "For relational primitives, the chain edge flagged, e.g. 'L5->L6'."),
]


def main() -> int:
    g = Graph()
    g.bind("cfr", CFR)
    g.bind("ck", CK)
    g.bind("dcterms", DCTERMS)
    g.bind("skos", SKOS)

    g.add((CK_IRI, RDF.type, OWL.Ontology))
    g.add((CK_IRI, OWL.versionIRI, URIRef("http://davidkoepsell.com/cfr404/kernel/2026-07-30")))
    g.add((CK_IRI, RDFS.label, Literal("cfr404 provenance and contradiction-kernel vocabulary")))
    g.add((CK_IRI, DCTERMS.license, URIRef("https://creativecommons.org/licenses/by/4.0/")))
    g.add((CK_IRI, RDFS.comment, Literal(
        "Provenance properties are minted in the cfr: namespace because they describe the "
        "artifact's own terms. Audit vocabulary is minted in ck: because it describes "
        "findings about the artifact, which are not part of it.")))

    for name, comment, rng, required in PROVENANCE:
        u = CFR[name]
        g.add((u, RDF.type, OWL.AnnotationProperty))
        g.add((u, RDFS.label, Literal(f"cfr:{name}")))
        g.add((u, RDFS.comment, Literal(comment)))
        g.add((u, RDFS.range, rng))
        g.add((u, SKOS.editorialNote, Literal(f"required on: {required}")))

    for name, comment in KERNEL_PROPS:
        u = CK[name]
        g.add((u, RDF.type, OWL.AnnotationProperty))
        g.add((u, RDFS.label, Literal(f"ck:{name}")))
        g.add((u, RDFS.comment, Literal(comment)))

    cc = CK["ContradictionCandidate"]
    g.add((cc, RDF.type, OWL.Class))
    g.add((cc, RDFS.label, Literal("contradiction candidate")))
    g.add((cc, RDFS.comment, Literal(
        "A detector's suspicion about a term or an edge. Candidates are not confirmed "
        "contradictions: they are the input to hand adjudication, and their precision is "
        "reported, never assumed.")))

    # Locus individuals: the index set the kernel is instantiated over.
    for lid in LOCI + ["L0"]:
        u = CK[lid]
        g.add((u, RDF.type, OWL.NamedIndividual))
        g.add((u, RDF.type, CK["ChainLocus"]))
        g.add((u, RDFS.label, Literal(lid)))
        g.add((u, SKOS.prefLabel, Literal(
            LOCUS_NAME.get(lid, "outside the recognition chain (substrate, not institutional)"))))
    g.add((CK["ChainLocus"], RDF.type, OWL.Class))
    g.add((CK["ChainLocus"], RDFS.label, Literal("recognition chain locus")))
    g.add((CK["ChainLocus"], RDFS.comment, Literal(
        "L1..L7 are the seven links. L0 is a sentinel for terms that are not institutional "
        "at all - the medical and biological substrate the regulation refers to but does "
        "not constitute. L0 is excluded from every locus density in Table 13.")))

    # Primitive individuals, so the flag ledger is self-describing.
    g.add((CK["KernelPrimitive"], RDF.type, OWL.Class))
    g.add((CK["KernelPrimitive"], RDFS.label, Literal("contradiction kernel primitive")))
    for pid, desc in PRIMITIVES.items():
        u = CK[pid.replace("-", "_")]
        g.add((u, RDF.type, OWL.NamedIndividual))
        g.add((u, RDF.type, CK["KernelPrimitive"]))
        g.add((u, RDFS.label, Literal(pid)))
        g.add((u, RDFS.comment, Literal(desc)))
        g.add((u, CK["stratum"], Literal(STRATUM[pid])))
        g.add((u, CK["instrument"], Literal(INSTRUMENT[pid])))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(OUT), format="pretty-xml")
    print(f"wrote {OUT.relative_to(ROOT)}  ({len(g)} triples, "
          f"{len(PROVENANCE)} provenance props, {len(KERNEL_PROPS)} kernel props, "
          f"{len(PRIMITIVES)} primitives, {len(LOCI) + 1} loci)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
