"""Structural analysis of icd11bfo_v2.owl to ground the cleanup spec."""
import sys
from owlready2 import get_ontology, Thing, Nothing

onto = get_ontology("file://" + sys.argv[1]).load()

def label(c):
    try:
        l = c.label.first() if hasattr(c, "label") else None
    except Exception:
        l = None
    return l or c.name

def iri(c):
    return getattr(c, "iri", str(c))

classes = list(onto.classes())
print(f"== total classes in onto: {len(classes)} ==")

# --- Legal-kernel classes and children ---
LEGAL = ["LegalProcess", "LegalRole", "LegalStatus", "LegalDocument", "Jurisdiction"]
print("\n== LEGAL-KERNEL classes (FIX-1) ==")
by_name = {c.name: c for c in classes}
for name in LEGAL:
    c = by_name.get(name)
    if not c:
        print(f"  {name}: NOT FOUND")
        continue
    subs = [s for s in c.subclasses()]
    parents = [p for p in c.is_a if p is not Thing]
    print(f"  {name}  iri={iri(c)}")
    print(f"     parents: {[getattr(p,'name',str(p)) for p in parents]}")
    print(f"     direct subclasses ({len(subs)}): {[s.name for s in subs]}")

# --- Medical children the spec names ---
MED = ["PituitarySurgery", "CataractExtraction", "DiagnosticProcess", "AttachmentFigure",
       "SurgicalProcedure", "TherapeuticProcess", "Person"]
print("\n== NAMED medical children / anchors ==")
for name in MED:
    c = by_name.get(name)
    if not c:
        print(f"  {name}: NOT FOUND")
        continue
    parents = [p for p in c.is_a if p is not Thing]
    subs = list(c.subclasses())
    print(f"  {name}: parents={[getattr(p,'name',str(p)) for p in parents]}  #subs={len(subs)}")
    if name == "Person":
        print(f"     Person subclasses: {[s.name for s in subs]}")

# --- Any OTHER children of legal classes (transitive) not in MED list ---
print("\n== ALL descendants of legal classes (must be reparented) ==")
for name in LEGAL:
    c = by_name.get(name)
    if not c:
        continue
    desc = [d for d in c.descendants() if d is not c]
    if desc:
        print(f"  under {name}: {[d.name for d in desc]}")

# --- Section 0 invariant: SDC classes bearing has_disposition (BFO_0000196) ---
print("\n== INVARIANT check: has_disposition on SDC classes (must be 0) ==")
hasdisp = onto.search(iri="*BFO_0000196")
print(f"  has_disposition prop found: {[p.name for p in hasdisp]}")

# --- defined classes (equivalentClass) ---
defined = [c for c in classes if getattr(c, "equivalent_to", [])]
print(f"\n== defined classes (equivalentClass): {len(defined)} ==")

# --- disjointness / complement pairs ---
disj = list(onto.disjoint_classes())
print(f"\n== disjoint axioms: {len(disj)} (AllDisjoint groups) ==")

# --- longest label (IP surface) ---
longest = ("", 0)
lab_over_120 = []
for c in classes:
    for l in (c.label or []):
        if len(str(l)) > longest[1]:
            longest = (str(l), len(str(l)))
        if len(str(l)) > 120:
            lab_over_120.append((c.name, len(str(l))))
print(f"\n== longest rdfs:label: {longest[1]} chars -> {longest[0][:100]!r} ==")
print(f"   labels >120 chars: {len(lab_over_120)}")

# --- comments (IP surface) ---
ncomment = sum(1 for c in classes if getattr(c, "comment", []))
print(f"== classes with rdfs:comment: {ncomment} ==")

print("\nDONE")
