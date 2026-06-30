"""SOoL / MLC extension layer, anchored under BFO 2020 (bfo-agent-spec.md §3/§7).

SCOPE (hard constraint): the Minimal Legal Cluster (MLC) is relevant ONLY to
SOoL. It is a domain pattern of the Statement-of-Law ontology and MUST NOT be
incorporated into, or applied to, any other ontology the workbench builds
(Spinoza, Leibniz, GeometryOfTheGood, ...). BFO anchoring (PC-1..PC-8, the
closed BFO vocabulary) is the universal discipline that applies to every
ontology; MLC well-formedness (FR-4) and the contradiction typology (FR-5) are
SOoL-only and are gated behind :func:`applies_to`. Nothing in this module is
imported by the general linter/gate path.

BFO 2020 is the dominant kernel. The frozen class/property signature (``K_C`` /
``K_P``) lives in :mod:`app.bfo_catalog` and is BFO-only -- this module never
adds to it. The MLC is a *thin extension*: each of its node types is required to
reduce to a BFO category (FR-3/FR-4), and nothing here may stand as a top-level
primitive.

The agent does not mint these as new classes (default mode emits zero new
classes, FR-7). This table is the anchor contract: when the agent populates an
MLC node, the occupant individual MUST be typed to the BFO kind named here, and
the well-formedness / typology machinery (FR-4/FR-5) keys off it.

References
----------
FR-4 chain: 1 Source of Authority -> 2 Norm -> 3 Actor-in-Role ->
4 Triggering Facts -> 5 Legal Act/Omission -> 6 Target -> 7 Legal Effect ->
8 Remedy.
§7 BFO grounding: legal norms/roles/obligations/statuses/legal effects are
specifically dependent continuants (BFO_0000020); roles are realizable DCs
inhering in their bearers (BFO_0000023); agents are material entities
(BFO_0000040); legal events/processes are occurrents (BFO_0000015); legal facts
are generically dependent continuants / information content entities
(BFO_0000031).
"""
from __future__ import annotations

from dataclasses import dataclass

from . import bfo_catalog

# Generically dependent continuant (information content / legal facts) and
# material entity (role bearers). Named here for readability; both are in K_C.
GDC = "BFO_0000031"
MATERIAL_ENTITY = "BFO_0000040"


def applies_to(ontology: str | None) -> bool:
    """True iff MLC machinery may run for this ontology -- SOoL only.

    ``ontology`` is the library name or working-file path of the active
    ontology (e.g. ``"SOoL_v1"`` or ``ontology/library/SOoL_v1/working.owl``).
    Every other ontology returns False, so FR-4/FR-5 never touch a non-SOoL
    build. Errs closed: an unknown/empty ontology is treated as non-SOoL.
    """
    if not ontology:
        return False
    name = str(ontology).replace("\\", "/").rstrip("/")
    # Match the library directory or bare name, case-insensitively.
    return any(
        part.lower().startswith("sool")
        for part in name.split("/")
        if part
    )


@dataclass(frozen=True)
class MLCNode:
    index: int           # position 1..8 in the MLC chain
    name: str            # human label
    bfo_anchor: str      # the BFO category (a K_C fragment) the occupant types to
    note: str = ""


# The 8 MLC node types, each anchored to a BFO 2020 category in K_C.
# Anchors are intentionally the *most specific* BFO kind the §7 grounding names;
# the validator accepts any occupant typed to a descendant of the anchor.
MLC_NODES: tuple[MLCNode, ...] = (
    MLCNode(1, "Source of Authority", bfo_catalog.SDC,
            "norm-bearing dependent continuant (a constitution, statute, custom)"),
    MLCNode(2, "Norm", bfo_catalog.SDC,
            "specifically dependent continuant"),
    MLCNode(3, "Actor-in-Role", bfo_catalog.ROLE,
            "role (realizable DC) inhering in a material entity"),
    MLCNode(4, "Triggering Facts", GDC,
            "legal facts = generically dependent continuant / information content"),
    MLCNode(5, "Legal Act/Omission", bfo_catalog.PROCESS,
            "occurrent / process"),
    MLCNode(6, "Target", bfo_catalog.SDC,
            "relational (specifically) dependent continuant"),
    MLCNode(7, "Legal Effect", bfo_catalog.REALIZABLE,
            "realizable dependent continuant"),
    MLCNode(8, "Remedy", bfo_catalog.REALIZABLE,
            "realizable dependent continuant (a disposition to be realized)"),
)

