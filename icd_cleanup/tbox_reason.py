"""Reason over the TBox only (individuals stripped) to enumerate unsatisfiable
classes cleanly. Usage: tbox_reason.py <base.owl> <label> [overlay.owl ...]"""
import io, os, sys
from contextlib import redirect_stdout, redirect_stderr
from owlready2 import World, onto_path, sync_reasoner, destroy_entity

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
onto_path.append(os.path.join(REPO, "ontology"))

base_path = os.path.abspath(sys.argv[1])
label = sys.argv[2]
overlays = sys.argv[3:]

w = World()
base = w.get_ontology("file://" + base_path).load()
for o in overlays:
    w.get_ontology("file://" + os.path.abspath(o)).load()

# strip ABox so unsatisfiable classes are enumerable (empty unsat class != inconsistent onto)
inds = list(w.individuals())
for i in inds:
    destroy_entity(i)

buf = io.StringIO()
consistent, err = True, ""
try:
    with redirect_stdout(buf), redirect_stderr(buf):
        with w:
            sync_reasoner(w, infer_property_values=False)
except Exception as e:
    consistent = False
    err = f"{type(e).__name__}: {str(e)[:300]}"

unsat = sorted(getattr(c, "iri", str(c)).split("#")[-1].split("/")[-1]
               for c in w.inconsistent_classes())
print(f"### TBOX RUN: {label}")
print(f"individuals_stripped: {len(inds)}")
print(f"tbox_consistent: {consistent}")
if err:
    print(f"error: {err}")
print(f"unsatisfiable_class_count: {len(unsat)}")
for u in unsat:
    print(f"  UNSAT: {u}")
print("### END")
