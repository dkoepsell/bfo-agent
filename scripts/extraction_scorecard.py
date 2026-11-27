#!/usr/bin/env python3
"""extraction_scorecard.py: content-tier quality scorecard for one bfo-agent run.

Complements ``scripts/ontology_audit.py`` (construction tier, QS-G1). The audit
asks whether the artifact is well formed; the scorecard asks whether what was
extracted is *right*: in scope, placed in a hierarchy, defined, encoded at the
strength the source text has, and stable from one run to the next.

Report-only. Nothing here alters, drops or rolls back a claim, so it is
consistent with FG-0 in both fidelity modes. ``--fail-on`` turns selected
metrics into gates for CI or curated-mode finalization, as the audit does.

Usage:
    python scripts/extraction_scorecard.py run.owl
    python scripts/extraction_scorecard.py run.owl --previous prior.owl \
        --source source.txt --scope scope.yaml --gold gold.json \
        --reasoner --json card.json --fail-on flat_share>0.5

Sections (each key in the JSON report):
    counts        classes, individuals, restrictions
    structure     flat_share, definition_absent_share, possible_relations,
                  caveat_as_definition
    strength      false-universal candidates: participation restrictions on
                  material entities, axioms whose source span is modal or
                  generic, quantifier-defaulted axioms
    category      kind-like individuals, hasValue in class axioms,
                  taxa as material entities, self or parent distinctFrom
    surface       plural labels, label case mix, span noise, label collisions
    scope         share of classes no scope term reaches (needs --scope)
    drift         against a previous run (needs --previous)
    reasoner      unsatisfiable classes under BFO (needs --reasoner)
    gold          class and axiom precision / recall (needs --gold)
    construction  the QS-G1 audit summary, when the audit module loads
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import tempfile
import warnings
from pathlib import Path

import rdflib
from rdflib import OWL, RDF, RDFS, BNode, Literal, URIRef

ROOT = Path(__file__).resolve().parent.parent
OBO = "http://purl.obolibrary.org/obo/"
META = "http://davidkoepsell.com/bfo-agent/meta#"
IAO_DEF = URIRef(OBO + "IAO_0000115")
SPAN = URIRef(META + "sourceSpan")
DEF_STATUS = URIRef(META + "definitionStatus")
POSSIBLE = URIRef(META + "possibleRelation")
DISTINCT = URIRef(META + "distinctFrom")
QDEFAULT = URIRef(META + "quantifierDefaulted")

BFO_NAMES = {
    "BFO_0000001": "entity", "BFO_0000002": "continuant", "BFO_0000003": "occurrent",
    "BFO_0000004": "independent continuant", "BFO_0000040": "material entity",
    "BFO_0000030": "object", "BFO_0000027": "object aggregate",
    "BFO_0000024": "fiat object part", "BFO_0000141": "immaterial entity",
    "BFO_0000020": "specifically dependent continuant", "BFO_0000019": "quality",
    "BFO_0000017": "realizable entity", "BFO_0000016": "disposition",
    "BFO_0000023": "role", "BFO_0000034": "function",
    "BFO_0000031": "generically dependent continuant",
    "BFO_0000015": "process", "BFO_0000035": "process boundary",
    "BFO_0000008": "temporal region", "BFO_0000006": "spatial region",
    "BFO_0000029": "site", "BFO_0000145": "relational quality",
    "BFO_0000182": "history",
}
MATERIAL = {"BFO_0000040", "BFO_0000030", "BFO_0000027", "BFO_0000024"}
PARTICIPATES_IN = URIRef(OBO + "BFO_0000056")
HAS_PARTICIPANT = URIRef(OBO + "BFO_0000057")

# A source span carrying one of these cues states a tendency, a capability or a
# typical case, not a universal. Encoded as `SubClassOf p some X`, it says
# something about every instance.
MODAL = re.compile(
    r"\b(often|usually|typically|generally|commonly|frequently|sometimes|"
    r"tend(?:s|ed)? to|can|could|may|might|most|many|some(?! of the)|"
    r"rarely|seldom|likely|potential(?:ly)?|allows?|enables?)\b", re.I)
# A definition that reads as a bias or an evaluation is a caveat, not a genus
# and differentia.
CAVEAT_DEF = re.compile(
    r"\b(greater importance|overestimat|underestimat|bias|tends? to|"
    r"more difficult|less (?:reliable|accurate)|often)\b", re.I)
# a binomial in parentheses, as in "Barn Owl (Tyto alba)"
BINOMIAL = re.compile(r"\(([A-Z][a-z]+ [a-z]{3,})\)")
NOT_PLURAL = re.compile(r"(ss|us|is|sis|ics|ous|ness|ies?s)$")
PLURAL_OK = {"contents", "species", "series", "means", "data", "remains",
             "physics", "genetics", "statistics"}


# ---------------------------------------------------------------- loading
def load(path: str | Path) -> rdflib.Graph:
    g = rdflib.Graph()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        g.parse(str(path))
    return g


def local_ns(g: rdflib.Graph) -> str:
    for o in g.subjects(RDF.type, OWL.Ontology):
        s = str(o)
        return s if s.endswith(("#", "/")) else s + "#"
    return "http://davidkoepsell.com/bfo-agent/working#"


def is_local(u, ns: str) -> bool:
    return isinstance(u, URIRef) and str(u).startswith(ns)


def short(u) -> str:
    s = str(u)
    return s.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


def label(g, u) -> str:
    v = g.value(u, RDFS.label)
    return str(v) if v is not None else short(u)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class View:
    """Local classes, individuals, parents and restrictions of one graph."""

    def __init__(self, g: rdflib.Graph):
        self.g = g
        self.ns = local_ns(g)
        self.classes = sorted({c for c in g.subjects(RDF.type, OWL.Class)
                               if is_local(c, self.ns)}, key=str)
        self.individuals = sorted({i for i in g.subjects(RDF.type, OWL.NamedIndividual)
                                   if is_local(i, self.ns)}, key=str)
        self.parents = {c: [o for o in g.objects(c, RDFS.subClassOf)
                            if isinstance(o, URIRef)] for c in self.classes}
        self.restrictions = {c: [self._restr(b) for b in g.objects(c, RDFS.subClassOf)
                                 if isinstance(b, BNode)
                                 and (b, RDF.type, OWL.Restriction) in g]
                             for c in self.classes}

    def _restr(self, b):
        g = self.g
        prop = g.value(b, OWL.onProperty)
        for kind in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue):
            f = g.value(b, kind)
            if f is not None:
                return {"node": b, "prop": prop, "kind": short(kind), "filler": f}
        return {"node": b, "prop": prop, "kind": "other", "filler": None}

    def bfo_ancestors(self, c, seen=None) -> set[str]:
        seen = seen if seen is not None else set()
        out = set()
        for p in self.parents.get(c, []):
            if p in seen:
                continue
            seen.add(p)
            s = str(p)
            if s.startswith(OBO + "BFO_"):
                out.add(short(p))
            elif p in self.parents:
                out |= self.bfo_ancestors(p, seen)
        return out

    def category(self, c) -> str:
        anc = self.bfo_ancestors(c)
        named = sorted(BFO_NAMES.get(a, a) for a in anc)
        return "+".join(named) if named else "none"

    def spans(self, u) -> list[str]:
        return [str(s) for s in self.g.objects(u, SPAN)]


# ---------------------------------------------------------------- sections
def structure(v: View) -> dict:
    g = v.g
    flat = [c for c in v.classes
            if v.parents[c] and all(not is_local(p, v.ns) for p in v.parents[c])]
    orphan = [c for c in v.classes if not v.parents[c]]
    absent = [c for c in v.classes
              if (c, DEF_STATUS, Literal("absent-in-source")) in g
              or g.value(c, IAO_DEF) is None]
    caveat = [c for c in v.classes
              if any(CAVEAT_DEF.search(str(d)) for d in g.objects(c, IAO_DEF))]
    n = max(len(v.classes), 1)
    return {
        "flat_share": round(len(flat) / n, 4),
        "flat_classes": len(flat),
        "orphan_classes": [label(g, c) for c in orphan],
        "definition_absent_share": round(len(absent) / n, 4),
        "possible_relations": sum(len(list(g.objects(c, POSSIBLE))) for c in v.classes),
        "caveat_as_definition": [label(g, c) for c in caveat],
        "max_fanout": max((sum(1 for k in v.classes if c in v.parents[k])
                           for c in set(p for ps in v.parents.values() for p in ps)),
                          default=0),
    }


def strength(v: View) -> dict:
    g = v.g
    rows, modal_rows = [], []
    for c, rs in v.restrictions.items():
        material = bool(v.bfo_ancestors(c) & MATERIAL)
        modal = any(MODAL.search(s) for s in v.spans(c))
        for r in rs:
            item = {"class": label(g, c), "property": short(r["prop"]),
                    "kind": r["kind"],
                    "filler": label(g, r["filler"]) if r["filler"] is not None else None}
            if material and r["prop"] == PARTICIPATES_IN and r["kind"] == "someValuesFrom":
                rows.append({**item, "modal_span": modal})
            if modal and r["kind"] == "someValuesFrom":
                modal_rows.append(item)
    # axiom annotations (owl:Axiom reification) marking a defaulted quantifier
    defaulted = sum(1 for a in g.subjects(RDF.type, OWL.Axiom)
                    if (a, QDEFAULT, None) in g)
    n_some = sum(1 for rs in v.restrictions.values() for r in rs
                 if r["kind"] == "someValuesFrom")
    return {
        "some_restrictions": n_some,
        "material_participation": len(rows),
        "material_participation_modal": sum(1 for r in rows if r["modal_span"]),
        "material_participation_rows": rows,
        "modal_span_axioms": len(modal_rows),
        "modal_span_axiom_rows": modal_rows,
        "quantifier_defaulted_annotated": defaulted,
        "quantifier_annotation_missing": n_some > 0 and defaulted == 0
        and (QDEFAULT, RDF.type, OWL.AnnotationProperty) in g,
    }


def category(v: View) -> dict:
    g = v.g
    kind_like = []
    for i in v.individuals:
        lab = label(g, i)
        if lab[:1].islower() and not re.search(r"\d", lab):
            kind_like.append(lab)
    has_value = [label(g, c) for c, rs in v.restrictions.items()
                 for r in rs if r["kind"] == "hasValue"]
    taxa = []
    for u in list(v.classes) + list(v.individuals):
        lab = label(g, u)
        types = set(g.objects(u, RDF.type)) | set(v.parents.get(u, []))
        material = (bool(v.bfo_ancestors(u) & MATERIAL) if u in v.parents
                    else any(short(t) in MATERIAL for t in types))
        if material and (norm(lab) in ("species", "taxon", "genus")
                         or BINOMIAL.search(lab)):
            taxa.append(lab)
        for t in g.objects(u, RDF.type):
            if is_local(t, v.ns) and norm(label(g, t)) in ("species", "taxon"):
                taxa.append(lab)
    self_distinct = [label(g, c) for c in v.classes
                     for d in g.objects(c, DISTINCT) if d == c or d in v.parents[c]]
    return {
        "kind_like_individuals": sorted(set(kind_like)),
        "has_value_in_class_axioms": has_value,
        "taxa_as_material_entities": sorted(set(taxa)),
        "distinct_from_self_or_parent": self_distinct,
    }


def surface(v: View, source_text: str | None) -> dict:
    g = v.g
    labels = [(u, label(g, u)) for u in v.classes]

    def head(l: str) -> str:  # "determination of original volumes" -> determination
        return re.split(r" (?:of|for|in|from|with|on) ", l)[0].split()[-1].lower()
    plural = [l for _, l in labels
              if head(l).endswith("s") and not NOT_PLURAL.search(head(l))
              and head(l) not in PLURAL_OK]
    upper = sum(1 for _, l in labels if len(l) > 1 and l[0].isupper() and l[1].islower())
    by_norm = collections.defaultdict(list)
    for u, l in labels:
        by_norm[norm(l)].append(short(u))
    collisions = {k: v_ for k, v_ in by_norm.items() if len(v_) > 1}
    spans = [s for u in v.classes for s in v.spans(u)]
    newline = sum(1 for s in spans if "\n" in s)
    header = sum(1 for s in spans if re.search(r"\b[A-Z]{3,}(?: [A-Z]{3,})+\b", s))
    out = {
        "plural_labels": plural,
        "label_case_upper_share": round(upper / max(len(labels), 1), 4),
        "label_collisions": collisions,
        "spans": len(spans),
        "spans_with_line_breaks": newline,
        "spans_with_header_text": header,
    }
    if source_text is not None:
        flat = re.sub(r"\s+", " ", source_text)
        low = flat.lower()
        missing = truncated = 0
        for s in spans:
            q = re.sub(r"\s+", " ", s).strip().lower()
            i = low.find(q[:80])
            if i < 0:
                missing += 1
            elif i > 0 and low[i - 1].isalpha():
                truncated += 1
        out["spans_not_in_source"] = missing
        out["spans_truncated_start"] = truncated
    return out


def scope(v: View, scope_path: str) -> dict:
    """Scope file: YAML or JSON with `terms` (keywords) and optionally
    `questions` (competency questions; their content words become terms)."""
    text = Path(scope_path).read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:
        data = json.loads(text)
    terms = {norm(t) for t in data.get("terms", []) if norm(t)}
    stop = {"what", "which", "does", "from", "with", "that", "this", "were",
            "have", "their", "there", "into", "about", "each", "when", "where",
            "how", "the", "and", "for", "are", "was"}
    for q in data.get("questions", []):
        terms |= {norm(w) for w in re.findall(r"[A-Za-z][A-Za-z-]{3,}", q)
                  if w.lower() not in stop}
    terms = {t[:-1] if t.endswith("s") and len(t) > 4 else t for t in terms}
    g = v.g

    def hit(c) -> bool:
        words = [norm(w) for w in re.findall(r"[A-Za-z]+", label(g, c))]
        words = [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words]
        return any(w in terms for w in words) or norm(label(g, c)) in terms

    reached = {c for c in v.classes if hit(c)}
    changed = True
    while changed:  # a parent or child of an in-scope class is in scope
        changed = False
        for c in v.classes:
            if c in reached:
                continue
            if any(p in reached for p in v.parents[c]) or \
               any(c in v.parents[k] for k in reached):
                reached.add(c)
                changed = True
    out = [label(g, c) for c in v.classes if c not in reached]
    return {"terms": len(terms), "out_of_scope_share":
            round(len(out) / max(len(v.classes), 1), 4),
            "out_of_scope_classes": out}


def drift(v: View, prev: View) -> dict:
    cur = {short(c): c for c in v.classes}
    old = {short(c): c for c in prev.classes}
    shared = cur.keys() & old.keys()
    cat_conflicts, label_changes = [], []
    for k in sorted(shared):
        a, b = prev.category(old[k]), v.category(cur[k])
        if a != b:
            cat_conflicts.append({"class": k, "previous": a, "current": b})
        la, lb = label(prev.g, old[k]), label(v.g, cur[k])
        if norm(la) != norm(lb):
            label_changes.append({"class": k, "previous": la, "current": lb})
    # a dropped class whose label reappears under a new IRI was re-minted
    new_by_label = {norm(label(v.g, cur[k])): k for k in cur.keys() - old.keys()}
    reminted = [{"previous": k, "current": new_by_label[norm(label(prev.g, old[k]))]}
                for k in sorted(old.keys() - cur.keys())
                if norm(label(prev.g, old[k])) in new_by_label]
    dropped = sorted(old.keys() - cur.keys())
    return {
        "previous_classes": len(old), "current_classes": len(cur),
        "shared": len(shared), "dropped": len(dropped), "added": len(cur.keys() - old.keys()),
        "retention": round(len(shared) / max(len(old), 1), 4),
        "dropped_classes": dropped,
        "category_conflicts": cat_conflicts,
        "label_changes": label_changes,
        "reminted": reminted,
    }


def reasoner(path: str, bfo: str | None) -> dict:
    """Unsatisfiable classes under BFO's disjointness axioms (HermiT)."""
    try:
        import owlready2 as o
    except Exception as e:  # pragma: no cover
        return {"error": f"owlready2 unavailable: {e}"}
    bfo_path = bfo or str(ROOT / "ontology" / "bfo.owl")
    if not Path(bfo_path).exists():
        return {"error": f"BFO not found at {bfo_path}; run scripts/download_bfo.py "
                         f"or pass --bfo"}
    g = load(path)
    for t in list(g.triples((None, OWL.imports, None))):
        g.remove(t)
    for p in set(g.objects(None, OWL.onProperty)):
        if (p, RDF.type, OWL.AnnotationProperty) not in g:
            g.add((p, RDF.type, OWL.ObjectProperty))
    g += load(bfo_path)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "check.owl"
        g.serialize(str(f), format="xml")
        world = o.World()
        onto = world.get_ontology(f.as_uri()).load()
        try:
            with onto:
                o.sync_reasoner(world, infer_property_values=False, debug=0)
        except o.OwlReadyInconsistentOntologyError:
            return {"consistent": False, "unsatisfiable": None}
        uns = sorted(c.name for c in world.inconsistent_classes() if c.name != "Nothing")
    return {"consistent": True, "unsatisfiable": uns, "unsatisfiable_count": len(uns)}


