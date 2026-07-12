"""Section-0 invariant: no specifically-dependent-continuant (SDC) class bears
has_disposition (BFO_0000196). has_disposition must be borne only by independent
continuants. Prints violations (must be empty)."""
import sys, os
from owlready2 import World, onto_path, Restriction

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
onto_path.append(os.path.join(REPO, "ontology"))
w = World()
onto = w.get_ontology("file://" + os.path.abspath(sys.argv[1])).load()

SDC = w.search_one(iri="http://purl.obolibrary.org/obo/BFO_0000020")  # SDC
HASDISP = w.search_one(iri="http://purl.obolibrary.org/obo/BFO_0000196")
print("SDC class:", SDC, "| has_disposition:", HASDISP)

def is_sdc(c):
    try:
        return SDC in c.ancestors()
    except Exception:
        return False

violations = []
n_hasdisp = 0
for c in onto.classes():
    for sup in c.is_a:
        if isinstance(sup, Restriction) and getattr(sup, "property", None) is HASDISP:
            n_hasdisp += 1
            if is_sdc(c):
                violations.append(c.name)
print(f"has_disposition restrictions total: {n_hasdisp}")
print(f"SDC classes bearing has_disposition (MUST be 0): {len(violations)} {violations}")
