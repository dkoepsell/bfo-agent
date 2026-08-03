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
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config
from .. import recognition as rec
from .repair import repair_for_digestibility, serialise

log = logging.getLogger(__name__)

REPORT_SCHEMA = "2.0"

# R7. A section that ran and produced output which does not meet its own
# validity gates is not ok. Consumers must not aggregate from it without
# carrying the mark, so it needs a name of its own.
OK = "ok"
UNRELIABLE = "unreliable"
FAILED = "failed"
SKIPPED = "skipped"
NOT_APPLICABLE = "not_applicable"

STATUSES = (OK, UNRELIABLE, FAILED, SKIPPED, NOT_APPLICABLE)

STATUS_MEANING = {
    OK: "ran, and its output meets its own validity gates",
    UNRELIABLE: ("ran and produced output that does not meet its own validity "
                 "gates; do not aggregate from it without carrying this mark"),
    FAILED: "could not run to completion",
    SKIPPED: "did not run; this is an absence of evidence, not a clean result",
    NOT_APPLICABLE: "the artifact's declared kind puts this section out of scope",
}


@dataclass
class Gate:
    """One validity condition a section must meet to call its own output usable."""

    id: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "passed": self.passed, "detail": self.detail}


@dataclass
class Section:
    """One part of the report, and whether it could be produced at all."""

    id: str
    title: str
    status: str = OK
    data: Any = None
    error: str = ""
    seconds: float = 0.0
    gates: list[Gate] = field(default_factory=list)
    # A section skipped as a downstream consequence of an upstream error is not
    # a neutral gap. It records what caused it so the causal chain can be read.
    skipped_because: Optional[dict[str, str]] = None

    @property
    def gates_passed(self) -> int:
        return sum(1 for g in self.gates if g.passed)

    @property
    def gates_failed(self) -> list[Gate]:
        return [g for g in self.gates if not g.passed]

    def apply_gates(self) -> None:
        """Downgrade to unreliable when this section's own gates fail.

        Called after gates are attached. A section may not report ok while its
        own validity conditions are failing.
        """
        if self.status == OK and self.gates_failed:
            self.status = UNRELIABLE

    def to_dict(self) -> dict[str, Any]:
        blob: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "status_meaning": STATUS_MEANING.get(self.status, ""),
            "error": self.error,
            "seconds": round(self.seconds, 2),
            "gates": [g.to_dict() for g in self.gates],
            "gates_passed": self.gates_passed,
            "gates_total": len(self.gates),
            "data": self.data,
        }
        if self.skipped_because:
            blob["skipped_because"] = dict(self.skipped_because)
        return blob


@dataclass
class Provenance:
    """Where this artifact came from, stated where a reader will see it.

    A name referencing a specific legal instrument, alongside a null source text
    and a seed from an unrelated library member, invites a reader to infer text
    derivation. Burying that in the manifest is what let the inference stand.
    """

    derivation: str = "unknown"   # text_extraction | seeded_bootstrap | manual | hybrid
    source_text: Optional[str] = None
    source_text_sha256: Optional[str] = None
    seeded_from: Optional[str] = None
    fidelity: str = ""
    name_implies_source: bool = False
    implied_source: str = ""

    @property
    def needs_disclaimer(self) -> bool:
        return self.derivation != "text_extraction" and self.name_implies_source

    def to_dict(self) -> dict[str, Any]:
        return {
            "derivation": self.derivation,
            "source_text": self.source_text,
            "source_text_sha256": self.source_text_sha256,
            "seeded_from": self.seeded_from,
            "fidelity": self.fidelity,
            "name_implies_source": self.name_implies_source,
            "implied_source": self.implied_source,
        }

    def line(self) -> str:
        if not self.needs_disclaimer:
            return ""
        origin = (f"seeded bootstrap from `{self.seeded_from}`"
                  if self.seeded_from else self.derivation.replace("_", " "))
        implied = self.implied_source or "the source its name references"
        return (f"**Provenance:** {origin}. No source text was ingested. The name "
                f"references {implied}; nothing in this artifact was derived from "
                f"that text.")


