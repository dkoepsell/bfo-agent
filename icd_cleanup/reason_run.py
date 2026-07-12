"""Run HermiT on an ICD artifact; report consistency + unsatisfiable classes.

Usage: reason_run.py <owl_path> <label> [extra_import.owl ...]
The verified stack: owlready2 -> HermiT (same reasoner the app uses).
"""
import io, os, sys
from contextlib import redirect_stdout, redirect_stderr
from owlready2 import World, onto_path, sync_reasoner, Nothing

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
onto_path.append(os.path.join(REPO, "ontology"))   # resolves obo/bfo.owl

owl_path = os.path.abspath(sys.argv[1])
label = sys.argv[2]
extra = sys.argv[3:]

w = World()
onto = w.get_ontology("file://" + owl_path).load()
for e in extra:
    w.get_ontology("file://" + os.path.abspath(e)).load()

n_classes = len(list(onto.classes()))
buf = io.StringIO()
consistent = True
err = ""
try:
    with redirect_stdout(buf), redirect_stderr(buf):
        with w:
            sync_reasoner(w, infer_property_values=False)
except Exception as e:  # HermiT raises on outright inconsistency
    consistent = False
    err = f"{type(e).__name__}: {str(e)[:400]}"

# Unsatisfiable named classes = inferred equivalent to owl:Nothing
unsat = []
try:
    for c in w.inconsistent_classes():
        unsat.append(getattr(c, "iri", str(c)))
except Exception as e:
    err += f" | inconsistent_classes() failed: {e}"

print(f"### REASONER RUN: {label}")
print(f"file: {owl_path}")
print(f"classes_loaded: {n_classes}")
print(f"ontology_consistent: {consistent}")
if err:
    print(f"error: {err}")
print(f"unsatisfiable_class_count: {len(unsat)}")
for u in sorted(unsat):
    print(f"  UNSAT: {u}")
out = buf.getvalue()
# surface any reasoner markers
for marker in ("Inconsistent", "inconsistent", "Unsatisfiable", "nothing"):
    if marker in out:
        print(f"[reasoner-output contains '{marker}']")
        break
print("### END")
