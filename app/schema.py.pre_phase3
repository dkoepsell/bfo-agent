"""Pydantic schemas for proposals, entities, relations, and API payloads."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Entity(BaseModel):
    """A candidate entity proposed by the LLM.

    An entity is either a class (a universal like 'Mother') or an individual
    (a particular like 'Vanessa'). BFO typing applies to both: classes are
    subclasses of some BFO class; individuals are instances of some class.
    """
    label: str = Field(..., description="Human-readable name, e.g. 'Vanessa'")
    iri_suggestion: str = Field(
        ..., description="Suggested IRI with prefix, e.g. 'working:Vanessa'"
    )
    bfo_type: str = Field(
        ...,
        description="BFO class IRI fragment, e.g. 'BFO_0000040' for material entity",
    )
    bfo_label: str = Field(..., description="Human-readable BFO label")
    kind: Literal["individual", "class"] = "individual"
    parent_class: Optional[str] = Field(
        None,
        description="For class kind: IRI of parent class (defaults to bfo_type).",
    )
    rationale: str = Field(..., description="Why this BFO typing was chosen")
    is_new: bool = True
    existing_iri: Optional[str] = None


class Relation(BaseModel):
    """A proposed triple (subject, predicate, object)."""
    s: str = Field(..., description="Subject IRI")
    p: str = Field(..., description="Predicate IRI (BFO relation or rdfs:subClassOf)")
    o: str = Field(..., description="Object IRI or literal")
    rationale: str


class Proposal(BaseModel):
    """The full structured output of one proposer turn."""
    proposal_id: str = Field(default_factory=lambda: _new_id("prop"))
    session_id: str
    utterance: str
    entities: list[Entity] = []
    relations: list[Relation] = []
    open_questions: list[str] = []
    rationale_summary: str = ""
    created_at: str = Field(default_factory=_now_iso)

    # Populated by the orchestrator after reasoner check
    reasoner_verdict: Optional[Literal["consistent", "inconsistent", "error"]] = None
    reasoner_detail: Optional[str] = None


class ProposeRequest(BaseModel):
    utterance: str
    session_id: Optional[str] = None


class CommitRequest(BaseModel):
    proposal_id: str
    session_id: str
    # User may edit the proposal before committing; send the full edited Proposal.
    proposal: Proposal
    user_decision: Literal["accept", "accept_with_edits", "reject"] = "accept"
    user_notes: Optional[str] = None


class QueryRequest(BaseModel):
    question: str
    session_id: Optional[str] = None


class QueryResponse(BaseModel):
    question: str
    answer: str
    grounded: bool
    referenced_iris: list[str] = []
    missing_iris: list[str] = []


class GraphStats(BaseModel):
    num_classes: int
    num_individuals: int
    num_object_properties: int
    num_axioms: int
    recent_additions: list[dict]
