#!/usr/bin/env python3
"""Repair a legal draft built before the legal feed fixes, without refeeding.

Applies to an existing working.owl (or exported draft) the corrections the
feed now makes as it goes:

1. text written as IRIs (legal_kernel literal bug) becomes literals:
   definitions, definiens, scope, labels, sources, statuses, locators;
2. on legal positions (LOF ODP-003): ``has_realization some X`` becomes
   ``has_realization only X``; ``inheres_in some <role>`` becomes
   ``held_by some <Role>Holder`` and ``inheres_in some <Role>Holder`` with
   ``<Role>Holder subClassOf material entity and has_role some <role>``;
   a material-entity bearer also gets ``held_by``;
3. every position is ``grounded_in`` the content of the act its provenance
   names (``bfoagent:sourceAct``);
4. a position individual with no holder becomes a class under its LOF
   position class (realization -> only, named objects -> value);
5. with ``--source``, a class whose definition came from a recital or an
   unlocated passage gets its operative definition ("'term' means ...")
   from the act text, the earlier one kept as ``supersededDefinition``.

Usage:
  python scripts/repair_legal_draft.py draft.owl -o repaired.owl \\
      [--source act.txt] [--report repair-report.md]

Writes RDF/XML. The input file is never modified.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import rdflib
from rdflib import BNode, Literal, URIRef
from rdflib.namespace import OWL, RDF, RDFS

BASE = "http://davidkoepsell.com/bfo-agent/"
W = BASE + "working#"
META = BASE + "meta#"
OBO = "http://purl.obolibrary.org/obo/"
LOF = "https://w3id.org/lof/"
HAS_REALIZATION = URIRef(OBO + "BFO_0000054")
INHERES_IN = URIRef(OBO + "BFO_0000197")
HAS_ROLE = URIRef(OBO + "RO_0000087")
MATERIAL = URIRef(OBO + "BFO_0000040")
ROLE = URIRef(OBO + "BFO_0000023")
IC = URIRef(OBO + "BFO_0000004")
HELD_BY = URIRef(LOF + "ODP003_0001100")
GROUNDED_IN = URIRef(LOF + "ODP003_0001101")
POSITION_PREFIX = LOF + "ODP003_00010"
IAO_DEF = URIRef(OBO + "IAO_0000115")
SPAN = URIRef(META + "sourceSpan")
ACT = URIRef(META + "sourceAct")
LOC = URIRef(META + "sourceLocator")
SUPERSEDED = URIRef(META + "supersededDefinition")

# Predicates whose values are text in English (language-tagged on repair).
TAGGED = {RDFS.label, IAO_DEF, URIRef(META + "definiens"), URIRef(META + "scope"),
          URIRef(META + "derivedDefinition"), URIRef(META + "supersededDefinition")}


def _ancestors(g, c):
    out, stack = set(), [c]
    while stack:
        x = stack.pop()
        for p in g.objects(x, RDFS.subClassOf):
            if isinstance(p, URIRef) and p not in out:
                out.add(p)
                stack.append(p)
    return out


def _is_position_class(g, c):
    return any(str(a).startswith(POSITION_PREFIX) and a != HELD_BY for a in _ancestors(g, c) | {c})


def _is_position_individual(g, i):
    return any(isinstance(t, URIRef) and t != OWL.NamedIndividual and _is_position_class(g, t)
               for t in g.objects(i, RDF.type))


def _category(g, c):
    anc = _ancestors(g, c) | {c}
    if ROLE in anc:
        return "role"
    if {URIRef(OBO + x) for x in ("BFO_0000015", "BFO_0000003", "BFO_0000035")} & anc:
        return "occurrent"
    if MATERIAL in anc or IC in anc or URIRef(OBO + "BFO_0000027") in anc \
            or URIRef(OBO + "BFO_0000024") in anc or URIRef(OBO + "BFO_0000030") in anc:
        return "ic"
    return "other"


def _restriction(g, prop, kind, filler):
    r = BNode()
    g.add((r, RDF.type, OWL.Restriction))
    g.add((r, OWL.onProperty, prop))
    g.add((r, kind, filler))
    return r


def _label(g, x):
    return str(next(g.objects(x, RDFS.label), str(x).rsplit("#", 1)[-1]))


def fix_text_iris(g, stats):
    for s, p, o in list(g):
        if isinstance(o, URIRef) and str(o).startswith(BASE) and \
                not str(o).startswith(W) and not str(o).startswith(META) and \
                re.search(r"[ '‘’(),]", str(o)[len(BASE):]):
            text = str(o)[len(BASE):]
            g.remove((s, p, o))
            g.add((s, p, Literal(text, lang="en") if p in TAGGED else Literal(text)))
            stats["text IRIs made literals"] += 1
        elif isinstance(o, URIRef) and p in (IAO_DEF, URIRef(OBO + "IAO_0000119"), LOC,
                                             URIRef(META + "definitionStatus"),
                                             URIRef(META + "definedIn")) \
                and str(o).startswith(BASE) and not str(o).startswith(W):
            text = str(o)[len(BASE):]
            g.remove((s, p, o))
            g.add((s, p, Literal(text, lang="en") if p in TAGGED else Literal(text)))
            stats["text IRIs made literals"] += 1
        elif isinstance(o, URIRef) and p == URIRef(OBO + "IAO_0000119") and " " in str(o):
            g.remove((s, p, o))
            g.add((s, p, Literal(str(o))))
            stats["text IRIs made literals"] += 1


def _holder(g, role, stats):
    name = re.sub(r"\W", "", str(role).rsplit("#", 1)[-1].rsplit("/", 1)[-1]) + "Holder"
    h = URIRef(W + name)
    if (h, RDF.type, OWL.Class) not in g:
        g.add((h, RDF.type, OWL.Class))
        g.add((h, RDFS.subClassOf, MATERIAL))
        g.add((h, RDFS.subClassOf, _restriction(g, HAS_ROLE, OWL.someValuesFrom, role)))
        g.add((h, RDFS.label, Literal(f"{_label(g, role)} holder", lang="en")))
        g.add((h, IAO_DEF, Literal(f"A material entity that has the role {_label(g, role)}.",
                                   lang="en")))
        stats["holder classes added"] += 1
    return h


def fix_position_classes(g, stats):
    for c in list(g.subjects(RDF.type, OWL.Class)):
        if not str(c).startswith(W) or not _is_position_class(g, c):
            continue
        for r in list(g.objects(c, RDFS.subClassOf)):
            if not isinstance(r, BNode):
                continue
            p = next(g.objects(r, OWL.onProperty), None)
            f = next(g.objects(r, OWL.someValuesFrom), None)
            if f is None:
                continue
            if p == HAS_REALIZATION:
                g.remove((r, OWL.someValuesFrom, f))
                g.add((r, OWL.allValuesFrom, f))
                stats["realization some -> only"] += 1
            elif p in (INHERES_IN, HELD_BY):
                cat = _category(g, f)
                if cat == "role":
                    h = _holder(g, f, stats)
                    g.remove((r, OWL.someValuesFrom, f))
                    g.add((r, OWL.someValuesFrom, h))
                    if p == INHERES_IN:
                        g.add((c, RDFS.subClassOf, _restriction(g, HELD_BY, OWL.someValuesFrom, h)))
                    stats["role inherence -> holder"] += 1
                elif cat == "ic" and p == INHERES_IN:
                    g.add((c, RDFS.subClassOf, _restriction(g, HELD_BY, OWL.someValuesFrom, f)))
                    stats["held by added (class)"] += 1


def _act_content(g, s):
    act = next(g.objects(s, ACT), None)
    if act is None:
        return None
    u = URIRef(W + f"Act_{re.sub(r'[^A-Za-z0-9]+', '_', str(act)).strip('_')}_Content")
    return u if (u, None, None) in g else None


def ground_positions(g, stats):
    # Individuals carried no provenance before the fix: ground them in the
    # act most of the draft's provenance names.
    acts = Counter(str(o) for o in g.objects(None, ACT))
    dominant = None
    if acts:
        top = acts.most_common(1)[0][0]
        u = URIRef(W + f"Act_{re.sub(r'[^A-Za-z0-9]+', '_', top).strip('_')}_Content")
        dominant = u if (u, None, None) in g else None
    for c in list(g.subjects(RDF.type, OWL.Class)):
        if str(c).startswith(W) and _is_position_class(g, c):
            content = _act_content(g, c)
            if content and not any(next(g.objects(r, OWL.onProperty), None) == GROUNDED_IN
                                   for r in g.objects(c, RDFS.subClassOf) if isinstance(r, BNode)):
                g.add((c, RDFS.subClassOf, _restriction(g, GROUNDED_IN, OWL.hasValue, content)))
                stats["grounded in act (class)"] += 1
    for i in list(g.subjects(RDF.type, OWL.NamedIndividual)):
        if str(i).startswith(W) and _is_position_individual(g, i):
            content = _act_content(g, i)
            if content is None:
                # Individuals had no provenance before the fix: ground them in
                # the only act whose content the draft holds, when unique.
                content = dominant
            if content and (i, GROUNDED_IN, None) not in g:
                g.add((i, GROUNDED_IN, content))
                stats["grounded in act (individual)"] += 1
            for b in list(g.objects(i, INHERES_IN)):
                if (i, HELD_BY, b) not in g:
                    g.add((i, HELD_BY, b))
                    stats["held by added (individual)"] += 1


def unheld_to_classes(g, stats):
    for i in list(g.subjects(RDF.type, OWL.NamedIndividual)):
        if not str(i).startswith(W) or not _is_position_individual(g, i):
            continue
        if (i, INHERES_IN, None) in g or (i, HELD_BY, None) in g:
            continue
        if any(True for _ in g.subjects(None, i)):
            # something points at this particular; leave it for a person
            stats["unheld individuals left (referenced)"] += 1
            continue
        types = [t for t in g.objects(i, RDF.type) if t != OWL.NamedIndividual]
        g.remove((i, RDF.type, OWL.NamedIndividual))
        g.add((i, RDF.type, OWL.Class))
        for t in types:
            g.remove((i, RDF.type, t))
            g.add((i, RDFS.subClassOf, t))
        for p, o in list(g.predicate_objects(i)):
            if p in (RDF.type, RDFS.subClassOf) or isinstance(o, Literal):
                continue
            if (p, RDF.type, OWL.ObjectProperty) not in g and not str(p).startswith((OBO, LOF)):
                continue
            g.remove((i, p, o))
            is_ind = (o, RDF.type, OWL.NamedIndividual) in g
            if is_ind and p == HAS_REALIZATION:
                # not "every such duty is realized in this one process"
                g.add((i, URIRef(META + "possibleRelation"),
                       Literal(f"BFO_0000054 value {str(o).rsplit('#', 1)[-1]}")))
                continue
            kind = OWL.hasValue if is_ind else (OWL.allValuesFrom if p == HAS_REALIZATION
                                                else OWL.someValuesFrom)
            g.add((i, RDFS.subClassOf, _restriction(g, p, kind, o)))
        stats["unheld position individuals -> classes"] += 1


def split_bearer_and_role(g, stats):
    """An individual typed both as an independent continuant (person, body)
    and as a role is two things: the bearer, and the role it bears."""
    for i in list(g.subjects(RDF.type, OWL.NamedIndividual)):
        if not str(i).startswith(W):
            continue
        types = [t for t in g.objects(i, RDF.type) if isinstance(t, URIRef) and t != OWL.NamedIndividual]
        roles = [t for t in types if _category(g, t) == "role"]
        if not roles or not any(_category(g, t) == "ic" for t in types):
            continue
        r = URIRef(str(i) + "Role")
        g.add((r, RDF.type, OWL.NamedIndividual))
        for t in roles:
            g.remove((i, RDF.type, t))
            g.add((r, RDF.type, t))
        g.add((r, INHERES_IN, i))
        g.add((i, URIRef(OBO + "BFO_0000196"), r))
        g.add((r, RDFS.label, Literal(f"role of {_label(g, i)}", lang="en")))
        stats["bearer/role individuals split"] += 1


def drop_occurrent_participants(g, stats):
    """BFO: only continuants participate. ``P has_participant Q`` with Q a
    process (an order, a legal act) is removed and kept as a note."""
    # The draft does not restate BFO's own hierarchy: match the occurrent
    # categories themselves.
    occ = {URIRef(OBO + x) for x in ("BFO_0000003", "BFO_0000015", "BFO_0000035",
                                     "BFO_0000008", "BFO_0000011", "BFO_0000182")}
    hp = URIRef(OBO + "BFO_0000057")
    for s_, o in list(g.subject_objects(hp)):
        types = [t for t in g.objects(o, RDF.type) if isinstance(t, URIRef)]
        if any(occ & (_ancestors(g, t) | {t}) for t in types):
            g.remove((s_, hp, o))
            g.add((s_, URIRef(META + "possibleRelation"),
                   Literal(f"involves {str(o).rsplit('#', 1)[-1]} (an occurrent; not a participant)")))
            stats["occurrent participants removed"] += 1


def declare(g, stats):
    """Declare what the draft uses undeclared (OWL 2 DL for the OWL API):
    bfoagent/IAO/DC annotation properties, and individuals that were only
    ever typed by an rdf:type relation."""
    for p in set(g.predicates()):
        if str(p).startswith((META, "http://purl.org/dc/terms/", OBO + "IAO_")) and \
                not list(g.objects(p, RDF.type)):
            g.add((p, RDF.type, OWL.AnnotationProperty))
            stats["annotation properties declared"] += 1
    # Object properties used with IRI objects but declared only in BFO/LOF:
    # declaring them here keeps the draft parseable as OWL 2 DL on its own.
    for p in set(g.predicates()):
        if str(p).startswith((OBO + "BFO_", OBO + "RO_", LOF)) and not list(g.objects(p, RDF.type)) \
                and any(isinstance(o, URIRef) for o in g.objects(None, p)):
            g.add((p, RDF.type, OWL.ObjectProperty))
            stats["object properties declared"] += 1
    owl_kinds = {OWL.Class, OWL.NamedIndividual, OWL.ObjectProperty, OWL.DatatypeProperty,
                 OWL.AnnotationProperty, OWL.Restriction, OWL.Ontology, OWL.Axiom,
                 OWL.AllDisjointClasses, OWL.TransitiveProperty, OWL.SymmetricProperty,
                 OWL.FunctionalProperty, OWL.InverseFunctionalProperty, RDFS.Datatype}
    for x in set(g.subjects(RDF.type, None)):
        if not isinstance(x, URIRef) or not str(x).startswith(W):
            continue
        ts = set(g.objects(x, RDF.type))
        if ts & owl_kinds:
            continue
        g.add((x, RDF.type, OWL.NamedIndividual))
        stats["individuals declared"] += 1


def individual_class_assertions(g, stats):
    """``ind p Class`` is not an OWL object-property assertion (the OWL API
    reads it as an annotation, and the property then clashes with every
    real use). It becomes ``ind rdf:type (p some Class)``; on a class
    subject an individual filler becomes ``hasValue``."""
    for s_, p, o in list(g):
        if not (isinstance(o, URIRef) and str(p).startswith((OBO + "BFO_", OBO + "RO_", LOF))):
            continue
        s_ind = (s_, RDF.type, OWL.NamedIndividual) in g
        o_cls = (o, RDF.type, OWL.Class) in g
        if s_ind and o_cls:
            g.remove((s_, p, o))
            kind = OWL.allValuesFrom if p == HAS_REALIZATION and _is_position_individual(g, s_) \
                else OWL.someValuesFrom
            g.add((s_, RDF.type, _restriction(g, p, kind, o)))
            stats["individual-to-class assertions made type restrictions"] += 1
    for r in list(g.subjects(RDF.type, OWL.Restriction)):
        for kind in (OWL.someValuesFrom, OWL.allValuesFrom):
            for f in list(g.objects(r, kind)):
                if (f, RDF.type, OWL.NamedIndividual) in g and (f, RDF.type, OWL.Class) not in g:
                    g.remove((r, kind, f))
                    g.add((r, OWL.hasValue, f))
                    stats["individual fillers made hasValue"] += 1


def operative_definitions(g, source_text, stats):
    a3 = re.findall(r"‘([^’]+)’\s+means\s+(.+?)(?:;|\.)\s*$", source_text, flags=re.M)
    defs = {re.sub(r"\W", "", t.lower()): f"‘{t}’ means {d}" for t, d in a3}
    for c in list(g.subjects(RDF.type, OWL.Class)):
        if not str(c).startswith(W):
            continue
        key = re.sub(r"\W", "", _label(g, c).lower())
        if key not in defs:
            continue
        current = [d for d in g.objects(c, IAO_DEF)]
        if any(re.match(r"^['‘’\"]?[^'‘’\"]+['‘’\"]\s+means\b", str(d)) for d in current):
            continue  # already an operative "'term' means ..." definition
        for d in current:
            g.remove((c, IAO_DEF, d))
            g.add((c, SUPERSEDED, d if isinstance(d, Literal) else Literal(str(d), lang="en")))
        g.add((c, IAO_DEF, Literal(defs[key], lang="en")))
        stats["definitions replaced by the operative definition"] += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("draft")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--source", help="plain text of the act, for operative definitions")
    ap.add_argument("--report", help="write a Markdown summary here")
    a = ap.parse_args(argv)

    g = rdflib.Graph()
    import logging
    logging.getLogger("rdflib").setLevel(logging.ERROR)
    g.parse(a.draft)
    stats: Counter = Counter()
    fix_text_iris(g, stats)
    declare(g, stats)
    split_bearer_and_role(g, stats)
    drop_occurrent_participants(g, stats)
    unheld_to_classes(g, stats)       # first: their type restrictions become class axioms
    fix_position_classes(g, stats)
    ground_positions(g, stats)
    if a.source:
        operative_definitions(g, Path(a.source).read_text(encoding="utf-8"), stats)
    individual_class_assertions(g, stats)
    declare(g, stats)
    g.serialize(a.out, format="xml")
    lines = [f"# Repair of {Path(a.draft).name}", ""] + \
            [f"- {k}: {v}" for k, v in sorted(stats.items())]
    if a.report:
        Path(a.report).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
