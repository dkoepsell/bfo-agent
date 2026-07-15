"""Detect and repair *realizable-misuse* defects (bfo-agent-realizable-misuse-fix-SPEC).

The extractor keeps grounding a class as a realizable (role/disposition/function,
or another specifically dependent continuant) and *then* asserting it **bears** a
realizable -- or puts a bearer relation whose range is one realizable type on a
filler of a different realizable type. Under HermiT the class goes unsatisfiable
and cascades to its dependents. This module is the standing detector + repair:

* :func:`detect_graph` runs a **full-signature** HermiT pass and classifies every
  unsatisfiable class:

  - ``P1``  an SDC/realizable subject asserted to *bear* a realizable.
  - ``P2``  an independent continuant bearing a realizable through a property
            whose realizable range clashes with the filler's realizable type.
  - ``CONTRA`` a self-contradictory placement: ``D`` and ``complementOf(D)``.
  - ``OTHER``  none of the above -- a candidate *source* incoherence, never
            auto-repaired (Section 8 attribution guardrail).

  Detection does **not** trust owlready2's ``inconsistent_classes()`` alone: on
  the real ICD-11 artifact that list silently *omits* genuinely unsatisfiable
  named classes (the Section-7 "reduced-world false-coherent commit" failure,
  reproduced at the reasoner-reporting level -- VitaminC/Creatine/Anovulation are
  provably unsatisfiable yet unlisted). Instead a cheap **structural scan** finds
  candidate misuse axioms and each candidate is **confirmed** by cloning the
  class onto a fresh probe class and reasoning -- clone probes ARE reported,
  where the original is not.

* :func:`repair_graph` applies the content-preserving repairs R1-R4, each with a
  per-transform HermiT re-verify (also probe-based), roots first so cascades
  heal, and **never deletes source content** (retype/re-relate/relax over
  delete). It emits an annotated diff (rule + MUPS per transform).

Reasoning is a whole-ontology HermiT run over the full BFO imports closure --
never a reduced signature (Section 7). Stack: owlready2 + rdflib + HermiT, OWL 2
DL, Java 21, no Pellet/SWRL/SHACL.
"""
from __future__ import annotations

import io
import itertools
import logging
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rdflib import Graph, RDF, RDFS, OWL, URIRef, BNode

from owlready2 import World, Nothing, onto_path

from . import bfo_catalog
from .ontology_manager import _sync_reasoner_guarded, _REASONER_LOCK

log = logging.getLogger(__name__)

OBO = "http://purl.obolibrary.org/obo/"
_PROBE_SUFFIX = "__RM_PROBE"

# RO "has X" relations, keyed by the realizable type they range over. R2 swaps a
# mismatched bearer property for the one whose range matches the actual filler.
_RO_BY_TYPE = {
    bfo_catalog.ROLE:        ("RO_0000087", "has role"),
    bfo_catalog.DISPOSITION: ("RO_0000091", "has disposition"),
    bfo_catalog.FUNCTION:    ("RO_0000085", "has function"),
}
_RO_BY_TYPE_BY_FRAG = {ro: rt for rt, (ro, _lbl) in _RO_BY_TYPE.items()}
_INHERES_IN = "BFO_0000197"          # SDC -> independent continuant (R1 target)
_REALIZABLE_TYPES = (bfo_catalog.ROLE, bfo_catalog.DISPOSITION, bfo_catalog.FUNCTION)

# A "bears" property: an SDC subject carrying it is forced independent (domain
# clash) -> P1.
_BEARS_PROPS = {"BFO_0000196", "RO_0000087", "RO_0000091", "RO_0000085"}

_AXIOM_PREDS = (RDFS.subClassOf, OWL.equivalentClass)


def _frag(iri) -> str:
    s = str(iri)
    return s.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