def gold(v: View, gold_path: str) -> dict:
    """Gold file (JSON): {"classes": [{"label", "bfo"?}],
    "axioms": [{"subject", "property", "filler"}]} with labels as a careful
    annotator would write them; property = a BFO/RO id (BFO_0000056) or
    "subClassOf"."""
    data = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    g = v.g
    got_cls = {norm(label(g, c)): v.category(c) for c in v.classes}
    want = data.get("classes", [])
    tp = [w for w in want if norm(w["label"]) in got_cls]
    cat_ok = [w for w in tp if not w.get("bfo")
              or BFO_NAMES.get(w["bfo"], w["bfo"]) in got_cls[norm(w["label"])]]
    got_ax = set()
    for c in v.classes:
        for p in v.parents[c]:
            got_ax.add((norm(label(g, c)), "subclassof", norm(label(g, p))))
        for r in v.restrictions[c]:
            if r["filler"] is not None:
                got_ax.add((norm(label(g, c)), short(r["prop"]).lower(),
                            norm(label(g, r["filler"]))))
    want_ax = {(norm(a["subject"]), a["property"].lower(), norm(a["filler"]))
               for a in data.get("axioms", [])}
    ax_tp = got_ax & want_ax
    pr = lambda t, n: round(t / n, 4) if n else None
    return {
        "class_recall": pr(len(tp), len(want)),
        "class_precision_vs_gold_scope": pr(len(tp), len(got_cls)),
        "class_category_accuracy": pr(len(cat_ok), len(tp)),
        "axiom_recall": pr(len(ax_tp), len(want_ax)),
        "axiom_precision": pr(len(ax_tp), len(got_ax)),
        "missed_classes": [w["label"] for w in want if norm(w["label"]) not in got_cls],
        "missed_axioms": [list(a) for a in sorted(want_ax - got_ax)],
    }


