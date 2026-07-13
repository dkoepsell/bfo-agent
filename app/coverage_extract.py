"""Ephemeral coverage extraction (Track-2: analyze NEW policy wording).

Extraction runs into a fresh owlready World seeded on the coverage kernel and is
never persisted, so it cannot touch any library corpus or a running feed.

Two paths:
  * structured  - a list of clause dicts (deterministic, no LLM). This is the
                  builder the LLM path also targets.
  * text        - raw wording; _llm_extract turns it into structured clauses via
                  a bounded coverage-extraction call, then the same builder runs.

Structured clause vocabulary (well-formed coverage claim language):
  {"type":"grant"|"exclusion"|"carveback", "peril":"<Name>",
   "features":["<FeatureName>", ...], "modifies":"<PerilName>"?}
  {"type":"disjoint", "classes":["<Name>", "<Name>", ...]}
  {"type":"fact_pattern", "exhibits":["<FeatureName>", ...], "id":"<loss id>"?}
"""
from __future__ import annotations

import os
from typing import Optional

import owlready2 as o2

# Import the kernel builder from the demonstrator package (single source of truth
# for the coverage kernel; reused, not reimplemented).
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from nfip import kernel as _kernel  # noqa: E402

EPHEMERAL_IRI = "https://sool.tamu.edu/coverage/ephemeral"


def _build(clauses: list[dict]) -> tuple[o2.World, o2.Ontology, dict, dict]:
    w = o2.World()
    kern = _kernel.build_coverage_kernel(w)
    Loss = kern.Loss
    onto = w.get_ontology(EPHEMERAL_IRI)
    onto.imported_ontologies.append(kern)

    report = {"perils": [], "features": [], "disjoint": [], "fact_patterns": 0,
              "skipped": []}
    with onto:
        class LossFeature(o2.Thing):
            pass

    feat_cache: dict[str, type] = {}
    peril_cache: dict[str, type] = {}

    def feature(name: str):
        if name not in feat_cache:
            with onto:
                c = o2.types.new_class(name, (LossFeature,))
            feat_cache[name] = c
            report["features"].append(name)
        return feat_cache[name]

    def peril(name: str, features: list[str]):
        if name not in peril_cache:
            with onto:
                c = o2.types.new_class(name, (Loss,))
                if features:
                    expr = Loss
                    for f in features:
                        expr = expr & kern.exhibits.some(feature(f))
                    c.equivalent_to = [expr]
            peril_cache[name] = c
            report["perils"].append(name)
        return peril_cache[name]

    for cl in clauses:
        t = cl.get("type")
        if t in ("grant", "exclusion", "carveback"):
            p = peril(cl["peril"], cl.get("features", []))
            with onto:
                if t == "grant":
                    p.is_a.append(kern.GrantedLoss)
                elif t == "exclusion":
                    p.is_a.append(kern.ExcludedLoss)
                else:
                    p.is_a.append(kern.RestoredLoss)
        elif t == "disjoint":
            named = [peril_cache.get(n) or feat_cache.get(n) for n in cl.get("classes", [])]
            named = [c for c in named if c is not None]
            if len(named) >= 2:
                with onto:
                    o2.AllDisjoint(named)
                report["disjoint"].append(cl["classes"])
            else:
                report["skipped"].append(cl)
        elif t == "fact_pattern":
            with onto:
                loss = Loss(cl.get("id", "wording_loss"))
                loss.exhibits = [feature(f)() for f in cl.get("exhibits", [])]
            report["fact_patterns"] += 1
        else:
            report["skipped"].append(cl)

    handles = {"Loss": Loss, "kernel": kern, "onto": onto}
    return w, onto, handles, report


def _llm_extract(text: str) -> list[dict]:
    """Turn raw policy wording into structured coverage clauses via a bounded
    LLM call. Kept thin and dependency-lazy; monkeypatchable for tests."""
    prompt = COVERAGE_EXTRACT_PROMPT.format(text=text[:8000])
    raw = _call_llm(prompt)
    import json
    try:
        data = json.loads(raw)
    except Exception:
        # tolerate fenced json
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        data = json.loads(raw)
    return data if isinstance(data, list) else data.get("clauses", [])


def _call_llm(prompt: str) -> str:
    """Single bounded model call. Isolated so tests can monkeypatch it."""
    from app import cached_client
    client = cached_client.get_client()
    return client.complete(prompt)


COVERAGE_EXTRACT_PROMPT = """You convert insurance policy wording into a structured coverage model.
Return ONLY a JSON array of clause objects. Vocabulary:
  {{"type":"grant"|"exclusion"|"carveback","peril":"<CamelCaseName>","features":["<FeatureName>"...],"modifies":"<PerilName>"}}
  {{"type":"disjoint","classes":["<Name>","<Name>"]}}
  {{"type":"fact_pattern","exhibits":["<FeatureName>"...]}}
Rules: a carveback MUST name the peril it modifies; if the wording says two
categories are mutually exclusive, emit a disjoint clause; features are the
physical criteria a loss must exhibit. Names are valid NCNames.

WORDING:
{text}
"""


def extract_ephemeral(clauses_or_text, seed_path: Optional[str] = None):
    """Public entry: structured list -> deterministic; str -> LLM then build."""
    if isinstance(clauses_or_text, str):
        clauses = _llm_extract(clauses_or_text)
    else:
        clauses = list(clauses_or_text)
    return _build(clauses)
