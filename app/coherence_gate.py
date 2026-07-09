"""In-loop BFO coherence gate.

This sits between "LLM proposes axioms" and "commit to the working ontology".
It holds the global BFO constraint that the proposer, emitting locally-plausible
single axioms, cannot. Two tiers, cheap first:

  1. Lint tier (no reasoner): structural checks over BFO anchors
     (app/gate_structural.py, SPEC-bfo-agent-speed.md change 3). For each
     newly proposed class, fold its proposed BFO parents together with its
     already-committed BFO parents and test the resulting set for a
     disjoint-parent straddle via bfo_catalog.straddles; same fold for each
     proposed/typed individual's types; plus a domain/range clash test
     against bfo_catalog.relation_signatures(). This catches the classic
     "Force is a Quality and Force is a Disposition" family instantly with a
     localized reason and no JVM.
  2. Reasoner tier (HermiT): run the coherence-correct dry-run on the candidate
     ontology and reject if any class becomes unsatisfiable. Catches clashes
     mediated by restrictions or class expressions that the lint cannot see.
     When GATE_REASONER_STRUCTURAL_SKIP is on and the lint resolved every
     touched reference with nothing structure cannot decide, this tier is
     skipped (the commit/checkpoint full pass remains the certificate).

The gate returns a GateResult (ACCEPT, REPAIR, or REJECT). Policy handling
(reject-resample, repair, reground) is dispatched by apply_policy; the gate
itself only decides coherence.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Optional

from . import bfo_catalog
from . import config
from . import construction_linter
from . import gate_structural
from .gate_structural import (  # noqa: F401  (re-exported; scaffolding/repair use them)
    _collect_class_parents,
    _is_subclass_predicate,
    _local,
    _proposed_class_types,
    _ref_anchors,
    proposal_needs_reasoner,
)

log = logging.getLogger(__name__)


class GateOutcome(str, enum.Enum):
    ACCEPT = "accept"
    REPAIR = "repair"
    REJECT = "reject"
    # Faithful-extraction mode (fidelity-mode-spec.md FM-4): the claim clashes,
    # but it is what the text asserts -- commit it unmodified and record the
    # clash as evidence instead of rejecting or repairing it.
    FLAG = "flag"


class GateTier(str, enum.Enum):
    NONE = "none"
    CONSTRUCTION = "construction"
    LINT = "lint"
    REASONER = "reasoner"


class GatePolicy(str, enum.Enum):
    """How the loop reacts when the gate fires. See SPEC Task 3."""
    REJECT_RESAMPLE = "reject_resample"
    REPAIR = "repair"
    REGROUND = "reground"
    # fidelity-mode-spec.md FM-4: lint/reasoner clashes become FLAG (commit
    # as-asserted, evidence recorded by the caller); no repair, no content
    # resample. Construction violations still resample (FM-3): they are the
    # proposer's rendering errors, never the text's claims.
    ANNOTATE = "annotate"


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
    # Prohibited-construction violations, when known (construction tier). Each
    # is {rule, offending_term, suggested_rewrite, detail}.
    violations: list[dict] = field(default_factory=list)
    # FM-10: True when the reasoner tier was skipped because the coherent
    # view itself was incoherent (faithful mode only). Logged loudly by the
    # caller; construction+lint verdicts still stand.
    degraded: bool = False
    # SPEC-bfo-agent-speed.md change 3: True when the reasoner tier was
    # skipped because the lint fully resolved every touched reference and the
    # proposal introduces nothing structure cannot decide. Lets telemetry
    # count JVM avoidance. Only serialized when True so flag-off event dicts
    # stay byte-identical to before.
    reasoner_skipped: bool = False

    @property
    def accepted(self) -> bool:
        return self.outcome == GateOutcome.ACCEPT

    def to_dict(self) -> dict:
        d = {
            "outcome": self.outcome.value,
            "tier": self.tier.value,
            "reason": self.reason,
            "justification": self.justification,
            "clash_pair": list(self.clash_pair) if self.clash_pair else None,
            "unsat_classes": self.unsat_classes,
            "subject": self.subject,
            "violations": self.violations,
            "degraded": self.degraded,
        }
        if self.reasoner_skipped:
            d["reasoner_skipped"] = True
        return d


def construction_check(
    proposal, strict_closed_vocab: bool = False
) -> Optional[GateResult]:
    """Construction tier: run the PC-1..PC-6 prohibited-construction linter.

    This is the cheapest tier and runs first. It catches the privation
    primitives, relation-baked names, untyped entities, invented predicates,
    and continuant/occurrent conflations that drive class proliferation
    (bfo-agent-spec.md §6). Returns a REJECT GateResult carrying every
    violation, or None when the draft is clean.
    """
    report = construction_linter.lint(
        proposal, strict_closed_vocab=strict_closed_vocab
    )
    if report.ok:
        return None
    rules = ", ".join(sorted({v["rule"] for v in report.to_dicts()}))
    return GateResult(
        outcome=GateOutcome.REJECT,
        tier=GateTier.CONSTRUCTION,
        reason=(
            f"Prohibited construction(s) [{rules}]: the draft would mint "
            f"malformed terms instead of anchoring to BFO. See violations."
        ),
        justification=report.summary(),
        violations=report.to_dicts(),
    )


def lint_check(proposal, manager) -> Optional[GateResult]:
    """Lint tier: structural checks, no reasoner (speed-spec change 3).

    Detects disjoint-parent straddles on proposed classes, disjoint-type
    straddles on proposed individuals (including rdf:type edges onto existing
    individuals), and BFO relation-signature (domain/range) clashes. Returns
    a REJECT GateResult on the first clash found, else None.
    """
    result, _resolved_all = _lint_check_with_resolution(proposal, manager)
    return result


def _lint_check_with_resolution(
    proposal, manager
) -> tuple[Optional[GateResult], bool]:
    """lint_check plus whether every touched reference resolved to anchors.

    The resolution bit feeds proposal_needs_reasoner: an unresolvable anchor
    means the structure could not see the whole proposal, so the reasoner
    tier must not be skipped.
    """
    clash, resolved_all = gate_structural.structural_lint(proposal, manager)
    if clash is None:
        return None, resolved_all
    return (
        GateResult(
            outcome=GateOutcome.REJECT,
            tier=GateTier.LINT,
            reason=clash.reason,
            clash_pair=clash.clash_pair,
            subject=clash.subject,
        ),
        resolved_all,
    )


def reasoner_check(
    proposal, manager, exclude_axioms: Optional[list[dict]] = None
) -> Optional[GateResult]:
    """Reasoner tier: reject if the candidate ontology has unsatisfiable classes.

    ``exclude_axioms`` (faithful mode, FM-9) is the ledgered clash-axiom set;
    the dry-run evaluates the candidate against the coherent view (working
    minus those axioms) so each new claim is judged on its own merits instead
    of drowning in previously flagged incoherence.
    """
    coherent, unsat, detail = manager.check_coherence_dry_run(
        proposal, exclude_axioms=exclude_axioms
    )
    if coherent:
        return None

    # Outright inconsistent (reasoner error): unsat is empty but the ontology
    # is unusable -- always reject.
    if not unsat:
        return GateResult(
            outcome=GateOutcome.REJECT,
            tier=GateTier.REASONER,
            reason="Reasoner error: the proposal makes the ontology "
                   "inconsistent under BFO.",
            justification=detail,
            unsat_classes=[],
        )

    # Baseline-aware rejection (incoherence-cascade fix): reject only for
    # classes THIS proposal asserts an axiom about. A class that was already
    # unsatisfiable in the committed ontology (a poisoned earlier commit) is
    # surfaced by every dry-run but was not caused by this claim -- letting it
    # reject here turns one bad commit into a wall of false "inconsistent"
    # verdicts for every subsequent, unrelated claim.
    try:
        touched = manager.proposal_touched_iris(proposal)
    except Exception:  # noqa: BLE001 -- never let the guard brick the tier
        touched = set()
    new_unsat = [u for u in unsat
                 if u.rsplit("#", 1)[-1].rsplit("/", 1)[-1] in touched]
    if not new_unsat:
        # Pure pre-existing poison: accept on this claim's own merits.
        log.warning(
            "reasoner tier: ignoring %d pre-existing unsatisfiable class(es) "
            "not touched by this proposal: %s",
            len(unsat), unsat,
        )
        return None
    return GateResult(
        outcome=GateOutcome.REJECT,
        tier=GateTier.REASONER,
        reason="Reasoner found unsatisfiable class(es); the proposal makes "
               "one or more classes incoherent under BFO.",
        justification=detail,
        unsat_classes=new_unsat,
    )


def gate(
    proposal,
    manager,
    run_reasoner: bool = True,
    run_construction: bool = True,
    strict_closed_vocab: bool = False,
    exclude_axioms: Optional[list[dict]] = None,
) -> GateResult:
    """Run the gate. Construction first (cheapest), then lint, then reasoner.

    Returns ACCEPT only if every enabled tier passes.
    """
    if run_construction:
        construction = construction_check(
            proposal, strict_closed_vocab=strict_closed_vocab
        )
        if construction is not None:
            return construction

    lint, lint_resolved_all = _lint_check_with_resolution(proposal, manager)
    if lint is not None:
        return lint

    if run_reasoner:
        # SPEC-bfo-agent-speed.md change 3: when every touched reference
        # resolved to BFO anchors and the proposal introduces nothing
        # structure cannot decide, skip the JVM. Default off; the
        # commit-time / checkpoint full pass remains the backstop.
        if config.GATE_REASONER_STRUCTURAL_SKIP:
            needed, why = proposal_needs_reasoner(
                proposal, manager, lint_resolved_all
            )
            if not needed:
                return GateResult(
                    outcome=GateOutcome.ACCEPT,
                    tier=GateTier.LINT,
                    reason=f"reasoner skipped (structural): {why}",
                    reasoner_skipped=True,
                )
        reasoner = reasoner_check(proposal, manager,
                                  exclude_axioms=exclude_axioms)
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


def clash_exclusion_triples(proposal, result: GateResult) -> list[dict]:
    """The subClassOf axioms this proposal adds to the flagged subject(s).

    This is the minimal set whose removal restores the prior coherent typing
    (fidelity-mode-spec.md FM-7/FM-9): the coherent-view dry-run retracts
    exactly these on a scratch copy. Entity-level bfo_type edges are kept --
    removing them would orphan the class rather than resolve the straddle.
    """
    subjects: set[str] = set()
    if result.subject:
        subjects.add(result.subject)
    for iri in result.unsat_classes:
        subjects.add(_local(iri))
    triples: list[dict] = []
    for rel in proposal.relations:
        if _is_subclass_predicate(rel.p) and _local(rel.s) in subjects:
            triples.append({"s": rel.s, "p": rel.p, "o": rel.o})
    for ent in proposal.entities:
        if getattr(ent, "kind", None) != "class":
            continue
        if not getattr(ent, "parent_class", None):
            continue
        if _local(ent.iri_suggestion or ent.label) in subjects:
            triples.append({
                "s": ent.iri_suggestion or ent.label,
                "p": "rdfs:subClassOf",
                "o": ent.parent_class,
            })
    return triples


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


def _base_view_coherent(manager, proposal, exclude_axioms) -> bool:
    """FM-10 probe: is the coherent view (working minus ledgered clash axioms)
    itself coherent, before this proposal? Used only after a reasoner-tier
    failure in faithful mode, to tell "this claim clashes" apart from "the
    view reconstruction is incomplete and everything would flag"."""
    from .schema import Proposal

    empty = Proposal(session_id=proposal.session_id, utterance="")
    try:
        ok, _unsat, _detail = manager.check_coherence_dry_run(
            empty, exclude_axioms=exclude_axioms
        )
    except Exception:
        return False
    return ok