@dataclass
class Report:
    ontology: str
    generated_at: str
    report_schema: str
    artifact_sha256: str
    sections: list[Section] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    kernel_registry_version: str = ""
    kernel_registry_digest: str = ""
    provenance: Provenance = field(default_factory=Provenance)
    sample_limit: int = 10

    def section(self, sid: str) -> Optional[Section]:
        for s in self.sections:
            if s.id == sid:
                return s
        return None

    def data(self, sid: str) -> Any:
        """The section's data, only when the section is usable.

        ``unreliable`` output is returned so it can be rendered, but every caller
        that aggregates must check the status. Rendering carries the mark.
        """
        s = self.section(sid)
        if not s or s.status in (FAILED, SKIPPED, NOT_APPLICABLE):
            return None
        return s.data

    def ids_with_status(self, status: str) -> list[str]:
        return [s.id for s in self.sections if s.status == status]

    @property
    def unreliable_sections(self) -> list[Section]:
        return [s for s in self.sections if s.status == UNRELIABLE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology": self.ontology,
            "generated_at": self.generated_at,
            "report_schema": self.report_schema,
            "artifact_sha256": self.artifact_sha256,
            "kernel_registry_version": self.kernel_registry_version,
            "kernel_registry_digest": self.kernel_registry_digest,
            "sample_limit": self.sample_limit,
            "provenance": self.provenance.to_dict(),
            "metrics": dict(self.metrics),
            "status_vocabulary": dict(STATUS_MEANING),
            "sections_ok": self.ids_with_status(OK),
            "sections_unreliable": self.ids_with_status(UNRELIABLE),
            "sections_failed": self.ids_with_status(FAILED),
            "sections_skipped": self.ids_with_status(SKIPPED),
            "sections_not_applicable": self.ids_with_status(NOT_APPLICABLE),
            "sections": [s.to_dict() for s in self.sections],
            "caveat": (
                "Every result here is about the artifact as delivered. The "
                "repaired variant shipped alongside is a separate artifact and "
                "no result in this report was computed from it."
            ),
        }


def _run(report: Report, sid: str, title: str, fn: Callable[[], Any],
         gates: Optional[Callable[[Any], list[Gate]]] = None) -> Section:
    section = Section(sid, title)
    start = time.monotonic()
    try:
        section.data = fn()
        if gates is not None:
            section.gates = gates(section.data)
    except Exception as e:  # one bad section must not sink the bundle
        section.status = FAILED
        section.error = f"{type(e).__name__}: {e}"
        log.warning("report section %s failed for %s: %s", sid, report.ontology, e)
    section.seconds = time.monotonic() - start
    section.apply_gates()
    report.sections.append(section)
    return section


def _skip(report: Report, sid: str, title: str, reason: str,
          because: Optional[dict[str, str]] = None,
          status: str = SKIPPED) -> Section:
    section = Section(sid, title, status=status, error=reason,
                      skipped_because=because)
    report.sections.append(section)
    return section


# Legal instruments a library name or description may reference. Used only to
# decide whether the provenance disclaimer is needed, never to infer content.
_SOURCE_PATTERNS = (
    (re.compile(r"\b(\d+)\s*CFR\s*(\d+)", re.I), lambda m: f"{m.group(1)} CFR {m.group(2)}"),
    (re.compile(r"\bCFR[- ]?(\w+)", re.I), lambda m: f"the CFR ({m.group(1)})"),
    (re.compile(r"\b(\d+)\s*U\.?S\.?C\.?\s*(\d+)", re.I),
     lambda m: f"{m.group(1)} USC {m.group(2)}"),
    (re.compile(r"\bDSM[- ]?5", re.I), lambda m: "DSM-5"),
    (re.compile(r"\bICD[- ]?(\d+)", re.I), lambda m: f"ICD-{m.group(1)}"),
)


