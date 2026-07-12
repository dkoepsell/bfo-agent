"""FIX-1: reparent medical children off the SOoL legal kernel, delete dead legal classes.

Input:  icd11bfo_v2.owl
Output: icd11bfo_fix1.owl
"""
import sys, os
from owlready2 import World, onto_path, destroy_entity, Thing

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
onto_path.append(os.path.join(REPO, "ontology"))
SRC = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2])

w = World()
onto = w.get_ontology("file://" + SRC).load()

def C(name):
    for c in onto.classes():
        if c.name == name:
            return c
    return None

BFO_PROCESS = w.search_one(iri="http://purl.obolibrary.org/obo/BFO_0000015")
BFO_ROLE = w.search_one(iri="http://purl.obolibrary.org/obo/BFO_0000023")
print("BFO process:", BFO_PROCESS, "| BFO role:", BFO_ROLE)

LEGAL = ["LegalProcess", "LegalRole", "LegalStatus", "LegalDocument", "Jurisdiction"]
legal_classes = {n: C(n) for n in LEGAL}
legal_iris = {c.iri for c in legal_classes.values() if c}

# --- 1. Find EVERY reference to a legal class (parent edge, restriction filler,
#        disjoint/equivalent), so nothing dangles after deletion. ---
print("\n== references to legal classes ==")
refs = {n: [] for n in LEGAL}
for c in onto.classes():
    if c in legal_classes.values():
        continue
    for parent in list(c.is_a) + list(getattr(c, "equivalent_to", [])):
        # named parent
        pname = getattr(parent, "name", None)
        if pname in legal_classes:
            refs[pname].append((c.name, "is_a/equiv"))
        # restriction whose filler is a legal class
        filler = getattr(parent, "value", None)
        fname = getattr(filler, "name", None)
        if fname in legal_classes:
            refs[fname].append((c.name, "restriction-filler"))
for n, rs in refs.items():
    print(f"  {n}: {rs}")

# --- 2. Reparent the medical children (remove legal parent, ensure BFO anchor). ---
REPARENT = {  # child -> intended BFO parent
    "PituitarySurgery": BFO_PROCESS,
    "CataractExtraction": BFO_PROCESS,
    "DiagnosticProcess": BFO_PROCESS,
    "AttachmentFigure": BFO_ROLE,
}
print("\n== reparenting ==")
for cname, bfo_parent in REPARENT.items():
    c = C(cname)
    if not c:
        print(f"  {cname}: NOT FOUND (skip)")
        continue
    before = [getattr(p, "name", str(p)) for p in c.is_a]
    # drop legal parents
    c.is_a = [p for p in c.is_a if getattr(p, "name", None) not in legal_classes]
    # ensure a concrete BFO/domain parent remains
    named_parents = [p for p in c.is_a if p is not Thing and hasattr(p, "iri")]
    if not any(getattr(p, "iri", "").startswith("http://purl.obolibrary.org/obo/BFO")
               or getattr(p, "name", "") not in legal_classes for p in named_parents):
        c.is_a.append(bfo_parent)
    if not [p for p in c.is_a if p is not Thing]:
        c.is_a.append(bfo_parent)
    after = [getattr(p, "name", str(p)) for p in c.is_a]
    print(f"  {cname}: {before} -> {after}")

# --- 3. Any OTHER descendant of a legal class must also be reparented. ---
for n, c in legal_classes.items():
    if not c:
        continue
    for sub in list(c.subclasses()):
        if sub.name in REPARENT:
            continue
        before = [getattr(p, "name", str(p)) for p in sub.is_a]
        sub.is_a = [p for p in sub.is_a if getattr(p, "name", None) not in legal_classes]
        if not [p for p in sub.is_a if p is not Thing]:
            sub.is_a.append(BFO_PROCESS)
        print(f"  extra child {sub.name}: {before} -> {[getattr(p,'name',str(p)) for p in sub.is_a]}")

# --- 4. Delete the dead legal classes. ---
print("\n== deleting legal classes ==")
for n, c in legal_classes.items():
    if c:
        destroy_entity(c)
        print(f"  destroyed {n}")
    else:
        print(f"  {n}: already absent")

# --- 5. Verify no orphans among former children; Person retained. ---
print("\n== verification ==")
for cname in list(REPARENT):
    c = C(cname)
    parents = [getattr(p, "name", str(p)) for p in c.is_a if p is not Thing]
    print(f"  {cname} parents now: {parents}  {'OK' if parents else 'ORPHAN!!'}")
person = C("Person")
print(f"  Person present: {person is not None}  subclasses={len(list(person.subclasses())) if person else 0}")

# --- 6. Purge orphaned seed# legal-kernel label triples (different namespace,
#        no real edges) so ZERO legal-kernel references remain (acceptance #1). ---
import rdflib
g = w.as_rdflib_graph()
SEED = "http://davidkoepsell.com/bfo-agent/seed#"
purged = 0
for n in LEGAL:
    subj = rdflib.URIRef(SEED + n)
    for t in list(g.triples((subj, None, None))):
        g.remove(t); purged += 1
    for t in list(g.triples((None, None, subj))):
        g.remove(t); purged += 1
print(f"\n== purged {purged} orphaned seed# legal triples ==")

onto.save(file=OUT, format="rdfxml")
print(f"\nsaved -> {OUT}")
