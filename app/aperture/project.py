"""The authorization boundary.

The client renderer must be constructed so that it cannot emit an identifier it
never receives. So the projection happens here, at the boundary, and the client
renderer takes a :class:`ClientView` rather than the internal report. Filtering
at render time would leave the internal model within reach of a later edit; this
does not.

Concretely: adding a field to :class:`app.aperture.resolve.ResolutionReport` must
not cause that field to appear in client output. The projection names what it
copies, one field at a time, and a test asserts the attribute set is exactly the
allowlist.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any

from .loader import LoadedManifest
from .resolve import Verdict

PROFILES: tuple[str, ...] = ("internal", "client")

# What a reader is told about reachability, in plain language. The verdict names
# themselves are internal vocabulary and never cross the boundary.
REACHABILITY: dict[str, str] = {
    str(Verdict.IN_SCOPE_MECHANICAL): "Yes, checked automatically",
    str(Verdict.IN_SCOPE_HUMAN): "Yes, by review",
    str(Verdict.GATED_RARE): "Rarely",
    str(Verdict.OUT_CHAIN): "No",
    str(Verdict.OUT_PRECONDITION): "No",
}

# Why, in plain language. Keyed by the resolver's machine reason.
PLAIN_REASON: dict[str, str] = {
    "formal_theory": ("the artifact states no rules of the kind this check "
                      "reads, only names and typings"),
    "definitions": "the artifact defines no category in logical form",
    "complement_available": ("the artifact cannot express a category as what it "
                             "excludes, so this pattern could not appear in it"),
    "upper_ontology": ("the artifact is not anchored to a top-level set of "
                       "kinds, so there is nothing to check it against"),
    "bearer_relations": ("the artifact asserts no dependence between things, so "
                         "a severed dependence could not be seen"),
    "acts": ("the artifact describes no acts, so a failure about how an act is "
             "performed cannot arise in it"),
    "individuals": "the artifact names no particular cases",
    "profile_dl": ("the artifact falls outside the standard fragment the "
                   "automated checker is defined on, so no automated result "
                   "about it would be meaningful"),
    "chain_absent": ("the system under review has no such step, so this failure "
                     "cannot occur in it"),
    "chain_external": ("this step is carried out outside the system under "
                       "review, so the failure is possible but uncommon here"),
    "chain_performed": "the system carries out this step itself",
    "detector_available": "an automated check covers this",
    "human_judgment": "this is settled by reading rather than by an automated check",
}


@dataclass(frozen=True)
class ClientRow:
    """One row of the client coverage table. Three columns and nothing else."""

    defect_class: str
    reachable: str
    result_or_reason: str
    basis: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_class": self.defect_class,
            "reachable": self.reachable,
            "result_or_reason": self.result_or_reason,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class ClientView:
    """Everything the client renderer is allowed to know.

    No stratum letter, no failure identifier, no detector name, no manifest
    version, no digest, no probe evidence. It cannot leak what it was not given.
    """

    engagement: str
    declared_on: str
    basis: str
    rows: tuple[ClientRow, ...]
    reachable_count: int
    total_count: int
    profile_verified: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "engagement": self.engagement,
            "declared_on": self.declared_on,
            "basis": self.basis,
            "rows": [r.to_dict() for r in self.rows],
            "reachable_count": self.reachable_count,
            "total_count": self.total_count,
            "profile_verified": self.profile_verified,
        }


# The allowlist, stated once so a test can pin it. Adding a field to the internal
# report must not widen this.
CLIENT_VIEW_FIELDS: frozenset[str] = frozenset({
    "engagement", "declared_on", "basis", "rows",
    "reachable_count", "total_count", "profile_verified",
})
CLIENT_ROW_FIELDS: frozenset[str] = frozenset({
    "defect_class", "reachable", "result_or_reason", "basis",
})


def project_client(report,
                   manifest: LoadedManifest,
                   engagement: str,
                   declared_on: str = "") -> ClientView:
    """Build the client view from an internal resolution report.

    Every field is copied by name. Nothing is spread, and nothing is derived from
    iterating the internal object, so a new internal field cannot arrive here by
    accident.
    """
    labels = manifest.client_labels()

    rows: list[ClientRow] = []
    for row in report.rows:
        label = labels.get(row.failure_id)
        if not label:
            # A failure with no client label must not be described at all rather
            # than described by its identifier.
            continue
        reason = PLAIN_REASON.get(
            row.reason, "this was not reachable on the artifact as delivered")
        rows.append(ClientRow(
            defect_class=label,
            reachable=REACHABILITY[str(row.verdict)],
            result_or_reason=reason,
            # The computed against declared distinction is the point of the
            # tool, so it crosses the boundary in plain words.
            basis=("measured from the artifact" if row.basis == "computed"
                   else "based on the declared role of the system"),
        ))

    reachable = sum(1 for r in report.rows if r.in_scope)
    return ClientView(
        engagement=engagement,
        declared_on=declared_on,
        basis=report.chain_basis,
        rows=tuple(rows),
        reachable_count=reachable,
        total_count=len(report.rows),
        profile_verified=report.profile_dl is True,
    )


def assert_projection_is_narrow() -> None:
    """Self-check used by the leak tests and by startup validation."""
    got_view = {f.name for f in fields(ClientView)}
    if got_view != set(CLIENT_VIEW_FIELDS):
        raise AssertionError(
            f"ClientView fields drifted from the allowlist: {got_view ^ set(CLIENT_VIEW_FIELDS)}")
    got_row = {f.name for f in fields(ClientRow)}
    if got_row != set(CLIENT_ROW_FIELDS):
        raise AssertionError(
            f"ClientRow fields drifted from the allowlist: {got_row ^ set(CLIENT_ROW_FIELDS)}")
