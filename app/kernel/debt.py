"""Contradiction debt, computed so that it stops counting the ontology's size.

The failure this replaces: on the reference artifact the debt read 1020.0, of
which 1009 was a single predicate firing once per named class because none of
them carried a definition. The debt figure was the class count. At the same time
the reasoner section reported the ontology consistent, coherent, with zero
unsatisfiable classes. Two sections of one bundle contradicted each other and
nothing noticed.

Four mechanisms keep that from recurring:

1. **Static exclusion.** Only ``kind: defect`` predicates are summed. Coverage
   predicates are excluded by what they are, not by a threshold, and are
   reported in their own section.
2. **Saturation guard.** A defect predicate firing on most of its eligible
   population is measuring the population. It is marked saturated, excluded from
   the core figure, and listed with its rate.
3. **Weights from the registry**, with the uncalibrated status carried into
   every rendering rather than dropped.
4. **Subject aggregation.** One finding naming 74 unrealized capacities is not
   the same size as one class missing an annotation, and the weighted figure
   says so.

``cd_core`` is a typed count over instrumented defect primitives, which is what
the rendering rule requires it to be called. The weighted view lives alongside
it rather than replacing it, because with uncalibrated weights the count is the
more defensible number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .registry import Registry, load_registry

DEFAULT_SATURATION_TAU = 0.5


@dataclass(frozen=True)
class SaturatedPrimitive:
    code: str
    findings: int
    eligible_population: int
    fire_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "findings": self.findings,
            "eligible_population": self.eligible_population,
            "fire_rate": round(self.fire_rate, 4),
            "reason": ("a defect predicate that fires on most of its eligible "
                       "population is measuring the population, not the defect"),
        }


@dataclass(frozen=True)
class DebtResult:
    cd_core: float
    cd_per_class: float
    calibrated: bool
    lower_bound: bool
    weights_source: str
    registry_version: str
    named_classes: int
    per_type: dict[str, int] = field(default_factory=dict)
    per_type_weighted: dict[str, float] = field(default_factory=dict)
    per_locus: dict[str, int] = field(default_factory=dict)
    excluded_coverage: dict[str, int] = field(default_factory=dict)
    saturated_primitives: list[SaturatedPrimitive] = field(default_factory=list)
    attributed_to_translation: int = 0
    attribution_undetermined: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cd_core": self.cd_core,
            "cd_per_class": self.cd_per_class,
            "calibrated": self.calibrated,
            "lower_bound": self.lower_bound,
            "weights_source": self.weights_source,
            "kernel_registry_version": self.registry_version,
            "named_classes": self.named_classes,
            "per_type": dict(self.per_type),
            "per_type_weighted": dict(self.per_type_weighted),
            "per_locus": dict(self.per_locus),
            "excluded_coverage": dict(self.excluded_coverage),
            "saturated_primitives": [s.to_dict() for s in self.saturated_primitives],
            "attributed_to_translation": self.attributed_to_translation,
            "attribution_undetermined": self.attribution_undetermined,
        }

    def render_line(self) -> str:
        """The debt figure, never as a bare number while the weights are uncalibrated."""
        if self.calibrated:
            return f"Debt (weighted, calibrated): {self.cd_core:g}"
        return (
            "Debt (typed count, uncalibrated weights, lower bound over "
            f"instrumented defect primitives): {self.cd_core:g}"
        )


def compute_debt(findings: Iterable[dict],
                 *,
                 registry: Optional[Registry] = None,
                 named_classes: int = 0,
                 eligible_population: Optional[dict[str, int]] = None,
                 totals: Optional[dict[str, int]] = None,
                 saturation_tau: float = DEFAULT_SATURATION_TAU) -> DebtResult:
    """Contradiction debt over ``findings``.

    ``eligible_population`` gives, per code, how many entities the predicate
    could have fired on. Without it a predicate cannot be tested for saturation,
    so the guard falls back to the named class count, which is the right
    denominator for the class-scoped predicates that caused the problem.
    """
    registry = registry or load_registry()
    findings = list(findings or ())
    eligible_population = dict(eligible_population or {})

    # An unregistered code is a build error, not something to skip past.
    registry.validate_codes(f.get("kernel_code") for f in findings)

    per_type: dict[str, int] = {}
    per_type_subjects: dict[str, int] = {}
    per_locus: dict[str, int] = {}
    excluded_coverage: dict[str, int] = {}
    translation = 0
    undetermined = 0

    for finding in findings:
        code = (finding.get("kernel_code") or "").strip()
        if not code:
            continue
        primitive = registry.get(code)

        attribution = (finding.get("attribution") or "undetermined").strip()
        if attribution == "translation":
            # Never folded into the debt of the source artifact.
            translation += 1
            continue

        if primitive.is_coverage:
            excluded_coverage[code] = excluded_coverage.get(code, 0) + 1
            continue

        if attribution == "undetermined":
            undetermined += 1

        per_type[code] = per_type.get(code, 0) + 1
        subjects = finding.get("subject_count")
        if subjects is None:
            subjects = len(finding.get("subjects") or ()) or 1
        per_type_subjects[code] = per_type_subjects.get(code, 0) + int(subjects)

        locus = finding.get("locus")
        if locus:
            per_locus[str(locus)] = per_locus.get(str(locus), 0) + 1

    # A caller that sampled findings but knows the full population says so here.
    for code, full in (totals or {}).items():
        if code not in registry:
            continue
        primitive = registry.get(code)
        if primitive.is_coverage:
            excluded_coverage[code] = max(excluded_coverage.get(code, 0), int(full))
            continue
        seen = per_type.get(code, 0)
        if full > seen:
            per_type[code] = int(full)
            per_type_subjects[code] = max(per_type_subjects.get(code, 0), int(full))
            undetermined += int(full) - seen

    # Saturation: a defect predicate that fires on most of its population.
    saturated: list[SaturatedPrimitive] = []
    for code, count in sorted(per_type.items()):
        population = eligible_population.get(code, named_classes)
        if not population:
            continue
        rate = count / population
        if rate > saturation_tau:
            saturated.append(SaturatedPrimitive(code, count, population, rate))

    saturated_codes = {s.code for s in saturated}

    cd_core = 0.0
    per_type_weighted: dict[str, float] = {}
    for code, count in per_type.items():
        primitive = registry.get(code)
        if code in saturated_codes:
            continue
        cd_core += primitive.weight * count
        subjects = per_type_subjects.get(code, count)
        per_type_weighted[code] = primitive.weight * (
            subjects if primitive.aggregates_subjects else count)

    calibrated = all(p.calibrated for p in registry.defects()) and bool(registry.defects())

    return DebtResult(
        cd_core=round(cd_core, 4),
        cd_per_class=round(cd_core / named_classes, 4) if named_classes else 0.0,
        calibrated=calibrated,
        lower_bound=True,
        weights_source=registry.weights_source,
        registry_version=registry.version,
        named_classes=named_classes,
        per_type=per_type,
        per_type_weighted=per_type_weighted,
        per_locus=per_locus,
        excluded_coverage=excluded_coverage,
        saturated_primitives=saturated,
        attributed_to_translation=translation,
        attribution_undetermined=undetermined,
    )


# --------------------------------------------------------------------------
# The coverage section (R2)
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CoverageRow:
    attribute: str
    code: str
    present: int
    missing: int

    @property
    def total(self) -> int:
        return self.present + self.missing

    @property
    def pct(self) -> float:
        return round(100 * self.present / self.total, 2) if self.total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "attribute": self.attribute,
            "code": self.code,
            "present": self.present,
            "missing": self.missing,
            "total": self.total,
            "coverage_pct": self.pct,
        }


def compute_coverage(debt: DebtResult, metrics,
                     registry: Optional[Registry] = None) -> list[CoverageRow]:
    """Coverage gaps as their own measure, never as debt.

    Built from the metrics provider rather than from the finding list, so one
    row stands for a thousand identical findings. A list of 1009 rows saying the
    same thing is not a findings list.
    """
    registry = registry or load_registry()
    named = metrics.get("named_classes_local", 0)
    rows = [
        CoverageRow(
            "IAO definition", "K-A3b",
            metrics.get("classes_with_iao_definition_local", 0),
            named - metrics.get("classes_with_iao_definition_local", 0)),
        CoverageRow(
            "equivalentClass", "K-A3b",
            metrics.get("classes_with_equivalent_class_local", 0),
            named - metrics.get("classes_with_equivalent_class_local", 0)),
        CoverageRow(
            "BFO anchoring (direct)", "",
            metrics.get("classes_anchored_direct", 0),
            named - metrics.get("classes_anchored_direct", 0)),
    ]
    return rows
