"""Phase B: the NFIP Dwelling-Form domain layer, sourced from the FED extraction.

The domain classes are NOT hand-authored here. They are loaded from the NFIP
extraction corpus -- the working.owl the feed pipeline produced over
44CFR61NFIP.txt (fidelity=curated). This module adds only the thin coverage
OVERLAY the flat extraction lacks: it assigns kernel coverage roles to the
extracted peril classes and groups the II.C.20 earth-movement forms, then returns
handles the proof shapes run over.

The load-bearing disjointness both findings turn on --

    Mudflow  DISJOINT  {Landslide, SlopeFailure, SaturatedSoilMovement}

-- is the extraction's own faithful rendering of II.C.20 ("Other earth movements,
such as landslide, slope failure, or a saturated soil mass moving by liquidity
down a slope, are not mudflows"). It is NOT asserted here. So the findings are
proved over the policy's own extracted text, and every domain axiom is
independently checkable in the extraction corpus.

Two proof shapes fall out (see nfip/proofs.py):
  Shape 1  RestoredFromEarthMovement -- the losses the mudflow carveback restores
           FROM the earth-movement exclusion (one event that is both mudflow and
           an earth-movement form) -- is unsatisfiable. The carveback restores
           nothing from the exclusion it modifies.
  Shape 2  a real saturated debris flow: one causing event the extracted
           definitions class as BOTH mudflow AND a saturated soil mass movement
           (disjoint) -> one loss, covered and excluded at once.
"""
from __future__ import annotations

import os

import owlready2 as o2

from . import kernel

HERE = os.path.dirname(__file__)
ROOT = os.path.dirname(HERE)
NFIP_IRI = "https://sool.tamu.edu/nfip/sfip-dwelling"

# The fed extraction's ontology IRI (its classes are <EXTRACTION_IRI>#Name).
EXTRACTION_IRI = "http://davidkoepsell.com/bfo-agent/working"

# Extracted peril classes this overlay anchors to (fragments under EXTRACTION_IRI).
_X = {
    "Mudflow": "#Mudflow",
    "Landslide": "#Landslide",
    "SlopeFailure": "#SlopeFailure",
    "SaturatedSoilMovement": "#SaturatedSoilMovement",
    "Flood": "#Flood",
}


def extraction_path(extraction_owl: str | None = None) -> str:
    """Resolve the fed NFIP extraction OWL.

    Prefer an explicit path, then the LIVE fed corpus
    (ontology/library/NFIP/working.owl) when present -- so on the server the demo
    tracks the corpus as fed -- then the vendored snapshot shipped in this package
    for reproducible local builds."""
    if extraction_owl:
        return extraction_owl
    live = os.path.join(ROOT, "ontology", "library", "NFIP", "working.owl")
    if os.path.exists(live):
        return live
    return os.path.join(HERE, "data", "nfip_extraction.owl")


def build_nfip_base(world: o2.World, extraction_owl: str | None = None):
    """Build kernel + fed extraction + coverage overlay in `world`.

    Returns (overlay_ontology, handles). Handles expose the kernel Loss/coverage
    classes, the overlay peril loss-classes, and the extracted peril classes the
    ABox proof instantiates."""
    kern = kernel.build_coverage_kernel(world)
    Loss = kern.Loss
    exhibits = kern.exhibits

    # Load the FED extraction into the same world. Its BFO process backbone
    # (BFO_0000015 etc., canonical obo IRIs) unifies with the kernel's; the
    # external bfo.owl import is elided in the vendored snapshot so reasoning
    # stays offline and the demo needs no network.
    ext_path = extraction_path(extraction_owl)
    world.get_ontology("file://" + os.path.abspath(ext_path)).load()

    def X(key):
        iri = EXTRACTION_IRI + _X[key]
        cls = world[iri]
        if cls is None:
            raise RuntimeError(
                f"extraction is missing expected class {iri!r} "
                f"(loaded from {ext_path})")
        return cls

    xMudflow = X("Mudflow")
    xLandslide = X("Landslide")
    xSlopeFailure = X("SlopeFailure")
    xSSM = X("SaturatedSoilMovement")
    xFlood = X("Flood")

    onto = world.get_ontology(NFIP_IRI)
    onto.imported_ontologies.append(kern)

    with onto:
        # LossFeature marks which extracted classes are the factual features a
        # Loss can exhibit (peril/mechanism). It is the coverage kernel's tag on
        # the fed taxonomy -- it drives the interactive claim-checker's feature
        # vocabulary; it adds no domain axiom.
        class LossFeature(o2.Thing):
            """An extracted peril/mechanism a Loss can exhibit."""

        for feat in (xMudflow, xLandslide, xSlopeFailure, xSSM, xFlood):
            feat.is_a.append(LossFeature)

        # II.C.20 earth-movement forms: exactly the ones the text says are NOT
        # mudflows. Grouping them is the analytical overlay the flat extraction
        # lacked; the disjointness with mudflow is the extraction's, not ours.
        # (Subsidence is deliberately excluded -- it belongs to the carveback's
        # separate erosion-driven limb, which the finding does not implicate.)
        class EarthMovementForm(o2.Thing):
            """Union of the II.C.20 forms declared disjoint from mudflow."""

        EarthMovementForm.equivalent_to = [xLandslide | xSlopeFailure | xSSM]

        # Peril loss-classes: a Loss classified by the extracted peril its single
        # causing event exhibits. DEFINED, so membership is entailed. Named
        # *Loss so they never collide with the extracted peril classes.
        class MudflowLoss(Loss):
            pass

        class EarthMovementLoss(Loss):
            pass

        MudflowLoss.equivalent_to = [Loss & exhibits.some(xMudflow)]
        EarthMovementLoss.equivalent_to = [Loss & exhibits.some(EarthMovementForm)]

        # Coverage roles (kernel markers). Mudflow is granted (II.B.1.c) and
        # restored by the V.C carveback; the earth-movement forms are excluded.
        MudflowLoss.is_a.append(kern.GrantedLoss)
        MudflowLoss.is_a.append(kern.RestoredLoss)
        EarthMovementLoss.is_a.append(kern.ExcludedLoss)

        # Shape 1 target: the losses the mudflow carveback restores FROM the
        # earth-movement exclusion -- a single causing event that is both a
        # mudflow and an earth-movement form. Unsatisfiable, because the
        # extraction makes those disjoint, so the carveback restores nothing.
        class RestoredFromEarthMovement(Loss):
            pass

        RestoredFromEarthMovement.equivalent_to = [
            Loss & exhibits.some(xMudflow & EarthMovementForm)
        ]

    handles = {
        "Loss": Loss,
        "exhibits": exhibits,
        # Peril loss-classes (keyed by the peril concept; the class objects are
        # *Loss so proofs use them by identity).
        "Mudflow": MudflowLoss,
        "EarthMovement": EarthMovementLoss,
        "RestoredFromEarthMovement": RestoredFromEarthMovement,
        "CoveredLoss": kern.CoveredLoss,
        "UncoveredLoss": kern.UncoveredLoss,
        # Extracted peril classes the ABox proof (Shape 2) instantiates.
        "xMudflow": xMudflow,
        "xSaturatedSoilMovement": xSSM,
        "xLandslide": xLandslide,
        "xSlopeFailure": xSlopeFailure,
        "xFlood": xFlood,
    }
    return onto, handles
