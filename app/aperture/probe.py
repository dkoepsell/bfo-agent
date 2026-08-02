"""Phase 1: the precondition probe.

Eight mechanical probes over the artifact as a merged closure. Every one reports
a value plus the evidence that produced it. No probe returns a bare boolean,
because "not satisfied" has to be arguable from the output alone.

Two rules shape the whole module:

* **Counts are over the artifact, not the closure.** The closure is merged so
  that BFO's taxonomy and disjointness axioms are visible for ancestor
  questions, but a precondition must never be satisfied on the artifact's behalf
  by axioms BFO brought with it. Probes that count constructs count them in the
  target graph; probes that ask about anchoring use the merged graph.

* **profile_dl is never assumed to pass.** Without ROBOT it reports unknown, and
  the resolver treats unknown exactly as it treats a failure.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

from .. import bfo_catalog, config
from ..kernel_audit import BEARER_PROPS, BFO, IAO_DEFINITION

log = logging.getLogger(__name__)

PROBE_VERSION = "1.0.0"

# The eight preconditions, in the order the spec lists them.
PRECONDITIONS: tuple[str, ...] = (
    "formal_theory",
    "definitions",
    "complement_available",
    "upper_ontology",
    "bearer_relations",
    "acts",
    "individuals",
    "profile_dl",
)

SKOS_DEFINITION = URIRef("http://www.w3.org/2004/02/skos/core#definition")
OBO_HAS_DEFINITION = URIRef("http://www.geneontology.org/formats/oboInOwl#hasDefinition")
PROSE_DEFINITION_PREDICATES = (IAO_DEFINITION, SKOS_DEFINITION, OBO_HAS_DEFINITION)

# Upper ontologies we can recognise by namespace. The probe reports which one was
# found rather than a bare yes, because "anchored to something" and "anchored to
# BFO" are different claims.
UPPER_ONTOLOGY_NAMESPACES: tuple[tuple[str, str], ...] = (
    ("BFO", "http://purl.obolibrary.org/obo/BFO_"),
    ("DOLCE", "http://www.loa-cnr.it/ontologies/DOLCE"),
    ("DOLCE-Lite", "http://www.ontologydesignpatterns.org/ont/dul/DUL.owl"),
    ("SUMO", "http://www.adampease.org/OP/SUMO.owl"),
    ("UFO", "http://purl.org/nemo/gufo"),
    ("CCO", "http://www.ontologyrepository.com/CommonCoreOntologies/"),
)

# BFO fragments whose descendants are occurrents, which is what Stratum D needs.
OCCURRENT_ANCHORS: tuple[str, ...] = (
    "BFO_0000003",  # occurrent
    "BFO_0000015",  # process
    "BFO_0000035",  # process boundary
    "BFO_0000011",  # spatiotemporal region
)

# Class-expression and axiom constructs that make a vocabulary a theory. Split by
# where they appear so the evidence names the construct rather than a total.
_CLASS_EXPRESSION_PREDICATES: tuple[tuple[str, URIRef], ...] = (
    ("someValuesFrom", OWL.someValuesFrom),
    ("allValuesFrom", OWL.allValuesFrom),
    ("hasValue", OWL.hasValue),
    ("cardinality", OWL.cardinality),
    ("minCardinality", OWL.minCardinality),
    ("maxCardinality", OWL.maxCardinality),
    ("qualifiedCardinality", OWL.qualifiedCardinality),
    ("minQualifiedCardinality", OWL.minQualifiedCardinality),
    ("maxQualifiedCardinality", OWL.maxQualifiedCardinality),
    ("intersectionOf", OWL.intersectionOf),
    ("unionOf", OWL.unionOf),
    ("complementOf", OWL.complementOf),
    ("oneOf", OWL.oneOf),
    ("disjointWith", OWL.disjointWith),
    ("disjointUnionOf", OWL.disjointUnionOf),
    ("propertyChainAxiom", OWL.propertyChainAxiom),
    ("inverseOf", OWL.inverseOf),
    ("equivalentClass", OWL.equivalentClass),
    ("equivalentProperty", OWL.equivalentProperty),
    ("propertyDisjointWith", OWL.propertyDisjointWith),
    ("hasKey", OWL.hasKey),
)

_PROPERTY_CHARACTERISTICS: tuple[tuple[str, URIRef], ...] = (
    ("TransitiveProperty", OWL.TransitiveProperty),
    ("SymmetricProperty", OWL.SymmetricProperty),
    ("AsymmetricProperty", OWL.AsymmetricProperty),
    ("FunctionalProperty", OWL.FunctionalProperty),
    ("InverseFunctionalProperty", OWL.InverseFunctionalProperty),
    ("IrreflexiveProperty", OWL.IrreflexiveProperty),
    ("ReflexiveProperty", OWL.ReflexiveProperty),
)

# Recorded, but not counted as a theory under the strict reading. This is the
# distinction ADJUDICATE item 2 turns on.
_WEAK_PREDICATES: tuple[tuple[str, URIRef], ...] = (
    ("domain", RDFS.domain),
    ("range", RDFS.range),
)


@dataclass(frozen=True)
class ProbeResult:
    """One precondition, its value, and the evidence that produced it.

    ``satisfied`` is tri-state. ``None`` means the probe could not decide, which
    is a different claim from "not satisfied" and must stay distinguishable all
    the way to the renderers.
    """

    id: str
    satisfied: Optional[bool]
    evidence: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    @property
    def unknown(self) -> bool:
        return self.satisfied is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "satisfied": self.satisfied,
            "unknown": self.unknown,
            "evidence": self.evidence,
            "note": self.note,
        }


@dataclass(frozen=True)
class ProbeReport:
    artifact_path: str
    artifact_sha256: str
    probe_version: str
    formal_theory_strict: bool
    results: dict[str, ProbeResult]

    def satisfied(self, precondition: str) -> Optional[bool]:
        return self.results[precondition].satisfied

    def unsatisfied(self, preconditions) -> Optional[str]:
        """The first precondition in ``preconditions`` that is not satisfied.

        Unknown counts as not satisfied. The resolver needs the *first* one so
        the reason it reports is deterministic across runs.
        """
        for pid in preconditions:
            result = self.results.get(pid)
            if result is None or result.satisfied is not True:
                return pid
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "probe_version": self.probe_version,
            "formal_theory_strict": self.formal_theory_strict,
            "preconditions": {k: v.to_dict() for k, v in self.results.items()},
        }


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Closure:
    """The artifact alone, and the artifact merged with the upper ontology."""

    target: rdflib.Graph
    merged: rdflib.Graph
    owned: frozenset[str]
    sha256: str


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_closure(artifact_path: str | Path, bfo_path: str | Path | None = None) -> Closure:
    """Parse the artifact, then merge the upper ontology for ancestor questions.

    ``owned`` is recorded before the merge so that no probe can be satisfied by
    a construct the upper ontology contributed.
    """
    p = Path(artifact_path)
    target = rdflib.Graph()
    target.parse(str(p))

    owned = {str(s) for s in target.subjects(RDF.type, OWL.Class) if isinstance(s, URIRef)}
    owned |= {str(s) for s in target.subjects(RDF.type, OWL.NamedIndividual)
              if isinstance(s, URIRef)}

    merged = target
    if bfo_path and Path(bfo_path).exists():
        merged = rdflib.Graph()
        for triple in target:
            merged.add(triple)
        merged.parse(str(bfo_path))

    return Closure(target=target, merged=merged, owned=frozenset(owned),
                   sha256=_sha256(p))


def _named_classes(c: Closure) -> set[str]:
    return {str(s) for s in c.target.subjects(RDF.type, OWL.Class)
            if isinstance(s, URIRef) and str(s) in c.owned}


def _ancestor_index(g: rdflib.Graph) -> dict[str, set[str]]:
    """Reflexive-transitive rdfs:subClassOf closure over named classes.

    Written here rather than reused from kernel_audit because that one is keyed
    to its own finding logic; this one has to survive cycles without reporting
    them, since circularity is a defect the resolver decides about later.
    """
    parents: dict[str, set[str]] = {}
    for s, o in g.subject_objects(RDFS.subClassOf):
        if isinstance(s, URIRef) and isinstance(o, URIRef):
            parents.setdefault(str(s), set()).add(str(o))

    closure: dict[str, set[str]] = {}

    def ancestors(node: str, seen: frozenset[str]) -> set[str]:
        if node in closure:
            return closure[node]
        acc: set[str] = set()
        for parent in parents.get(node, ()):
            if parent in seen:
                continue  # cycle guard
            acc.add(parent)
            acc |= ancestors(parent, seen | {parent})
        closure[node] = acc
        return acc

    for node in list(parents):
        ancestors(node, frozenset({node}))
    return closure


# --------------------------------------------------------------------------
# The eight probes
# --------------------------------------------------------------------------

def probe_formal_theory(c: Closure, strict: bool) -> ProbeResult:
    """Is there anything here stronger than a typing constraint?"""
    constructs: dict[str, int] = {}
    for name, pred in _CLASS_EXPRESSION_PREDICATES:
        n = len(list(c.target.triples((None, pred, None))))
        if n:
            constructs[name] = n
    for name, cls in _PROPERTY_CHARACTERISTICS:
        n = len(list(c.target.subjects(RDF.type, cls)))
        if n:
            constructs[name] = n

    weak: dict[str, int] = {}
    for name, pred in _WEAK_PREDICATES:
        n = len(list(c.target.triples((None, pred, None))))
        if n:
            weak[name] = n

    strong_total = sum(constructs.values())
    weak_total = sum(weak.values())
    satisfied = strong_total > 0 if strict else (strong_total + weak_total) > 0

    if strict and strong_total == 0 and weak_total > 0:
        note = ("only domain and range axioms are present, which the strict "
                "reading does not accept as a theory")
    elif strong_total == 0 and weak_total == 0:
        note = "no class expression, cardinality or property axiom of any kind"
    else:
        note = ""

    return ProbeResult(
        "formal_theory", satisfied,
        {
            "constructs": constructs,
            "construct_total": strong_total,
            "domain_range": weak,
            "domain_range_total": weak_total,
            "reading": "strict" if strict else "permissive",
        },
        note,
    )


def probe_definitions(c: Closure) -> ProbeResult:
    """Defined classes, and prose that was never given a logical counterpart.

    Counted over the target graph rather than intersected with the set of
    declared classes. The target graph already excludes everything the upper
    ontology contributed, so the intersection buys no protection, and it costs
    correctness: an artifact whose declarations and whose axioms use different
    IRI forms for the same term would have real definitions reported as absent.
    Under-reporting here would put a whole stratum out of scope on a false basis,
    which is the one error this probe must not make.
    """
    named = _named_classes(c)
    equivalent = {str(s) for s in c.target.subjects(OWL.equivalentClass, None)
                  if isinstance(s, URIRef)}
    disjoint_union = {str(s) for s in c.target.subjects(OWL.disjointUnionOf, None)
                      if isinstance(s, URIRef)}
    defined = equivalent | disjoint_union

    prose: dict[str, int] = {}
    prose_subjects: set[str] = set()
    for pred in PROSE_DEFINITION_PREDICATES:
        subjects = {str(s) for s in c.target.subjects(pred, None) if isinstance(s, URIRef)}
        if subjects:
            prose[bfo_catalog.normalize_fragment(str(pred))] = len(subjects)
            prose_subjects |= subjects

    orphaned = prose_subjects - defined
    return ProbeResult(
        "definitions", bool(defined),
        {
            "equivalent_class": len(equivalent),
            "disjoint_union": len(disjoint_union),
            "defined_classes": len(defined),
            "named_classes": len(named),
            # A definition on a term the artifact never declares as a class is
            # worth surfacing: it is also an undeclared-entity smell that
            # profile_dl would flag independently.
            "defined_but_undeclared": len(defined - named),
            "prose_definitions_by_predicate": prose,
            "prose_without_logical_counterpart": len(orphaned),
        },
        ("" if defined else
         f"{len(prose_subjects)} classes carry a prose definition and none has a "
         f"logical counterpart"),
    )


def probe_complement_available(c: Closure) -> ProbeResult:
    """Can a category be defined by what it excludes?"""
    complements = len(list(c.target.triples((None, OWL.complementOf, None))))
    partitions = len(list(c.target.triples((None, OWL.disjointUnionOf, None))))
    all_disjoint = len(list(c.target.subjects(RDF.type, OWL.AllDisjointClasses)))
    return ProbeResult(
        "complement_available", (complements + partitions) > 0,
        {
            "complement_of": complements,
            "disjoint_union_of": partitions,
            "all_disjoint_classes": all_disjoint,
        },
        ("" if complements or partitions else
         "no complement and no covering partition, so a residual definition "
         "could not be expressed here in the first place"),
    )


def probe_upper_ontology(c: Closure) -> ProbeResult:
    """Which upper ontology, at what version, and how much is actually anchored?"""
    referenced: list[str] = []
    for name, namespace in UPPER_ONTOLOGY_NAMESPACES:
        for _s, _p, o in c.target:
            if isinstance(o, URIRef) and str(o).startswith(namespace):
                referenced.append(name)
                break

    ancestors = _ancestor_index(c.merged)
    named = _named_classes(c)
    anchored = {
        cls for cls in named
        if any(a.startswith(BFO) for a in ancestors.get(cls, set()))
    }
    directly_anchored = {
        str(s) for s, o in c.target.subject_objects(RDFS.subClassOf)
        if isinstance(s, URIRef) and isinstance(o, URIRef) and str(o).startswith(BFO)
    } & named

    versions = sorted(
        str(o) for o in c.target.objects(None, OWL.versionIRI) if isinstance(o, URIRef)
    )
    imports = sorted(
        str(o) for o in c.target.objects(None, OWL.imports) if isinstance(o, URIRef)
    )

    return ProbeResult(
        "upper_ontology", bool(referenced),
        {
            "referenced": sorted(set(referenced)),
            "version_iris": versions,
            "imports": imports,
            "named_classes": len(named),
            "classes_anchored": len(anchored),
            "classes_directly_anchored": len(directly_anchored),
            "anchored_pct": round(100 * len(anchored) / max(1, len(named)), 2),
        },
        ("" if referenced else "no known upper-ontology namespace appears in the closure"),
    )


def probe_bearer_relations(c: Closure) -> ProbeResult:
    """Is any dependence or inherence relation actually asserted?

    Reuses ``kernel_audit.BEARER_PROPS``, the same set that discharges the
    needs-a-bearer obligation in the structural audit, so the probe and the
    detector cannot disagree about what counts as a bearer link.
    """
    used: dict[str, int] = {}
    for pred in set(c.target.predicates(None, None)):
        if not isinstance(pred, URIRef):
            continue
        frag = bfo_catalog.normalize_fragment(str(pred))
        if frag in BEARER_PROPS:
            used[frag] = len(list(c.target.triples((None, pred, None))))

    declared = sorted(
        bfo_catalog.normalize_fragment(str(s))
        for s in c.target.subjects(RDF.type, OWL.ObjectProperty)
        if isinstance(s, URIRef) and bfo_catalog.normalize_fragment(str(s)) in BEARER_PROPS
    )

    return ProbeResult(
        "bearer_relations", bool(used),
        {
            "asserted": dict(sorted(used.items())),
            "assertion_total": sum(used.values()),
            "declared_but_unused": sorted(set(declared) - set(used)),
            "recognised_properties": sorted(BEARER_PROPS),
        },
        ("" if used else
         "no inherence, bearer or realization relation is asserted anywhere"),
    )


def probe_acts(c: Closure) -> ProbeResult:
    """Are there occurrents? Stratum D fires only where there are acts."""
    ancestors = _ancestor_index(c.merged)
    named = _named_classes(c)
    hits: list[str] = []
    for cls in named:
        frags = {bfo_catalog.normalize_fragment(a) for a in ancestors.get(cls, set())}
        frags.add(bfo_catalog.normalize_fragment(cls))
        if frags & set(OCCURRENT_ANCHORS):
            hits.append(cls)
            continue
        if any(bfo_catalog.is_descendant_of(f, anchor)
               for f in frags for anchor in OCCURRENT_ANCHORS):
            hits.append(cls)

    return ProbeResult(
        "acts", bool(hits),
        {
            "occurrent_classes": len(hits),
            "sample": sorted(hits)[:25],
            "anchors_checked": list(OCCURRENT_ANCHORS),
        },
        ("" if hits else
         "no occurrent or process class, so there are no acts for the pragmatic "
         "stratum to be about"),
    )


def probe_individuals(c: Closure) -> ProbeResult:
    individuals = {str(s) for s in c.target.subjects(RDF.type, OWL.NamedIndividual)
                   if isinstance(s, URIRef)}
    return ProbeResult(
        "individuals", bool(individuals),
        {"named_individuals": len(individuals), "sample": sorted(individuals)[:25]},
        ("" if individuals else "no named individual is declared"),
    )


_ROBOT_VIOLATION = re.compile(r"^\s*(?:\[?)(ERROR|WARN|Violation)\b[:\]]?\s*(.+)$", re.I)


def probe_profile_dl(c: Closure, robot_path: str, timeout: float) -> ProbeResult:
    """OWL 2 DL profile validation, with ROBOT when it is available.

    A hard gate: outside the profile, reasoner behaviour is undefined and every
    mechanical verdict that depends on one is undefined with it. When ROBOT is
    absent this reports unknown, which the resolver treats exactly as it treats a
    failure. It never reports a pass it did not observe.

    Validation runs against the merged closure with ``owl:imports`` stripped, so
    ROBOT has nothing to dereference. The network stays off.
    """
    resolved = shutil.which(robot_path)
    if not resolved:
        return ProbeResult(
            "profile_dl", None,
            {"checker": None, "robot_path": robot_path},
            "ROBOT is not available, so the profile could not be verified; every "
            "reasoner-dependent verdict is out of scope on that basis alone",
        )

    with tempfile.TemporaryDirectory(prefix="aperture-profile-") as tmp:
        tmp_path = Path(tmp)
        closure_file = tmp_path / "closure.owl"
        report_file = tmp_path / "report.txt"

        offline = rdflib.Graph()
        for s, p, o in c.merged:
            if p == OWL.imports:
                continue
            offline.add((s, p, o))
        offline.serialize(destination=str(closure_file), format="xml")

        cmd = [
            resolved, "validate-profile",
            "--profile", "DL",
            "--input", str(closure_file),
            "--output", str(report_file),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return ProbeResult(
                "profile_dl", None,
                {"checker": "robot", "robot_path": resolved, "timeout_seconds": timeout},
                f"ROBOT did not finish within {timeout:.0f}s, so the profile is unverified",
            )
        except OSError as e:
            return ProbeResult(
                "profile_dl", None,
                {"checker": "robot", "robot_path": resolved, "error": str(e)},
                "ROBOT could not be run, so the profile is unverified",
            )

        report = report_file.read_text() if report_file.exists() else ""

    by_kind: dict[str, int] = {}
    for line in report.splitlines():
        m = _ROBOT_VIOLATION.match(line)
        if not m:
            continue
        kind = m.group(2).split(":")[0].strip()[:80]
        by_kind[kind] = by_kind.get(kind, 0) + 1

    total = sum(by_kind.values())
    satisfied = proc.returncode == 0 and total == 0
    return ProbeResult(
        "profile_dl", satisfied,
        {
            "checker": "robot",
            "robot_path": resolved,
            "exit_code": proc.returncode,
            "violations_total": total,
            "violations_by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
            "stderr_tail": (proc.stderr or "").strip()[-500:],
        },
        ("" if satisfied else
         "the artifact is outside OWL 2 DL, so reasoner behaviour on it is "
         "undefined and no reasoner-dependent verdict can be given"),
    )


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------

def run(artifact_path: str | Path,
        bfo_path: str | Path | None = None,
        strict_formal_theory: bool | None = None,
        robot_path: str | None = None,
        robot_timeout: float | None = None) -> ProbeReport:
    """Run all eight probes over one artifact."""
    if strict_formal_theory is None:
        strict_formal_theory = config.APERTURE_FORMAL_THEORY_STRICT
    if robot_path is None:
        robot_path = config.APERTURE_ROBOT_PATH
    if robot_timeout is None:
        robot_timeout = config.APERTURE_ROBOT_TIMEOUT_SECONDS
    if bfo_path is None:
        bfo_path = config.BFO_PATH

    closure = load_closure(artifact_path, bfo_path)

    results = [
        probe_formal_theory(closure, strict_formal_theory),
        probe_definitions(closure),
        probe_complement_available(closure),
        probe_upper_ontology(closure),
        probe_bearer_relations(closure),
        probe_acts(closure),
        probe_individuals(closure),
        probe_profile_dl(closure, robot_path, robot_timeout),
    ]
    by_id = {r.id: r for r in results}
    assert set(by_id) == set(PRECONDITIONS), "probe set drifted from PRECONDITIONS"

    return ProbeReport(
        artifact_path=str(artifact_path),
        artifact_sha256=closure.sha256,
        probe_version=PROBE_VERSION,
        formal_theory_strict=strict_formal_theory,
        results=by_id,
    )


def run_cached(artifact_path: str | Path,
               cache_path: str | Path,
               **kwargs) -> ProbeReport:
    """Run the probe, reusing a cached report when the artifact has not changed.

    The cache key is the artifact digest and the probe version together, so a
    change to either invalidates. A stale precondition would silently widen or
    narrow the scope of every resolution built on it.
    """
    cache = Path(cache_path)
    digest = _sha256(Path(artifact_path))
    if cache.exists():
        try:
            data = json.loads(cache.read_text())
            if (data.get("artifact_sha256") == digest
                    and data.get("probe_version") == PROBE_VERSION):
                return _from_dict(data)
        except (OSError, ValueError, KeyError) as e:
            log.warning("aperture probe cache at %s unusable, recomputing: %s", cache, e)

    report = run(artifact_path, **kwargs)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(report.to_dict(), indent=2))
    except OSError as e:
        log.warning("could not write aperture probe cache at %s: %s", cache, e)
    return report


def _from_dict(data: dict[str, Any]) -> ProbeReport:
    results = {
        pid: ProbeResult(pid, blob.get("satisfied"), blob.get("evidence") or {},
                         blob.get("note") or "")
        for pid, blob in (data.get("preconditions") or {}).items()
    }
    return ProbeReport(
        artifact_path=data.get("artifact_path", ""),
        artifact_sha256=data.get("artifact_sha256", ""),
        probe_version=data.get("probe_version", ""),
        formal_theory_strict=bool(data.get("formal_theory_strict", True)),
        results=results,
    )
