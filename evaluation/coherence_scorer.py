"""Coherence-aware win condition for generated ontologies (SPEC Task 5).

Two ideas, composed:

  1. Coherence is a HARD precondition. An ontology with any unsatisfiable class
     scores zero, because every entailment from owl:Nothing is vacuous: a
     consistent-but-incoherent ontology can rack up "entailments" that mean
     nothing. We detect this with owlready2 world.inconsistent_classes() after
     sync_reasoner, NOT by grepping reasoner output.

  2. Among coherent ontologies, reward DISCRIMINATING ENTAILMENTS: the
     subsumptions and type memberships the reasoner derives that were not
     asserted. These are exactly the inferences the BFO scaffolding buys; a
     flat is-a tree with no BFO grounding derives almost none of them. We
     measure this as the before/after-reasoning diff on a single world, which
     is operationally the same as diffing against a flat baseline but robust.

A coherence-preservation reward credits runs that reached coherence cleanly
versus by recovery, read from the gate event log (the experimental record).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import io
from contextlib import redirect_stderr, redirect_stdout


def _load_world(working_path: Path, bfo_path: Path):
    from owlready2 import World, onto_path

    bfo_path = Path(bfo_path)
    working_path = Path(working_path)
    onto_path.append(str(bfo_path.parent))
    world = World()
    world.get_ontology(str(bfo_path)).load()
    if working_path.exists():
        world.get_ontology(working_path.as_uri()).load()
    return world


def _direct_parents_snapshot(world) -> dict[str, set[str]]:
    snap: dict[str, set[str]] = {}
    for c in world.classes():
        snap[c.iri] = {p.iri for p in c.is_a if hasattr(p, "iri")}
    return snap


def _direct_types_snapshot(world) -> dict[str, set[str]]:
    snap: dict[str, set[str]] = {}
    for i in world.individuals():
        snap[i.iri] = {t.iri for t in i.is_a if hasattr(t, "iri")}
    return snap


def reason_and_diff(working_path: Path, bfo_path: Path) -> dict:
    """Load fresh, snapshot asserted axioms, reason, and report what changed.

    Returns:
      reasoner_ok: bool (False means the ontology is outright inconsistent)
      unsatisfiable_classes: list[str] IRIs
      inferred_subsumptions: set[(child_iri, parent_iri)] new after reasoning
      inferred_types: set[(individual_iri, class_iri)] new after reasoning
    """
    from owlready2 import sync_reasoner, Nothing

    world = _load_world(working_path, bfo_path)
    before_parents = _direct_parents_snapshot(world)
    before_types = _direct_types_snapshot(world)

    buf = io.StringIO()
    reasoner_ok = True
    err = ""
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            with world:
                sync_reasoner(world, infer_property_values=False)
    except Exception as e:
        reasoner_ok = False
        err = f"{e}\n{buf.getvalue()}".strip()

    inferred_subs: set[tuple[str, str]] = set()
    inferred_types: set[tuple[str, str]] = set()
    unsat: list[str] = []

    if reasoner_ok:
        nothing_iri = Nothing.iri
        for c in world.classes():
            prior = before_parents.get(c.iri, set())
            for p in c.is_a:
                if not hasattr(p, "iri"):
                    continue
                if p.iri == nothing_iri:
                    continue  # the unsat signal, not a discriminating entailment
                if p.iri not in prior and p.iri != c.iri:
                    inferred_subs.add((c.iri, p.iri))
        for i in world.individuals():
            prior = before_types.get(i.iri, set())
            for t in i.is_a:
                if hasattr(t, "iri") and t.iri not in prior:
                    inferred_types.add((i.iri, t.iri))
        unsat = [c.iri for c in world.inconsistent_classes() if c is not Nothing]

    return {
        "reasoner_ok": reasoner_ok,
        "error": err,
        "unsatisfiable_classes": unsat,
        "inferred_subsumptions": inferred_subs,
        "inferred_types": inferred_types,
    }


def coherence_field(working_path: Path, bfo_path: Path) -> dict:
    """{'coherent': bool, 'unsatisfiable_classes': [...], 'consistent': bool}."""
    diff = reason_and_diff(working_path, bfo_path)
    consistent = diff["reasoner_ok"]
    coherent = consistent and not diff["unsatisfiable_classes"]
    return {
        "coherent": coherent,
        "consistent": consistent,
        "unsatisfiable_classes": diff["unsatisfiable_classes"],
        "detail": diff["error"],
    }


def coherence_preservation(gate_log: list[dict]) -> dict:
    """Read the gate event log into proposed-vs-recovered clash counts.

    Groups events into runs (by proposal_id, else claim_id). A run "proposed a
    clash" if any event fired (tier lint/reasoner, outcome not accept). A run
    "recovered" if it nonetheless ended accepted.
    """
    runs: dict[str, list[dict]] = {}
    for ev in gate_log:
        key = ev.get("proposal_id") or ev.get("claim_id") or str(id(ev))
        runs.setdefault(key, []).append(ev)

    proposed = recovered = reasoner_tier = lint_tier = 0
    for events in runs.values():
        fired = False
        fired_reasoner = False
        for ev in events:
            tier = ev.get("tier")
            outcome = ev.get("outcome")
            if tier in ("lint", "reasoner") and outcome != "accept":
                fired = True
                if tier == "reasoner":
                    fired_reasoner = True
        if fired:
            proposed += 1
            if fired_reasoner:
                reasoner_tier += 1
            else:
                lint_tier += 1
            final_outcome = events[-1].get("outcome")
            if final_outcome == "accept":
                recovered += 1

    return {
        "proposed_clashes": proposed,
        "recovered_clashes": recovered,
        "reasoner_tier_clashes": reasoner_tier,
        "lint_tier_clashes": lint_tier,
        "never_clashed": proposed == 0,
    }


def score_ontology(
    working_path: Path,
    bfo_path: Path,
    gate_log: Optional[list[dict]] = None,
) -> dict:
    """Top-level win condition.

    Coherence is a hard gate: any unsatisfiable class (or outright
    inconsistency) yields score 0. Otherwise the score is the count of
    discriminating entailments plus a coherence-preservation bonus.
    """
    diff = reason_and_diff(working_path, bfo_path)
    coherence = {
        "coherent": diff["reasoner_ok"] and not diff["unsatisfiable_classes"],
        "consistent": diff["reasoner_ok"],
        "unsatisfiable_classes": diff["unsatisfiable_classes"],
        "detail": diff["error"],
    }

    preservation = coherence_preservation(gate_log or [])

    if not coherence["coherent"]:
        return {
            "score": 0.0,
            "coherence": coherence,
            "discriminating_count": 0,
            "discriminating": {"subsumptions": [], "types": []},
            "coherence_preservation": preservation,
            "note": "incoherent ontology: entailments are vacuous, score is zero",
        }

    discriminating_count = (
        len(diff["inferred_subsumptions"]) + len(diff["inferred_types"])
    )
    # A clean run (never proposed a clash at the reasoner tier) earns a small
    # preservation bonus, separating "never confabulated" from "recovered".
    preservation_bonus = 0.0
    if gate_log:
        if preservation["reasoner_tier_clashes"] == 0:
            preservation_bonus = 1.0

    return {
        "score": float(discriminating_count) + preservation_bonus,
        "coherence": coherence,
        "discriminating_count": discriminating_count,
        "discriminating": {
            "subsumptions": sorted(diff["inferred_subsumptions"]),
            "types": sorted(diff["inferred_types"]),
        },
        "coherence_preservation": preservation,
        "preservation_bonus": preservation_bonus,
    }
