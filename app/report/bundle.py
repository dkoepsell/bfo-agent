"""Gather every audit and statistic for one ontology into a single bundle.

Each section is collected independently and a failure in one is recorded rather
than allowed to sink the report. A bundle that is missing the reasoner section
and says so is far more useful than a 500, and it keeps the same discipline the
rest of this system has: a section that could not be run is reported as not run,
never as empty.
"""
from __future__ import annotations

import io
import json
import logging
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config
from .. import recognition as rec
from .repair import repair_for_digestibility, serialise

log = logging.getLogger(__name__)

REPORT_SCHEMA = "1.0"


@dataclass
class Section:
    """One part of the report, and whether it could be produced at all."""

    id: str
    title: str
    status: str = "ok"          # ok | failed | skipped
    data: Any = None
    error: str = ""
    seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "error": self.error,
            "seconds": round(self.seconds, 2),
            "data": self.data,
        }


@dataclass
class Report:
    ontology: str
    generated_at: str
    report_schema: str
    artifact_sha256: str
    sections: list[Section] = field(default_factory=list)

    def section(self, sid: str) -> Optional[Section]:
        for s in self.sections:
            if s.id == sid:
                return s
        return None

    def data(self, sid: str) -> Any:
        s = self.section(sid)
        return s.data if s and s.status == "ok" else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology": self.ontology,
            "generated_at": self.generated_at,
            "report_schema": self.report_schema,
            "artifact_sha256": self.artifact_sha256,
            "sections_ok": [s.id for s in self.sections if s.status == "ok"],
            "sections_failed": [s.id for s in self.sections if s.status == "failed"],
            "sections_skipped": [s.id for s in self.sections if s.status == "skipped"],
            "sections": [s.to_dict() for s in self.sections],
            "caveat": (
                "Every result here is about the artifact as delivered. The "
                "repaired variant shipped alongside is a separate artifact and "
                "no result in this report was computed from it."
            ),
        }


def _run(report: Report, sid: str, title: str, fn: Callable[[], Any]) -> Section:
    section = Section(sid, title)
    start = time.monotonic()
    try:
        section.data = fn()
    except Exception as e:  # one bad section must not sink the bundle
        section.status = "failed"
        section.error = f"{type(e).__name__}: {e}"
        log.warning("report section %s failed for %s: %s", sid, report.ontology, e)
    section.seconds = time.monotonic() - start
    report.sections.append(section)
    return section


def _declared_profile(registry, name: str) -> rec.DomainProfile:
    declared = registry.recognition_profile(name)
    profile = rec.profile_for(declared.get("domain"))
    if (declared.get("act_thickness") != profile.act_thickness
            or declared.get("repair") != profile.repair):
        from dataclasses import replace
        profile = replace(profile,
                          act_thickness=declared.get("act_thickness"),
                          repair=declared.get("repair"))
    return profile


