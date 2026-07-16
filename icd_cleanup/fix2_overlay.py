"""FIX-2: selective sufficiency overlay (S-2/S-3). Promotes only clinically
substantive indistinguishable-yet-disjoint pairs to owl:equivalentClass over
their OWN existing criteria, in a SEPARATE overlay ontology importing the base.

All findings here are CURATED (analyst-supplied sufficiency); the base had 0
native unsat (baseline). Writes icd11_sufficiency_overlay.owl.
"""
import os
from functools import reduce
from owlready2 import World, onto_path, Restriction

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.dirname(os.path.abspath(__file__))
onto_path.append(os.path.join(REPO, "ontology"))

BASE = os.path.join(CLEAN, "icd11bfo_fix1.owl")
OUT = os.path.join(CLEAN, "icd11_sufficiency_overlay.owl")

# Selected groups: substantive shared criteria only (no degenerate 'realizes some process').
SELECTED = [
    ["OphthalmicDivision", "MaxillaryDivision", "MandibularDivision"],  # part-of TrigeminalNerve
    ["Epidermis", "Dermis"],                                            # part-of Skin
    ["FriedreichAtaxia", "SpinocerebellarAtaxia"],                      # realizes Ataxia
    ["FoodAnaphylaxis", "InhaledAllergenAnaphylaxis"],                  # realizes AnaphylaxisProcess
    ["AnticoagulantHaemorrhagicDisorder", "ConstitutionalHaemorrhagicCondition"],  # realizes HaemorrhagicProcess
]

w = World()
base = w.get_ontology("file://" + BASE).load()
by_name = {c.name: c for c in base.classes()}

overlay = w.get_ontology("http://davidkoepsell.com/bfo-agent/icd11_sufficiency_overlay")
overlay.imported_ontologies.append(base)

def substantive_restrictions(c):
    """someValuesFrom restrictions on the class, excluding the degenerate
    'realizes (BFO_0000054) some process (BFO_0000015)'."""
    out = []
    for r in c.is_a:
        if isinstance(r, Restriction) and getattr(r, "value", None) is not None:
            prop = r.property.iri.split("/")[-1]
            filler = getattr(r.value, "iri", "")
            if prop == "BFO_0000054" and filler.endswith("BFO_0000015"):
                continue  # degenerate: realizes some process
            out.append(r)
    return out

promoted = []
with overlay:
    for group in SELECTED:
        for name in group:
            c = by_name.get(name)
            if not c:
                print(f"  {name}: NOT FOUND -- skip")
                continue
            rs = substantive_restrictions(c)
            if not rs:
                print(f"  {name}: no substantive restriction -- skip")
                continue
            expr = reduce(lambda a, b: a & b, rs) if len(rs) > 1 else rs[0]
            c.equivalent_to.append(expr)   # necessary conditions become sufficient too
            promoted.append(name)
            print(f"  promoted {name}  <=>  {expr}")

overlay.save(file=OUT, format="rdfxml")
print(f"\npromoted {len(promoted)} classes across {len(SELECTED)} groups")
print(f"saved overlay -> {OUT}")
