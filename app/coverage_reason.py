"""Coverage-coherence reasoning service (prod).

Reasons over a coverage-profile ontology's persisted working.owl in a throwaway
owlready World (the manager's no-pollution pattern), so this never touches the
live in-memory ontology or any running feed.

Three entry points, all additive:
  * score(working_path)                  - base reasoner score for the ontology.
  * check_claim(working_path, claim)     - a well-formed claim / fact pattern ->
                                           coherence verdict + colliding clauses.
  * analyze_wording(text, ...)           - paste NEW policy wording -> ephemeral
                                           coverage extraction + check (Track-2).

Findings are the product: verdict + verbatim clause + plain English + a compact
score. The kernel axioms and typology are never returned.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import owlready2 as o2

from .ontology_manager import _REASONER_LOCK, _sync_reasoner_guarded

ROOT = os.path.dirname(os.path.dirname(__file__))
KERNEL_NS = "https://sool.tamu.edu/coverage-kernel#"
NFIP_NS = "https://sool.tamu.edu/nfip/sfip-dwelling#"
_CLAUSES_PATH = os.path.join(ROOT, "nfip", "sfip_clauses.json")

# Which SFIP clauses classify each loss feature (for colliding-clause reporting).
# Faithful to the Dwelling Form: the mudflow-grant side vs the earth-movement
# exclusion side. Kept small and explicit; extend as the corpus grows.
# Keyed by the EXTRACTED feature-class names (the fed corpus's own vocabulary).
_FEATURE_CLAUSES = {
    "Mudflow": ["SFIP-DW-II-B", "SFIP-DW-II-C-20"],
    "SaturatedSoilMovement": ["SFIP-DW-II-C-20", "SFIP-DW-V-C"],
    "Landslide": ["SFIP-DW-II-C-20", "SFIP-DW-V-C"],
    "SlopeFailure": ["SFIP-DW-II-C-20", "SFIP-DW-V-C"],
}
# Which clauses an unsatisfiable coverage class implicates.
_UNSAT_CLAUSES = {
    "RestoredFromEarthMovement": ["SFIP-DW-V-C-CB", "SFIP-DW-V-C", "SFIP-DW-II-C-20"],
}


def _clause_records() -> dict:
    if not os.path.exists(_CLAUSES_PATH):
        return {}
    data = json.load(open(_CLAUSES_PATH, encoding="utf-8"))
    return {c["id"]: c for c in data.get("clauses", [])}


def _cite(clauses: dict, ids: list[str]) -> list[dict]:
    out = []
    for cid in dict.fromkeys(ids):  # de-dup, preserve order
        c = clauses.get(cid, {})
        out.append({"id": cid, "cfr_cite": c.get("cfr_cite", ""),
                    "sfip_section": c.get("sfip_section", ""),
                    "verbatim_text": c.get("verbatim_text", "")})
    return out


def _load(working_path: str) -> tuple[o2.World, o2.Ontology]:
    w = o2.World()
    onto = w.get_ontology("file://" + os.path.abspath(working_path)).load()
    return w, onto


def _by_name(world: o2.World, name: str):
    hits = [c for c in world.classes() if c.name == name]
    return hits[0] if hits else None


def _band(debt: float) -> str:
    if debt <= 0:
        return "coherent"
    if debt < 100:
        return "structural-warning"
    return "structural-failure"


def score(working_path: str) -> dict:
    """Base reasoner score for a coverage ontology (no claim)."""
    w, _ = _load(working_path)
    clauses = _clause_records()
    try:
        with _REASONER_LOCK, w:
            _sync_reasoner_guarded(w)
    except o2.OwlReadyInconsistentOntologyError:
        return {"verdict": "inconsistent", "coherent": False, "inconsistent": True,
                "unsat_coverage_classes": [], "contradiction_debt": 1000,
                "band": "structural-failure", "findings": []}
    # Scope to COVERAGE classes (descendants of the kernel Loss). The fed corpus
    # may carry its own unrelated incoherences (e.g. an unsatisfiable domain
    # class); those are not coverage findings and must not leak into the score.
    def _is_coverage(c):
        return any(getattr(a, "name", None) == "Loss" for a in c.ancestors())
    unsat = [c.name for c in w.inconsistent_classes()
             if c.name != "Nothing" and _is_coverage(c)]
    findings = []
    for cls in unsat:
        findings.append({
            "kind": "unsatisfiable_coverage_class",
            "coverage_class": cls,
            "shape": 1,
            "colliding_clauses": _cite(clauses, _UNSAT_CLAUSES.get(cls, [])),
        })
    debt = 50 * len(unsat)  # each un-payable coverage class is a structural defect
    return {"verdict": "coherent" if not unsat else "coherence-warning",
            "coherent": not unsat, "inconsistent": False,
            "unsat_coverage_classes": unsat, "contradiction_debt": debt,
            "band": _band(debt), "findings": findings}


def validate_claim(working_path: str, claim: dict) -> dict:
    """Is the claim well-formed against this ontology's loss vocabulary?"""
    errors = []
    feats = claim.get("exhibits") or []
    if not isinstance(feats, list) or not feats:
        errors.append("claim must list at least one feature under 'exhibits'")
    w, _ = _load(working_path)
    feature_names = _feature_names(w)
    unknown = [f for f in feats if f not in feature_names]
    if unknown:
        errors.append(f"unknown loss feature(s): {sorted(unknown)}")
    return {"well_formed": not errors, "errors": errors,
            "known_features": sorted(feature_names)}


