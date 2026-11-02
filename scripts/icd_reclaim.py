"""Reconstruct the ICD-11 claim corpus from an already-imported OWL file.

The imported `icd11bfo.owl` embeds its entire source corpus as reified claim
individuals (`ICD_Claim`), each carrying the verbatim ICD text (`source_quote`),
the natural-language claim (`claim_text`), the subject class it is about
(IAO_0000136 / "is about"), extraction confidence/notes, and -- for 840 of them
-- a Contradiction-Kernel candidate flag (`has_contradiction_candidate`).

Because that corpus is lossless, "restructuring the imported ontology" reduces
to: pull the claims back out, then re-run them through the real proposer ->
construction-linter -> gate pipeline (which the raw import never passed through).

This script only does the extraction half. It writes a claims JSON in the shape
`app.jobs.append_claims` consumes:
    {claim, source_quote, confidence, note, section, chunk_index, approved,
     ck_candidate?}

Usage:
    python -m scripts.icd_reclaim icd11bfo.owl --out icd_11_claims.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import rdflib
from rdflib import RDF, OWL, RDFS, URIRef

NS = "http://purl.obolibrary.org/obo/icd11bfo/"
IAO_IS_ABOUT = URIRef("http://purl.obolibrary.org/obo/IAO_0000136")
P = {k: URIRef(NS + k) for k in (
    "claim_text", "source_quote", "extraction_confidence",
    "extraction_note", "chunk_index", "has_contradiction_candidate",
)}


def _one(g, s, p):
    for o in g.objects(s, p):
        return str(o)
    return ""


def reclaim(target: str) -> list[dict]:
    g = rdflib.Graph()
    g.parse(target)
    labels = {str(s): str(o) for s, o in g.subject_objects(RDFS.label)}
    inds = [i for i in g.subjects(RDF.type, OWL.NamedIndividual)
            if isinstance(i, URIRef)]

    claims = []
    for i in sorted(inds, key=str):
        subject_iri = _one(g, i, IAO_IS_ABOUT)
        section = labels.get(subject_iri, "")
        ck = _one(g, i, P["has_contradiction_candidate"])
        claim_text = _one(g, i, P["claim_text"])
        if not claim_text:
            continue
        claims.append({
            "claim": claim_text,
            "source_quote": _one(g, i, P["source_quote"]),
            "confidence": (_one(g, i, P["extraction_confidence"]) or "medium").lower(),
            "note": _one(g, i, P["extraction_note"]),
            # `section` carries the subject class label so the proposer's reuse
            # ranking can anchor the claim to the existing class it is about.
            "section": section,
            "subject_iri": subject_iri,
            "chunk_index": int(_one(g, i, P["chunk_index"]) or 0),
            # Re-propose everything; approval is the gate's job now, not the
            # old import's. Kept faithful (extraction-fidelity principle).
            "approved": True,
            "ck_candidate": ck.rsplit("/", 1)[-1] if ck else None,
        })
    return claims


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reconstruct ICD-11 claim corpus")
    ap.add_argument("target")
    ap.add_argument("--out", default="icd_11_claims.json")
    args = ap.parse_args(argv)

    claims = reclaim(args.target)
    Path(args.out).write_text(json.dumps(claims, indent=2))
    flagged = sum(1 for c in claims if c["ck_candidate"])
    with_quote = sum(1 for c in claims if c["source_quote"])
    print(f"reclaimed {len(claims)} claims -> {args.out}")
    print(f"  with source_quote ......... {with_quote}")
    print(f"  with CK candidate flag .... {flagged}")
    # confidence distribution
    from collections import Counter
    dist = Counter(c["confidence"] for c in claims)
    print(f"  confidence ................ {dict(dist)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
