"""C-3 IP gate (icd11 cleanup spec, acceptance #5).

Prevents a run from ingesting third-party descriptive prose (e.g. WHO ICD-11
definitions/clinical descriptions) into term annotations. Labels must stay short
term-names authored in the extraction's own words; provenance rides on codes and
seeAlso links, never copied source text.

Fails if any CLASS/PROPERTY annotation literal on a prose-bearing predicate
exceeds ``max_len`` chars. Ontology-header annotations (our own attribution
metadata) are exempt.

Usage as a check:
    from app.ip_gate import check_ip
    violations = check_ip(path_or_graph)          # [] means pass
CLI:
    python -m app.ip_gate <owl_file> [max_len]    # exit 1 on violation
"""
from __future__ import annotations

import sys
import rdflib
from rdflib import RDF, RDFS, OWL, URIRef, Literal

DEFAULT_MAX_LEN = 120

# annotation predicates that could smuggle third-party prose onto a term
_PROSE_PREDS = {
    RDFS.label,
    RDFS.comment,
    URIRef("http://www.w3.org/2004/02/skos/core#definition"),
    URIRef("http://purl.obolibrary.org/obo/IAO_0000115"),          # definition
    URIRef("http://www.geneontology.org/formats/oboInOwl#hasDefinition"),
}

_TERM_TYPES = (OWL.Class, OWL.ObjectProperty, OWL.AnnotationProperty, OWL.DatatypeProperty)


def check_ip(source, max_len: int = DEFAULT_MAX_LEN) -> list[dict]:
    """Return a list of violation dicts (empty == pass).

    ``source`` may be a file path/URL or an rdflib.Graph.
    """
    if isinstance(source, rdflib.Graph):
        g = source
    else:
        g = rdflib.Graph()
        g.parse(str(source))

    onto_subjects = set(g.subjects(RDF.type, OWL.Ontology))
    term_subjects = set()
    for t in _TERM_TYPES:
        term_subjects |= set(g.subjects(RDF.type, t))

    violations: list[dict] = []
    for s, p, o in g:
        if not isinstance(o, Literal) or s in onto_subjects:
            continue
        is_prose = p in _PROSE_PREDS or "definition" in str(p).lower() or "description" in str(p).lower()
        if is_prose and s in term_subjects and len(str(o)) > max_len:
            violations.append({
                "term": str(s).split("#")[-1].split("/")[-1],
                "predicate": str(p).split("#")[-1].split("/")[-1],
                "length": len(str(o)),
                "text": str(o)[:100],
            })
    return violations


def main(argv: list[str]) -> int:
    path = argv[1]
    max_len = int(argv[2]) if len(argv) > 2 else DEFAULT_MAX_LEN
    v = check_ip(path, max_len)
    print(f"IP GATE ({path}): max_len={max_len}, violations={len(v)}")
    for x in v[:20]:
        print(f"  FAIL {x['term']} [{x['predicate']}] {x['length']} chars: {x['text']!r}")
    print("RESULT:", "PASS" if not v else "FAIL")
    return 0 if not v else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
