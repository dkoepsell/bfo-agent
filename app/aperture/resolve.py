"""Phase 3: the resolver.

Takes the manifest, the precondition probe and the chain declaration, and says
for each of the twelve failures whether a review of this artifact could reach it.

Five verdicts and no sixth. In particular there is no CLEAN: cleanliness is a
detector result, and the resolver never produces one. That is enforced by the
type rather than by a comment, and by a test that counts the members.

The one thing every row must carry beyond its verdict is whether the input that
produced it was measured or declared. A reader has to be able to see which parts
of a coverage statement rest on measurement of the artifact and which rest on the
reviewer's judgment about the artifact's role.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Optional

from .chain import ChainDeclaration
from .loader import LoadedManifest
from .probe import ProbeReport
from .schema import FailureSpec

# Versioned separately from the tool, so that resolutions stay comparable across
# runs even when the tool moves. A schema change that breaks cross-run
# comparison costs the corpus study.
RESOLUTION_SCHEMA = "1.0"


class Verdict(StrEnum):
    """What a review of this artifact can do about one failure.

    Exactly five. There is deliberately no CLEAN member: the resolver says what
    could be reached, never what was found.
    """

    OUT_PRECONDITION = "OUT_PRECONDITION"
    OUT_CHAIN = "OUT_CHAIN"
    GATED_RARE = "GATED_RARE"
    IN_SCOPE_MECHANICAL = "IN_SCOPE_MECHANICAL"
    IN_SCOPE_HUMAN = "IN_SCOPE_HUMAN"


IN_SCOPE_VERDICTS = frozenset({Verdict.IN_SCOPE_MECHANICAL, Verdict.IN_SCOPE_HUMAN})

COMPUTED = "computed"
DECLARED = "declared"


@dataclass(frozen=True)
class Resolution:
    """One failure, resolved."""

    failure_id: str
    stratum: str
    verdict: Verdict
    reason: str
    reason_detail: str
    basis: str
    produced_by: str
    loci: tuple[str, ...]
    instrument: str
    link_states: dict[str, str] = field(default_factory=dict)
    detectors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def in_scope(self) -> bool:
        return self.verdict in IN_SCOPE_VERDICTS

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "stratum": self.stratum,
            "verdict": str(self.verdict),
            "reason": self.reason,
            "reason_detail": self.reason_detail,
            "basis": self.basis,
            "produced_by": self.produced_by,
            "loci": list(self.loci),
            "instrument": self.instrument,
            "link_states": dict(self.link_states),
            "detectors": list(self.detectors),
            "notes": list(self.notes),
            "in_scope": self.in_scope,
        }


@dataclass(frozen=True)
class ResolutionReport:
    resolution_schema: str
    manifest_version: str
    manifest_digest: str
    probe_version: str
    artifact_sha256: str
    formal_theory_strict: bool
    profile_dl: Optional[bool]
    chain_digest: str
    chain_basis: str
    domain: str
    act_thickness: str
    repair: str
    rows: tuple[Resolution, ...]
    advisories: tuple[dict[str, Any], ...] = ()

    def by_id(self, failure_id: str) -> Resolution:
        for row in self.rows:
            if row.failure_id == failure_id:
                return row
        raise KeyError(failure_id)

    def verdict(self, failure_id: str) -> Verdict:
        return self.by_id(failure_id).verdict

    @property
    def counts(self) -> dict[str, int]:
        out = {str(v): 0 for v in Verdict}
        for row in self.rows:
            out[str(row.verdict)] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolution_schema": self.resolution_schema,
            "manifest_version": self.manifest_version,
            "manifest_digest": self.manifest_digest,
            "probe_version": self.probe_version,
            "artifact_sha256": self.artifact_sha256,
            "formal_theory_strict": self.formal_theory_strict,
            "profile_dl": self.profile_dl,
            "chain_digest": self.chain_digest,
            "chain_basis": self.chain_basis,
            "domain": self.domain,
            "act_thickness": self.act_thickness,
            "repair": self.repair,
            "counts": self.counts,
            "rows": [r.to_dict() for r in self.rows],
            "advisories": list(self.advisories),
        }


def _available_detectors(failure: FailureSpec, profile_dl_ok: bool) -> tuple[str, ...]:
    """Detectors that can actually run on this artifact.

    Outside OWL 2 DL a reasoner's behaviour is undefined, so a reasoner detector
    is dropped. A structural detector on the same primitive is untouched: it never
    needed a reasoner, so blocking it would overstate what the profile failure
    costs.
    """
    return tuple(
        d.path for d in failure.detectors
        if d.instrument != "dl_reasoner" or profile_dl_ok
    )


def _resolve_one(failure: FailureSpec,
                 manifest: LoadedManifest,
                 probe: ProbeReport,
                 declaration: ChainDeclaration,
                 profile_dl_ok: bool) -> Resolution:
    notes: list[str] = []
    detectors = _available_detectors(failure, profile_dl_ok)

    if failure.detectors and not detectors:
        notes.append("every detector for this failure needs a reasoner")
    elif len(detectors) < len(failure.detectors):
        notes.append("the reasoner half of this check is unavailable; the "
                     "structural half still runs")

    common = dict(
        failure_id=failure.id,
        stratum=failure.stratum,
        loci=tuple(failure.loci),
        instrument=failure.instrument,
        detectors=detectors,
    )

    # 1. Preconditions, in order, so the reason is deterministic.
    unsatisfied = probe.unsatisfied(manifest.preconditions_for(failure.id))
    if unsatisfied is not None:
        result = probe.results.get(unsatisfied)
        detail = (result.note if result and result.note else
                  f"the artifact does not satisfy {unsatisfied}")
        return Resolution(
            verdict=Verdict.OUT_PRECONDITION,
            reason=unsatisfied,
            reason_detail=detail,
            basis=COMPUTED,
            produced_by=unsatisfied,
            link_states={},
            notes=tuple(notes),
            **common,
        )

    # 2. The profile_dl hard gate. Only bites a failure with nothing left to run.
    if not profile_dl_ok and failure.detectors and not detectors:
        result = probe.results.get("profile_dl")
        return Resolution(
            verdict=Verdict.OUT_PRECONDITION,
            reason="profile_dl",
            reason_detail=(result.note if result and result.note else
                           "the OWL 2 DL profile is unverified, so no "
                           "reasoner-dependent verdict can be given"),
            basis=COMPUTED,
            produced_by="profile_dl",
            link_states={},
            notes=tuple(notes),
            **common,
        )

    # 3. Chain gating, for the strata that need acts.
    if manifest.gated_by_chain(failure.id):
        states = {locus: declaration.state(locus) for locus in failure.loci}
        distinct = set(states.values())

        if "performed" in distinct:
            performed = sorted(l for l, s in states.items() if s == "performed")
            verdict = (Verdict.IN_SCOPE_MECHANICAL if detectors
                       else Verdict.IN_SCOPE_HUMAN)
            return Resolution(
                verdict=verdict,
                reason="chain_performed",
                reason_detail=(
                    "the system performs this link itself, so the failure can "
                    f"fire on the artifact ({', '.join(performed)})"),
                basis=DECLARED,
                produced_by=performed[0],
                link_states=states,
                notes=tuple(notes),
                **common,
            )

        if distinct == {"absent"}:
            return Resolution(
                verdict=Verdict.OUT_CHAIN,
                reason="chain_absent",
                reason_detail=(
                    "the system has no such link, so this failure cannot occur "
                    "in it at all"),
                basis=DECLARED,
                produced_by=sorted(states)[0],
                link_states=states,
                notes=tuple(notes),
                **common,
            )

        external = sorted(l for l, s in states.items() if s == "external")
        absent = sorted(l for l, s in states.items() if s == "absent")
        if absent:
            notes.append(
                "mixed link states: external at " + ", ".join(external)
                + "; absent at " + ", ".join(absent))
        return Resolution(
            verdict=Verdict.GATED_RARE,
            reason="chain_external",
            reason_detail=(
                "the link is performed outside the system, so the failure is "
                "possible but rare on the artifact itself"),
            basis=DECLARED,
            produced_by=external[0] if external else sorted(states)[0],
            link_states=states,
            notes=tuple(notes),
            **common,
        )

    # 4. Ungated. A null detector is human judgment in scope, never out of scope.
    if detectors:
        return Resolution(
            verdict=Verdict.IN_SCOPE_MECHANICAL,
            reason="detector_available",
            reason_detail="every precondition is satisfied and a check can run",
            basis=COMPUTED,
            produced_by="detector",
            link_states={},
            notes=tuple(notes),
            **common,
        )
    return Resolution(
        verdict=Verdict.IN_SCOPE_HUMAN,
        reason="human_judgment",
        reason_detail=(
            "every precondition is satisfied, and this failure is decided by "
            "reading rather than by a mechanical check"),
        basis=COMPUTED,
        produced_by="detector",
        link_states={},
        notes=tuple(notes),
        **common,
    )


def resolve(manifest: LoadedManifest,
            probe: ProbeReport,
            declaration: ChainDeclaration,
            advisories=()) -> ResolutionReport:
    """Resolve all twelve failures against one artifact and one declared chain."""
    profile_dl_ok = probe.satisfied("profile_dl") is True

    rows = tuple(
        _resolve_one(failure, manifest, probe, declaration, profile_dl_ok)
        for failure in manifest.failures
    )

    return ResolutionReport(
        resolution_schema=RESOLUTION_SCHEMA,
        manifest_version=manifest.version,
        manifest_digest=manifest.digest,
        probe_version=probe.probe_version,
        artifact_sha256=probe.artifact_sha256,
        formal_theory_strict=probe.formal_theory_strict,
        profile_dl=probe.satisfied("profile_dl"),
        chain_digest=declaration.digest,
        chain_basis=declaration.basis,
        domain=declaration.domain,
        act_thickness=declaration.act_thickness,
        repair=declaration.repair,
        rows=rows,
        advisories=tuple(
            a.to_dict() if hasattr(a, "to_dict") else dict(a) for a in advisories
        ),
    )
