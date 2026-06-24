# Regression corpus

`aero_straddle_baseline.owl` is a frozen fixture: ten classes (Force, Weight,
Drag, Lift, Toughness, NoiseNuisance, InducedDrag, WingLoad, GustLoad,
LandingLoad) each typed as BOTH a BFO quality (BFO_0000019) and a BFO
disposition (BFO_0000016). Quality is disjoint from Realizable (Disposition's
parent), so every one of these classes is unsatisfiable. The ontology is still
consistent (no individual instantiates them), which is exactly the
consistent-but-incoherent defect the legacy stdout check missed.

`tests/test_regression_corpus.py` is the before/after that demonstrates the gate
works: the frozen baseline has 10 unsatisfiable classes; regenerating the same
domain through the coherence gate produces zero. This is the natural figure for
the paper.

This stands in for the spec's `aero_base.owl`, which does not exist in this repo
(the repo's real domains are legal and philosophy corpora).
