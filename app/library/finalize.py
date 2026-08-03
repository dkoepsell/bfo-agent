"""Finalize is a gated transition, not a flag.

The reference artifact reached ``status: finalized`` while carrying a live
thickness contradiction, zero definition coverage, thirteen unbound classes and
thirty-one undeclared IRIs. Nothing stopped it, because finalizing was setting a
field.

Six gates. Four cannot be waived, because an artifact that fails them is not
internally coherent and no reason makes it so. Two can be waived with a written
reason, because they are judgments about completeness rather than about
correctness, and a project may legitimately decide to freeze something
incomplete. A waiver is recorded in the manifest and reproduced verbatim in the
report, so an unexplained waiver is not possible.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

DEFINITION_MIN = 0.60
MLC_MIN_PER_LINK = 3

# gate id -> waivable
GATES: dict[str, bool] = {
    "chain.consistent": False,
    "declarations.complete": False,
    "imports.resolve": False,
    "reasoner.coherent": False,
    "definitions.coverage": True,
    "mlc.anchoring": True,
}


@dataclass(frozen=True)
class Waiver:
    gate: str
    reason: str
    waived_by: str
    waived_on: str

    @classmethod
    def from_dict(cls, raw: dict) -> "Waiver":
        return cls(
            gate=str(raw.get("gate") or ""),
            reason=str(raw.get("reason") or ""),
            waived_by=str(raw.get("waived_by") or ""),
            waived_on=str(raw.get("waived_on") or ""),
        )

    @property
    def valid(self) -> bool:
        """A waiver with no reason is not a waiver."""
        return bool(self.gate and self.reason.strip() and self.waived_by.strip())

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate, "reason": self.reason,
            "waived_by": self.waived_by, "waived_on": self.waived_on,
        }


@dataclass
class GateResult:
    id: str
    passed: bool
    detail: str = ""
    waivable: bool = False
    waiver: Optional[Waiver] = None

    @property
    def waived(self) -> bool:
        return bool(self.waiver and self.waiver.valid and self.waivable
                    and not self.passed)

    @property
    def blocking(self) -> bool:
        return not self.passed and not self.waived

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "passed": self.passed,
            "detail": self.detail,
            "waivable": self.waivable,
            "waived": self.waived,
            "blocking": self.blocking,
            "waiver": self.waiver.to_dict() if self.waiver else None,
        }


@dataclass
class GateReport:
    ontology: str
    gates: list[GateResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[GateResult]:
        return [g for g in self.gates if g.blocking]

    @property
    def waived(self) -> list[GateResult]:
        return [g for g in self.gates if g.waived]

    @property
    def finalizable(self) -> bool:
        return not self.blocking and not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ontology": self.ontology,
            "finalizable": self.finalizable,
            "blocking": [g.id for g in self.blocking],
            "waived": [g.id for g in self.waived],
            "gates": [g.to_dict() for g in self.gates],
            "errors": list(self.errors),
            "note": ("A waivable gate may be waived only with a written reason, "
                     "which is recorded in the manifest and reproduced in the "
                     "report. An unexplained waiver is not possible."),
        }

    def render(self) -> list[str]:
        lines = [f"Finalize check: {self.ontology}", ""]
        for g in self.gates:
            if g.passed:
                mark = "pass"
            elif g.waived:
                mark = "WAIVED"
            else:
                mark = "FAIL"
            lines.append(f"- `{g.id}` {mark}: {g.detail}")
            if g.waiver and g.waiver.valid:
                lines.append(
                    f"    waived by {g.waiver.waived_by} on {g.waiver.waived_on}: "
                    f"{g.waiver.reason}")
        for err in self.errors:
            lines.append(f"- could not be checked: {err}")
        lines.append("")
        lines.append("Finalizable." if self.finalizable else
                     "Not finalizable: " + ", ".join(g.id for g in self.blocking))
        return lines


def _waivers(manifest: dict) -> dict[str, Waiver]:
    out: dict[str, Waiver] = {}
    for raw in manifest.get("finalize_waivers") or ():
        waiver = Waiver.from_dict(raw)
        if waiver.gate:
            out[waiver.gate] = waiver
    return out


def check_finalizable(registry, name: str,
                      *,
                      definition_min: float = DEFINITION_MIN,
                      mlc_min_per_link: int = MLC_MIN_PER_LINK,
                      run_reasoner: bool = True) -> GateReport:
    """Run every finalize gate for one ontology.

    Never raises for a gate that cannot be evaluated: the reason is recorded in
    ``errors`` and the report is not finalizable, because a gate that could not
    run is not a gate that passed.
    """
    from .. import config
    from ..report import metrics as metrics_mod

    report = GateReport(ontology=name)

    try:
        manifest = registry.manifest(name)
    except Exception as e:
        report.errors.append(f"manifest unreadable: {e}")
        return report

    waivers = _waivers(manifest)

    def add(gate_id: str, passed: bool, detail: str) -> None:
        report.gates.append(GateResult(
            id=gate_id, passed=passed, detail=detail,
            waivable=GATES.get(gate_id, False),
            waiver=waivers.get(gate_id),
        ))

    try:
        manager = registry.get(name)
        working = Path(manager.working_path)
    except Exception as e:
        report.errors.append(f"working ontology unavailable: {e}")
        return report

    # --- chain.consistent --------------------------------------------------
    try:
        status = registry.chain_status(name)
        if status.get("usable"):
            add("chain.consistent", True,
                "the declared links and the declared thickness agree")
        else:
            reason = status.get("reason") or "undeclared"
            add("chain.consistent", False,
                f"{reason}: {status.get('message') or 'the chain is not usable'}")
    except Exception as e:
        report.errors.append(f"chain.consistent could not be checked: {e}")

    # --- declarations.complete --------------------------------------------
    try:
        from ..report.repair import repair_for_digestibility

        repair = repair_for_digestibility(str(working))
        undeclared = sum(
            r.count for r in repair.repairs if r.id.startswith("declare-"))
        add("declarations.complete", undeclared == 0,
            (f"{undeclared} IRIs are used but never typed. An artifact whose "
             f"entities are undeclared is outside OWL 2 DL and no reasoner "
             f"result about it is defined." if undeclared else
             "every used IRI is typed"))
    except Exception as e:
        report.errors.append(f"declarations.complete could not be checked: {e}")

    # --- imports.resolve ---------------------------------------------------
    try:
        add(*_check_imports(working, config))
    except Exception as e:
        report.errors.append(f"imports.resolve could not be checked: {e}")

    # --- reasoner.coherent -------------------------------------------------
    if run_reasoner:
        try:
            from .. import coherence_reason

            result = coherence_reason.coherence_check(str(working))
            consistent = bool(result.get("consistent", False))
            unsat = len(result.get("unsatisfiable") or result.get("incoherent") or ())
            add("reasoner.coherent", consistent and unsat == 0,
                (f"consistent={consistent}, unsatisfiable={unsat}"))
        except Exception as e:
            report.errors.append(f"reasoner.coherent could not be checked: {e}")
    else:
        report.errors.append(
            "reasoner.coherent was not run; a gate that did not run is not a "
            "gate that passed")

    # --- definitions.coverage ---------------------------------------------
    try:
        metrics = metrics_mod.from_paths(working)
        named = metrics.get("named_classes_local", 0)
        defined = max(metrics.get("classes_with_iao_definition_local", 0),
                      metrics.get("classes_with_equivalent_class_local", 0))
        coverage = (defined / named) if named else 0.0
        add("definitions.coverage", coverage >= definition_min,
            f"{defined} of {named} classes carry a definition "
            f"({coverage:.1%}); minimum is {definition_min:.0%}")
    except Exception as e:
        report.errors.append(f"definitions.coverage could not be checked: {e}")

    # --- mlc.anchoring -----------------------------------------------------
    try:
        add(*_check_mlc(registry, name, working, mlc_min_per_link))
    except Exception as e:
        report.errors.append(f"mlc.anchoring could not be checked: {e}")

    return report


def _check_imports(working: Path, config) -> tuple[str, bool, str]:
    """Every imported ontology resolves and its terms are declared.

    The observed failure was a build defect rather than an ontology defect:
    fifteen of nineteen class declarations and all twelve property declarations
    the repair pass added were BFO IRIs, because the BFO import was not
    resolving at serialization time. That should never reach a bundle.
    """
    import rdflib
    from rdflib import OWL, RDF, URIRef

    g = rdflib.Graph()
    g.parse(str(working))

    imports = [str(o) for o in g.objects(None, OWL.imports) if isinstance(o, URIRef)]
    if not imports:
        return ("imports.resolve", True, "the artifact imports nothing")

    bfo_path = Path(getattr(config, "BFO_PATH", ""))
    unresolved: list[str] = []
    undeclared_imported = 0

    declared = {s for s in g.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
    declared |= {s for s in g.subjects(RDF.type, OWL.ObjectProperty)
                 if isinstance(s, URIRef)}

    for iri in imports:
        if "bfo" in iri.lower():
            if not bfo_path.exists():
                unresolved.append(iri)
            continue
        unresolved.append(iri)

    # Terms from an import that the artifact uses but does not carry.
    used_foreign = {
        n for n in g.all_nodes()
        if isinstance(n, URIRef) and str(n).startswith(
            "http://purl.obolibrary.org/obo/BFO_")
    }
    undeclared_imported = len(used_foreign - declared)

    passed = not unresolved and undeclared_imported == 0
    detail = "every import resolves and its terms are declared"
    if unresolved:
        detail = f"unresolved imports: {unresolved}"
    elif undeclared_imported:
        detail = (f"{undeclared_imported} imported terms are used but not "
                  f"declared in the artifact. This is a build defect, not an "
                  f"ontology defect: the import is not being materialized at "
                  f"serialization.")
    return ("imports.resolve", passed, detail)


def _check_mlc(registry, name: str, working: Path,
               min_per_link: int) -> tuple[str, bool, str]:
    """MLC anchoring is asserted, never inferred.

    Only applies to an institutional domain. A scientific reference ontology has
    no chain and the gate does not apply to it.
    """
    from .. import recognition as rec
    from ..mlc_anchor import read_asserted_links

    declared = registry.recognition_profile(name)
    profile = rec.profile_for(declared.get("domain"))
    if not profile.has_chain and declared.get("act_thickness", "none") == "none":
        return ("mlc.anchoring", True,
                "no chain is declared, so there is nothing to anchor")

    asserted = read_asserted_links(str(working))
    thin = sorted(locus for locus in (l.value for l in rec.LOCUS_ORDER)
                  if len(asserted.get(locus, ())) < min_per_link)

    exclusive_required = ("authority", "assessor", "remedy")
    missing_exclusive = []
    for locus in exclusive_required:
        members = set(asserted.get(locus, ()))
        others: set[str] = set()
        for other, group in asserted.items():
            if other != locus:
                others |= set(group)
        if not (members - others):
            missing_exclusive.append(locus)

    passed = not thin and not missing_exclusive
    if passed:
        detail = "every link carries asserted members, with exclusive members where required"
    else:
        parts = []
        if thin:
            parts.append(f"links with fewer than {min_per_link} asserted members: "
                         f"{thin}")
        if missing_exclusive:
            parts.append(f"links with no member exclusive to them: "
                         f"{missing_exclusive}")
        detail = ("; ".join(parts) + ". Links are asserted with sool:mlcLink, "
                  "never inferred: the auditor does not manufacture domain "
                  "structure.")
    return ("mlc.anchoring", passed, detail)


def triage_library(registry, *, run_reasoner: bool = False) -> dict[str, Any]:
    """Run the gates across the whole library and report what would fail.

    The spec asks for this before enforcement, because C3 and C4 will fail
    existing members and that is the point. A triage report says which and why,
    so the failures are a work list rather than a surprise.
    """
    results: dict[str, Any] = {}
    for entry in registry.list_ontologies():
        name = entry.get("name") if isinstance(entry, dict) else str(entry)
        if not name:
            continue
        try:
            report = check_finalizable(registry, name, run_reasoner=run_reasoner)
            results[name] = report.to_dict()
        except Exception as e:  # a triage run must survive one bad member
            log.warning("triage failed for %s: %s", name, e)
            results[name] = {"ontology": name, "finalizable": False,
                             "errors": [str(e)]}

    blocking_counts: dict[str, int] = {}
    for blob in results.values():
        for gate in blob.get("blocking") or ():
            blocking_counts[gate] = blocking_counts.get(gate, 0) + 1

    return {
        "checked": len(results),
        "finalizable": sum(1 for b in results.values() if b.get("finalizable")),
        "blocking_by_gate": dict(sorted(blocking_counts.items(),
                                        key=lambda kv: -kv[1])),
        "ontologies": results,
    }