def build_report(name: str, registry, manager,
                 run_reasoner: bool = True,
                 sample: int = 25) -> Report:
    """Collect every audit and statistic for one ontology."""
    from ..aperture import binding as ap_binding
    from ..aperture import chain as ap_chain
    from ..aperture import probe as ap_probe
    from ..aperture.loader import load_manifest
    from ..aperture.resolve import resolve as ap_resolve

    working = str(manager.working_path)
    bfo_path = str(config.BFO_PATH)

    try:
        artifact_sha256 = ap_probe._sha256(Path(working))
    except OSError:
        artifact_sha256 = ""

    report = Report(
        ontology=name,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        report_schema=REPORT_SCHEMA,
        artifact_sha256=artifact_sha256,
    )

    # --- what is in it ----------------------------------------------------
    _run(report, "manifest", "Library manifest", lambda: registry.manifest(name))
    _run(report, "statistics", "Statistics", lambda: manager.stats())

    # --- structural and reasoner audits -----------------------------------
    def kernel():
        from .. import kernel_audit

        return kernel_audit.audit(working, bfo_path, sample=sample)

    _run(report, "kernel_audit", "Contradiction kernel, structural", kernel)

    profile = _declared_profile(registry, name)

    def recognition():
        from .. import recognition_audit

        return recognition_audit.audit(working, profile, bfo_path,
                                       run_reasoner=run_reasoner, sample=sample)

    _run(report, "recognition_audit",
         "Typed findings, contradiction debt and completeness", recognition)

    def reasoner():
        from .. import coherence_reason

        return coherence_reason.coherence_check(working)

    if run_reasoner:
        _run(report, "coherence", "Reasoner", reasoner)
    else:
        report.sections.append(Section("coherence", "Reasoner", status="skipped",
                                       error="the reasoner was not requested"))

    def ip():
        from .. import ip_gate

        violations = ip_gate.check_ip(working)
        return {"violations": violations[:sample], "total": len(violations)}

    _run(report, "ip_gate", "Third-party prose gate", ip)

    _run(report, "recognition_profile", "Declared institution",
         lambda: registry.recognition_profile(name))

    # --- what a review of it could establish ------------------------------
    probe_report = None

    def preconditions():
        nonlocal probe_report
        probe_report = ap_probe.run(working, bfo_path=bfo_path)
        return probe_report.to_dict()

    _run(report, "preconditions", "Preconditions", preconditions)

    def chain_status():
        return registry.chain_status(name)

    chain_section = _run(report, "chain", "Chain declaration", chain_status)

    def scope():
        block = registry.chain_block(name)
        declaration = ap_chain.validate(
            block, engagement=name, domain=profile.key,
            declared_act_thickness=profile.act_thickness,
            declared_repair=profile.repair,
            artifact_sha256=artifact_sha256,
        )
        closure = ap_probe.load_closure(working, bfo_path)
        advisories = ap_chain.cross_check(
            declaration, ap_binding.bind(closure), artifact_sha256)
        resolution = ap_resolve(load_manifest(), probe_report, declaration, advisories)
        return resolution.to_dict()

    usable = bool(chain_section.data and chain_section.data.get("usable"))
    if usable and probe_report is not None:
        _run(report, "scope", "Scope of review", scope)
    else:
        reason = "no usable chain declaration, so reachability cannot be resolved"
        if chain_section.data and chain_section.data.get("message"):
            reason = chain_section.data["message"]
        report.sections.append(Section(
            "scope", "Scope of review", status="skipped", error=reason))

    def bind():
        closure = ap_probe.load_closure(working, bfo_path)
        return ap_binding.bind(closure).to_dict()

    _run(report, "binding", "Chain binding", bind)

    # --- the digestible variant -------------------------------------------
    _run(report, "repair", "Digestibility repairs",
         lambda: repair_for_digestibility(working).to_dict())

    return report


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c).replace("|", "\\|") for c in row) + " |")
    return out