def construction(path: str) -> dict | None:
    try:
        sys.path.insert(0, str(ROOT))
        from app import quality_gates
        rep = quality_gates.run_audit(path)
        return {"summary": quality_gates.summary_line(rep),
                "failures": quality_gates.construction_failures(rep)}
    except Exception as e:  # the audit is optional here
        return {"error": str(e)}


# ---------------------------------------------------------------- driver
def scorecard(path: str, *, previous: str | None = None, source: str | None = None,
              scope_file: str | None = None, gold_file: str | None = None,
              use_reasoner: bool = False, bfo: str | None = None,
              with_construction: bool = True) -> dict:
    v = View(load(path))
    src = Path(source).read_text(encoding="utf-8", errors="replace") if source else None
    card = {
        "file": str(path),
        "counts": {"classes": len(v.classes), "individuals": len(v.individuals),
                   "restrictions": sum(len(r) for r in v.restrictions.values())},
        "structure": structure(v),
        "strength": strength(v),
        "category": category(v),
        "surface": surface(v, src),
    }
    if scope_file:
        card["scope"] = scope(v, scope_file)
    if previous:
        card["drift"] = drift(v, View(load(previous)))
    if use_reasoner:
        card["reasoner"] = reasoner(path, bfo)
    if gold_file:
        card["gold"] = gold(v, gold_file)
    if with_construction:
        card["construction"] = construction(path)
    card["headline"] = headline(card)
    return card


