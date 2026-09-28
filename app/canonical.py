"""Canonical anchoring (SPEC-bfo-agent-quality.md QS-F1..F4).

A library's ``manifest.json`` may name ``canonical_seed``: one path, or a
list of paths, relative to the library directory or to the repo root. Seed
classes are loaded read-only, like the BFO kernel; nothing here writes to
them. They are used to:

  F2  rank canonical classes first when retrieving reuse candidates, and
      give PC-10 (construction linter) a normalized-label index so a mint
      that matches a canonical label must reuse the canonical IRI;
  F3  report, at end of job, how much each canonical class is anchored to
      (subclasses, instances, restrictions). Gaps are findings, never
      prompts to invent content (FG-0).

The default core, ``ontology/canonical/sool-canonical.owl``, is generated
by :func:`build_sool_core` from the terms the spec enumerates, and encodes
decisions D-2 (Hohfeldian positions are roles) and D-3 (norm content is a
GDC, normative positions are SDCs) as data (F4).
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional

from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef

from . import relation_vocab as rv

ROOT = Path(__file__).resolve().parent.parent
CANONICAL_DIR = ROOT / "ontology" / "canonical"
SOOL_CORE_PATH = CANONICAL_DIR / "sool-canonical.owl"

SOOL = Namespace("http://davidkoepsell.com/sool#")
OBO = Namespace(rv.OBO)
BFOAGENT = Namespace(rv.BFOAGENT_NS)

ROLE, DISPOSITION = OBO.BFO_0000023, OBO.BFO_0000016
SDC, GDC, PROCESS = OBO.BFO_0000020, OBO.BFO_0000031, OBO.BFO_0000015
IC = OBO.BFO_0000004

# The eight Hohfeldian positions and the extracted labels that denote them.
HOHFELD_POSITIONS: dict[str, tuple[str, tuple[str, ...]]] = {
    "ClaimRight": ("claim right", ("Claim", "Right", "Claim Right")),
    "Duty": ("duty", ("Duty", "Obligation")),
    "Privilege": ("privilege", ("Privilege", "Liberty", "Permission")),
    "NoRight": ("no-right", ("No Right", "NoRight", "No-Right")),
    "Power": ("power", ("Power", "Legal Power")),
    "Liability": ("liability", ("Liability",)),
    "Immunity": ("immunity", ("Immunity",)),
    "Disability": ("disability", ("Disability", "Legal Disability")),
}

# Minimal Legal Chain nodes (MLC).
MLC_NODES: dict[str, tuple[str, URIRef]] = {
    "SourceOfAuthority": ("source of authority", SDC),
    "Norm": ("norm", GDC),                       # D-3
    "ActorInRole": ("actor in role", IC),
    "TriggeringFacts": ("triggering facts", OBO.BFO_0000001),
    "LegalActOrOmission": ("legal act or omission", PROCESS),
    "Target": ("target", OBO.BFO_0000001),
    "LegalEffect": ("legal effect", SDC),
    "Remedy": ("remedy", PROCESS),
}

KERNEL_CODES = ("K-A1", "K-A2", "K-A3", "K-B1", "K-B2", "K-B3", "K-C1",
                "K-C2", "K-C3", "K-D1", "K-D2", "K-D3")

OTHER_CANONICAL: dict[str, tuple[str, URIRef, tuple[str, ...]]] = {
    "LegalPosition": ("legal position", ROLE, ("Normative Position",)),  # D-2/D-3
    "LegalAct": ("legal act", PROCESS, ("Legal Action",)),
    "LegalDocument": ("legal document", GDC, ()),
    "LegalPerson": ("legal person", IC, ()),
    "ContradictionType": ("contradiction type", GDC, ()),
    "ContradictionDebt": ("contradiction debt", OBO.BFO_0000019, ()),
}


def norm_key(label: str) -> str:
    """Normalized label key, identical to scripts/ontology_audit.py's
    synonym clustering so reuse checks and the audit agree."""
    from .quality_gates import audit_module
    return audit_module().norm_key(label or "")


def _cls(g: Graph, local: str, label: str, parent: URIRef,
         aliases: Iterable[str] = ()) -> URIRef:
    u = SOOL[local]
    g.add((u, RDF.type, OWL.Class))
    g.add((u, RDFS.label, Literal(label, lang="en")))
    g.add((u, RDFS.subClassOf, parent))
    for a in aliases:
        g.add((u, BFOAGENT.alignLabel, Literal(a)))
    return u


def build_sool_core() -> Graph:
    """The SOoL canonical core (F1), with D-2/D-3 recorded as data (F4)."""
    g = Graph()
    g.bind("sool", SOOL); g.bind("obo", OBO); g.bind("bfoagent", BFOAGENT)
    onto = URIRef(str(SOOL).rstrip("#") + "/canonical")
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, RDFS.comment, Literal(
        "Canonical SOoL core for anchoring (SPEC-bfo-agent-quality QS-F1). "
        "Read-only. D-2: Hohfeldian positions are roles. D-3: norm content is "
        "a generically dependent continuant; normative positions are "
        "specifically dependent continuants.")))
    g.add((BFOAGENT.alignLabel, RDF.type, OWL.AnnotationProperty))
    g.add((BFOAGENT.designDecision, RDF.type, OWL.AnnotationProperty))
    for local, (label, parent, aliases) in OTHER_CANONICAL.items():
        _cls(g, local, label, parent, aliases)
    for local, (label, aliases) in HOHFELD_POSITIONS.items():
        _cls(g, local, label, SOOL.LegalPosition, aliases)
    for local, (label, parent) in MLC_NODES.items():
        _cls(g, local, label, parent)
    for code in KERNEL_CODES:
        _cls(g, code.replace("-", "_"), code, SOOL.ContradictionType)
    g.add((SOOL.LegalPosition, BFOAGENT.designDecision, Literal(
        "D-2 (2026-09-28): the eight Hohfeldian positions are roles")))
    g.add((SOOL.Norm, BFOAGENT.designDecision, Literal(
        "D-3 (2026-09-28): norm content is a GDC; positions are SDCs")))
    return g


def write_sool_core(path: Path = SOOL_CORE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    build_sool_core().serialize(destination=str(path), format="xml")
    return path


def _resolve(path: str, library_dir: Optional[Path]) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    if library_dir is not None and (library_dir / p).exists():
        return library_dir / p
    return ROOT / p


def seed_paths(manifest: dict, library_dir: Optional[Path] = None) -> list[Path]:
    raw = manifest.get("canonical_seed")
    if not raw:
        return []
    items = raw if isinstance(raw, list) else [raw]
    return [_resolve(str(x), library_dir) for x in items]


@lru_cache(maxsize=8)
def _load(paths: tuple[str, ...]) -> Graph:
    g = Graph()
    for p in paths:
        g.parse(p)
    return g


def load_seed(manifest: dict, library_dir: Optional[Path] = None) -> Graph:
    """Read-only merged graph of the manifest's canonical seed(s). Callers
    must not mutate it (it is cached)."""
    return _load(tuple(str(p) for p in seed_paths(manifest, library_dir)
                       if p.exists()))


def canonical_classes(seed: Graph) -> set[str]:
    return {str(c) for c in seed.subjects(RDF.type, OWL.Class)
            if isinstance(c, URIRef)}


def canonical_label_index(seed: Graph) -> dict[str, str]:
    """Normalized label key -> canonical IRI (F2 / PC-10). Includes the
    ``bfoagent:alignLabel`` aliases, so 'Obligation' finds sool:Duty."""
    idx: dict[str, str] = {}
    for c in canonical_classes(seed):
        u = URIRef(c)
        labels = [str(l) for l in seed.objects(u, RDFS.label)]
        labels += [str(l) for l in seed.objects(u, BFOAGENT.alignLabel)]
        labels.append(c.rsplit("#", 1)[-1].rsplit("/", 1)[-1])
        for lbl in labels:
            k = norm_key(lbl)
            if k:
                idx.setdefault(k, c)
    return idx


def rank_canonical_first(candidates: list[dict],
                         canonical_iris: Iterable[str]) -> list[dict]:
    """F2: stable re-ranking of reuse candidates (dicts with an ``iri`` key)
    so canonical classes precede extracted ones; order is otherwise kept."""
    canon = set(canonical_iris)
    return sorted(candidates,
                  key=lambda c: 0 if c.get("iri") in canon else 1)


def canonical_match(label: str, seed: Graph) -> Optional[str]:
    """The canonical IRI a new class with this label must reuse, if any."""
    return canonical_label_index(seed).get(norm_key(label))


def coverage_report(working: Graph, seed: Graph) -> dict:
    """F3: per canonical class, how many working-ontology subclasses,
    instances and restrictions are anchored to it (directly, or through an
    aligned class with the same normalized label). Zero-coverage terms are
    findings only."""
    idx = canonical_label_index(seed)
    aligned: dict[str, set] = {c: {URIRef(c)} for c in canonical_classes(seed)}
    for c in set(working.subjects(RDF.type, OWL.Class)):
        if not isinstance(c, URIRef):
            continue
        for lbl in working.objects(c, RDFS.label):
            hit = idx.get(norm_key(str(lbl)))
            if hit:
                aligned[hit].add(c)
    rows = []
    for canon_iri, nodes in sorted(aligned.items()):
        subs = {s for n in nodes for s in working.subjects(RDFS.subClassOf, n)
                if isinstance(s, URIRef)} - nodes
        inst = {i for n in nodes for i in working.subjects(RDF.type, n)}
        restr = {r for n in nodes
                 for p in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue)
                 for r in working.subjects(p, n)}
        label = next((str(l) for l in seed.objects(URIRef(canon_iri),
                                                   RDFS.label)), canon_iri)
        rows.append({"iri": canon_iri, "label": label,
                     "aligned_classes": sorted(str(n) for n in nodes
                                               if str(n) != canon_iri),
                     "subclasses": len(subs), "instances": len(inst),
                     "restrictions": len(restr)})
    gaps = [r["label"] for r in rows
            if not (r["aligned_classes"] or r["subclasses"] or r["instances"]
                    or r["restrictions"])]
    return {"canonical_classes": len(rows), "zero_coverage": gaps,
            "rows": rows}


def library_coverage(working_path: Path, manifest: dict) -> Optional[dict]:
    """F3 end-of-job entry point; None when the library declares no seed."""
    lib = Path(working_path).parent
    seed = load_seed(manifest, lib)
    if not len(seed):
        return None
    g = Graph(); g.parse(str(working_path))
    return coverage_report(g, seed)
