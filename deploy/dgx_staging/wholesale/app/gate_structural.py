"""Structural (no-reasoner) gate checks (SPEC-bfo-agent-speed.md change 3).

The lint tier of the coherence gate (app/coherence_gate.py) delegates here.
Everything in this module is pure catalog arithmetic: BFO anchor resolution,
disjoint-parent straddle detection for classes AND individuals, BFO
relation-signature (domain/range) clash detection, and the decision of
whether a proposal introduces anything structure cannot decide (in which
case the HermiT tier must still run).

No owlready2 world mutation and no JVM: the only ontology access is through
the manager's committed_bfo_anchors / committed_individual_anchors lookups.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import bfo_catalog
from . import owl_checks


def _is_subclass_predicate(p: str) -> bool:
    return "subClassOf" in p


def _local(ref: str) -> str:
    s = ref.split("#")[-1]
    s = s.split("/")[-1]
    return s.split(":")[-1]


def _is_type_predicate(p: str) -> bool:
    """True for rdf:type in any spelling (prefixed, full IRI, or 'a')."""
    p = (p or "").strip()
    return p == "a" or _local(p) == "type"


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


def _proposed_individual_types(proposal) -> dict[str, str]:
    """Map local individual name -> its proposed type reference."""
    out: dict[str, str] = {}
    for ent in proposal.entities:
        if getattr(ent, "kind", None) != "class" and getattr(ent, "bfo_type", None):
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


def _endpoint_anchors(
    ref: str,
    manager,
    proposed_types: dict[str, str],
    proposed_ind_types: dict[str, str],
) -> set[str]:
    """Anchor set for a relation endpoint, class or individual.

    Tries the class path (_ref_anchors: BFO fragment, committed class,
    same-turn proposed class), then a same-turn proposed individual's type,
    then a committed individual's asserted types. Empty when unresolvable;
    the caller must treat empty as "structure cannot see this", never as
    clean.
    """
    anchors = _ref_anchors(ref, manager, proposed_types)
    if anchors:
        return anchors
    local = _local(ref)
    if local in proposed_ind_types:
        return _ref_anchors(proposed_ind_types[local], manager, proposed_types)
    return manager.committed_individual_anchors(ref)


def _collect_individual_types(proposal) -> dict[str, set[str]]:
    """Map each individual reference to the set of type references it gains.

    Sources merged: non-class entities (their bfo_type) and rdf:type
    relations. An rdf:type relation may target an EXISTING individual that
    appears nowhere in proposal.entities; its subject still lands here so the
    committed types get folded into the straddle test. Subjects that are
    classes proposed this turn are excluded (punning is the reasoner's
    problem, not the lint's).
    """
    proposed_class_locals = {
        _local(e.iri_suggestion or e.label)
        for e in proposal.entities
        if getattr(e, "kind", None) == "class"
    }
    types_: dict[str, set[str]] = {}
    for ent in proposal.entities:
        if getattr(ent, "kind", None) == "class":
            continue
        subj = ent.iri_suggestion or ent.label
        bucket = types_.setdefault(subj, set())
        if getattr(ent, "bfo_type", None):
            bucket.add(ent.bfo_type)
    for rel in proposal.relations:
        if _is_type_predicate(rel.p) and _local(rel.s) not in proposed_class_locals:
            types_.setdefault(rel.s, set()).add(rel.o)
    return types_


def _describe_individual_clash(a: str, b: str) -> str:
    la = bfo_catalog.BFO_LABEL.get(bfo_catalog.normalize_fragment(a), a)
    lb = bfo_catalog.BFO_LABEL.get(bfo_catalog.normalize_fragment(b), b)
    return (
        f"BFO disjointness violation: an individual cannot instantiate both "
        f"a {la} ({bfo_catalog.normalize_fragment(a)}) and a {lb} "
        f"({bfo_catalog.normalize_fragment(b)}); these BFO categories are "
        f"disjoint."
    )


@dataclass
class StructuralClash:
    """A lint-tier rejection found without the reasoner."""
    reason: str
    subject: Optional[str] = None
    clash_pair: Optional[tuple[str, str]] = None


def structural_lint(proposal, manager) -> tuple[Optional[StructuralClash], bool]:
    """Run every structural check. Returns (clash_or_None, resolved_all).

    ``resolved_all`` is True iff every reference the lint touched resolved to
    at least one BFO anchor. When False the structure could not see the whole
    proposal and the reasoner tier must NOT be skipped (change 3: an
    unresolvable anchor never silently accepts).
    """
    proposed_types = _proposed_class_types(proposal)
    proposed_ind_types = _proposed_individual_types(proposal)
    resolved_all = True

    # --- Class straddle (K-C1): committed + proposed parents fold together.
    for subject, parent_refs in _collect_class_parents(proposal).items():
        anchors: set[str] = set(manager.committed_bfo_anchors(subject))
        for ref in parent_refs:
            ref_anchors = _ref_anchors(ref, manager, proposed_types)
            if not ref_anchors:
                resolved_all = False
            anchors |= ref_anchors
        hit, pair = bfo_catalog.straddles(anchors)
        if hit and pair is not None:
            return (
                StructuralClash(
                    reason=bfo_catalog.describe_clash(*pair),
                    subject=_local(subject),
                    clash_pair=pair,
                ),
                resolved_all,
            )

    # --- Individual-type straddle: committed + proposed types fold together.
    for subject, type_refs in _collect_individual_types(proposal).items():
        anchors = set(manager.committed_individual_anchors(subject))
        for ref in type_refs:
            ref_anchors = _ref_anchors(ref, manager, proposed_types)
            if not ref_anchors:
                resolved_all = False
            anchors |= ref_anchors
        hit, pair = bfo_catalog.straddles(anchors)
        if hit and pair is not None:
            return (
                StructuralClash(
                    reason=_describe_individual_clash(*pair),
                    subject=_local(subject),
                    clash_pair=pair,
                ),
                resolved_all,
            )

    # --- Relation-signature (domain/range) clash for BFO object properties.
    clash, sig_resolved = _signature_check(
        proposal, manager, proposed_types, proposed_ind_types
    )
    resolved_all = resolved_all and sig_resolved
    return clash, resolved_all


def _signature_check(
    proposal, manager, proposed_types, proposed_ind_types
) -> tuple[Optional[StructuralClash], bool]:
    """Reject a relation whose endpoint category is disjoint with the BFO
    signature's declared domain/range. Rejects ONLY when the endpoint's
    anchors resolved and EVERY anchor clashes with the declared category;
    an unresolvable endpoint just flags needs-reasoner.
    """
    signatures = bfo_catalog.relation_signatures()
    resolved_all = True

    for rel in proposal.relations:
        p = rel.p or ""
        if _is_subclass_predicate(p) or _is_type_predicate(p):
            continue
        frag = bfo_catalog.normalize_fragment(p)
        sig = signatures.get(frag)
        if sig is None:
            continue

        o_raw = (rel.o or "").strip()
        if o_raw.startswith("_:") or owl_checks.parse_class_expression(o_raw):
            # Expression object: structure cannot judge the range side.
            resolved_all = False
            continue

        subj_anchors = _endpoint_anchors(
            rel.s, manager, proposed_types, proposed_ind_types
        )
        obj_anchors = _endpoint_anchors(
            rel.o, manager, proposed_types, proposed_ind_types
        )
        if not subj_anchors or not obj_anchors:
            resolved_all = False

        for anchors, declared, slot, endpoint in (
            (subj_anchors, sig.get("domain"), "domain", rel.s),
            (obj_anchors, sig.get("range"), "range", rel.o),
        ):
            if not declared or not anchors:
                continue
            if all(bfo_catalog.clash(a, declared) for a in anchors):
                rep = sorted(anchors)[0]
                return (
                    StructuralClash(
                        reason=(
                            f"BFO relation signature violation: the {slot} of "
                            f"'{sig.get('label', frag)}' ({frag}) must be a "
                            f"{bfo_catalog.BFO_LABEL.get(declared, declared)} "
                            f"({declared}), but {_local(endpoint)} is anchored "
                            f"under "
                            f"{bfo_catalog.BFO_LABEL.get(rep, rep)} ({rep}), "
                            f"which is disjoint with it."
                        ),
                        subject=_local(endpoint),
                        clash_pair=(rep, declared),
                    ),
                    resolved_all,
                )
    return None, resolved_all


# Predicates whose semantics only the reasoner can evaluate.
_REASONER_ONLY_PREDICATES = ("equivalentClass", "disjointWith", "complementOf")


def proposal_needs_reasoner(
    proposal, manager, lint_resolved_all: bool
) -> tuple[bool, str]:
    """Decide whether the HermiT tier can be skipped for this proposal.

    SPEC-bfo-agent-speed.md change 3: reserve sync_reasoner for what
    structure cannot decide. The reasoner is needed iff the proposal carries
    a class expression or restriction object (parse_class_expression / '_:'
    forms, which also cover the negative constructs 'not' and 'not_some'),
    an equivalence/disjointness/complement assertion, or the lint could not
    resolve every touched reference to BFO anchors. Pure function: no
    ontology mutation, no JVM. ``manager`` is unused today but kept in the
    signature so future checks (e.g. exclude_axioms-touched subjects) slot
    in without changing callers.

    Returns (needed, reason).
    """
    for rel in proposal.relations:
        p = rel.p or ""
        o_raw = (rel.o or "").strip()
        if o_raw.startswith("_:") or owl_checks.parse_class_expression(o_raw):
            return True, (
                f"relation object {o_raw!r} is a class expression or "
                f"restriction the structure cannot decide"
            )
        for tok in _REASONER_ONLY_PREDICATES:
            if tok in p:
                return True, (
                    f"predicate {p!r} asserts {tok}, which only the reasoner "
                    f"can evaluate"
                )
    if not lint_resolved_all:
        return True, (
            "some touched reference did not resolve to BFO anchors; "
            "structure cannot decide"
        )
    return False, "plain typed entities and signature-clean assertions"