def headline(card: dict) -> dict:
    """The flat numbers to track across runs and to gate on."""
    s, st, c, su = card["structure"], card["strength"], card["category"], card["surface"]
    h = {
        "classes": card["counts"]["classes"],
        "flat_share": s["flat_share"],
        "definition_absent_share": s["definition_absent_share"],
        "possible_relations": s["possible_relations"],
        "caveat_as_definition": len(s["caveat_as_definition"]),
        "material_participation": st["material_participation"],
        "modal_span_axioms": st["modal_span_axioms"],
        "kind_like_individuals": len(c["kind_like_individuals"]),
        "has_value_in_class_axioms": len(c["has_value_in_class_axioms"]),
        "taxa_as_material_entities": len(c["taxa_as_material_entities"]),
        "label_collisions": len(su["label_collisions"]),
        "plural_labels": len(su["plural_labels"]),
        "spans_with_line_breaks": su["spans_with_line_breaks"],
    }
    if "scope" in card:
        h["out_of_scope_share"] = card["scope"]["out_of_scope_share"]
    if "drift" in card:
        d = card["drift"]
        h.update(retention=d["retention"], category_conflicts=len(d["category_conflicts"]),
                 reminted=len(d["reminted"]))
    if card.get("reasoner", {}).get("unsatisfiable") is not None:
        h["unsatisfiable"] = card["reasoner"]["unsatisfiable_count"]
    if "gold" in card:
        h.update(class_recall=card["gold"]["class_recall"],
                 axiom_precision=card["gold"]["axiom_precision"],
                 axiom_recall=card["gold"]["axiom_recall"])
    return h


