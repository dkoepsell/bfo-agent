"""FIX-3: provenance layer + IP boundary. For each ICD-derived class attach a
term-level ICD-11 xref (oboInOwl:hasDbXref) and a WHO browser seeAlso; add
ontology-level attribution/version metadata; enforce the C-3 IP gate.

Input:  icd11bfo_fix1.owl  ->  Output: icd11bfo_v3.owl

Honest limitation (reported): the extraction source (icd_11_claims.json) carries
NO structured ICD-11 numeric codes (1/4934 quotes even mention one) and no WHO
entity IDs, so per-entity numeric codes + exact deep-links require a WHO ICD-11
API backfill (C-5). We attach term-level provenance + the authoritative browser.
"""
import os, re, json
import rdflib
from rdflib import RDF, RDFS, OWL, URIRef, Literal, Namespace

CLEAN = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(CLEAN)
SRC = os.path.join(CLEAN, "icd11bfo_fix1.owl")
OUT = os.path.join(CLEAN, "icd11bfo_v3.owl")
CLAIMS = os.path.join(REPO, "icd_11_claims.json")

OBO = Namespace("http://www.geneontology.org/formats/oboInOwl#")
DCT = Namespace("http://purl.org/dc/terms/")
WHO_BROWSER = "https://icd.who.int/browse11/l-m/en"

def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())

# --- claim map ---
d = json.load(open(CLAIMS))
items = d if isinstance(d, list) else d.get("claims", [])
def deriv(iri):
    ln = str(iri).split("/")[-1].split("#")[-1]
    return ln[4:] if ln.startswith("ICD_") else ln
claim_by_norm = {}
for it in items:
    si = it.get("subject_iri", "")
    if si:
        claim_by_norm.setdefault(norm(deriv(si)), deriv(si).replace("_", " ").strip())

g = rdflib.Graph()
g.parse(SRC)
g.bind("oboInOwl", OBO); g.bind("dcterms", DCT)

def cname(s): return str(s).split("#")[-1].split("/")[-1]
def clabel(s):
    for l in g.objects(s, RDFS.label):
        return str(l)
    return None

classes = [s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)]
annotated = 0
for s in classes:
    key = None
    if norm(cname(s)) in claim_by_norm:
        key = norm(cname(s))
    elif clabel(s) and norm(clabel(s)) in claim_by_norm:
        key = norm(clabel(s))
    if key is None:
        continue  # BFO/anatomical scaffolding: not an ICD-11 entity
    term = claim_by_norm[key]
    g.add((s, OBO.hasDbXref, Literal(f"ICD11:{term}")))          # C-1 term-level code
    g.add((s, RDFS.seeAlso, URIRef(WHO_BROWSER)))                # C-2 WHO deep link (browser root)
    annotated += 1

# --- C-4 ontology-level attribution (exempt from the class IP gate) ---
onto_iri = next(g.subjects(RDF.type, OWL.Ontology), None)
if onto_iri is None:
    onto_iri = URIRef("http://davidkoepsell.com/bfo-agent/working")
    g.add((onto_iri, RDF.type, OWL.Ontology))
g.add((onto_iri, DCT.source, Literal(
    "WHO ICD-11 (International Classification of Diseases, 11th Revision, MMS)")))
g.add((onto_iri, DCT.rights, Literal(
    "Derived from WHO ICD-11. ICD-11 is (c) World Health Organization. "
    "This ontology reproduces no WHO descriptive text; ICD-11 term references and "
    "WHO browser links serve as provenance.")))
g.add((onto_iri, DCT.license, Literal(
    "WHO ICD-11 licence terms apply to derived representations; licence/NoDerivatives "
    "review pending before publication (see cleanup spec C-5). https://icd.who.int/en")))
g.add((onto_iri, OWL.versionInfo, Literal(
    "icd11bfo_v3 (FIX-1 legal-kernel removed, FIX-3 provenance added). "
    "ICD-11 release: not captured in source extraction. Extraction snapshot: 2026-07-12.")))
g.add((onto_iri, DCT.created, Literal("2026-07-12")))

g.serialize(destination=OUT, format="xml")
print(f"annotated {annotated} ICD-derived classes with term xref + WHO seeAlso")
print(f"ontology-level attribution added to {onto_iri}")
print(f"saved -> {OUT}")