# --------------------------------------------------------------- reasoning layer
def _sanitize(world: World) -> None:
    """Mirror OntologyManager load-time sanitation so a standalone reason gives
    the *same* verdict as the app's gate: drop self-contradicting BFO-vs-BFO
    disjointness inside a subclass relation, and strip ``subClassOf`` a property
    (a metaclass-conflict crash otherwise)."""
    g = world.as_rdflib_graph()
    for s, o in list(g.subject_objects(OWL.disjointWith)):
        sf, of = bfo_catalog.normalize_fragment(str(s)), bfo_catalog.normalize_fragment(str(o))
        if sf in bfo_catalog.KERNEL_CLASSES and of in bfo_catalog.KERNEL_CLASSES and (
            bfo_catalog.is_descendant_of(sf, of) or bfo_catalog.is_descendant_of(of, sf)
        ):
            g.remove((s, OWL.disjointWith, o))
            g.remove((o, OWL.disjointWith, s))
    props = set()
    for pt in (OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty,
               OWL.TransitiveProperty, OWL.FunctionalProperty, OWL.SymmetricProperty):
        props |= set(g.subjects(RDF.type, pt))
    for s, o in list(g.subject_objects(RDFS.subClassOf)):
        if o in props:
            g.remove((s, RDFS.subClassOf, o))


def _deep_clone(g: Graph, node, counter):
    """Copy a (possibly nested-bnode) class expression to a fresh bnode subtree
    so a probe class can carry the same axioms independently."""
    if not isinstance(node, BNode):
        return node
    nb = BNode(f"rmp{next(counter)}")
    for p, o in list(g.predicate_objects(node)):
        g.add((nb, p, _deep_clone(g, o, counter)))
    return nb


def _run(work_graph: Graph, bfo_path: Path, probe_iris=()):
    """One whole-ontology HermiT run over BFO + ``work_graph``. For each IRI in
    ``probe_iris`` a fresh clone class carrying that class's own axioms is added
    (in a private copy) -- clone probes are reliably reported where the original
    class is not. Returns ``(ok, unsat)`` where ``ok`` is False iff the ontology
    is globally inconsistent, and ``unsat`` is the set of ORIGINAL class IRIs
    that are unsatisfiable (reported originals plus probes mapped back)."""
    if probe_iris:
        g = Graph()
        for t in work_graph:
            g.add(t)
        counter = itertools.count()
        probe_map = {}
        for iri in probe_iris:
            probe = URIRef(iri + _PROBE_SUFFIX)
            probe_map[str(probe)] = iri
            g.add((probe, RDF.type, OWL.Class))
            for pred in _AXIOM_PREDS:
                for o in list(g.objects(URIRef(iri), pred)):
                    g.add((probe, pred, _deep_clone(g, o, counter)))
    else:
        g, probe_map = work_graph, {}

    onto_path.append(str(Path(bfo_path).parent))
    w = World()
    w.get_ontology(str(bfo_path)).load()
    data = g.serialize(format="xml")
    if isinstance(data, str):
        data = data.encode("utf-8")
    w.get_ontology("http://davidkoepsell.com/bfo-agent/rm-scratch").load(
        fileobj=io.BytesIO(data))
    _sanitize(w)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            with _REASONER_LOCK, w:
                _sync_reasoner_guarded(w)
    except Exception:  # noqa: BLE001 -- HermiT raises on inconsistency
        return False, set()
    unsat = set()
    for c in w.inconsistent_classes():
        if c is Nothing:
            continue
        iri = c.iri
        unsat.add(probe_map.get(iri, iri))
    return True, {i for i in unsat if not str(i).endswith(_PROBE_SUFFIX)}


def _class_sat(work_graph: Graph, bfo_path: Path, cls: URIRef) -> bool:
    ok, unsat = _run(work_graph, bfo_path, probe_iris=[str(cls)])
    return ok and str(cls) not in unsat


# ------------------------------------------------------------------ graph model
def _class_axioms(g: Graph, cls: URIRef):
    """Removable logical axioms *about* ``cls`` -- each a single (s,p,o) triple
    whose removal drops the whole axiom (a restriction bnode simply orphans)."""
    out = []
    for p in _AXIOM_PREDS:
        for o in g.objects(cls, p):
            out.append((cls, p, o))
    return out


def _restriction(g: Graph, node) -> Optional[tuple[str, str]]:
    """(onProperty_frag, filler_iri) if ``node`` is a some/all-values restriction
    onto a named class, else ``None``."""
    if not isinstance(node, BNode):
        return None
    if (node, RDF.type, OWL.Restriction) not in g:
        return None
    prop = next(g.objects(node, OWL.onProperty), None)
    filler = (next(g.objects(node, OWL.someValuesFrom), None)
              or next(g.objects(node, OWL.allValuesFrom), None))
    if prop is None or not isinstance(filler, URIRef):
        return None
    return _frag(prop), str(filler)