def summary(card: dict) -> str:
    h = card["headline"]
    lines = [f"scorecard: {card['file']}"]
    for k, val in h.items():
        lines.append(f"  {k:<28} {val}")
    st = card["strength"]
    if st["material_participation_rows"]:
        lines.append("  false-universal candidates (participates in, material subject):")
        for r in st["material_participation_rows"][:15]:
            flag = " [modal span]" if r["modal_span"] else ""
            lines.append(f"    {r['class']} -> {r['filler']}{flag}")
    if st.get("quantifier_annotation_missing"):
        lines.append("  note: quantifierDefaulted is declared but no axiom carries it; "
                     "the export may be dropping axiom annotations")
    con = card.get("construction")
    if con and "summary" in con:
        lines.append("  " + con["summary"])
    return "\n".join(lines)


GATE = re.compile(r"^(\w+)\s*(<=|>=|<|>|==)\s*([0-9.]+)$")


def gate_failures(card: dict, gates: list[str]) -> list[str]:
    ops = {"<": lambda a, b: a < b, ">": lambda a, b: a > b, "<=": lambda a, b: a <= b,
           ">=": lambda a, b: a >= b, "==": lambda a, b: a == b}
    out = []
    for g in gates:
        m = GATE.match(g.strip())
        if not m:
            raise SystemExit(f"bad --fail-on {g!r}; use metric>value")
        k, op, val = m.group(1), m.group(2), float(m.group(3))
        have = card["headline"].get(k)
        if have is None:
            out.append(f"{g} (metric not computed)")
        elif ops[op](float(have), val):
            out.append(f"{g} (got {have})")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("owl")
    ap.add_argument("--previous", help="the prior run's OWL, for drift")
    ap.add_argument("--source", help="the source text, to check spans")
    ap.add_argument("--scope", help="YAML/JSON with terms and competency questions")
    ap.add_argument("--gold", help="gold annotations (JSON)")
    ap.add_argument("--reasoner", action="store_true", help="HermiT satisfiability under BFO")
    ap.add_argument("--bfo", help="path to bfo.owl (default ontology/bfo.owl)")
    ap.add_argument("--no-construction", action="store_true")
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--fail-on", action="append", default=[],
                    help="fail when true, e.g. flat_share>0.5 (repeatable)")
    a = ap.parse_args(argv)
    card = scorecard(a.owl, previous=a.previous, source=a.source, scope_file=a.scope,
                     gold_file=a.gold, use_reasoner=a.reasoner, bfo=a.bfo,
                     with_construction=not a.no_construction)
    if a.json:
        Path(a.json).write_text(json.dumps(card, indent=2, default=str), encoding="utf-8")
    print(summary(card))
    fails = gate_failures(card, a.fail_on)
    for f in fails:
        print("FAIL", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
