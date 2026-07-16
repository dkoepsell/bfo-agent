"""Synthetic regression fixtures (coverage-kernel-spec.md section 9/10).

These prove the INSTRUMENT works, independent of any NFIP modelling choice:
the reasoner detects the two proof shapes on tiny, obviously-correct inputs.
run_fixtures() must be all-green before any NFIP verdict is trusted.
"""
from __future__ import annotations

import owlready2 as o2

from . import kernel


def _fx_bivalence() -> dict:
    """The pivot axiom fires: one loss asserted both covered and uncovered is
    inconsistent (proves CoveredLoss/UncoveredLoss disjointness is present)."""
    w = o2.World()
    k = kernel.build_coverage_kernel(w)
    with k:
        l = k.Loss("l")
        l.is_a.append(k.CoveredLoss)
        l.is_a.append(k.UncoveredLoss)
    try:
        o2.sync_reasoner(w, debug=0)
        return {"name": "kernel_bivalence", "expect": "inconsistent",
                "got": "consistent", "pass": False}
    except o2.OwlReadyInconsistentOntologyError:
        return {"name": "kernel_bivalence", "expect": "inconsistent",
                "got": "inconsistent", "pass": True}


def _fx_illusory_carveback() -> dict:
    """Shape 1: a carveback restoring class Y from an exclusion removing class X,
    with X and Y disjoint, restores an empty class."""
    w = o2.World()
    k = kernel.build_coverage_kernel(w)
    onto = w.get_ontology("https://sool.tamu.edu/fx/carveback")
    with onto:
        class ExcludedForm(k.ExcludedLoss):
            pass

        class RestoredForm(k.RestoredLoss):
            pass

        o2.AllDisjoint([ExcludedForm, RestoredForm])

        class RestoredFromExclusion(k.Loss):
            pass

        RestoredFromExclusion.equivalent_to = [k.Loss & ExcludedForm & RestoredForm]
    o2.sync_reasoner(w, debug=0)
    unsat = list(w.inconsistent_classes())
    ok = RestoredFromExclusion in unsat and ExcludedForm not in unsat
    return {"name": "illusory_carveback", "expect": "target unsatisfiable",
            "got": "unsatisfiable" if RestoredFromExclusion in unsat else "satisfiable",
            "pass": ok}


def _fx_same_term() -> dict:
    """Shape 2: a term with two disjoint senses; an individual satisfying both
    makes the ontology inconsistent."""
    w = o2.World()
    onto = w.get_ontology("https://sool.tamu.edu/fx/sameterm")
    with onto:
        class SenseA(o2.Thing):
            pass

        class SenseB(o2.Thing):
            pass

        o2.AllDisjoint([SenseA, SenseB])
        x = SenseA("term_x")
        x.is_a.append(SenseB)
    try:
        o2.sync_reasoner(w, debug=0)
        return {"name": "same_term", "expect": "inconsistent",
                "got": "consistent", "pass": False}
    except o2.OwlReadyInconsistentOntologyError:
        return {"name": "same_term", "expect": "inconsistent",
                "got": "inconsistent", "pass": True}


def run_fixtures() -> dict:
    results = [_fx_bivalence(), _fx_illusory_carveback(), _fx_same_term()]
    return {"all_pass": all(r["pass"] for r in results), "fixtures": results}


if __name__ == "__main__":
    import json
    print(json.dumps(run_fixtures(), indent=2))