def _is_complement(g: Graph, node) -> bool:
    return isinstance(node, BNode) and (node, OWL.complementOf, None) in g


def _anchor_all(g: Graph, cls_iri: str) -> list[str]:
    """BFO kernel fragments reachable by walking named ``subClassOf`` up from a
    working class."""
    seen, frontier, hits = set(), [URIRef(cls_iri)], []
    while frontier:
        cur = frontier.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for parent in g.objects(cur, RDFS.subClassOf):
            if isinstance(parent, URIRef):
                frag = bfo_catalog.normalize_fragment(str(parent))
                if frag in bfo_catalog.KERNEL_CLASSES:
                    hits.append(frag)
                else:
                    frontier.append(parent)
    return hits


def _anchor_type(g: Graph, cls_iri: str) -> Optional[str]:
    """The realizable subtype (ROLE/DISPOSITION/FUNCTION) a class descends from,
    else ``None``."""
    for frag in _anchor_all(g, cls_iri):
        for rt in _REALIZABLE_TYPES:
            if bfo_catalog.is_descendant_of(frag, rt):
                return rt
    return None


def _prop_range_type(g: Graph, prop_frag: str) -> Optional[str]:
    """Realizable range of a property: local rdfs:range first, then the BFO
    signature. Returns ROLE/DISPOSITION/FUNCTION or ``None``."""
    for o in g.objects(URIRef(OBO + prop_frag), RDFS.range):
        if isinstance(o, URIRef):
            rng = bfo_catalog.normalize_fragment(str(o))
            if rng in _REALIZABLE_TYPES:
                return rng
    sig = bfo_catalog.relation_signatures().get(prop_frag)
    rng = sig.get("range") if sig else None
    return rng if rng in _REALIZABLE_TYPES else None


# --------------------------------------------------------------------- findings
@dataclass
class Finding:
    class_iri: str
    pattern: str                       # P1 | P2 | CONTRA | OTHER (or "+"-joined)
    detail: str
    mups: list[str] = field(default_factory=list)          # human-readable
    # every content-preserving transform needed to heal this class; each is a
    # dict {rule, target(triple), new_prop?}. Empty for OTHER (no auto-repair).
    transforms: list[dict] = field(default_factory=list)

    @property
    def rule(self) -> Optional[str]:
        rules = sorted({t["rule"] for t in self.transforms})
        return "+".join(rules) if rules else None

    def as_dict(self) -> dict:
        return {
            "class": _frag(self.class_iri), "class_iri": self.class_iri,
            "pattern": self.pattern, "rule": self.rule, "detail": self.detail,
            "mups": self.mups,
        }


def _axiom_str(g: Graph, triple) -> str:
    s, p, o = triple
    r = _restriction(g, o)
    if r:
        return f"{_frag(s)} {_frag(p)} ({r[0]} some {_frag(r[1])})"
    if _is_complement(g, o):
        inner = next(g.objects(o, OWL.complementOf), None)
        ri = _restriction(g, inner)
        body = f"({ri[0]} some {_frag(ri[1])})" if ri else _frag(inner)
        return f"{_frag(s)} {_frag(p)} complementOf {body}"
    return f"{_frag(s)} {_frag(p)} {_frag(o)}"


def _structural_candidates(g: Graph) -> set:
    """Named working classes carrying a syntactic realizable-misuse axiom
    (P1/P2/CONTRA shape). Cheap, no reasoning -- these are confirmed by probe."""
    cands = set()
    for cls in set(g.subjects(RDF.type, OWL.Class)):
        if not isinstance(cls, URIRef) or "obolibrary.org" in str(cls):
            continue
        pos_restr = set()          # (prop, filler) restrictions asserted positively
        for pred in _AXIOM_PREDS:
            for o in g.objects(cls, pred):
                if _is_complement(g, o):
                    inner = next(g.objects(o, OWL.complementOf), None)
                    r = _restriction(g, inner)
                    if r:
                        cands.add(str(cls))          # complement placement
                r = _restriction(g, o)
                if not r:
                    continue
                prop_frag, filler_iri = r
                pos_restr.add((prop_frag, filler_iri))
                ft = _anchor_type(g, filler_iri)
                pr = _prop_range_type(g, prop_frag)
                if ft and pr and bfo_catalog.clash(pr, ft):
                    cands.add(str(cls))              # P2 range/filler clash
                subj_types = _anchor_all(g, str(cls))
                subj_sdc = any(bfo_catalog.is_descendant_of(f, bfo_catalog.SDC) for f in subj_types)
                subj_indep = any(bfo_catalog.is_descendant_of(f, bfo_catalog.INDEPENDENT_CONTINUANT)
                                 for f in subj_types)
                if subj_sdc and not subj_indep and prop_frag in _BEARS_PROPS:
                    cands.add(str(cls))              # P1 SDC bears realizable
    return cands


