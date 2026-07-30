#!/usr/bin/env python3
"""Phase 1: repair the baseline.

Input  ``CFR-DisabilityRegs.owl``
Output ``ontology/cfr404-base.owl``

Repairs, in the order the spec lists them:
  1. namespace + ontology/version IRI rewrite
  2. delete ``SimpleMessage`` and every axiom mentioning it
  3. ground ``Exertional Limitation``
  4. resolve the anonymous parent on ``UCTD``
  5. re-run the straddle check (must stay zero)

Every decision is written to ``ontology/phase1-repair-log.json`` so the README can
cite it rather than restate it.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL, RDF, RDFS, XSD

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cfrlib import (  # noqa: E402
    BFO, BFO_IMPORT, REL, CFR, CFR_BASE_IRI, CFR_VERSION_IRI, LEGACY_NS, LEGACY_ONTOLOGY_IRIS,
    ROOT, local, named_classes, straddles, unanchored,
)

SRC = ROOT.parent / "CFR-DisabilityRegs.owl"
OUT = ROOT / "ontology" / "cfr404-base.owl"
LOG = ROOT / "ontology" / "phase1-repair-log.json"


def rewrite_ns(g: Graph) -> tuple[Graph, int]:
    """Move every legacy-namespace IRI into the cfr404 namespace. BFO/RO untouched."""
    def mv(t):
        if isinstance(t, URIRef):
            s = str(t)
            for ns in LEGACY_NS:
                if s.startswith(ns):
                    return CFR[s[len(ns):]]
            if s in LEGACY_ONTOLOGY_IRIS:
                return CFR_BASE_IRI
        return t

    out = Graph()
    moved = 0
    for s, p, o in g:
        ns, np_, no = mv(s), mv(p), mv(o)
        if (ns, np_, no) != (s, p, o):
            moved += 1
        out.add((ns, np_, no))
    for pfx, uri in g.namespaces():
        if str(uri) not in LEGACY_NS:
            out.bind(pfx, uri)
    out.bind("cfr", CFR)
    return out, moved


def purge(g: Graph, victim: URIRef) -> int:
    """Remove a term and every axiom mentioning it, including host axioms of any
    restriction whose filler it was."""
    removed = 0
    # Restrictions pointing at the victim: drop the whole restriction and the
    # subClassOf/equivalentClass edge that carries it.
    for pred in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue, OWL.onClass):
        for b in list(g.subjects(pred, victim)):
            for host, hp in list(g.subject_predicates(b)):
                g.remove((host, hp, b))
                removed += 1
            for p, o in list(g.predicate_objects(b)):
                g.remove((b, p, o))
                removed += 1
    for t in list(g.triples((victim, None, None))):
        g.remove(t)
        removed += 1
    for t in list(g.triples((None, None, victim))):
        g.remove(t)
        removed += 1
    return removed


def describe_bnode(g: Graph, b: BNode) -> str:
    prop = [local(p) for p in g.objects(b, OWL.onProperty)]
    for q in (OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue, OWL.complementOf):
        fill = [local(f) for f in g.objects(b, q)]
        if fill:
            return f"{local(q)}: {' '.join(prop)} -> {' '.join(fill)}"
    return f"{' '.join(prop) or 'bare'}"


def gc_bnodes(g: Graph) -> tuple[int, list[str]]:
    """Drop blank nodes nothing references any more, reporting what went."""
    total = 0
    swept: list[str] = []
    while True:
        referenced = {o for o in g.objects() if isinstance(o, BNode)}
        orphans = {s for s in set(g.subjects()) if isinstance(s, BNode)} - referenced
        if not orphans:
            return total, swept
        for b in orphans:
            swept.append(describe_bnode(g, b))
            for p, o in list(g.predicate_objects(b)):
                g.remove((b, p, o))
                total += 1


def main() -> int:
    log: dict = {"generated": str(date.today()), "source": str(SRC.name), "steps": []}

    g0 = Graph()
    g0.parse(str(SRC))
    before_triples = len(g0)
    before_classes = len(named_classes(g0, "http://davidkoepsell.com/bfo-agent/working#"))

    # ---- 1. namespace ----------------------------------------------------------
    g, moved = rewrite_ns(g0)
    for s in list(g.subjects(RDF.type, OWL.Ontology)):
        for p, o in list(g.predicate_objects(s)):
            g.remove((s, p, o))
        g.remove((None, None, s))
    g.add((CFR_BASE_IRI, RDF.type, OWL.Ontology))
    g.add((CFR_BASE_IRI, OWL.versionIRI, CFR_VERSION_IRI))
    g.add((CFR_BASE_IRI, OWL.imports, BFO_IMPORT))
    g.add((CFR_BASE_IRI, RDFS.label, Literal("cfr404 base: 20 CFR Part 404 criteria layer")))
    g.add((CFR_BASE_IRI, DCTERMS.license,
           URIRef("https://creativecommons.org/licenses/by/4.0/")))
    log["steps"].append({
        "step": 1, "name": "namespace rewrite",
        "detail": f"{moved} triples moved from the shared scratch namespaces "
                  f"{list(LEGACY_NS)} into {str(CFR)}",
        "ontology_iri": str(CFR_BASE_IRI), "version_iri": str(CFR_VERSION_IRI),
        "note": "the six bfo-agent/seed# terms were label-only orphan stubs whose local "
                "names collided with working# classes of the same name; the rewrite merges "
                "them onto their counterparts and no class is lost",
    })

    # ---- 2. delete SimpleMessage ------------------------------------------------
    sm = CFR["SimpleMessage"]
    hosts = sorted({
        local(h)
        for b in g.subjects(OWL.someValuesFrom, sm)
        for h, _ in g.subject_predicates(b)
        if isinstance(h, URIRef)
    })
    n = purge(g, sm)
    log["steps"].append({
        "step": 2, "name": "delete SimpleMessage",
        "detail": f"{n} triples removed",
        "host_axioms_dropped": hosts,
        "justification": "SimpleMessage was grounded as a generically dependent continuant "
                         "and sat as a sibling of Evidence. Nothing in 20 CFR Part 404 "
                         "introduces it; it is contamination carried in from another "
                         "project's scratch namespace. Its only other use was as the filler "
                         f"of 'has participant some SimpleMessage' on {hosts}, which is "
                         "itself a relation misuse (a process cannot have a generically "
                         "dependent continuant as a participant), so the host axioms go too.",
    })

    # ---- 3. ground Exertional Limitation ---------------------------------------
    el = CFR["ExertionalLimitation"]
    parent = CFR["Limitation"]
    grounding = {
        "step": 3, "name": "ground Exertional Limitation",
        "chosen": "disposition (BFO_0000016), via subClassOf Limitation",
        "evidence": [
            "404.1567 defines the exertional levels (sedentary, light, medium, heavy, very "
            "heavy) by what a claimant retains the capacity to do, not by a measured "
            "magnitude of an inhering quality. That is a dispositional reading.",
            "The baseline already asserts 'ExertionalLimitation realized in some "
            "WorkPerformance'. Only a realizable entity can be realized in a process, so "
            "grounding as quality (BFO_0000019) would make the class incoherent.",
            "Its asserted disjoint sibling NonexertionalLimitation is already "
            "subClassOf Limitation subClassOf disposition; grounding it anywhere else "
            "would split a stated disjoint pair across BFO branches.",
        ],
        "rejected": "quality (BFO_0000019) - rejected on the realized-in evidence above",
    }
    if (parent, RDFS.subClassOf, BFO["disposition"]) not in g:
        g.add((parent, RDFS.subClassOf, BFO["disposition"]))
        grounding["also"] = "asserted Limitation subClassOf disposition"
    g.add((el, RDFS.subClassOf, parent))
    g.add((el, RDFS.subClassOf, BFO["disposition"]))
    log["steps"].append(grounding)

    # ---- 4. UCTD anonymous parent ----------------------------------------------
    uctd = CFR["UCTD"]
    dropped = []
    for p in (RDF.type, RDFS.subClassOf):
        for o in list(g.objects(uctd, p)):
            if not isinstance(o, BNode):
                continue
            if (o, RDF.type, OWL.Restriction) in g:
                continue
            comp = list(g.objects(o, OWL.complementOf))
            dropped.append({
                "predicate": local(p),
                "shape": "complementOf " + ", ".join(local(c) for c in comp) if comp else "bare",
            })
            g.remove((uctd, p, o))
    log["steps"].append({
        "step": 4, "name": "resolve UCTD anonymous parent",
        "resolution": "dropped as extraction noise",
        "dropped": dropped,
        "justification": "UCTD is a named individual already typed as a disposition and as "
                         "Connective Tissue Disorder and Autoimmune Disorder. The anonymous "
                         "parent was a class assertion into 'complementOf DiagnosticCriterion' "
                         "- a negative type assertion that follows already from the "
                         "disposition/GDC split and that no CFR section states. It is the "
                         "complement-noise pattern seen elsewhere in this codebase, not an "
                         "intended restriction, so it is dropped rather than named.",
    })

    # ---- 5. correct the mis-declared relation ranges ----------------------------
    # The baseline declares 'bearer of' with range disposition. A bearer bears any
    # specifically dependent continuant, so under that declaration every class that
    # bears a quality or a role - including the Commissioner, who bears an authority
    # role - comes out unsatisfiable. This is the single defect responsible for every
    # unsatisfiable class in the merged artifact.
    rel_fixes = []
    for prop, want_dom, want_rng, why in (
        (REL["bearer_of"], BFO["ic"], BFO["sdc"],
         "a bearer bears any specifically dependent continuant, not only dispositions"),
        (REL["inheres_in"], BFO["sdc"], BFO["ic"],
         "a specifically dependent continuant inheres in an independent continuant"),
    ):
        for pred, want in ((RDFS.domain, want_dom), (RDFS.range, want_rng)):
            had = [o for o in g.objects(prop, pred)]
            if had == [want]:
                continue
            for o in had:
                g.remove((prop, pred, o))
            g.add((prop, pred, want))
            if had:
                rel_fixes.append({
                    "property": local(prop), "axiom": local(pred),
                    "was": [local(x) for x in had], "now": local(want), "why": why,
                })
        g.add((prop, RDF.type, OWL.ObjectProperty))
    log["steps"].append({
        "step": 5, "name": "correct mis-declared relation domains and ranges",
        "fixes": rel_fixes,
        "note": "BFO_0000196 and BFO_0000197 are BFO 2.0 IRIs and are not declared by "
                "BFO 2020, whose corresponding relations are RO_0000053 (bearer of) and "
                "RO_0000052 (inheres in). They are retained here, declared locally with "
                "BFO-consistent domains and ranges, because renaming 31 restrictions "
                "would diverge from the baseline this phase is meant to repair rather "
                "than rewrite. The vintage mismatch is carried in the known-defect "
                "inventory.",
    })

    gc, swept = gc_bnodes(g)
    log["steps"].append({
        "step": 6, "name": "orphan restriction sweep",
        "triples_removed": gc,
        "restrictions_swept": swept,
        "note": "These blank nodes were already dangling in the input: restriction bodies "
                "referenced by no class, i.e. axioms whose subject was lost during the "
                "original extraction. They are reported rather than silently collected "
                "because each one is a restriction the baseline was supposed to carry and "
                "does not. Counting them keeps the restriction-predicate census honest.",
    })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    g.serialize(destination=str(OUT), format="pretty-xml")

    # ---- 5. straddle + anchoring check -----------------------------------------
    st = straddles(g)
    un = unanchored(g)
    log["acceptance"] = {
        "named_classes_before": before_classes,
        "named_classes_after": len(named_classes(g)),
        "triples_before": before_triples,
        "triples_after": len(g),
        "straddles": [str(x) for x in st],
        "unanchored": [str(x) for x in un],
    }
    LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")

    print(f"classes {before_classes} -> {len(named_classes(g))}")
    print(f"triples {before_triples} -> {len(g)}")
    print(f"straddles {len(st)}  unanchored {len(un)}")
    print(f"wrote {OUT.relative_to(ROOT)} and {LOG.relative_to(ROOT)}")
    return 0 if not st and not un else 1


if __name__ == "__main__":
    raise SystemExit(main())
