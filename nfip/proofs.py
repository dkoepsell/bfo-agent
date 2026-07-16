"""Phase E: run the two proof shapes over the NFIP base ontology.

Each proof runs in its own owlready World so state never bleeds between runs.
The reasoner is HermiT (via owlready2). We report the verdict the reasoner
actually returns, plus the clauses whose axioms entered the proof (the
justification: minimal because the model is minimal).
"""
from __future__ import annotations

import owlready2 as o2

from . import nfip_model


def run_shape1() -> dict:
    """Shape 1 (TBox): the mudflow limb of the V.C carveback restores an empty
    class from the earth-movement exclusion. Expect the base ontology to be
    CONSISTENT but with RestoredFromEarthMovement UNSATISFIABLE."""
    w = o2.World()
    onto, h = nfip_model.build_nfip_base(w)
    result = {"shape": 1, "verdict": None, "unsat_classes": [], "target_unsat": False,
              "perils_satisfiable": None}
    try:
        o2.sync_reasoner(w, debug=0)
    except o2.OwlReadyInconsistentOntologyError:
        result["verdict"] = "inconsistent (unexpected for Shape 1)"
        return result
    unsat = list(w.inconsistent_classes())
    names = [c.name for c in unsat]
    result["verdict"] = "consistent"
    result["unsat_classes"] = names
    result["target_unsat"] = h["RestoredFromEarthMovement"] in unsat
    # sanity: the perils themselves must remain satisfiable (only their
    # intersection collapses), else the finding would be trivial.
    result["perils_satisfiable"] = (
        h["Mudflow"] not in unsat and h["EarthMovement"] not in unsat
    )
    return result


def run_shape2() -> dict:
    """Shape 2 (ABox): a single real saturated debris flow. Its one causing event
    answers to two of the policy's own EXTRACTED definitions at once -- Mudflow
    and SaturatedSoilMovement -- which II.C.20 declares disjoint. Expect
    INCONSISTENT (the disjointness is the extraction's, not asserted by the demo)."""
    w = o2.World()
    onto, h = nfip_model.build_nfip_base(w)
    with onto:
        loss = h["Loss"]("saturated_debris_flow_loss")
        # One physical event, classed by the extraction as BOTH a mudflow and a
        # saturated soil mass movement -- the exact fact pattern NFIP disputes
        # turn on. A shared filler, so the extracted disjointness actually fires.
        event = h["xMudflow"]("saturated_debris_flow_event")
        event.is_a.append(h["xSaturatedSoilMovement"])
        loss.exhibits = [event]
    result = {"shape": 2, "verdict": None, "loss": "saturated_debris_flow_loss"}
    try:
        o2.sync_reasoner(w, debug=0)
        # If we get here the loss was NOT forced into both disjoint classes.
        cov = h["CoveredLoss"] in loss.INDIRECT_is_a
        unc = h["UncoveredLoss"] in loss.INDIRECT_is_a
        result["verdict"] = "consistent (unexpected for Shape 2)"
        result["covered"] = cov
        result["uncovered"] = unc
    except o2.OwlReadyInconsistentOntologyError:
        result["verdict"] = "inconsistent"
    return result


if __name__ == "__main__":
    import json
    print(json.dumps({"shape1": run_shape1(), "shape2": run_shape2()}, indent=2))