def report_markdown(report: Report) -> str:
    lines = [
        f"# Ontology report: {report.ontology}",
        "",
        f"Generated {report.generated_at}. Artifact `{report.artifact_sha256[:16]}`.",
        "",
        "Every result below is about the artifact **as delivered**. The repaired "
        "variant in this bundle is a separate artifact and nothing here was "
        "computed from it.",
        "",
        "## Sections",
        "",
    ]
    lines += _table(["Section", "Status", "Seconds"],
                    [[s.title, s.status, f"{s.seconds:.1f}"] for s in report.sections])

    stats = report.data("statistics")
    if stats:
        lines += ["", "## Statistics", ""]
        lines += _table(["Measure", "Value"],
                        [[k, v] for k, v in sorted(stats.items())
                         if not isinstance(v, (dict, list))])

    kernel = report.data("kernel_audit")
    if kernel:
        lines += ["", "## Structural audit", ""]
        lines += _table(["Metric", "Value"],
                        [[k, v] for k, v in sorted((kernel.get("metrics") or {}).items())])
        counts = kernel.get("finding_counts") or {}
        if counts:
            lines += ["", "### Findings by primitive", ""]
            lines += _table(["Primitive", "Count"],
                            [[k, v] for k, v in sorted(counts.items())])

    recognition = report.data("recognition_audit")
    if recognition:
        debt = recognition.get("contradiction_debt") or recognition.get("debt") or {}
        lines += ["", "## Contradiction debt", ""]
        if debt:
            lines += [
                f"- Debt: {debt.get('cd')} "
                f"({'calibrated' if debt.get('calibrated') else 'uncalibrated, so a typed count'})",
                f"- Lower bound over instrumented primitives only: {debt.get('lower_bound')}",
                "",
            ]
            rows = [[r.get("code"), r.get("name"), r.get("stratum"), r.get("status"),
                     r.get("note", "")]
                    for r in (debt.get("completeness") or [])]
            if rows:
                lines += ["### What this run could see", ""]
                lines += _table(["Code", "Name", "Stratum", "Status", "Note"], rows)

    scope = report.data("scope")
    if scope:
        lines += ["", "## Scope of review", ""]
        lines += [f"- Counts: {scope.get('counts')}", ""]
        lines += _table(["Failure", "Verdict", "Basis", "Reason"],
                        [[r["failure_id"], r["verdict"], r["basis"], r["reason_detail"]]
                         for r in scope.get("rows", [])])
    else:
        section = report.section("scope")
        lines += ["", "## Scope of review", "",
                  f"Not resolved: {section.error if section else 'unavailable'}", ""]

    repair = report.data("repair")
    if repair:
        lines += ["", "## Digestibility repairs", "",
                  repair["guarantee"], "",
                  f"Triples {repair['triples_before']} to {repair['triples_after']}, "
                  f"{repair['total_changes']} changes.", ""]
        applied = repair.get("repairs") or []
        if applied:
            lines += _table(["Repair", "Count", "What it did"],
                            [[r["id"], r["count"], r["description"]] for r in applied])
        else:
            lines += ["No repair was needed: the file already loads as delivered.", ""]
        lines += ["", "### Left alone, and why", ""]
        lines += _table(["Defect class", "Reason"],
                        [[k, v] for k, v in sorted(repair["not_repaired"].items())])

    failed = [s for s in report.sections if s.status == "failed"]
    if failed:
        lines += ["", "## Sections that could not be produced", ""]
        lines += [f"- **{s.title}**: {s.error}" for s in failed]

    lines += ["", "---", "",
              "A section reported as not run is not a clean result. It is an "
              "absence of evidence, and it is recorded as such deliberately.", ""]
    return "\n".join(lines)


def write_bundle(report: Report, repaired_owl: str,
                 scope_client_markdown: str = "") -> bytes:
    """The downloadable zip."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.json", json.dumps(report.to_dict(), indent=2, default=str))
        z.writestr("report.md", report_markdown(report))
        z.writestr("repaired.owl", repaired_owl)
        repair = report.data("repair")
        if repair:
            z.writestr("repairs.json", json.dumps(repair, indent=2))
        if scope_client_markdown:
            z.writestr("scope-client.md", scope_client_markdown)
        z.writestr("README.txt", (
            "Ontology report bundle\n"
            "======================\n\n"
            f"Ontology: {report.ontology}\n"
            f"Generated: {report.generated_at}\n"
            f"Artifact sha256: {report.artifact_sha256}\n\n"
            "report.json      every audit and statistic, machine readable\n"
            "report.md        the same, readable\n"
            "repaired.owl     a loadable variant of the ontology\n"
            "repairs.json     what was changed to produce it, and what was not\n"
            "scope-client.md  the coverage table, safe to hand to a client\n\n"
            "Every audit result in this bundle is about the ontology AS\n"
            "DELIVERED. repaired.owl is a separate artifact. No result here was\n"
            "computed from it, and it is not a corrected ontology: it is the\n"
            "delivered one made loadable, with no claim added and no axiom\n"
            "removed. See the 'not_repaired' section of repairs.json for what\n"
            "was deliberately left alone.\n"
        ))
    return buffer.getvalue()
