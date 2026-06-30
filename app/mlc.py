"""MLC well-formedness + contradiction typology (bfo-agent-spec.md FR-4/FR-5).

SCOPE (hard constraint): the Minimal Legal Cluster is relevant ONLY to SOoL.
Every public function here is gated behind :func:`app.sool_extension.applies_to`
and is a no-op for any other ontology. MLC machinery must never be incorporated
into a non-SOoL build. Like :mod:`app.sool_extension`, this module is NOT wired
into the general linter/gate path -- callers invoke it explicitly, and only for
SOoL.

FR-4 -- MLC well-formedness: when a SOoL scenario asserts a legal effect, the
agent should populate the node occupants it can support along the chain
(1..8), each typed to the BFO kind the node requires. Missing nodes are left
*absent*, never stubbed with a placeholder class.

FR-5 -- failure => typology, not class: a structural defect is asserted as an
instance of a contradiction type indexed to its MLC link, NEVER as a new
``AbsenceOf*``/``Lack*``/``Failure*`` class.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import sool_extension as sx
from .stable_iri import content_hash


# The contradiction typology is indexed to MLC links. The kernel (hand-authored,
# read-only) is the eventual source of truth for the full 13-type set; this table
# is the agent-side registry it asserts against. Each entry names the MLC link it
# diagnoses. Anchored as a realizable/SDC defect, never a primitive absence class.
@dataclass(frozen=True)
class ContradictionType:
    name: str
    mlc_link: tuple[int, int]   # the (from, to) MLC nodes the defect sits between
    gloss: str


CONTRADICTION_TYPES: dict[str, ContradictionType] = {
    c.name: c for c in (
        ContradictionType("Authority Inflation", (1, 2),
                          "a norm claims more authority than its source grants"),
        ContradictionType("Recognition Failure", (6, 7),
                          "the target is not recognized as bearing the legal effect"),
        ContradictionType("Norm Misalignment", (1, 2),
                          "a norm does not align with its rule of recognition"),
        ContradictionType("Role Vacancy", (2, 3),
                          "a norm addresses a role no actor occupies"),
        ContradictionType("Trigger Gap", (4, 5),
                          "asserted facts do not satisfy the norm's trigger"),
        ContradictionType("Act Omission Conflict", (5, 5),
                          "an act and an omission are both required"),
        ContradictionType("Effect Without Norm", (2, 7),
                          "a legal effect is asserted with no grounding norm"),
        ContradictionType("Remedy Without Effect", (7, 8),
                          "a remedy is asserted with no legal effect to remedy"),
        ContradictionType("Circular Authority", (1, 1),
                          "a source of authority grounds itself"),
        ContradictionType("Conflicting Norms", (2, 2),
                          "two norms attach incompatible effects to one trigger"),
        ContradictionType("Target Mismatch", (6, 6),
                          "the target's BFO kind cannot bear the asserted effect"),
        ContradictionType("Dangling Trigger", (4, 4),
                          "triggering facts reference no act or actor"),
        ContradictionType("Unsourced Norm", (1, 2),
                          "a norm has no source of authority"),
    )
}


@dataclass
class MLCWellFormedness:
    present: list[int]          # MLC node indices with at least one occupant
    absent: list[int]          # MLC node indices left absent (NOT stubbed)
    mistyped: list[str]        # human-readable anchor violations


def _occupant_node_for(bfo_type: str) -> int | None:
    """Best MLC node an occupant of this BFO type could fill (first matching
    anchor along the chain). Used only to report presence, never to mint."""
    for node in sx.MLC_NODES:
        if sx.occupant_anchored(bfo_type, node.index).ok:
            return node.index
    return None


def assess_well_formedness(occupant_types, ontology):
    """FR-4 (SOoL only): report which MLC nodes are present/absent given the
    BFO types of the occupants the scenario supports.

    ``occupant_types`` is an iterable of BFO-type fragments. Returns an
    MLCWellFormedness, or None when MLC does not apply to this ontology.
    """
    if not sx.applies_to(ontology):
        return None
    present: set[int] = set()
    mistyped: list[str] = []
    for t in occupant_types:
        node = _occupant_node_for(t)
        if node is None:
            res = sx.validate_term_anchored(t)
            if not res.ok:
                mistyped.append(res.reason)
        else:
            present.add(node)
    absent = [n.index for n in sx.MLC_NODES if n.index not in present]
    return MLCWellFormedness(
        present=sorted(present), absent=absent, mistyped=mistyped
    )


def contradiction_individual(defect_type: str, source: str, ontology,
                             working_prefix: str = "working") -> dict | None:
    """FR-5 (SOoL only): build the assertion for a contradiction-type INDIVIDUAL
    (never a class). Returns a dict describing the individual to emit, or None
    when MLC does not apply or the type is unknown.

    The individual is typed to the kernel contradiction type; its IRI is
    stable-hashed from (type, source) for diffability (FR-6).
    """
    if not sx.applies_to(ontology):
        return None
    ctype = CONTRADICTION_TYPES.get(defect_type)
    if ctype is None:
        return None
    ind = f"{ctype.name.replace(' ', '')}_{content_hash(defect_type, source)}"
    return {
        "individual": f"{working_prefix}:{ind}",
        "type": f"{working_prefix}:{ctype.name.replace(' ', '')}",
        "mlc_link": list(ctype.mlc_link),
        "gloss": ctype.gloss,
        "kind": "individual",
    }
