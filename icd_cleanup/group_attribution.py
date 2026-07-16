"""Per-group blast-radius attribution: build an isolated overlay for each FIX-2
group, TBox-reason, report which classes go unsatisfiable. Distinguishes
contained (specific-filler) contradictions from family-wide collapse."""
import io, os, json
from functools import reduce
from contextlib import redirect_stdout, redirect_stderr
from owlready2 import World, onto_path, sync_reasoner, destroy_entity, Restriction

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.dirname(os.path.abspath(__file__))
onto_path.append(os.path.join(REPO, "ontology"))
BASE = os.path.join(CLEAN, "icd11bfo_fix1.owl")

GROUPS = {
    "trigeminal_divisions": ["OphthalmicDivision", "MaxillaryDivision", "MandibularDivision"],
    "skin_layers": ["Epidermis", "Dermis"],
    "ataxia": ["FriedreichAtaxia", "SpinocerebellarAtaxia"],
    "anaphylaxis": ["FoodAnaphylaxis", "InhaledAllergenAnaphylaxis"],
    "haemorrhagic": ["AnticoagulantHaemorrhagicDisorder", "ConstitutionalHaemorrhagicCondition"],
}

def substantive(c):
    out = []
    for r in c.is_a:
        if isinstance(r, Restriction) and getattr(r, "value", None) is not None:
            prop = r.property.iri.split("/")[-1]
            filler = getattr(r.value, "iri", "")
            if prop == "BFO_0000054" and filler.endswith("BFO_0000015"):
                continue
            out.append(r)
    return out

results = {}
for gname, members in GROUPS.items():
    w = World()
    base = w.get_ontology("file://" + BASE).load()
    by_name = {c.name: c for c in base.classes()}
    ov = w.get_ontology("http://x/ov_" + gname)
    with ov:
        for name in members:
            c = by_name.get(name)
            rs = substantive(c)
            expr = reduce(lambda a, b: a & b, rs) if len(rs) > 1 else rs[0]
            c.equivalent_to.append(expr)
    for i in list(w.individuals()):
        destroy_entity(i)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            with w:
                sync_reasoner(w, infer_property_values=False)
    except Exception as e:
        pass
    unsat = sorted(c.name for c in w.inconsistent_classes() if c.name != "Nothing")
    direct = [m for m in members if m in unsat]
    cascade = [u for u in unsat if u not in members]
    results[gname] = {"promoted": members, "n_unsat": len(unsat),
                      "direct_unsat": direct, "cascade": cascade}
    print(f"[{gname}] promoted {members} -> {len(unsat)} unsat "
          f"(direct={len(direct)}, cascade={len(cascade)})")
    print(f"    cascade: {cascade}")

with open(os.path.join(CLEAN, "group_attribution.json"), "w") as f:
    json.dump(results, f, indent=2)
print("\nwrote group_attribution.json")