def _feature_names(world: o2.World) -> set[str]:
    """True loss-feature classes = subclasses of LossFeature."""
    lf = _by_name(world, "LossFeature")
    if lf is None:
        return set()
    return {c.name for c in lf.descendants() if c is not lf}


def check_claim(working_path: str, claim: dict) -> dict:
    """Encode a claim/fact pattern as an ABox individual, reason, report."""
    v = validate_claim(working_path, claim)
    if not v["well_formed"]:
        return {"well_formed": False, "errors": v["errors"],
                "known_features": v["known_features"]}

    clauses = _clause_records()
    feats = claim["exhibits"]
    w, onto = _load(working_path)
    Loss = _by_name(w, "Loss")
    with onto:
        loss = Loss(claim.get("loss_id", "claim_loss"))
        loss.exhibits = [_by_name(w, f)() for f in feats]

    try:
        with _REASONER_LOCK, w:
            _sync_reasoner_guarded(w)
    except o2.OwlReadyInconsistentOntologyError:
        ids = []
        for f in feats:
            ids += _FEATURE_CLAUSES.get(f, [])
        return {"well_formed": True, "verdict": "inconsistent", "shape": 2,
                "covered": None, "uncovered": None,
                "plain_english": "This fact pattern is entailed to be BOTH covered "
                                 "and excluded: the policy does not determine the "
                                 "outcome of this claim.",
                "colliding_clauses": _cite(clauses, ids),
                "contradiction_debt": 1000, "band": "structural-failure"}

    covered_cls = _by_name(w, "CoveredLoss")
    uncovered_cls = _by_name(w, "UncoveredLoss")
    inferred = list(loss.INDIRECT_is_a)
    covered = covered_cls in inferred
    uncovered = uncovered_cls in inferred
    if covered and not uncovered:
        pe = "This fact pattern is entailed COVERED."
    elif uncovered and not covered:
        pe = "This fact pattern is entailed EXCLUDED."
    else:
        pe = ("The policy does not entail a determinate outcome for this fact "
              "pattern on the encoded features (neither covered nor excluded).")
    return {"well_formed": True, "verdict": "coherent", "shape": 0,
            "covered": covered, "uncovered": uncovered, "plain_english": pe,
            "colliding_clauses": [], "contradiction_debt": 0, "band": "coherent"}


def analyze_wording(clauses_or_text, ephemeral_seed_path: Optional[str] = None) -> dict:
    """Analyze NEW policy wording (Track-2).

    Accepts either a list of pre-structured clause dicts (deterministic path,
    no LLM) or raw text (LLM coverage extraction). Extraction runs into an
    EPHEMERAL ontology seeded on the coverage kernel; nothing is persisted.
    """
    from . import coverage_extract  # lazy: keeps the LLM dependency optional
    world, onto, handles, extract_report = coverage_extract.extract_ephemeral(
        clauses_or_text, seed_path=ephemeral_seed_path)
    try:
        with _REASONER_LOCK, world:
            _sync_reasoner_guarded(world)
        unsat = [c.name for c in world.inconsistent_classes() if c.name != "Nothing"]
        verdict = "coherent" if not unsat else "coherence-warning"
        inconsistent = False
    except o2.OwlReadyInconsistentOntologyError:
        unsat, verdict, inconsistent = [], "inconsistent", True
    debt = 1000 if inconsistent else 50 * len(unsat)
    return {"verdict": verdict, "inconsistent": inconsistent,
            "unsat_coverage_classes": unsat, "contradiction_debt": debt,
            "band": _band(debt), "extraction": extract_report}