def run_with_policy(
    proposal,
    manager,
    policy: GatePolicy,
    resample_fn=None,
    run_reasoner: bool = True,
    max_attempts: int = 2,
    run_construction: bool = True,
    strict_closed_vocab: bool = False,
    exclude_axioms: Optional[list[dict]] = None,
) -> GateRun:
    """Run the gate and apply the configured policy on a clash.

    resample_fn(proposal, gate_result, neighborhood: bool) -> new proposal is
    supplied by the caller (the orchestrator) so this module stays decoupled
    from the proposer. It may return None to signal it could not resample.
    """
    events: list[dict] = []
    current = proposal

    for attempt in range(max_attempts + 1):
        result = gate(
            current,
            manager,
            run_reasoner=run_reasoner,
            run_construction=run_construction,
            strict_closed_vocab=strict_closed_vocab,
            exclude_axioms=exclude_axioms,
        )
        events.append({
            "attempt": attempt,
            "policy": policy.value,
            **result.to_dict(),
        })

        if result.accepted:
            return GateRun(GateOutcome.ACCEPT, current, result, events, attempt)

        # Construction-tier violations cannot be programmatically repaired by
        # the straddle rule table; they require the proposer to regenerate.
        # Fall through to the resample path regardless of the configured policy.
        if result.tier == GateTier.CONSTRUCTION:
            if resample_fn is not None and attempt < max_attempts:
                events[-1]["policy_action"] = "resample_construction"
                resampled = resample_fn(current, result, False)
                if resampled is not None:
                    current = resampled
                    continue
            return GateRun(GateOutcome.REJECT, current, result, events, attempt)

        # The gate fired. React per policy.
        if policy == GatePolicy.ANNOTATE:
            # fidelity-mode-spec.md FM-4: the claim stands as the text
            # asserted it. A lint/reasoner clash becomes FLAG -- the caller
            # commits the proposal unmodified and records the evidence.
            # Never repair, never content-resample, no retry loop.
            import dataclasses

            if result.tier == GateTier.REASONER and not _base_view_coherent(
                manager, current, exclude_axioms
            ):
                # FM-10: the coherent view is itself incoherent, so this
                # reasoner verdict says nothing about the current claim.
                # Degrade (construction+lint already passed) rather than
                # flag every subsequent claim with someone else's clash.
                degraded = GateResult(
                    outcome=GateOutcome.ACCEPT,
                    tier=GateTier.REASONER,
                    reason=(
                        "reasoner tier degraded: the coherent view (working "
                        "minus ledgered clash axioms) is itself incoherent; "
                        "claim accepted on construction+lint tiers only"
                    ),
                    degraded=True,
                )
                events[-1]["policy_action"] = "degrade_reasoner"
                events.append({
                    "attempt": attempt,
                    "policy": policy.value,
                    **degraded.to_dict(),
                })
                return GateRun(GateOutcome.ACCEPT, current, degraded,
                               events, attempt)

            flagged = dataclasses.replace(result, outcome=GateOutcome.FLAG)
            events[-1]["outcome"] = GateOutcome.FLAG.value
            events[-1]["policy_action"] = "flag"
            return GateRun(GateOutcome.FLAG, current, flagged, events, attempt)

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