MLC_BY_INDEX: dict[int, MLCNode] = {n.index: n for n in MLC_NODES}
MLC_BY_NAME: dict[str, MLCNode] = {n.name: n for n in MLC_NODES}

# The bearer kind for node 3's role (Actor-in-Role inheres in a material entity).
ACTOR_BEARER_ANCHOR = MATERIAL_ENTITY


@dataclass
class AnchorResult:
    ok: bool
    reason: str = ""


def _frag(value: str) -> str:
    return bfo_catalog.normalize_fragment(value or "")


def anchor_for_node(node: int | str) -> str:
    """The required BFO anchor (K_C fragment) for an MLC node, by index or name."""
    n = MLC_BY_INDEX.get(node) if isinstance(node, int) else MLC_BY_NAME.get(node)
    if n is None:
        raise KeyError(f"unknown MLC node: {node!r}")
    return n.bfo_anchor


def is_anchored(bfo_type: str) -> bool:
    """True iff ``bfo_type`` resolves to a BFO 2020 category in K_C.

    This is the closed-vocabulary invariant for the extension: a SOoL/MLC term
    is legitimate only when it reduces to BFO. K_C itself stays BFO-only.
    """
    frag = _frag(bfo_type)
    return frag in bfo_catalog.KERNEL_CLASSES


def occupant_anchored(bfo_type: str, node: int | str) -> AnchorResult:
    """Validate that an occupant's ``bfo_type`` satisfies an MLC node's anchor.

    The occupant must be typed to the node's required BFO anchor, or to a
    descendant of it (e.g. a Disposition BFO_0000016 for a Legal Effect whose
    anchor is Realizable BFO_0000017).
    """
    frag = _frag(bfo_type)
    if not frag:
        return AnchorResult(False, "occupant has no bfo_type")
    if frag not in bfo_catalog.KERNEL_CLASSES:
        return AnchorResult(False, f"bfo_type '{frag}' is not a BFO category in K_C")
    anchor = anchor_for_node(node)
    if frag == anchor or bfo_catalog.is_descendant_of(frag, anchor):
        return AnchorResult(True)
    label = MLC_BY_INDEX.get(node, MLC_BY_NAME.get(node))
    return AnchorResult(
        False,
        f"occupant typed '{frag}' does not descend from {anchor} "
        f"(required for MLC node '{getattr(label, 'name', node)}')",
    )


def validate_term_anchored(bfo_type: str) -> AnchorResult:
    """Generic FR-3 anchor check for any extension term (not tied to a node)."""
    if is_anchored(bfo_type):
        return AnchorResult(True)
    return AnchorResult(
        False, f"term bfo_type '{_frag(bfo_type)}' has no path to a BFO category"
    )


def self_check() -> list[str]:
    """Sanity-check the anchor table at import/test time: every anchor must be a
    real BFO category in K_C (so the extension can never drift off BFO)."""
    problems: list[str] = []
    for n in MLC_NODES:
        if n.bfo_anchor not in bfo_catalog.KERNEL_CLASSES:
            problems.append(
                f"MLC node {n.index} '{n.name}' anchor {n.bfo_anchor} not in K_C"
            )
    if ACTOR_BEARER_ANCHOR not in bfo_catalog.KERNEL_CLASSES:
        problems.append(f"actor bearer anchor {ACTOR_BEARER_ANCHOR} not in K_C")
    return problems
