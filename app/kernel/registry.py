"""The primitive registry: one place that says what the kernel predicates are.

Loaded from ``primitives.yaml``. Every primitive and every subtype is an entry,
so a code that appears in a findings table and a code that appears in a
completeness table are drawn from the same set by construction.

Two rules here are enforcement rather than description:

* A finding carrying a code the registry does not know is a **build error**, not
  a warning. That is what let ``K-A3b`` show up among the findings while the
  completeness table listed only the twelve top-level primitives.
* A ``coverage`` predicate can never contribute to contradiction debt. The
  exclusion is structural, not a threshold, because a predicate that detects the
  absence of an attribute will fire once per entity and so measures the size of
  the ontology.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import yaml

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent / "primitives.yaml"

DEFECT = "defect"
COVERAGE = "coverage"
KINDS = (DEFECT, COVERAGE)

# What a predicate needs in order to run. A predicate whose requirement is not
# met reports silent_by_principle rather than zero, because zero would read as a
# measurement.
REQUIREMENTS = (
    "structural",     # the axioms themselves
    "reasoner",       # a description-logic reasoner
    "definitions",    # defined classes must exist for the test to mean anything
    "acts",           # occurrents must be present
    "world_data",     # evidence from outside the artifact
    "prose",          # a reading of what terms mean
)

STRATA = ("A", "B", "C", "D")


class RegistryError(Exception):
    """The registry is malformed, or something referenced a code it does not hold."""


@dataclass(frozen=True)
class Primitive:
    """One kernel predicate: a top-level primitive or a subtype of one."""

    iri: str
    code: str
    name: str
    stratum: str
    kind: str
    weight: float
    calibrated: bool
    requires: tuple[str, ...]
    aggregates_subjects: bool
    parent: Optional[str] = None
    attribute: str = ""

    @property
    def is_defect(self) -> bool:
        return self.kind == DEFECT

    @property
    def is_coverage(self) -> bool:
        return self.kind == COVERAGE

    @property
    def is_subtype(self) -> bool:
        return self.parent is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "iri": self.iri,
            "code": self.code,
            "name": self.name,
            "stratum": self.stratum,
            "kind": self.kind,
            "weight": self.weight,
            "calibrated": self.calibrated,
            "requires": list(self.requires),
            "aggregates_subjects": self.aggregates_subjects,
            "parent": self.parent,
            "attribute": self.attribute,
        }


@dataclass(frozen=True)
class Registry:
    version: str
    weights_source: str
    namespace: str
    digest: str
    entries: dict[str, Primitive] = field(default_factory=dict)

    def __iter__(self) -> Iterator[Primitive]:
        return iter(self.entries.values())

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, code: str) -> bool:
        return code in self.entries

    def get(self, code: str) -> Primitive:
        try:
            return self.entries[code]
        except KeyError:
            raise RegistryError(
                f"unknown kernel code {code!r}. Every code emitted by a detector "
                f"must be declared in the registry; an undeclared one is a build "
                f"error because it would appear in a findings table while the "
                f"completeness table knows nothing about it."
            ) from None

    def codes(self) -> tuple[str, ...]:
        return tuple(self.entries)

    def defects(self) -> tuple[Primitive, ...]:
        return tuple(p for p in self.entries.values() if p.is_defect)

    def coverage(self) -> tuple[Primitive, ...]:
        return tuple(p for p in self.entries.values() if p.is_coverage)

    def top_level(self) -> tuple[Primitive, ...]:
        return tuple(p for p in self.entries.values() if not p.is_subtype)

    def for_strata(self, strata) -> tuple[Primitive, ...]:
        wanted = set(strata)
        return tuple(p for p in self.entries.values() if p.stratum in wanted)

    def weight(self, code: str) -> float:
        return self.get(code).weight

    def is_defect(self, code: str) -> bool:
        return self.get(code).is_defect

    def validate_codes(self, codes) -> None:
        """Raise on the first code the registry does not know.

        Called wherever findings are collected, so an unregistered predicate
        fails the build rather than surfacing as a mismatch between two tables.
        """
        unknown = sorted({c for c in codes if c and c not in self.entries})
        if unknown:
            raise RegistryError(
                f"build error: findings carry kernel codes absent from registry "
                f"{self.version}: {unknown}. Add them to primitives.yaml with an "
                f"explicit kind, or stop emitting them. An unregistered code "
                f"appears in a findings table while the completeness table knows "
                f"nothing about it, which is the overlap this registry exists to "
                f"prevent."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "weights_source": self.weights_source,
            "namespace": self.namespace,
            "digest": self.digest,
            "entries": [p.to_dict() for p in self.entries.values()],
        }


def _primitive(raw: dict[str, Any], *, stratum: str,
               parent: Optional[str], namespace: str) -> Primitive:
    for required in ("code", "name", "kind"):
        if not raw.get(required):
            raise RegistryError(f"registry entry missing {required!r}: {raw}")

    code = str(raw["code"])
    kind = str(raw["kind"])
    if kind not in KINDS:
        raise RegistryError(
            f"{code}: kind must be one of {list(KINDS)}, got {kind!r}")

    requires = tuple(str(r) for r in (raw.get("requires") or ()))
    unknown = [r for r in requires if r not in REQUIREMENTS]
    if unknown:
        raise RegistryError(
            f"{code}: unknown requirement(s) {unknown}; expected "
            f"{list(REQUIREMENTS)}")

    weight = float(raw.get("weight", 1.0))
    if kind == COVERAGE and weight != 0.0:
        raise RegistryError(
            f"{code}: a coverage predicate must carry weight 0.0. It detects the "
            f"absence of an attribute, so a non-zero weight would let the size of "
            f"the ontology enter the debt figure.")

    return Primitive(
        iri=str(raw.get("iri") or f"{namespace}{code}"),
        code=code,
        name=str(raw["name"]),
        stratum=stratum,
        kind=kind,
        weight=weight,
        calibrated=bool(raw.get("calibrated", False)),
        requires=requires,
        aggregates_subjects=bool(raw.get("aggregates_subjects", False)),
        parent=parent,
        attribute=str(raw.get("attribute") or ""),
    )


def load_registry(path: str | Path | None = None) -> Registry:
    """Read and validate the registry. Raises :class:`RegistryError` on any defect."""
    p = Path(path) if path is not None else DEFAULT_REGISTRY_PATH
    try:
        raw_bytes = p.read_bytes()
    except OSError as e:
        raise RegistryError(f"cannot read registry at {p}: {e}") from e

    try:
        data = yaml.safe_load(raw_bytes.decode("utf-8"))
    except yaml.YAMLError as e:
        raise RegistryError(f"registry at {p} is not valid YAML: {e}") from e
    if not isinstance(data, dict):
        raise RegistryError(f"registry at {p} must be a mapping")

    version = str(data.get("version") or "")
    if not version:
        raise RegistryError("registry declares no version")
    namespace = str(data.get("namespace") or "")

    entries: dict[str, Primitive] = {}
    for raw in data.get("primitives") or ():
        stratum = str(raw.get("stratum") or "")
        if stratum not in STRATA:
            raise RegistryError(
                f"{raw.get('code')}: stratum must be one of {list(STRATA)}, "
                f"got {stratum!r}")
        primitive = _primitive(raw, stratum=stratum, parent=None, namespace=namespace)
        if primitive.code in entries:
            raise RegistryError(f"duplicate code in registry: {primitive.code}")
        entries[primitive.code] = primitive

        for raw_subtype in raw.get("subtypes") or ():
            subtype = _primitive(raw_subtype, stratum=stratum,
                                 parent=primitive.code, namespace=namespace)
            if subtype.code in entries:
                raise RegistryError(f"duplicate code in registry: {subtype.code}")
            entries[subtype.code] = subtype

    if not entries:
        raise RegistryError("registry declares no primitives")

    return Registry(
        version=version,
        weights_source=str(data.get("weights_source") or "uniform_fallback"),
        namespace=namespace,
        digest="sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
        entries=entries,
    )


def base_code(code: str) -> str:
    """The top-level primitive a subtype instantiates.

    ``kernel_audit`` reports subtypes such as ``K-A3b``. Callers that need the
    primitive rather than the subtype use this, but they must not use it to
    launder a coverage subtype into its defect parent: the registry entry for
    the subtype is what decides whether it counts.
    """
    return code[:4] if len(code) > 4 and code[:2] == "K-" else code