def _implied_source(*texts: str) -> str:
    """The most specific instrument any of these texts names.

    Patterns are tried in specificity order across every text before the next
    pattern is tried at all, so a name matching the loose CFR pattern does not
    win over a description naming the exact part.
    """
    for pattern, render in _SOURCE_PATTERNS:
        for text in texts:
            m = pattern.search(text or "")
            if m:
                return render(m)
    return ""


def build_provenance(name: str, manifest: dict[str, Any]) -> Provenance:
    """Read provenance off the manifest and decide whether it needs stating."""
    manifest = manifest or {}
    seeded_from = manifest.get("seeded_from")
    source_text = manifest.get("source_text")
    derivation = manifest.get("derivation")
    if not derivation:
        derivation = ("text_extraction" if source_text else
                      "seeded_bootstrap" if seeded_from else "unknown")
    implied = _implied_source(name, str(manifest.get("description") or ""))
    return Provenance(
        derivation=str(derivation),
        source_text=source_text,
        source_text_sha256=manifest.get("source_text_sha256"),
        seeded_from=seeded_from,
        fidelity=str(manifest.get("fidelity") or ""),
        name_implies_source=bool(implied),
        implied_source=implied,
    )


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
                 sample: int = 25,
                 sample_limit: int = 10) -> Report:
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

    from ..kernel import load_registry
    from ..kernel.debt import compute_coverage, compute_debt
    from . import metrics as metrics_mod

    kernel_registry = load_registry()

    try:
        library_manifest = registry.manifest(name)
    except Exception:
        library_manifest = {}

    report = Report(
        ontology=name,
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        report_schema=REPORT_SCHEMA,
        artifact_sha256=artifact_sha256,
        kernel_registry_version=kernel_registry.version,
        kernel_registry_digest=kernel_registry.digest,
        provenance=build_provenance(name, library_manifest),
        sample_limit=sample_limit,
    )

    # F2. Counted once, at the top, with every name carrying its scope. No
    # section recomputes; sections reference these names.
    try:
        report.metrics = metrics_mod.from_paths(working, bfo_path).to_dict()
    except Exception as e:
        log.warning("metrics failed for %s: %s", name, e)
        report.metrics = {}

    named_classes = report.metrics.get("named_classes_local", 0)

    # --- what is in it ----------------------------------------------------
    _run(report, "manifest", "Library manifest", lambda: library_manifest)

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

    recognition_section = _run(
        report, "recognition_audit",
        "Typed findings, contradiction debt and completeness", recognition)

    # R1 and R2. Recompute the debt from the registry rather than trusting the
    # figure the audit carried, and split coverage out of it.
    def debt_and_coverage():
        raw = recognition_section.data or {}
        findings = list(raw.get("findings") or ())
        legacy = raw.get("contradiction_debt") or raw.get("debt") or {}

        # Full population per code. The findings list holds a sample, and a
        # sampled count must never read as the whole. kernel_audit's own counts
        # are the authoritative figure and are keyed by the reported code,
        # including subtypes, which is what lets the registry classify them.
        kernel_section = report.section("kernel_audit")
        totals: dict[str, int] = {}
        if kernel_section and kernel_section.status != FAILED and kernel_section.data:
            totals.update(
                {k: int(v) for k, v in
                 (kernel_section.data.get("finding_counts") or {}).items()})
        for code, count in (legacy.get("per_type") or {}).items():
            totals.setdefault(code, int(count))

        result = compute_debt(
            findings,
            registry=kernel_registry,
            named_classes=named_classes,
            totals=totals or None,
        )
        rows = compute_coverage(result, metrics_mod.Metrics(report.metrics),
                                kernel_registry)
        return {
            "contradiction_debt": result.to_dict(),
            "render_line": result.render_line(),
            "coverage": [r.to_dict() for r in rows],
            "completeness": legacy.get("completeness") or [],
        }

    if recognition_section.status == FAILED:
        _skip(report, "contradiction_debt", "Contradiction debt",
              "the audit that produces findings did not run",
              because={"section": "recognition_audit", "reason": "failed"})
        _skip(report, "coverage", "Coverage",
              "the audit that produces findings did not run",
              because={"section": "recognition_audit", "reason": "failed"})
    else:
        debt_section = _run(report, "contradiction_debt", "Contradiction debt",
                            debt_and_coverage)
        # R2 renders above the debt, so it gets its own section drawing on the
        # same computation rather than a second one.
        coverage_section = Section(
            "coverage", "Coverage",
            data={"rows": (debt_section.data or {}).get("coverage", [])}
            if debt_section.status != FAILED else None,
            status=OK if debt_section.status != FAILED else FAILED,
        )
        report.sections.insert(report.sections.index(debt_section), coverage_section)

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
        # R7. Not a neutral gap. This is a downstream consequence of an upstream
        # error with a determinate correct answer, and the causal chain is
        # recorded so a reader is not left to guess at it.
        reason = "no usable chain declaration, so reachability cannot be resolved"
        cause = "undeclared"
        if chain_section.data:
            reason = chain_section.data.get("message") or reason
            cause = chain_section.data.get("reason") or cause
        _skip(report, "scope", "Scope of review", reason,
              because={"section": "chain", "reason": cause})

    # R3. Dispersion measured over the full sets, never the published sample.
    def bind():
        from ..chain_dispersion import compute_dispersion, dispersion_gates

        closure = ap_probe.load_closure(working, bfo_path)
        result = ap_binding.bind(closure)
        dispersion = compute_dispersion(
            {locus: members for locus, members in result.per_locus.items()},
            named_classes=named_classes or result.named_classes,
            unbound_classes=result.unanchored_classes,
        )
        blob = result.to_dict()
        blob["dispersion"] = dispersion.to_dict()
        blob["_gates"] = dispersion_gates(dispersion)
        return blob

    _run(report, "binding", "Chain binding", bind,
         gates=lambda data: [Gate(g["id"], g["passed"], g["detail"])
                             for g in (data or {}).get("_gates", [])])

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
        f"Generated {report.generated_at}. Artifact `{report.artifact_sha256[:16]}`. "
        f"Report schema {report.report_schema}, kernel registry "
        f"{report.kernel_registry_version}.",
        "",
    ]

    # R8. Provenance immediately under the title, before anything else, when the
    # name would otherwise invite a reader to infer a source that was not read.
    provenance_line = report.provenance.line()
    if provenance_line:
        lines += [provenance_line, ""]

    lines += [
        "Every result below is about the artifact **as delivered**. The repaired "
        "variant in this bundle is a separate artifact and nothing here was "
        "computed from it.",
        "",
    ]

    # R7. If any section is unreliable, the reader is told before the numbers,
    # not after them.
    unreliable = report.unreliable_sections
    if unreliable:
        lines += [
            f"> **{len(unreliable)} section(s) ran but did not meet their own "
            f"validity gates** and are marked unreliable: "
            + ", ".join(f"`{s.id}`" for s in unreliable)
            + ". Their output is shown, and must not be aggregated without "
              "carrying that mark.",
            "",
        ]

    lines += ["## Sections", ""]
    lines += _table(
        ["Section", "Status", "Gates", "Seconds"],
        [[s.title, s.status,
          (f"{s.gates_passed}/{len(s.gates)} passed" if s.gates else "-"),
          f"{s.seconds:.1f}"] for s in report.sections])

    # R7. A section skipped because of an upstream error states the chain.
    caused = [s for s in report.sections if s.skipped_because]
    if caused:
        lines += ["", "### Why a section did not run", ""]
        for s in caused:
            because = s.skipped_because or {}
            lines.append(
                f"- **{s.title}** did not run because the "
                f"`{because.get('section')}` section reported "
                f"`{because.get('reason')}`. This is a downstream consequence of "
                f"an upstream error, not a neutral gap: {s.error}")

    if report.metrics:
        lines += ["", "## Statistics", "",
                  "Counted once, at bundle level. Every name carries its scope, "
                  "so a figure over the artifact alone and the same figure over "
                  "the artifact plus its imports cannot be confused.", ""]
        lines += _table(["Measure", "Value"],
                        [[k, v] for k, v in sorted(report.metrics.items())
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

    # R2. Coverage renders above the debt, because a coverage gap is not debt.
    coverage = report.data("coverage")
    if coverage and coverage.get("rows"):
        lines += ["", "## Coverage", "",
                  "The absence of an artifact-level attribute. These are gaps in "
                  "documentation, not conflicts in the axioms, and they are "
                  "reported separately from contradiction debt so that the size "
                  "of the ontology cannot enter the debt figure.", ""]
        lines += _table(["Attribute", "Present", "Missing", "Coverage"],
                        [[r["attribute"], r["present"], r["missing"],
                          f"{r['coverage_pct']}%"] for r in coverage["rows"]])

    debt_blob = report.data("contradiction_debt") or {}
    debt = debt_blob.get("contradiction_debt") or {}
    if debt:
        lines += ["", "## Contradiction debt", ""]
        # The rendering rule: never a bare number while the weights are
        # uncalibrated, and the coverage line immediately after it.
        lines += [debt_blob.get("render_line", ""), ""]
        excluded = debt.get("excluded_coverage") or {}
        if excluded:
            total = sum(excluded.values())
            named = debt.get("named_classes") or 0
            pct = round(100 * (named - total) / named, 1) if named else 0.0
            lines += [
                f"Definition coverage: {max(0, named - total)} of {named} classes "
                f"({pct}%). Reported separately, not counted as debt.",
                "",
            ]
        lines += [
            f"- Per class: {debt.get('cd_per_class')}",
            f"- Weights: {debt.get('weights_source')} "
            f"({'calibrated' if debt.get('calibrated') else 'uncalibrated'})",
            f"- Lower bound over instrumented defect primitives: "
            f"{debt.get('lower_bound')}",
            "",
        ]
        if debt.get("per_type"):
            lines += ["### By primitive", ""]
            weighted = debt.get("per_type_weighted") or {}
            lines += _table(
                ["Primitive", "Findings", "Weighted"],
                [[k, v, weighted.get(k, v)] for k, v in sorted(debt["per_type"].items())])
        if debt.get("saturated_primitives"):
            lines += ["", "### Excluded as saturated", "",
                      "A defect predicate firing on most of its eligible "
                      "population is measuring the population.", ""]
            lines += _table(
                ["Primitive", "Findings", "Population", "Fire rate"],
                [[s["code"], s["findings"], s["eligible_population"],
                  f"{s['fire_rate']:.0%}"] for s in debt["saturated_primitives"]])

        rows = [[r.get("code"), r.get("name"), r.get("stratum"), r.get("status"),
                 r.get("note", "")]
                for r in (debt_blob.get("completeness") or [])]
        if rows:
            lines += ["", "### What this run could see", ""]
            lines += _table(["Code", "Name", "Stratum", "Status", "Note"], rows)

    # R3. The matrix is rendered whenever a binding gate trips, because the
    # number that explains a smear is the one showing two links holding the
    # same classes.
    binding_section = report.section("binding")
    if binding_section and binding_section.gates_failed and binding_section.data:
        from ..chain_dispersion import Dispersion, render_jaccard

        blob = binding_section.data.get("dispersion") or {}
        lines += ["", "## Chain binding dispersion", "",
                  f"Status **{binding_section.status}**: "
                  f"{len(binding_section.gates_failed)} of "
                  f"{len(binding_section.gates)} gates failed.", ""]
        for gate in binding_section.gates:
            mark = "pass" if gate.passed else "**FAIL**"
            lines.append(f"- `{gate.id}` {mark}: {gate.detail}")
        lines += ["", "### Link overlap (Jaccard)", ""]
        lines += render_jaccard(Dispersion(
            bindings_total=blob.get("bindings_total", 0),
            bound_classes=blob.get("bound_classes", 0),
            unbound_classes=blob.get("unbound_classes", 0),
            bindings_per_class_mean=blob.get("bindings_per_class_mean", 0.0),
            bindings_per_class_median=blob.get("bindings_per_class_median", 0.0),
            bindings_per_class_max=blob.get("bindings_per_class_max", 0),
            exclusivity_rate=blob.get("exclusivity_rate", 0.0),
            capture_pct=blob.get("capture_pct", {}),
            jaccard=blob.get("jaccard", {}),
        ))
        if blob.get("top_multiplicity"):
            lines += ["", "### Classes bound to the most links", ""]
            lines += _table(["Class", "Links", "Bound to"],
                            [[t["class"].rsplit("#", 1)[-1], t["links"],
                              ", ".join(t["bound_to"])]
                             for t in blob["top_multiplicity"]])

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


class BundleManifestError(Exception):
    """The README would have described a file the bundle does not contain."""


# What each member is, keyed by filename. The README is generated from the
# actual write manifest rather than written by hand, which is what stopped
# scope-client.md being advertised in a bundle that did not contain it.
_MEMBER_DESCRIPTIONS = {
    "report.json": "every audit and statistic, machine readable",
    "report.md": "the same, readable",
    "repaired.owl": "a loadable variant of the ontology",
    "repairs.json": "what was changed to produce it, and what was not",
    "scope-client.md": "the coverage table, safe to hand to a client",
    "README.txt": "this file",
}


def _readme(report: Report, members: dict[str, str]) -> str:
    """The README, listing exactly what is in the bundle and nothing else."""
    width = max((len(n) for n in members), default=0) + 2
    listing = "\n".join(
        f"{name:<{width}}{_MEMBER_DESCRIPTIONS.get(name, 'bundle member')}"
        for name in sorted(members)
    )
    unreliable = report.unreliable_sections
    warning = ""
    if unreliable:
        warning = (
            "\nWARNING: " + str(len(unreliable)) + " section(s) ran but did not "
            "meet their own\nvalidity gates and are marked 'unreliable': "
            + ", ".join(s.id for s in unreliable) + ".\nDo not aggregate from "
            "them without carrying that mark.\n"
        )
    return (
        "Ontology report bundle\n"
        "======================\n\n"
        f"Ontology: {report.ontology}\n"
        f"Generated: {report.generated_at}\n"
        f"Artifact sha256: {report.artifact_sha256}\n"
        f"Report schema: {report.report_schema}\n"
        f"Kernel registry: {report.kernel_registry_version}\n"
        f"{warning}\n"
        f"{listing}\n\n"
        "Every audit result in this bundle is about the ontology AS\n"
        "DELIVERED. repaired.owl is a separate artifact. No result here was\n"
        "computed from it, and it is not a corrected ontology: it is the\n"
        "delivered one made loadable, with no claim added and no axiom\n"
        "removed. See the 'not_repaired' section of repairs.json for what\n"
        "was deliberately left alone.\n"
    )


def write_bundle(report: Report, repaired_owl: str,
                 scope_client_markdown: str = "") -> bytes:
    """The downloadable zip.

    Two gates run before anything is written. The bundle does not write if a
    metric name carries two values, and it does not write if the README would
    name a file that is not present.
    """
    from .metrics import duplicate_metric_gate

    payload = report.to_dict()

    collisions = duplicate_metric_gate(payload)
    if collisions:
        raise BundleManifestError(
            "the bundle was not written because a metric name carries more than "
            "one value:\n  " + "\n  ".join(collisions))

    members: dict[str, str] = {
        "report.json": json.dumps(payload, indent=2, default=str),
        "report.md": report_markdown(report),
        "repaired.owl": repaired_owl,
    }
    repair = report.data("repair")
    if repair:
        members["repairs.json"] = json.dumps(repair, indent=2)
    if scope_client_markdown:
        members["scope-client.md"] = scope_client_markdown

    members["README.txt"] = _readme(report, dict(members, **{"README.txt": ""}))

    # R6. The README is generated from this manifest, so the two cannot drift.
    # The assertion is kept anyway: it is cheap and it is the thing that failed.
    described = set(_MEMBER_DESCRIPTIONS) & set(members)
    missing = [n for n in described if n not in members]
    if missing:
        raise BundleManifestError(
            f"the README names files the bundle does not contain: {missing}")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for filename, content in members.items():
            z.writestr(filename, content)
    return buffer.getvalue()
