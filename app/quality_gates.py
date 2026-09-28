"""Construction-tier finalization gates and the finalization certificate
(SPEC-bfo-agent-quality.md QS-G2, QS-G3, QS-E3).

Everything here reads the JSON produced by ``scripts/ontology_audit.py``.
The QS-G3 gates are construction-tier only, so they apply identically in
faithful and curated mode (FG-0): they measure the tool's rendering, never
the text's claims. Definition coverage is the one mode-dependent rule:
curated mode gates it, faithful mode counts ``definitionStatus
"absent-in-source"`` as covered and reports it.
"""
from __future__ import annotations

import importlib.util
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_AUDIT_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ontology_audit.py"

# QS-G3: metric -> (operator, threshold) that must HOLD for finalization.
CONSTRUCTION_GATES: tuple[tuple[str, str, float], ...] = (
    ("malformed_iris", "==", 0),
    ("mangled_standard_predicates", "==", 0),
    ("punned_triples", "==", 0),
    ("empty_restrictions", "==", 0),
    ("undeclared_properties_used", "==", 0),
    ("file_scheme_namespaces", "==", 0),
    ("visibility_ratio", ">=", 0.99),
)
CURATED_DEFINITION_MIN = 0.95

_OPS = {"==": lambda a, b: a == b, ">=": lambda a, b: a >= b,
        "<=": lambda a, b: a <= b}


@lru_cache(maxsize=1)
def audit_module():
    """scripts/ontology_audit.py loaded as a module (it is a script, not a
    package member)."""
    spec = importlib.util.spec_from_file_location("ontology_audit", _AUDIT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_audit(owl_path: str | Path) -> dict:
    """Run the read-only audit (QS-G1) on a saved ontology file."""
    return audit_module().audit(str(owl_path))


def audit_summary(report: dict) -> str:
    return audit_module().summary(report)


def metric(report: dict, key: str) -> float:
    """Numeric value of an audit metric; list-valued metrics count items."""
    v = report.get(key)
    if isinstance(v, (list, tuple, dict)):
        return len(v)
    return v if v is not None else 0


def construction_failures(report: dict) -> list[str]:
    """QS-G3 failures, e.g. ``["punned_triples == 0 (got 24)"]``."""
    out = []
    for key, op, want in CONSTRUCTION_GATES:
        got = metric(report, key)
        if not _OPS[op](got, want):
            out.append(f"{key} {op} {want:g} (got {got:g})")
    return out


def definition_check(report: dict, fidelity: str) -> tuple[bool, str]:
    """Curated: definition_coverage >= 0.95 gates. Faithful: report only,
    with absent-in-source counted as covered."""
    if fidelity == "faithful":
        cov = report.get("definition_coverage_faithful",
                         report.get("definition_coverage", 0.0))
        return True, (f"faithful mode: definition coverage {cov:.1%} "
                      f"(absent-in-source counted as covered); report only")
    cov = report.get("definition_coverage", 0.0)
    return (cov >= CURATED_DEFINITION_MIN,
            f"definition coverage {cov:.1%}; curated minimum is "
            f"{CURATED_DEFINITION_MIN:.0%}")


def certificate(report: dict, *, fidelity: str,
                verify: Optional[dict] = None,
                profiles: Optional[list[str]] = None) -> dict[str, Any]:
    """QS-E3 finalization certificate. ``verify`` is the
    ``OntologyManager.verify_full`` result when the reasoner was run."""
    verify = verify or {}
    return {
        "consistent": verify.get("consistent", verify.get("ok")),
        "unsatisfiable_classes": list(verify.get("unsat_classes")
                                      or verify.get("unsatisfiable") or []),
        "visibility_ratio": report.get("visibility_ratio"),
        "local_disjointness_axioms": report.get("local_disjointness_axioms"),
        "profiles_active": list(profiles or []),
        "fidelity_mode": fidelity,
        "construction_failures": construction_failures(report),
    }


def summary_line(report: dict) -> str:
    """One line for ntfy / interim reports (QS-G2)."""
    fails = construction_failures(report)
    head = (f"quality: visibility {report.get('visibility_ratio')}, "
            f"punned {metric(report, 'punned_triples')}, "
            f"malformed {metric(report, 'malformed_iris')}, "
            f"defs {report.get('definition_coverage')}, "
            f"local disjointness {report.get('local_disjointness_axioms')}")
    return head + ("; finalization blocked: " + "; ".join(fails) if fails
                   else "; construction gates pass")
