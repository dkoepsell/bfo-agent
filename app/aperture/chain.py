"""Phase 2: the chain declaration.

The declaration is the one place a human judgment enters the calculus, and it
must be traceable to something in the record. Everything here follows from that.

A link takes exactly one of ``performed``, ``external`` or ``absent``, read
relative to the *system under review*: the system performs the link itself, the
link is performed outside it, or the system has no such link. That is what fixes
whether a pragmatic failure can fire, fires rarely, or cannot fire at all.

Three refusals, all deliberate:

* **An empty basis is refused at load.** A declaration with no stated basis is
  not a declaration.
* **An incomplete declaration is refused.** An undeclared link is not ``absent``;
  absent is a claim about the system, and defaulting to it would silently narrow
  the scope of a review.
* **A derived thickness that contradicts the declared one halts.** Both values
  are reported and neither is preferred.

Note on defaults. The Table 1 columns on a :class:`app.recognition.DomainProfile`
describe what the *institution* does in the world, which is a different question
from where a link sits relative to the artifact. Only ``act_thickness`` and
``repair`` encode the latter, and only for two of the seven loci. So the module
suggests those two and leaves the other five for the reviewer. Deriving all seven
from Table 1 would contradict the profile's own thickness for the two
recognition-only rows, which is the tell that the columns do not mean what a
link state means.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional

from .. import recognition as rec

LinkState = Literal["performed", "external", "absent"]
LINK_STATES: tuple[str, ...] = ("performed", "external", "absent")

LOCI: tuple[str, ...] = tuple(locus.value for locus in rec.LOCUS_ORDER)

# Thickness is derived from the links, then cross-checked against what the
# operator declared on the recognition profile. The two maps are inverses, so a
# suggestion round-trips and only a human disagreement can trigger the halt.
ACT_THICKNESS_FROM_LINK: dict[str, str] = {
    "performed": "thick",
    "external": "thin",
    "absent": "none",
}
REPAIR_FROM_LINK: dict[str, str] = {
    "performed": "internal",
    "external": "external",
    "absent": "none",
}
LINK_FROM_ACT_THICKNESS: dict[str, str] = {
    v: k for k, v in ACT_THICKNESS_FROM_LINK.items()
}
LINK_FROM_REPAIR: dict[str, str] = {v: k for k, v in REPAIR_FROM_LINK.items()}

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

MIN_BASIS_CHARS = 8


class ChainError(Exception):
    """The chain declaration cannot be used as it stands."""


class IncompleteChain(ChainError):
    """A link is undeclared, or the basis is missing.

    Not a validation nicety. Resolving on a partial declaration would report a
    scope the reviewer never actually claimed.
    """


class ThicknessContradiction(ChainError):
    """The thickness derived from the links contradicts the declared thickness.

    Halts and reports both. Silently preferring either one would let the tool
    decide something the spec reserves for a person.
    """

    def __init__(self, field: str, derived: str, declared: str):
        self.field = field
        self.derived = derived
        self.declared = declared
        super().__init__(
            f"{field} derived from the links is {derived!r} but the recognition "
            f"profile declares {declared!r}; neither is preferred, so the "
            f"declaration must be corrected before it can be used"
        )


@dataclass(frozen=True)
class Advisory:
    """A warning that never overrides the declaration."""

    kind: str
    locus: Optional[str]
    message: str
    detail: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "locus": self.locus,
            "message": self.message,
            "detail": self.detail,
            "advisory": True,
        }


@dataclass(frozen=True)
class ChainDeclaration:
    """A complete, basis-backed declaration of where each chain link sits."""

    engagement: str
    artifact_sha256: str
    declared_by: str
    declared_on: str
    basis: str
    links: dict[str, str]
    domain: str
    act_thickness: str
    repair: str

    @property
    def has_chain(self) -> bool:
        return self.act_thickness != "none"

    @property
    def stratum_d_thin(self) -> bool:
        """Acts or repair sit outside the artifact, so the pragmatic stratum is
        reachable only in part. Mirrors DomainProfile.stratum_d_thin."""
        return self.has_chain and (
            self.act_thickness == "thin" or self.repair == "external"
        )

    def state(self, locus: str) -> str:
        return self.links[locus]

    def states_for(self, loci) -> tuple[str, ...]:
        return tuple(self.links[locus] for locus in loci)

    @property
    def digest(self) -> str:
        payload = json.dumps({
            "engagement": self.engagement,
            "artifact_sha256": self.artifact_sha256,
            "declared_by": self.declared_by,
            "declared_on": self.declared_on,
            "basis": self.basis,
            "links": dict(sorted(self.links.items())),
            "domain": self.domain,
        }, sort_keys=True)
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def is_stale(self, current_artifact_sha256: str) -> bool:
        return bool(self.artifact_sha256) and self.artifact_sha256 != current_artifact_sha256

    def to_profile(self) -> rec.DomainProfile:
        """The declaration as a DomainProfile, so existing helpers keep working.

        ``app.recognition.active_primitives`` and ``completeness`` both take a
        profile. Building one here means the Aperture declaration and the Audits
        tab cannot end up describing different institutions.
        """
        from dataclasses import replace

        base = rec.profile_for(self.domain)
        return replace(base, act_thickness=self.act_thickness, repair=self.repair)

    def to_dict(self) -> dict[str, Any]:
        return {
            "engagement": self.engagement,
            "artifact_sha256": self.artifact_sha256,
            "declared_by": self.declared_by,
            "declared_on": self.declared_on,
            "basis": self.basis,
            "links": dict(self.links),
            "domain": self.domain,
            "act_thickness": self.act_thickness,
            "repair": self.repair,
            "has_chain": self.has_chain,
            "stratum_d_thin": self.stratum_d_thin,
            "digest": self.digest,
        }


def suggest_links(profile: rec.DomainProfile) -> dict[str, Optional[str]]:
    """A starting point for the reviewer, not a declaration.

    Only ``act`` and ``remedy`` are suggested, from the thickness the operator
    has already declared on the recognition profile. The other five are left
    unset because nothing in the profile records where they sit relative to the
    artifact, and inventing them is exactly what the basis requirement exists to
    prevent.
    """
    return {
        "authority": None,
        "criteria": None,
        "assessor": None,
        "facts": None,
        "act": LINK_FROM_ACT_THICKNESS.get(profile.act_thickness),
        "effect": None,
        "remedy": LINK_FROM_REPAIR.get(profile.repair),
    }


def derive_thickness(links: dict[str, str]) -> tuple[str, str]:
    """``(act_thickness, repair)`` implied by the declared links."""
    return (
        ACT_THICKNESS_FROM_LINK[links["act"]],
        REPAIR_FROM_LINK[links["remedy"]],
    )


def validate(block: dict[str, Any],
             *,
             engagement: str,
             domain: str,
             declared_act_thickness: Optional[str] = None,
             declared_repair: Optional[str] = None,
             artifact_sha256: str = "") -> ChainDeclaration:
    """Turn a stored or submitted chain block into a usable declaration.

    Raises :class:`IncompleteChain` when a link or the basis is missing, and
    :class:`ThicknessContradiction` when the derived thickness disagrees with
    what the recognition profile declares.
    """
    block = block or {}

    raw_links = block.get("links") or {}
    if not isinstance(raw_links, dict):
        raise IncompleteChain("links must be a mapping of locus to link state")

    missing: list[str] = []
    links: dict[str, str] = {}
    for locus in LOCI:
        value = raw_links.get(locus)
        if value is None or value == "":
            missing.append(locus)
            continue
        value = str(value).strip().lower()
        if value not in LINK_STATES:
            raise ChainError(
                f"link {locus!r} is {value!r}; each link takes exactly one of "
                f"{list(LINK_STATES)}"
            )
        links[locus] = value

    unknown = sorted(set(raw_links) - set(LOCI))
    if unknown:
        raise ChainError(f"unknown chain loci: {unknown}")

    if missing:
        raise IncompleteChain(
            "the chain is not fully declared; undeclared links are "
            f"{missing}. An undeclared link is not 'absent': absent is a claim "
            "about the system, and defaulting to it would narrow the scope of "
            "the review without anyone saying so"
        )

    basis = str(block.get("basis") or "").strip()
    if len(basis) < MIN_BASIS_CHARS:
        raise IncompleteChain(
            "basis is mandatory and must state where in the record the "
            "declaration comes from; a declaration with no stated basis is "
            "refused"
        )

    declared_on = str(block.get("declared_on") or "").strip() or date.today().isoformat()
    if not _ISO_DATE.match(declared_on):
        raise ChainError(f"declared_on must be YYYY-MM-DD, got {declared_on!r}")

    derived_act, derived_repair = derive_thickness(links)
    if declared_act_thickness and derived_act != declared_act_thickness:
        raise ThicknessContradiction("act_thickness", derived_act, declared_act_thickness)
    if declared_repair and derived_repair != declared_repair:
        raise ThicknessContradiction("repair", derived_repair, declared_repair)

    return ChainDeclaration(
        engagement=engagement,
        artifact_sha256=str(block.get("artifact_sha256") or artifact_sha256 or ""),
        declared_by=str(block.get("declared_by") or "").strip(),
        declared_on=declared_on,
        basis=basis,
        links=links,
        domain=domain,
        act_thickness=derived_act,
        repair=derived_repair,
    )


def cross_check(declaration: ChainDeclaration,
                binding_result,
                current_artifact_sha256: str = "") -> list[Advisory]:
    """Compare the declaration against what the vocabulary actually supports.

    Advisory only, and recorded in the internal output rather than discarded: a
    system claiming a link its vocabulary does not represent is arguably the most
    interesting single signal the tool produces. It never overrides the
    declaration.
    """
    advisories: list[Advisory] = []

    for locus in LOCI:
        if declaration.links[locus] != "performed":
            continue
        bound = binding_result.counts.get(locus, 0)
        if bound == 0:
            advisories.append(Advisory(
                kind="performed_but_unbound",
                locus=locus,
                message=(
                    f"the {locus} link is declared performed, but no class in "
                    f"the artifact can occupy that link. The system claims to do "
                    f"something its vocabulary does not represent."
                ),
                detail={
                    "declared": "performed",
                    "bound_classes": 0,
                    "named_classes": binding_result.named_classes,
                },
            ))

    if current_artifact_sha256 and declaration.is_stale(current_artifact_sha256):
        advisories.append(Advisory(
            kind="declaration_predates_artifact",
            locus=None,
            message=(
                "the chain was declared against a different version of the "
                "artifact; the declaration may no longer describe what is here"
            ),
            detail={
                "declared_against": declaration.artifact_sha256,
                "current": current_artifact_sha256,
            },
        ))

    return advisories


def status(block: dict[str, Any],
           *,
           engagement: str,
           domain: str,
           profile: rec.DomainProfile) -> dict[str, Any]:
    """What the UI needs: the declaration if it is usable, the reason if not.

    Never raises. The editor has to be able to show a half-finished declaration
    without the server treating it as an error.
    """
    block = block or {}
    try:
        declaration = validate(
            block,
            engagement=engagement,
            domain=domain,
            declared_act_thickness=profile.act_thickness,
            declared_repair=profile.repair,
        )
    except ThicknessContradiction as e:
        return {
            "declared": True,
            "usable": False,
            "reason": "thickness_contradiction",
            "detail": {
                "field": e.field, "derived": e.derived, "declared": e.declared,
            },
            "message": str(e),
            "links": block.get("links") or {},
            "basis": block.get("basis") or "",
            "suggested_links": suggest_links(profile),
        }
    except ChainError as e:
        return {
            "declared": bool(block),
            "usable": False,
            "reason": "incomplete" if isinstance(e, IncompleteChain) else "invalid",
            "message": str(e),
            "links": block.get("links") or {},
            "basis": block.get("basis") or "",
            "suggested_links": suggest_links(profile),
        }
    return {
        "declared": True,
        "usable": True,
        "chain": declaration.to_dict(),
        "suggested_links": suggest_links(profile),
    }
