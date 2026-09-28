"""Domain profiles (SPEC-bfo-agent-quality.md QS-E1, QS-F4).

A profile is an OWL file under ``ontology/profiles/<name>.owl`` holding
disjointness and category constraints over *canonical* classes (never over
extracted IRIs). A library opts in with ``"profiles": ["legal-hohfeld"]`` in
its ``manifest.json``.

Extracted classes are tied to the canonical ones by label alignment: a class
whose normalized label matches a canonical label or one of its
``bfoagent:alignLabel`` aliases is treated as a subclass of it. So
"Permission ⊑ Obligation" in an extraction becomes Privilege ⊓ Duty, which
legal-hohfeld makes empty.

Tier (FG-0 / FM-3): profile findings are CONTENT judgments.
  faithful -> ledger entries (``tier: "profile"``), never a rejection;
  curated  -> the caller rejects at the reasoner tier.

The check is structural (named-ancestor closure over working + BFO + profile)
so it is cheap, deterministic and needs no reasoner lock. For curated mode's
reasoner tier, :func:`profile_axioms_for` plus :func:`alignment_axioms` give
the axioms to merge into the scratch world before running HermiT.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef

from . import canonical as canon

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "ontology" / "profiles"
BFO_PATH = ROOT / "ontology" / "bfo.owl"


# --------------------------------------------------------------- building
def build_legal_hohfeld() -> Graph:
    """legal-hohfeld: the eight positions pairwise disjoint and all roles
    (D-2); legal acts (process) disjoint from legal documents (GDC); norm
    content a GDC (D-3)."""
    core = canon.build_sool_core()
    S = canon.SOOL
    keep = ({S[k] for k in canon.HOHFELD_POSITIONS}
            | {S.LegalPosition, S.LegalAct, S.LegalDocument, S.Norm})
    g = Graph()
    g.bind("sool", S); g.bind("obo", canon.OBO); g.bind("bfoagent", canon.BFOAGENT)
    onto = URIRef("http://davidkoepsell.com/sool/profiles/legal-hohfeld")
    g.add((onto, RDF.type, OWL.Ontology))
    g.add((onto, RDFS.comment, Literal(
        "Profile legal-hohfeld (SPEC-bfo-agent-quality QS-E1). Constraints over "
        "canonical SOoL classes; extracted classes are bound by label "
        "alignment. Faithful mode: violations are ledger findings only.")))
    for p in (canon.BFOAGENT.alignLabel, canon.BFOAGENT.designDecision):
        g.add((p, RDF.type, OWL.AnnotationProperty))
    for s, p, o in core:
        if s in keep:
            g.add((s, p, o))
    positions = [S[k] for k in canon.HOHFELD_POSITIONS]
    for i, a in enumerate(positions):
        for b in positions[i + 1:]:
            g.add((a, OWL.disjointWith, b))
    g.add((S.LegalAct, OWL.disjointWith, S.LegalDocument))
    return g


BUILDERS = {"legal-hohfeld": build_legal_hohfeld}


def write_profiles() -> list[Path]:
    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for name, build in BUILDERS.items():
        path = PROFILES_DIR / f"{name}.owl"
        build().serialize(destination=str(path), format="xml")
        out.append(path)
    return out


# ---------------------------------------------------------------- loading
def profile_names(manifest: dict) -> list[str]:
    return [str(p) for p in (manifest.get("profiles") or [])]


def profile_axioms_for(manifest: dict) -> Graph:
    """Merged axioms of every profile the manifest activates. Missing
    profile files raise, because a declared-but-absent profile would make
    the gate silently weaker than the manifest claims."""
    g = Graph()
    for name in profile_names(manifest):
        path = PROFILES_DIR / f"{name}.owl"
        if not path.exists():
            raise FileNotFoundError(f"profile {name!r} not found at {path}")
        g.parse(str(path))
    return g


def alignment_axioms(working: Graph, profile: Graph) -> Graph:
    """``extracted ⊑ canonical`` for every working class whose normalized
    label matches a canonical label or alias in ``profile``."""
    idx = canon.canonical_label_index(profile)
    out = Graph()
    for c in set(working.subjects(RDF.type, OWL.Class)):
        if not isinstance(c, URIRef):
            continue
        for lbl in list(working.objects(c, RDFS.label)) + [
                Literal(str(c).rsplit("#", 1)[-1])]:
            hit = idx.get(canon.norm_key(str(lbl)))
            if hit and URIRef(hit) != c:
                out.add((c, RDFS.subClassOf, URIRef(hit)))
    return out


# ---------------------------------------------------------------- checking
def _named_parents(graphs: Iterable[Graph]) -> dict[URIRef, set[URIRef]]:
    parents: dict[URIRef, set[URIRef]] = {}
    for g in graphs:
        for s, o in g.subject_objects(RDFS.subClassOf):
            if isinstance(s, URIRef) and isinstance(o, URIRef):
                parents.setdefault(s, set()).add(o)
    return parents


def _disjoint_pairs(graphs: Iterable[Graph]) -> set[frozenset]:
    pairs = set()
    for g in graphs:
        for s, o in g.subject_objects(OWL.disjointWith):
            if isinstance(s, URIRef) and isinstance(o, URIRef):
                pairs.add(frozenset((s, o)))
    return pairs


def _clashes(nodes: Iterable[URIRef], parents, pairs, cache) -> set[frozenset]:
    def anc(x: URIRef) -> frozenset:
        if x in cache:
            return cache[x]
        cache[x] = frozenset()  # cycle guard
        acc = {x}
        for p in parents.get(x, ()):
            acc |= anc(p)
        cache[x] = frozenset(acc)
        return cache[x]

    closure = set()
    for n in nodes:
        closure |= anc(n)
    return {pair for pair in pairs if pair <= closure}


def _label(graphs, x: URIRef) -> str:
    for g in graphs:
        for lbl in g.objects(x, RDFS.label):
            return str(lbl)
    return str(x).rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def check_profiles(working: Graph, manifest: dict, *,
                   subjects: Optional[Iterable[str]] = None,
                   bfo: Optional[Graph] = None) -> list[dict]:
    """Profile violations: classes (or individuals) whose named ancestry,
    once extracted classes are aligned to canonical ones, contains a
    disjoint pair that exists only because of the profile. BFO-only
    straddles are the coherence gate's business and are not repeated here.

    ``subjects`` limits the check to those IRIs (per-commit use)."""
    names = profile_names(manifest)
    if not names:
        return []
    profile = profile_axioms_for(manifest)
    if bfo is None:
        bfo = Graph(); bfo.parse(str(BFO_PATH))
    align = alignment_axioms(working, profile)
    with_p = [working, bfo, profile, align]
    parents_p, pairs_p = _named_parents(with_p), _disjoint_pairs(with_p)
    parents_0, pairs_0 = _named_parents([working, bfo]), _disjoint_pairs([working, bfo])
    cache_p: dict = {}
    cache_0: dict = {}

    targets: list[tuple[URIRef, list[URIRef], str]] = []
    wanted = {URIRef(s) for s in subjects} if subjects is not None else None
    for c in set(working.subjects(RDF.type, OWL.Class)):
        if isinstance(c, URIRef) and (wanted is None or c in wanted):
            targets.append((c, [c], "class"))
    for i in set(working.subjects(RDF.type, OWL.NamedIndividual)):
        if isinstance(i, URIRef) and (wanted is None or i in wanted):
            types = [t for t in working.objects(i, RDF.type)
                     if isinstance(t, URIRef) and t != OWL.NamedIndividual]
            targets.append((i, types, "individual"))

    findings = []
    for subj, nodes, kind in targets:
        new = (_clashes(nodes, parents_p, pairs_p, cache_p)
               - _clashes(nodes, parents_0, pairs_0, cache_0))
        for pair in sorted(new, key=lambda p: sorted(map(str, p))):
            a, b = sorted(pair, key=str)
            la, lb = _label(with_p, a), _label(with_p, b)
            findings.append({
                "subject": str(subj),
                "subject_label": _label([working], subj),
                "kind": kind,
                "disjoint_pair": [str(a), str(b)],
                "profiles": names,
                "reason": (f"{_label([working], subj)} violates "
                           f"{'+'.join(names)}: {la} ⊓ {lb} = ⊥"),
            })
    return findings


def ledger_findings(working_path: Path, findings: list[dict],
                    provenance: Optional[dict] = None) -> list[str]:
    """Faithful mode: record profile violations as ledger entries. Nothing
    in the ontology changes (FG-0)."""
    from . import incoherence_ledger as ledger

    ids = []
    for f in findings:
        ids.append(ledger.append(working_path, {
            "mode": "faithful",
            "tier": "profile",
            "subjects": [f["subject"]],
            "gate_result": {"tier": "profile", "reason": f["reason"],
                            "disjoint_pair": f["disjoint_pair"]},
            "exclude_axioms": [],
            "provenance": provenance or {},
            "attribution": "source",
        }))
    return ids


def apply_profiles(working_path: Path, manifest: dict, fidelity: str, *,
                   subjects: Optional[Iterable[str]] = None,
                   provenance: Optional[dict] = None) -> dict:
    """One call for the commit path or end of job. Faithful: ledger and
    return ``reject=False``. Curated: ``reject=True`` when anything fired;
    the caller turns that into a reasoner-tier rejection."""
    g = Graph(); g.parse(str(working_path))
    findings = check_profiles(g, manifest, subjects=subjects)
    if fidelity == "faithful":
        ids = ledger_findings(working_path, findings, provenance)
        return {"findings": findings, "ledger_ids": ids, "reject": False}
    return {"findings": findings, "ledger_ids": [], "reject": bool(findings)}