def _mups(g: Graph, bfo_path: Path, cls: URIRef) -> list[tuple]:
    """QuickXplain over ``cls``'s own axioms -> minimal subset that keeps ``cls``
    unsatisfiable. Empty when the cause is entirely inherited (a cascade)."""
    cand = _class_axioms(g, cls)
    if not cand:
        return []
    cache: dict[frozenset, bool] = {}

    def sat(present: frozenset) -> bool:
        if present in cache:
            return cache[present]
        drop = [t for t in cand if t not in present]
        for t in drop:
            g.remove(t)
        try:
            ok = _class_sat(g, bfo_path, cls)
        finally:
            for t in drop:
                g.add(t)
        cache[present] = ok
        return ok

    if sat(frozenset(cand)):
        return []
    if not sat(frozenset()):
        return []

    def qx(bg: frozenset, c: list) -> set:
        if len(c) == 1:
            return set(c)
        mid = len(c) // 2
        c1, c2 = c[:mid], c[mid:]
        if not sat(bg | frozenset(c1)):
            return qx(bg, c1)
        if not sat(bg | frozenset(c2)):
            return qx(bg, c2)
        d1 = qx(bg | frozenset(c2), c1)
        d2 = qx(bg | frozenset(d1), c2)
        return d1 | d2

    return list(qx(frozenset(), cand))


def _positive_restrictions(g: Graph, cls: URIRef) -> set:
    out = set()
    for pred in _AXIOM_PREDS:
        for o in g.objects(cls, pred):
            r = _restriction(g, o)
            if r:
                out.add(r)
    return out


def _analyze(g: Graph, cls_iri: str, mups: list[tuple]) -> Finding:
    """Structurally analyse a class's own axioms and emit every content-preserving
    transform needed to heal it (independent of which single MUPS QuickXplain
    returned, so multiply-defective classes are handled). Falls back to OTHER
    when no realizable-misuse axiom is present."""
    cls = URIRef(cls_iri)
    mups_str = [_axiom_str(g, t) for t in mups] if mups else []
    subj_types = _anchor_all(g, cls_iri)
    subj_indep = any(bfo_catalog.is_descendant_of(f, bfo_catalog.INDEPENDENT_CONTINUANT)
                     for f in subj_types)
    subj_sdc = any(bfo_catalog.is_descendant_of(f, bfo_catalog.SDC) for f in subj_types)
    positive = _positive_restrictions(g, cls)

    patterns, transforms, details = [], [], []
    for pred in _AXIOM_PREDS:
        for o in list(g.objects(cls, pred)):
            t = (cls, pred, o)
            # CONTRA: complementOf(R) whose R the class also asserts positively.
            if _is_complement(g, o):
                inner = next(g.objects(o, OWL.complementOf), None)
                ri = _restriction(g, inner)
                if ri and ri in positive:
                    patterns.append("CONTRA")
                    transforms.append({"rule": "R4", "target": t})
                    details.append(f"asserts ({ri[0]} some {_frag(ri[1])}) and its complement")
                continue
            r = _restriction(g, o)
            if not r:
                continue
            prop_frag, filler_iri = r
            filler_type = _anchor_type(g, filler_iri)
            prop_range = _prop_range_type(g, prop_frag)
            # P2: independent continuant bears a realizable via a mismatched prop.
            if subj_indep and filler_type in _RO_BY_TYPE and prop_range \
                    and bfo_catalog.clash(prop_range, filler_type):
                new_prop, new_lbl = _RO_BY_TYPE[filler_type]
                patterns.append("P2")
                transforms.append({"rule": "R2", "target": t, "new_prop": new_prop})
                details.append(
                    f"bears {_frag(filler_iri)} "
                    f"({bfo_catalog.BFO_LABEL.get(filler_type, filler_type)}) via {prop_frag}; "
                    f"swap to {new_lbl}")
                continue
            # P1: an SDC/realizable subject asserted to bear a realizable.
            if subj_sdc and not subj_indep and prop_frag in _BEARS_PROPS:
                patterns.append("P1")
                transforms.append({"rule": "R1", "target": t, "new_prop": _INHERES_IN})
                details.append(f"SDC bears {_frag(filler_iri)} via {prop_frag}; retype to inheres-in")
                continue

    if not transforms:
        r3 = _try_r3(g, mups)
        if r3 is not None:
            return Finding(cls_iri, "P2", "over-tight locally-minted property "
                           "domain/range relaxed", mups_str,
                           transforms=[{"rule": "R3", "target": r3}])
        return Finding(cls_iri, "OTHER",
                       "unsatisfiable core does not match a translation pattern",
                       mups_str)
    pat = "+".join(sorted(set(patterns)))
    return Finding(cls_iri, pat, "; ".join(details), mups_str, transforms=transforms)


