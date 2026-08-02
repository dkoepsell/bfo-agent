"""Phase 5: output.

Two renderers with different inputs on purpose. The internal one takes the full
resolution report. The client one takes a :class:`app.aperture.project.ClientView`
and has no access to anything else, so it cannot disclose what it was not given.

The scope statement is the pre-engagement artifact: given this artifact and this
declared chain, here is what a review can and cannot conclude. It is produced
from Phases 1 to 3 alone, without running any detector.
"""
from __future__ import annotations

from typing import Any

from .loader import LoadedManifest
from .project import ClientView
from .resolve import Verdict

VERDICT_HEADING: dict[str, str] = {
    str(Verdict.IN_SCOPE_MECHANICAL): "In scope, mechanical",
    str(Verdict.IN_SCOPE_HUMAN): "In scope, human judgment",
    str(Verdict.GATED_RARE): "Gated, rare",
    str(Verdict.OUT_CHAIN): "Out of scope, no such link",
    str(Verdict.OUT_PRECONDITION): "Out of scope, precondition unmet",
}


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in row) + " |")
    return out


# --------------------------------------------------------------------------
# Internal
# --------------------------------------------------------------------------

def internal_markdown(report, manifest: LoadedManifest, engagement: str) -> str:
    """Full detail: identifiers, strata, loci, verdicts, reasons, digests."""
    lines: list[str] = [
        f"# Aperture coverage, internal: {engagement}",
        "",
        "What a review of this artifact can establish. This is not a findings "
        "report: no detector has been run.",
        "",
        "## Run",
        "",
    ]
    lines += _table(["Field", "Value"], [
        ["Artifact sha256", report.artifact_sha256],
        ["Manifest version", report.manifest_version],
        ["Manifest digest", report.manifest_digest],
        ["Probe version", report.probe_version],
        ["Resolution schema", report.resolution_schema],
        ["Chain digest", report.chain_digest],
        ["Domain", report.domain],
        ["Act thickness", report.act_thickness],
        ["Repair", report.repair],
        ["formal_theory reading", "strict" if report.formal_theory_strict else "permissive"],
        ["profile_dl", {True: "satisfied", False: "violated", None: "unverified"}[report.profile_dl]],
    ])

    lines += ["", "## Declared basis", "", f"> {report.chain_basis}", "", "## Resolution", ""]
    lines += _table(
        ["Failure", "Stratum", "Verdict", "Reason", "Basis", "Loci", "Detectors"],
        [[
            row.failure_id,
            row.stratum,
            str(row.verdict),
            row.reason,
            row.basis,
            ", ".join(row.loci),
            ", ".join(row.detectors) or "none",
        ] for row in report.rows],
    )

    lines += ["", "## Counts", ""]
    lines += _table(["Verdict", "Count"],
                    [[k, str(v)] for k, v in report.counts.items()])

    notes = [(r.failure_id, n) for r in report.rows for n in r.notes]
    if notes:
        lines += ["", "## Notes", ""]
        lines += [f"- **{fid}**: {note}" for fid, note in notes]

    if report.advisories:
        lines += ["", "## Advisories", "",
                  "Warnings only. None of these overrides the declaration.", ""]
        for a in report.advisories:
            where = f" ({a['locus']})" if a.get("locus") else ""
            lines.append(f"- **{a['kind']}**{where}: {a['message']}")

    lines += ["", "## Reading this table", "",
              "A verdict of IN_SCOPE says a check could have fired, not that it "
              "did. Nothing here reports a clean result, because cleanliness is "
              "a detector output and this stage runs no detectors.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

def client_markdown(view: ClientView) -> str:
    """One table plus a short preamble. Takes the projection and nothing else."""
    lines: list[str] = [
        f"# Review coverage: {view.engagement}",
        "",
        "This table states what a review of the delivered artifact was able to "
        "establish, and what it was not. A row marked not reachable was not "
        "checked and is not being reported as clean. The distinction is "
        "computed from the artifact and from the declared role of the system, "
        "not asserted by the reviewer.",
        "",
        f"Of {view.total_count} defect classes considered, {view.reachable_count} "
        f"were reachable on this artifact.",
        "",
    ]
    if not view.profile_verified:
        lines += [
            "The artifact was not confirmed to fall inside the standard fragment "
            "that the automated checker is defined on. Every automated result "
            "that would have depended on it is therefore reported as not "
            "reachable rather than as clean.",
            "",
        ]
    lines += _table(
        ["Defect class", "Reachable on this artifact", "Result or reason", "Basis"],
        [[r.defect_class, r.reachable, r.result_or_reason, r.basis] for r in view.rows],
    )
    if view.basis:
        lines += ["", "## Basis for the scope of this review", "", f"> {view.basis}", ""]
    if view.declared_on:
        lines += [f"Scope declared on {view.declared_on}.", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Scope statement
# --------------------------------------------------------------------------

def scope_statement(report, manifest: LoadedManifest, engagement: str,
                    view: ClientView | None = None) -> str:
    """The pre-engagement artifact: what a review will and will not conclude.

    Produced from Phases 1 to 3 alone. Fast, cheap, and the natural successor to
    a maturity screening gate.
    """
    if view is not None:
        body = client_markdown(view)
        return body.replace(
            f"# Review coverage: {engagement}",
            f"# Scope of review: {engagement}", 1)

    reachable = [r for r in report.rows if r.in_scope]
    gated = [r for r in report.rows if r.verdict is Verdict.GATED_RARE]
    out = [r for r in report.rows if r.verdict in
           (Verdict.OUT_CHAIN, Verdict.OUT_PRECONDITION)]

    lines = [
        f"# Scope of review: {engagement}",
        "",
        "Given this artifact and this declared chain, this is what a review can "
        "and cannot conclude. No detector has been run.",
        "",
        f"- Reachable: {len(reachable)} of {len(report.rows)}",
        f"- Reachable but rare: {len(gated)}",
        f"- Not reachable: {len(out)}",
        "",
        "## What this review can establish",
        "",
    ]
    lines += [f"- **{r.failure_id}** ({VERDICT_HEADING[str(r.verdict)]}): "
              f"{r.reason_detail}" for r in reachable] or ["- Nothing."]
    lines += ["", "## What it cannot", ""]
    lines += [f"- **{r.failure_id}**: {r.reason_detail}" for r in out] or ["- Nothing."]
    if gated:
        lines += ["", "## What it can reach only rarely", ""]
        lines += [f"- **{r.failure_id}**: {r.reason_detail}" for r in gated]
    lines += ["", "## Declared basis", "", f"> {report.chain_basis}", ""]
    return "\n".join(lines)


def render(report, manifest: LoadedManifest, engagement: str, profile: str,
           view: ClientView | None = None) -> dict[str, Any]:
    """Render under one profile. The client branch never sees ``report``."""
    if profile == "client":
        if view is None:
            raise ValueError("the client profile requires a projected view")
        return {"profile": "client", "markdown": client_markdown(view),
                "data": view.to_dict()}
    return {"profile": "internal",
            "markdown": internal_markdown(report, manifest, engagement),
            "data": report.to_dict()}
