"""In-loop BFO coherence gate.

This sits between "LLM proposes axioms" and "commit to the working ontology".
It holds the global BFO constraint that the proposer, emitting locally-plausible
single axioms, cannot. Two tiers, cheap first:

  1. Lint tier (no reasoner): for each newly proposed class, fold its proposed
     BFO parents together with its already-committed BFO parents and test the
     resulting set for a disjoint-parent straddle via bfo_catalog.straddles.
     This catches the classic "Force is a Quality and Force is a Disposition"
     family instantly with a localized reason.
  2. Reasoner tier (HermiT): run the coherence-correct dry-run on the candidate
     ontology and reject if any class becomes unsatisfiable. Catches clashes
     mediated by restrictions or relation signatures that the lint cannot see.

The gate returns a GateResult (ACCEPT, REPAIR, or REJECT). Policy handling
(reject-resample, repair, reground) is dispatched by apply_policy; the gate
itself only decides coherence.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional

from . import bfo_catalog


class GateOutcome(str, enum.Enum):
    ACCEPT = "accept"
    REPAIR = "repair"
    REJECT = "reject"


class GateTier(str, enum.Enum):
    NONE = "none"
    LINT = "lint"
    REASONER = "reasoner"


class GatePolicy(str, enum.Enum):
    """How the loop reacts when the gate fires. See SPEC Task 3."""
    REJECT_RESAMPLE = "reject_resample"
    REPAIR = "repair"
    REGROUND = "reground"


@dataclass
class GateResult:
    outcome: GateOutcome
    tier: GateTier = GateTier.NONE
    reason: str = ""
    justification: str = ""
    # Fragments of the clashing BFO category pair, when known (lint tier).
    clash_pair: Optional[tuple[str, str]] = None
    # IRIs of unsatisfiable classes, when known (reasoner tier).
    unsat_classes: list[str] = field(default_factory=list)
    # Subject (class) whose parents straddle, when known.
    subject: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.outcome == GateOutcome.ACCEPT

    def to_dict(self) -> dict:
        return {
            "outcome": self.outcome.value,
            "tier": self.tier.value,
            "reason": self.reason,
            "justification": self.justification,
            "clash_pair": list(self.clash_pair) if self.clash_pair else None,
            "unsat_classes": self.unsat_classes,
            "subject": self.subject,
        }


def _is_subclass_predicate(p: str) -> bool:
    return "subClassOf" in p


def _local(ref: str) -> str:
    s = ref.split("#")[-1]
    s = s.split("/")[-1]
    return s.split(":")[-1]


def _collect_class_parents(proposal) -> dict[str, set[str]]:
    """Map each subject class reference to the set of parent references it gains.

    Sources merged: class entities (their bfo_type and parent_class) and any
    rdfs:subClassOf relations in the proposal.
    """
    parents: dict[str, set[str]] = {}

    for ent in proposal.entities:
        if getattr(ent, "kind", None) != "class":
            continue
        subj = ent.iri_suggestion or ent.label
        bucket = parents.setdefault(subj, set())
        if getattr(ent, "bfo_type", None):
            bucket.add(ent.bfo_type)
        if getattr(ent, "parent_class", None):
            bucket.add(ent.parent_class)

    for rel in proposal.relations:
        if _is_subclass_predicate(rel.p):
            parents.setdefault(rel.s, set()).add(rel.o)

    return parents


def _proposed_class_types(proposal) -> dict[str, str]:
    """Map local class name -> its proposed BFO type fragment."""
    out: dict[str, str] = {}
    for ent in proposal.entities:
        if getattr(ent, "kind", None) == "class" and getattr(ent, "bfo_type", None):
            out[_local(ent.iri_suggestion or ent.label)] = ent.bfo_type
    return out


def _ref_anchors(ref: str, manager, proposed_types: dict[str, str]) -> set[str]:
    """Resolve a parent reference to its BFO category anchor(s).

    A BFO fragment resolves to itself; a committed working class resolves to
    its BFO ancestors; an as-yet-uncommitted class proposed in the same turn
    resolves to its proposed bfo_type.
    """
    frag = bfo_catalog.normalize_fragment(ref)
    if frag in bfo_catalog.BFO_PARENT:
        return {frag}

    committed = manager.committed_bfo_anchors(ref)
    if committed:
        return committed

    local = _local(ref)
    if local in proposed_types:
        t = bfo_catalog.normalize_fragment(proposed_types[local])
        if t in bfo_catalog.BFO_PARENT:
            return {t}
    return set()


def lint_check(proposal, manager) -> Optional[GateResult]:
    """Lint tier: detect disjoint-parent straddles without a reasoner.

    Returns a REJECT GateResult on the first straddle found, else None.
    """
    proposed_types = _proposed_class_types(proposal)
    class_parents = _collect_class_parents(proposal)

    for subject, parent_refs in class_parents.items():
        anchors: set[str] = set()
        # Already-committed BFO parents of this class (cross-turn straddles).
        anchors |= manager.committed_bfo_anchors(subject)
        # Newly proposed parents this turn.
        for ref in parent_refs:
            anchors |= _ref_anchors(ref, manager, proposed_types)

        hit, pair = bfo_catalog.straddles(anchors)
        if hit and pair is not None:
            return GateResult(
                outcome=GateOutcome.REJECT,
                tier=GateTier.LINT,
                reason=bfo_catalog.describe_clash(*pair),
                clash_pair=pair,
                subject=_local(subject),
            )
    return None


def reasoner_check(proposal, manager) -> Optional[GateResult]:
    """Reasoner tier: reject if the candidate ontology has unsatisfiable classes."""
    coherent, unsat, detail = manager.check_coherence_dry_run(proposal)
    if not coherent:
        return GateResult(
            outcome=GateOutcome.REJECT,
            tier=GateTier.REASONER,
            reason="Reasoner found unsatisfiable class(es); the proposal makes "
                   "one or more classes incoherent under BFO.",
            justification=detail,
            unsat_classes=unsat,
        )
    return None


def gate(proposal, manager, run_reasoner: bool = True) -> GateResult:
    """Run the gate. Lint first (cheap), then optionally the reasoner.

    Returns ACCEPT only if both tiers pass (or the reasoner tier is skipped).
    """
    lint = lint_check(proposal, manager)
    if lint is not None:
        return lint

    if run_reasoner:
        reasoner = reasoner_check(proposal, manager)
        if reasoner is not None:
            return reasoner

    return GateResult(outcome=GateOutcome.ACCEPT, tier=GateTier.NONE)


# ---------------------------------------------------------------------------
# Policy handling (SPEC Task 3): reject_resample / repair / reground.
# ---------------------------------------------------------------------------
@dataclass
class GateRun:
    """The result of running the gate under a policy, possibly with retries."""
    outcome: GateOutcome
    proposal: object                      # the final (possibly rewritten) proposal
    result: GateResult                    # the last gate verdict
    events: list[dict] = field(default_factory=list)
    attempts: int = 0


def _strip_clashing_parents(proposal, clash_subject: Optional[str]):
    """Repair helper: drop the subClassOf edges on the clashing subject so the
    class falls back to its prior coherent typing. Returns a new proposal-like
    object (mutates a copy)."""
    from .schema import Proposal

    if clash_subject is None:
        return proposal
    keep_rel = [
        r for r in proposal.relations
        if not (_is_subclass_predicate(r.p) and _local(r.s) == clash_subject)
    ]
    # For entities that are the clash subject, neutralize the added parent_class
    # by clearing it (the bfo_type alone keeps a single coherent anchor).
    new_entities = []
    for e in proposal.entities:
        if _local(e.iri_suggestion or e.label) == clash_subject and e.parent_class:
            e = e.model_copy(update={"parent_class": None})
        new_entities.append(e)
    return proposal.model_copy(update={"relations": keep_rel, "entities": new_entities})


def repair_proposal(proposal, result: GateResult):
    """Programmatic repair toward the nearest coherent form (fixed rule table).

    Current rules:
      - quality/realizable straddle: keep the quality typing, drop the
        realizable parent edge, and mint a separate realizable class carrying
        the realizable claim so it is not lost. The bearer/realization slots
        are left as open questions for Task 4 scaffolding to fill.
      - any other straddle: drop the offending parent edge (fall back to the
        prior coherent typing).
    Returns (repaired_proposal, action_description) or (None, reason) if no
    deterministic repair applies.
    """
    from .schema import Entity

    subject = result.subject
    repaired = _strip_clashing_parents(proposal, subject)

    action = f"dropped clashing parent edge on {subject}" if subject else "no-op"

    # quality (BFO_0000019) + realizable-side straddle: mint a separate class.
    if result.clash_pair and set(result.clash_pair) & {bfo_catalog.REALIZABLE,
                                                        bfo_catalog.DISPOSITION,
                                                        bfo_catalog.ROLE}:
        realizable_frag = next(
            (f for f in result.clash_pair
             if bfo_catalog.is_descendant_of(f, bfo_catalog.REALIZABLE)),
            None,
        )
        if realizable_frag and subject:
            new_name = f"{subject}Realizable"
            repaired.entities.append(
                Entity(
                    label=new_name,
                    iri_suggestion=f"working:{new_name}",
                    bfo_type=realizable_frag,
                    bfo_label=bfo_catalog.BFO_LABEL.get(realizable_frag, "realizable"),
                    kind="class",
                    rationale=(
                        f"Repair: the realizable claim about {subject} was "
                        f"re-homed here so {subject} can remain a quality."
                    ),
                    is_new=True,
                )
            )
            repaired.open_questions.append(
                f"Which independent continuant bears {new_name}, and in which "
                f"process is it realized?"
            )
            action = f"re-homed realizable claim about {subject} to {new_name}"

    return repaired, action


# ---------------------------------------------------------------------------
# Relation-aware scaffolding at emission (SPEC Task 4).
# ---------------------------------------------------------------------------
# Property fragments used for scaffolding. BFO 2020 uses its own relation IRIs
# (not the RO_ aliases): inheres in is BFO_0000197, and the realizable-to-process
# relation is BFO_0000054 (domain realizable entity, range process).
_INHERES_IN = "BFO_0000197"
_REALIZED_IN = "BFO_0000054"


def scaffolding_directives(proposal, manager) -> list[dict]:
    """Compute the constraints a newly placed dependent-continuant class needs.

    Bare SubClassOf under a BFO dependent category produces straddle-prone
    is-a trees. When a class lands under a quality, role, or
    disposition/function, propose the constraint that category requires so the
    ontology constrains rather than merely classifies.

    Returns a list of directives:
      {class_ref, prop, filler, kind, question}
    """
    proposed_types = _proposed_class_types(proposal)
    directives: list[dict] = []

    for ent in proposal.entities:
        if getattr(ent, "kind", None) != "class":
            continue
        class_ref = ent.iri_suggestion or ent.label
        anchors = _ref_anchors(class_ref, manager, proposed_types)
        if not anchors:
            # Uncommitted: fall back to the entity's own declared type.
            t = bfo_catalog.normalize_fragment(ent.bfo_type or "")
            if t in bfo_catalog.BFO_PARENT:
                anchors = {t}

        for anchor in anchors:
            if bfo_catalog.is_descendant_of(anchor, bfo_catalog.QUALITY):
                directives.append({
                    "class_ref": class_ref, "prop": _INHERES_IN,
                    "filler": bfo_catalog.INDEPENDENT_CONTINUANT, "kind": "quality",
                    "question": f"Which independent continuant bears {ent.label}?",
                })
                break
            if bfo_catalog.is_descendant_of(anchor, bfo_catalog.ROLE):
                directives.append({
                    "class_ref": class_ref, "prop": _REALIZED_IN,
                    "filler": bfo_catalog.PROCESS, "kind": "role",
                    "question": f"In which process is the role {ent.label} realized, "
                                f"and what bears it?",
                })
                break
            if bfo_catalog.is_descendant_of(anchor, bfo_catalog.DISPOSITION):
                is_function = bfo_catalog.is_descendant_of(anchor, bfo_catalog.FUNCTION)
                directives.append({
                    "class_ref": class_ref, "prop": _REALIZED_IN,
                    "filler": bfo_catalog.PROCESS,
                    "kind": "function" if is_function else "disposition",
                    "question": (
                        f"In which process is {ent.label} realized? Was its bearer "
                        f"engineered or selected for it (function) or does it merely "
                        f"have it (disposition)?"
                    ),
                })
                break
    return directives


def apply_scaffolding(directives: list[dict], manager) -> list[dict]:
    """Apply scaffolding directives to the live world, skipping any that would
    duplicate an existing restriction. Returns the directives actually applied.
    Caller is responsible for saving and for a follow-up coherence check.
    """
    applied = []
    for d in directives:
        if manager.has_restriction_on(d["class_ref"], d["prop"]):
            continue
        if manager.add_existential_restriction(d["class_ref"], d["prop"], d["filler"]):
            applied.append(d)
    return applied


def run_with_policy(
    proposal,
    manager,
    policy: GatePolicy,
    resample_fn=None,
    run_reasoner: bool = True,
    max_attempts: int = 2,
) -> GateRun:
    """Run the gate and apply the configured policy on a clash.

    resample_fn(proposal, gate_result, neighborhood: bool) -> new proposal is
    supplied by the caller (the orchestrator) so this module stays decoupled
    from the proposer. It may return None to signal it could not resample.
    """
    events: list[dict] = []
    current = proposal

    for attempt in range(max_attempts + 1):
        result = gate(current, manager, run_reasoner=run_reasoner)
        events.append({
            "attempt": attempt,
            "policy": policy.value,
            **result.to_dict(),
        })

        if result.accepted:
            return GateRun(GateOutcome.ACCEPT, current, result, events, attempt)

        # The gate fired. React per policy.
        if policy == GatePolicy.REPAIR:
            repaired, action = repair_proposal(current, result)
            events[-1]["policy_action"] = action
            if repaired is None:
                return GateRun(GateOutcome.REJECT, current, result, events, attempt)
            current = repaired
            continue

        # reject_resample and reground both lean on the proposer.
        if resample_fn is not None and attempt < max_attempts:
            neighborhood = policy == GatePolicy.REGROUND
            events[-1]["policy_action"] = (
                "reground" if neighborhood else "resample"
            )
            resampled = resample_fn(current, result, neighborhood)
            if resampled is not None:
                current = resampled
                continue

        return GateRun(GateOutcome.REJECT, current, result, events, attempt)

    return GateRun(GateOutcome.REJECT, current, result, events, max_attempts)