def _try_r3(g: Graph, mups: list[tuple]) -> Optional[tuple]:
    """A (prop, RDFS.range|domain, filler) triple on a locally-minted property
    that appears over-tight, or ``None``. R3 is last resort; never a BFO/RO
    axiom."""
    for t in mups:
        r = _restriction(g, t[2])
        if not r:
            continue
        for p in g.subjects(RDF.type, OWL.ObjectProperty):
            if _frag(p) != r[0] or "obolibrary.org" in str(p):
                continue
            for pred in (RDFS.range, RDFS.domain):
                trip = next(((p, pred, x) for x in g.objects(p, pred)), None)
                if trip is not None:
                    return trip
    return None


# ----------------------------------------------------------------------- public
def probe_confirmed_unsat(work_graph: Graph, bfo_path: Path) -> Optional[list[str]]:
    """Cheap, reliable full-signature unsatisfiable-class check for the gate's
    coherence certificate (§7): structural candidates confirmed by a single
    clone-probe reasoner run, unioned with anything the reasoner reports
    directly. No MUPS/classification, so it adds only ~2 HermiT runs. Returns the
    unsatisfiable IRIs, or ``None`` if the ontology is globally inconsistent."""
    ok, reported = _run(work_graph, bfo_path)
    if not ok:
        return None
    cands = _structural_candidates(work_graph)
    if not cands:
        return sorted(reported)
    _, confirmed = _run(work_graph, bfo_path, probe_iris=sorted(cands))
    return sorted(set(reported) | set(confirmed))


def probe_confirmed_unsat_file(artifact_path, bfo_path) -> Optional[list[str]]:
    g = Graph()
    g.parse(str(artifact_path))
    return probe_confirmed_unsat(g, Path(bfo_path))


def detect_graph(work_graph: Graph, bfo_path: Path) -> list[Finding]:
    """Full-signature detect + classify. One Finding per unsatisfiable class,
    caught via structural scan + clone-probe confirmation (plus any class the
    reasoner does report directly)."""
    ok, reported = _run(work_graph, bfo_path)
    if not ok:
        log.warning("realizable_misuse: ontology globally inconsistent")
        return [Finding("", "OTHER", "ontology is globally inconsistent", [])]
    cands = _structural_candidates(work_graph)
    _, confirmed = _run(work_graph, bfo_path, probe_iris=sorted(cands)) if cands else (True, set())
    unsat = sorted(set(reported) | set(confirmed))
    findings = []
    for iri in unsat:
        mups = _mups(work_graph, bfo_path, URIRef(iri))
        findings.append(_analyze(work_graph, iri, mups))
    return findings


def _apply(g: Graph, transform: dict) -> Optional[tuple]:
    """Apply one repair transform in place. Returns an undo token, or None."""
    rule = transform["rule"]
    s, p, o = transform["target"]
    if rule == "R4":                         # drop the contradictory complement
        g.remove((s, p, o))
        return ("re-add", (s, p, o))
    if rule in ("R1", "R2"):                 # swap the restriction's onProperty
        old_prop = next(g.objects(o, OWL.onProperty), None)
        new_prop = URIRef(OBO + transform["new_prop"])
        g.remove((o, OWL.onProperty, old_prop))
        g.add((o, OWL.onProperty, new_prop))
        decl = []
        if (new_prop, RDF.type, OWL.ObjectProperty) not in g:
            g.add((new_prop, RDF.type, OWL.ObjectProperty))
            decl.append((new_prop, RDF.type, OWL.ObjectProperty))
        if transform["new_prop"] in _RO_BY_TYPE_BY_FRAG:
            rng = URIRef(OBO + _RO_BY_TYPE_BY_FRAG[transform["new_prop"]])
            if (new_prop, RDFS.range, rng) not in g:
                g.add((new_prop, RDFS.range, rng))
                decl.append((new_prop, RDFS.range, rng))
        return ("restore-prop", (o, old_prop, new_prop, decl))
    if rule == "R3":                          # relax a locally-minted dom/range
        g.remove(transform["target"])
        return ("re-add", transform["target"])
    return None


