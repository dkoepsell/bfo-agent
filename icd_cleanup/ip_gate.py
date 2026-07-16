"""C-3 IP gate: fail if any CLASS/PROPERTY annotation literal is long enough to be
WHO descriptive prose (>MAX_LEN chars). Ontology-header annotations (our own
attribution text) are exempt. Exit 1 on violation.

Usage: ip_gate.py <owl_file> [max_len]
"""
import sys, os
import rdflib
from rdflib import RDF, RDFS, OWL, URIRef, Literal

MAX_LEN = int(sys.argv[2]) if len(sys.argv) > 2 else 120

# annotation predicates that could smuggle WHO prose onto a term
PROSE_PREDS = [
    RDFS.label, RDFS.comment,
    URIRef("http://www.w3.org/2004/02/skos/core#definition"),
    URIRef("http://purl.obolibrary.org/obo/IAO_0000115"),  # definition
    URIRef("http://www.geneontology.org/formats/oboInOwl#hasDefinition"),
]

def main(path):
    g = rdflib.Graph(); g.parse(path)
    onto_subjects = set(g.subjects(RDF.type, OWL.Ontology))
    term_subjects = set(g.subjects(RDF.type, OWL.Class)) | \
        set(g.subjects(RDF.type, OWL.ObjectProperty)) | \
        set(g.subjects(RDF.type, OWL.AnnotationProperty)) | \
        set(g.subjects(RDF.type, OWL.DatatypeProperty))
    violations = []
    for s, p, o in g:
        if not isinstance(o, Literal):
            continue
        if s in onto_subjects:            # exempt our attribution header
            continue
        if p in PROSE_PREDS or "definition" in str(p).lower() or "description" in str(p).lower():
            if s in term_subjects and len(str(o)) > MAX_LEN:
                violations.append((str(s).split("#")[-1], str(p).split("#")[-1].split("/")[-1], len(str(o)), str(o)[:80]))
    print(f"IP GATE ({os.path.basename(path)}): max_len={MAX_LEN}")
    print(f"  term annotation literals scanned; violations (>{MAX_LEN} chars): {len(violations)}")
    for name, pred, ln, txt in violations[:20]:
        print(f"    FAIL {name} [{pred}] {ln} chars: {txt!r}")
    ok = len(violations) == 0
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main(os.path.abspath(sys.argv[1])))
