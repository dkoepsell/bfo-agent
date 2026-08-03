"""Core data model for chainlab.

`NormTuple` and `CapacityRecord` come from norm_extractor_spec_v1.md §1;
`Finding`, `ChainProfile` and `LINKS` from bfo-agent_empirical_spec_v1.md §2.
Only the parts the norm extractor and K-D3 need are defined here — the other
strata add their own detectors against the same `Finding`.
"""
from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ chain
LINKS = [
    "authority",
    "criteria",
    "assessor_in_role",
    "presenting_facts",
    "recognition_act",
    "effect",
    "remedy",
]


class LinkPerformance(str, Enum):
    INTERNAL = "internal"   # the system performs this link itself
    EXTERNAL = "external"   # the link happens, but outside the system
    ABSENT = "absent"       # the system has no such link


class ChainProfile(BaseModel):
    corpus_id: str
    links: dict[str, LinkPerformance]     # keys must equal LINKS exactly
    evidence: dict[str, list[str]]        # link -> list of source locators
    assigned_by: str                      # "manual" | "annotator:<id>" | "auto"
    notes: str = ""


# ------------------------------------------------------------------ norms
class Modality(str, Enum):
    DUTY        = "duty"          # must, shall, is required to
    PROHIBITION = "prohibition"   # must not, may not, shall not
    PERMISSION  = "permission"    # may, is permitted to, need not
    POWER       = "power"         # shall have the power to, is authorized to,
                                  # may order/issue/grant — changes normative positions
    LIABILITY   = "liability"     # is subject to, correlative of another's power
    EXEMPTION   = "exemption"     # does not apply to, is exempt from


def _uuid() -> str:
    return str(uuid.uuid4())


class NormTuple(BaseModel):
    tuple_id: str = Field(default_factory=_uuid)
    corpus_id: str
    source_locator: str                 # "Rule 27(a)(1)", "28 U.S.C. 2072(c)"
    chunk_index: int

    bearer: str                         # who the norm falls on, verbatim noun phrase
    bearer_role: str | None = None      # normalized: "court", "party", "plaintiff",
                                        # "clerk", "Supreme Court", "Judicial Conference"
    modality: Modality
    action: str                         # what must/may be done, verbatim
    conditions: list[str] = Field(default_factory=list)   # antecedents; empty if unconditional
    deadline: str | None = None         # "21 days after being served"
    counterparty: str | None = None     # who benefits or is affected

    source_quote: str = ""              # verbatim sentence(s), <= 400 chars
    chain_link: str | None = None       # one of LINKS, or None
    external_reference: bool = False    # True if action/conditions point outside this corpus
    external_targets: list[str] = Field(default_factory=list)

    confidence: Literal["high", "medium", "low"] = "low"
    extraction_note: str | None = None


PATHWAY_KINDS = [
    "motion",
    "filing",
    "notice",
    "deadline",
    "form",
    "recipient",
    "hearing",
    "order",
]


class CapacityRecord(BaseModel):
    """Derived, not extracted. Every POWER/PERMISSION tuple projects to one."""

    capacity_id: str = Field(default_factory=_uuid)
    tuple_id: str                       # the POWER/PERMISSION tuple it came from
    bearer_role: str
    capacity: str                       # the action, normalized
    pathway_loci: list[str] = Field(default_factory=list)   # source_locators of procedural tuples found
    pathway_kind: list[str] = Field(default_factory=list)
    external_reference: bool = False
    verdict: Literal["pathway_found", "no_pathway", "external"]


# --------------------------------------------------------------- findings
class Finding(BaseModel):
    finding_id: str = Field(default_factory=_uuid)
    run_id: str
    corpus_id: str
    kernel_id: str                  # "K-A1" ... "K-D3"
    stratum: Literal["A", "B", "C", "D"]
    locus: str | None = None        # one of LINKS, or None if not locus-indexed
    detector_version: str
    detection_method: Literal["reasoner", "structural", "world_facing", "process_model"]
    subjects: list[str] = Field(default_factory=list)       # IRIs or source locators implicated
    source_locators: list[str] = Field(default_factory=list)
    justification: str              # human-readable, one sentence
    provenance: Literal["source", "artifact", "undetermined"] = "undetermined"
    confidence: float = 0.0         # 0.0-1.0, detector's own
