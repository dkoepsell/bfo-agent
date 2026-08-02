"""Pydantic models for the Aperture kernel manifest.

The manifest is a declarative data file and it is the asset: code reads it, code
does not embed it. See ``app/aperture/manifest.yaml``.

Three properties are enforced here rather than left to convention:

1. ``client_label`` is mandatory and carries no framework internals. Validated
   at load, so a leaky label fails the load rather than the render.
2. An empty ``detectors`` list means human judgment. It resolves to
   ``IN_SCOPE_HUMAN``, never to out of scope. A framework whose non-mechanical
   checks silently disappear has inverted its own purpose.
3. Strata are cumulative and ordered. The loader computes the transitive
   requirement set; the manifest does not restate it.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .redaction import find_leaks

# The eight preconditions the probe reports on. Every stratum precondition and
# every per-failure ``requires`` entry must be one of these.
PreconditionId = Literal[
    "formal_theory",
    "definitions",
    "complement_available",
    "upper_ontology",
    "bearer_relations",
    "acts",
    "individuals",
    "profile_dl",
]

# What it takes to see a primitive at all. Mirrors app.recognition.Instrument;
# the loader holds the translation and a drift test pins the two together.
InstrumentId = Literal["dl_reasoner", "structural", "world_evidence", "process_model"]

# The seven loci of the recognition chain. Mirrors app.recognition.Locus.
LocusId = Literal["authority", "criteria", "assessor", "facts", "act", "effect", "remedy"]

StratumId = Literal["A", "B", "C", "D"]

STRATUM_ORDER: tuple[StratumId, ...] = ("A", "B", "C", "D")


class StratumSpec(BaseModel):
    """One stratum, with the precondition that gates everything in it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: StratumId
    precondition: PreconditionId
    gated_by_chain: bool


class DetectorSpec(BaseModel):
    """One mechanical check, and the instrument it uses.

    ``instrument`` is recorded per detector rather than per failure because a
    primitive can be reachable by more than one route. A primitive that the
    reasoner sees and a structural pass also sees in part must not be written
    off entirely when the reasoner is unavailable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(..., description="Dotted path to the callable, e.g. app.kernel_audit.audit")
    instrument: InstrumentId

    @field_validator("path")
    @classmethod
    def _dotted(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) < 2 or not all(p.isidentifier() for p in parts):
            raise ValueError(f"detector path is not a dotted callable: {v!r}")
        return v


class FailureSpec(BaseModel):
    """One kernel failure, as the resolver needs to see it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    stratum: StratumId
    loci: tuple[LocusId, ...] = Field(..., min_length=1)
    instrument: InstrumentId = Field(
        ...,
        description="The principled primary instrument, mirroring app.recognition.KERNEL.",
    )
    instrument_partial: bool = Field(
        False,
        description="The instrument sees this primitive only in part or in special cases.",
    )
    detectors: tuple[DetectorSpec, ...] = ()
    requires: tuple[PreconditionId, ...] = Field(
        (),
        description="Preconditions beyond the cumulative stratum defaults.",
    )
    client_label: str

    @property
    def is_human(self) -> bool:
        """No mechanical detector, so this failure is a work item for a person."""
        return not self.detectors

    @field_validator("client_label")
    @classmethod
    def _no_internals(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("client_label is mandatory and must be non-empty")
        leaks = find_leaks(v)
        if leaks:
            detail = ", ".join(f"{text!r} ({what})" for text, what in leaks)
            raise ValueError(f"client_label discloses framework internals: {detail}")
        return v


class Manifest(BaseModel):
    """The kernel manifest as loaded from disk, before the loader derives anything."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str
    strata: tuple[StratumSpec, ...]
    failures: tuple[FailureSpec, ...]

    @field_validator("strata")
    @classmethod
    def _ordered_and_complete(cls, v: tuple[StratumSpec, ...]) -> tuple[StratumSpec, ...]:
        got = tuple(s.id for s in v)
        if got != STRATUM_ORDER:
            raise ValueError(
                f"strata must be exactly {list(STRATUM_ORDER)} in order, got {list(got)}"
            )
        return v

    @field_validator("failures")
    @classmethod
    def _unique_ids(cls, v: tuple[FailureSpec, ...]) -> tuple[FailureSpec, ...]:
        if not v:
            raise ValueError("manifest declares no failures")
        seen: set[str] = set()
        for f in v:
            if f.id in seen:
                raise ValueError(f"duplicate failure id: {f.id}")
            seen.add(f.id)
        return v
