"""Sec 4.3 candidate inventory: pairs asserted disjoint or mutually excluded that
ALSO share >=1 criterion restriction; rank classes by participation (debt proxy).

Emits JSON to icd_cleanup/candidate_inventory.json and a human summary.
"""
import sys, os, json
from collections import defaultdict
import rdflib
from rdflib import RDF, RDFS, OWL, URIRef, BNode

g = rdflib.Graph()
g.parse(os.path.abspath(sys.argv[1]))

def local(u):
    s = str(u)
    return s.split("#")[-1].split("/")[-1]

def label(u):
    for l in g.objects(u, RDFS.label):
        return str(l)
    return local(u)

named = lambda u: isinstance(u, URIRef)

# --- collect disjoint pairs ---
pairs = set()
for a, b in ((s, o) for s, o in g.subject_objects(OWL.disjointWith) if named(s) and named(o)):
    pairs.add(frozenset((a, b)))
# AllDisjointClasses groups
for adc in g.subjects(RDF.type, OWL.AllDisjointClasses):
    members = []
    for mlist in g.objects(adc, OWL.members):
        members = [m for m in rdflib.collection.Collection(g, mlist) if named(m)]
    for i in range(len(members)):
        for j in range(i + 1, len(members)):
            pairs.add(frozenset((members[i], members[j])))

# --- complementOf exclusions (named-named) ---
excl = set()
for a, b in g.subject_objects(OWL.complementOf):
    if named(a) and named(b):
        excl.add(frozenset((a, b)))
    # A subClassOf [ complementOf B ]  (a is a bnode restriction here)
for s, o in g.subject_objects(OWL.complementOf):
    if isinstance(s, BNode) and named(o):
        # find named class C with C rdfs:subClassOf s
        for c in g.subjects(RDFS.subClassOf, s):
            if named(c):
                excl.add(frozenset((c, o)))

all_rel = pairs | excl

# --- criteria per class: subClassOf restrictions (property, filler) ---
def criteria(cls):
    crit = set()
    for r in g.objects(cls, RDFS.subClassOf):
        if isinstance(r, BNode) and (r, RDF.type, OWL.Restriction) in g:
            prop = next(g.objects(r, OWL.onProperty), None)
            for pred in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue, OWL.onClass):
                filler = next(g.objects(r, pred), None)
                if filler is not None and named(filler):
                    crit.add((local(prop), local(filler)))
    return crit

# --- participation counts ---
participation = defaultdict(int)
for pr in all_rel:
    for c in pr:
        participation[c] += 1

# --- candidates: relation pairs sharing >=1 criterion ---
candidates = []
for pr in all_rel:
    a, b = tuple(pr)
    shared = criteria(a) & criteria(b)
    kind = "disjoint" if pr in pairs else "complement"
    if pr in pairs and pr in excl:
        kind = "disjoint+complement"
    candidates.append({
        "a": local(a), "b": local(b),
        "a_label": label(a), "b_label": label(b),
        "kind": kind,
        "shared_criteria": sorted(f"{p} {f}" for p, f in shared),
        "n_shared": len(shared),
        "a_participation": participation[a], "b_participation": participation[b],
    })
candidates.sort(key=lambda x: (x["n_shared"], x["a_participation"] + x["b_participation"]), reverse=True)

# --- spec's named priority pairs ---
NAMED = [("CognitiveImpairment", "NormalAging"), ("CornealDegeneration", "CornealDystrophy"),
         ("Leprosy", "LeprosySequela"), ("AcuteRheumaticPericarditis", "AcuteRheumaticFever")]
name2uri = {}
for u in set().union(*all_rel) if all_rel else []:
    name2uri.setdefault(local(u), u)
all_classes = {local(s): s for s in g.subjects(RDF.type, OWL.Class) if named(s)}

print(f"== relations: {len(pairs)} disjoint pairs, {len(excl)} complement exclusions, {len(all_rel)} total ==")
print(f"== candidate pairs sharing >=1 criterion: {sum(1 for c in candidates if c['n_shared']>0)} ==\n")
print("TOP candidates by shared-criteria then participation:")
for c in candidates[:15]:
    if c["n_shared"] == 0:
        break
    print(f"  [{c['kind']}] {c['a']} vs {c['b']}  shared={c['n_shared']}  "
          f"part=({c['a_participation']},{c['b_participation']})  {c['shared_criteria'][:3]}")

print("\nSPEC NAMED pairs (status):")
named_report = []
for an, bn in NAMED:
    au, bu = all_classes.get(an), all_classes.get(bn)
    if not au or not bu:
        print(f"  {an} vs {bn}: one/both NOT PRESENT (a={au is not None}, b={bu is not None})")
        named_report.append({"a": an, "b": bn, "present": False})
        continue
    pr = frozenset((au, bu))
    rel = "disjoint" if pr in pairs else ("complement" if pr in excl else "NONE-asserted")
    shared = criteria(au) & criteria(bu)
    print(f"  {an} vs {bn}: relation={rel}  shared_criteria={sorted(f'{p} {f}' for p,f in shared)}")
    named_report.append({"a": an, "b": bn, "present": True, "relation": rel,
                         "shared_criteria": sorted(f"{p} {f}" for p, f in shared)})

print("\nTOP participation (contradiction-debt proxy):")
for u, n in sorted(participation.items(), key=lambda x: -x[1])[:12]:
    print(f"  {local(u)}: {n}")

out = {"n_disjoint_pairs": len(pairs), "n_complement": len(excl),
       "candidates": candidates, "named_pairs": named_report,
       "top_participation": [{"class": local(u), "n": n}
                             for u, n in sorted(participation.items(), key=lambda x: -x[1])[:30]]}
with open(os.path.join(os.path.dirname(os.path.abspath(sys.argv[1])), "candidate_inventory.json"), "w") as f:
    json.dump(out, f, indent=2)
print("\nwrote candidate_inventory.json")