def _undo(g: Graph, token: tuple) -> None:
    kind, payload = token
    if kind == "re-add":
        g.add(payload)
    elif kind == "restore-prop":
        node, old_prop, new_prop, decl = payload
        g.remove((node, OWL.onProperty, new_prop))
        g.add((node, OWL.onProperty, old_prop))
        for t in decl:
            g.remove(t)


@dataclass
class RepairResult:
    before_unsat: int
    after_unsat: int
    transforms: list[dict] = field(default_factory=list)
    remaining: list[dict] = field(default_factory=list)


def repair_graph(work_graph: Graph, bfo_path: Path,
                 max_rounds: int = 8) -> RepairResult:
    """Apply R1-R4 per class (all of a class's transforms together, then verify
    the class heals), roots first so cascades heal, until zero unsatisfiable
    classes or no further progress. Never deletes source content. A class that
    does not fully heal is rolled back and left for a human (OTHER)."""
    before = detect_graph(work_graph, bfo_path)
    before_n = len([f for f in before if f.class_iri])
    applied: list[dict] = []
    for _ in range(max_rounds):
        findings = detect_graph(work_graph, bfo_path)
        actionable = [f for f in findings if f.transforms and f.class_iri]
        if not actionable:
            break
        progress = False
        for f in actionable:
            tokens = [tok for t in f.transforms if (tok := _apply(work_graph, t))]
            healed = _class_sat(work_graph, bfo_path, URIRef(f.class_iri))
            ok, _ = _run(work_graph, bfo_path)
            if healed and ok:
                applied.append({
                    "class": _frag(f.class_iri), "pattern": f.pattern,
                    "rule": f.rule, "mups": f.mups, "detail": f.detail,
                })
                progress = True
            else:
                for tok in reversed(tokens):
                    _undo(work_graph, tok)
        if not progress:
            break
    remaining_findings = detect_graph(work_graph, bfo_path)
    after_n = len([f for f in remaining_findings if f.class_iri])
    remaining = [f.as_dict() for f in remaining_findings if f.class_iri]
    return RepairResult(before_n, after_n, applied, remaining)

def detect_file(artifact_path, bfo_path) -> list[dict]:
    g = Graph()
    g.parse(str(artifact_path))
    return [f.as_dict() for f in detect_graph(g, Path(bfo_path))]


def repair_file(artifact_path, bfo_path, out_path=None) -> RepairResult:
    src = Path(artifact_path)
    g = Graph()
    g.parse(str(src))
    res = repair_graph(g, Path(bfo_path))
    if res.transforms:
        g.serialize(destination=str(out_path or src), format="xml")
    return res


def repair_report_md(res: RepairResult, artifact: str) -> str:
    lines = ["# Realizable-misuse repair report", "",
             f"- artifact: `{artifact}`",
             f"- unsatisfiable before: **{res.before_unsat}**",
             f"- unsatisfiable after: **{res.after_unsat}**",
             f"- transforms applied: **{len(res.transforms)}** "
             f"(0 source-content deletions)", ""]
    if res.transforms:
        lines += ["## Transforms", "",
                  "| class | pattern | rule | justification (MUPS) |",
                  "|---|---|---|---|"]
        for t in res.transforms:
            mups = "; ".join(t["mups"]) or t["detail"]
            lines.append(f"| `{t['class']}` | {t['pattern']} | {t['rule']} | {mups} |")
        lines.append("")
    if res.remaining:
        lines += ["## Unrepaired (candidate source incoherence / OTHER)", ""]
        for r in res.remaining:
            lines.append(f"- `{r['class']}` — {r['detail']}")
    return "\n".join(lines) + "\n"
