"""Gate-recall benchmark (SPEC-bfo-agent-quality.md QS-E2).

Each fixture is a known-bad proposal labelled with the tier that should
catch it. Every fixture runs on its own scratch copy of an artifact (never
the artifact itself) through the real pipeline:

  1. ``coherence_gate.gate``           -> construction / lint / reasoner
  2. ``OntologyManager.commit_proposal`` -> write-path guards (QS-A*)
  3. a per-fixture defect probe on the saved graph: if the defect is not
     there, the tool refused or converted it
  4. ``profiles.check_profiles``        -> profile tier (QS-E1), when enabled

A fixture is *caught* when any step stopped the defect from being persisted
as-is. Recall is reported per expected tier, with and without profiles.

    python -m evaluation.gate_recall [--artifact path/to/working.owl]
                                     [--seed 7] [--json out.json]
                                     [--no-reasoner]
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rdflib import BNode, Graph, OWL, RDFS, URIRef  # noqa: E402

WORKING = "http://davidkoepsell.com/bfo-agent/working#"
OBO = "http://purl.obolibrary.org/obo/"
BFO_PATH = ROOT / "ontology" / "bfo.owl"
PROFILE_MANIFEST = {"profiles": ["legal-hohfeld"]}


@dataclass
class Fixture:
    id: str
    description: str
    expected: str
    entities: list[dict]
    relations: list[dict]
    defect: Callable[[Graph], bool]
    subjects: tuple[str, ...] = ()


def _cls(local: str, label: str, bfo: str, bfo_label: str,
         parent: str | None = None) -> dict:
    return {"label": label, "iri_suggestion": f"working:{local}",
            "bfo_type": bfo, "bfo_label": bfo_label, "kind": "class",
            "parent_class": parent, "rationale": "gate-recall fixture",
            # Keep PC-14 (QS-D1) out of the way so each fixture is credited
            # to the tier that targets it, not to the missing definition.
            "definition_status": "absent-in-source"}


def _w(local: str) -> URIRef:
    return URIRef(WORKING + local)


def _has(g: Graph, s, p, o) -> bool:
    return (s, p, o) in g


def _any_iri(g: Graph, pred: Callable[[str], bool]) -> bool:
    return any(isinstance(t, URIRef) and pred(str(t))
               for triple in g for t in triple)


def _sentence_iri(iri: str) -> bool:
    if not iri.startswith(WORKING):
        return False
    frag = iri[len(WORKING):]
    return bool(re.search(r"\s", frag)) or len(re.findall(r"[A-Z][a-z]+", frag)) >= 6


FIXTURES: list[Fixture] = [
    Fixture(
        "bfo_disjointness", "quality ⊑ process", "reasoner",
        [_cls("Loudness", "Loudness", "BFO_0000019", "quality")],
        [{"s": "working:Loudness", "p": "rdfs:subClassOf", "o": "BFO_0000015"}],
        lambda g: _has(g, _w("Loudness"), RDFS.subClassOf, URIRef(OBO + "BFO_0000015"))),
    Fixture(
        "mangled_predicate", "working#rdfs:subClassOf predicate", "QS-A4",
        [_cls("Tort", "Tort", "BFO_0000015", "process"),
         _cls("Wrong", "Wrong", "BFO_0000015", "process")],
        [{"s": "working:Tort", "p": "working:rdfs:subClassOf", "o": "working:Wrong"}],
        lambda g: _any_iri(g, lambda i: i.startswith(WORKING) and ":" in i[len(WORKING):])),
    Fixture(
        "bare_ro", "RO_0000052 bare", "QS-A1",
        [_cls("Standing", "Standing", "BFO_0000023", "role"),
         _cls("Litigant", "Litigant", "BFO_0000040", "material entity")],
        [{"s": "working:Standing", "p": "RO_0000052", "o": "working:Litigant"}],
        lambda g: _any_iri(g, lambda i: i == WORKING + "RO_0000052")),
    Fixture(
        "bnode_object", "_:r1 as object", "QS-A2",
        [_cls("Venue", "Venue", "BFO_0000023", "role")],
        [{"s": "working:Venue", "p": "BFO_0000197", "o": "_:r1"}],
        lambda g: _any_iri(g, lambda i: "_:" in i)),
    Fixture(
        "punned_realizes", "class-to-class realizes triple", "QS-A3",
        [_cls("Adjudication", "Adjudication", "BFO_0000015", "process"),
         _cls("JudicialRole", "Judicial Role", "BFO_0000023", "role")],
        [{"s": "working:Adjudication", "p": "BFO_0000055", "o": "working:JudicialRole"}],
        lambda g: _has(g, _w("Adjudication"), URIRef(OBO + "BFO_0000055"), _w("JudicialRole"))),
    Fixture(
        "kernel_restriction", "restriction on a kernel class", "kernel-guard",
        [_cls("Person", "Person", "BFO_0000040", "material entity")],
        [{"s": "BFO_0000023", "p": "rdfs:subClassOf", "o": "BFO_0000197 some working:Person"}],
        lambda g: any(isinstance(o, BNode) and (o, OWL.onProperty, None) in g
                      for o in g.objects(URIRef(OBO + "BFO_0000023"), RDFS.subClassOf))),
    Fixture(
        "hohfeld_profile", "Permission ⊑ Obligation", "profile",
        [_cls("Permission", "Permission", "BFO_0000023", "role"),
         _cls("Obligation", "Obligation", "BFO_0000023", "role")],
        [{"s": "working:Permission", "p": "rdfs:subClassOf", "o": "working:Obligation"}],
        lambda g: _has(g, _w("Permission"), RDFS.subClassOf, _w("Obligation")),
        subjects=(WORKING + "Permission",)),
    Fixture(
        "sentence_iri", "sentence as object IRI", "QS-A6",
        [_cls("Court", "Court", "BFO_0000040", "material entity")],
        [{"s": "working:Court", "p": "BFO_0000056",
          "o": "the court must weigh the evidence fairly and without delay"}],
        lambda g: _any_iri(g, _sentence_iri)),
]


def _proposal(fx: Fixture):
    from app.schema import Entity, Proposal, Relation

    return Proposal(
        session_id="gate-recall", utterance=fx.description,
        entities=[Entity(**e) for e in fx.entities],
        relations=[Relation(rationale="gate-recall fixture", **r)
                   for r in fx.relations])


def run_fixture(fx: Fixture, artifact: Path | None, *, reasoner: bool,
                with_profiles: bool) -> dict:
    from app import coherence_gate, profiles
    from app.ontology_manager import OntologyManager

    with tempfile.TemporaryDirectory(prefix="gate_recall_") as td:
        working = Path(td) / "working.owl"
        if artifact is not None:
            shutil.copy(artifact, working)  # never touch the artifact itself
        mgr = OntologyManager(BFO_PATH, working)
        proposal = _proposal(fx)
        caught_by, detail = None, ""

        res = coherence_gate.gate(proposal, mgr, run_reasoner=reasoner)
        if res.outcome.value != "accept":
            caught_by, detail = res.tier.value, res.reason or ""
        else:
            try:
                warnings = mgr.commit_proposal(proposal, verify=reasoner)
                if warnings:
                    detail = "; ".join(warnings)[:300]
            except Exception as e:  # commit-path refusal / rollback
                caught_by, detail = "commit", f"{type(e).__name__}: {e}"[:300]
            if caught_by is None:
                g = Graph(); g.parse(str(working))
                if not fx.defect(g):
                    caught_by = "write-path" if detail else "converted"
                elif with_profiles:
                    found = profiles.check_profiles(
                        g, PROFILE_MANIFEST, subjects=fx.subjects or None)
                    if found:
                        caught_by, detail = "profile", found[0]["reason"]
        return {"id": fx.id, "description": fx.description,
                "expected": fx.expected, "caught": caught_by is not None,
                "caught_by": caught_by or "missed", "detail": detail}


def run(artifact: Path | None = None, *, seed: int = 7, reasoner: bool = True,
        with_profiles: bool = True) -> dict:
    fixtures = list(FIXTURES)
    random.Random(seed).shuffle(fixtures)  # results must not depend on order
    rows = [run_fixture(fx, artifact, reasoner=reasoner,
                        with_profiles=with_profiles) for fx in fixtures]
    rows.sort(key=lambda r: r["id"])
    per_tier: dict[str, dict] = {}
    for r in rows:
        t = per_tier.setdefault(r["expected"], {"fixtures": 0, "caught": 0})
        t["fixtures"] += 1
        t["caught"] += int(r["caught"])
    return {"seed": seed, "artifact": str(artifact) if artifact else None,
            "reasoner": reasoner, "profiles": with_profiles,
            "recall": round(sum(r["caught"] for r in rows) / len(rows), 3),
            "per_tier": per_tier, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--artifact", type=Path, default=None,
                    help="working.owl to copy per fixture (default: empty)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--no-reasoner", action="store_true")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--min-recall", type=float, default=None,
                    help="exit non-zero when recall (with profiles) is below")
    a = ap.parse_args(argv)
    out = {
        "with_profiles": run(a.artifact, seed=a.seed,
                             reasoner=not a.no_reasoner, with_profiles=True),
        "without_profiles": run(a.artifact, seed=a.seed,
                                reasoner=not a.no_reasoner, with_profiles=False),
    }
    for name, res in out.items():
        print(f"{name}: recall {res['recall']}")
        for r in res["rows"]:
            print(f"  {r['id']:<20} expected {r['expected']:<13} "
                  f"-> {r['caught_by']}")
    if a.json:
        a.json.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    if a.min_recall is not None and out["with_profiles"]["recall"] < a.min_recall:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
