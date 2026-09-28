#!/usr/bin/env python3
"""Migrate reasoner-invisible axioms in a legacy bfo-agent artifact
(SPEC-bfo-agent-quality.md QS-C1, QS-C2, QS-C3).

Read-only on its input. Writes a new OWL file, a JSON migration report with
per-step counts, and a JSON ledger of every axiom that could not be restored
as reasoner-visible OWL (so nothing is silently dropped). Construction-tier
only (FG-0): every step restores the claim the text made; none alters it.

Steps, in order (counts reported per step):
  1 namespace unification     file:///...#X, working#file:///...#X,
                              http://anonymous#X  ->  WORKING#X
  2 mangled predicates        working#rdfs:subClassOf -> rdfs:subClassOf, ...
  3 locally minted upper ids  working#BFO_0000040 -> obo:BFO_0000040, plus the
                              QS-B1 alias table (RO_0000052 -> BFO_0000197)
  4 restriction text in IRIs  'RO_0000052 some working:X' -> real restriction
  5 sentence IRIs             -> rdfs:comment + bfoagent:migratedFrom
  6 punned triples            QS-A3 table (some / hasValue / refuse)
  7 blank-node label IRIs     working#_:r1 -> recovered restriction, or dropped
                              and ledgered

Output is deterministic: blank nodes get content-derived ids and triples are
emitted in sorted order, so a second run (and a run over the output) is
byte-identical (QS-C2).

Usage:
    python scripts/migrate_invisible_axioms.py IN.owl OUT.owl \
        [--report OUT.report.json] [--ledger OUT.ledger.json] [--unverified]
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import logging
import re
import shutil
import sys
import tempfile
import warnings
from pathlib import Path

import rdflib
from rdflib import OWL, RDF, RDFS, XSD, BNode, Literal, URIRef

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app import relation_vocab as rv  # noqa: E402
import _migration_parse as mp  # noqa: E402

WORKING_BASE = "http://davidkoepsell.com/bfo-agent/working"
W = WORKING_BASE + "#"
OBO = rv.OBO
BFOAGENT = rdflib.Namespace(rv.BFOAGENT_NS)
BFO_OWL_IMPORT = URIRef("http://purl.obolibrary.org/obo/bfo.owl")

STD_NS = (str(RDF), str(RDFS), str(OWL), str(XSD))
STD_BY_PREFIX = {"rdf": str(RDF), "rdfs": str(RDFS), "owl": str(OWL)}
# Predicates that are standard vocabulary only in the *wrong* namespace.
STD_FIXUPS = {str(OWL) + "subClassOf": str(RDFS.subClassOf),
              str(OWL) + "type": str(RDF.type)}
ANNOTATION_PREDICATES = {
    RDFS.label, RDFS.comment, RDFS.seeAlso, RDFS.isDefinedBy,
    URIRef(rv.IAO_DEFINITION),
    URIRef("http://www.w3.org/2004/02/skos/core#definition"),
    OWL.versionInfo, OWL.deprecated,
}
# BFO 2020 classes (from ontology/bfo.owl): used to classify OBO terms and to
# refuse a class IRI in predicate position without needing bfo.owl at hand.
BFO_CLASS_IDS = frozenset(
    "BFO_%07d" % n for n in (
        1, 2, 3, 4, 6, 8, 9, 11, 15, 16, 17, 18, 19, 20, 23, 24, 26, 27, 28,
        29, 30, 31, 34, 35, 38, 40, 140, 141, 142, 145, 146, 147, 148, 182,
        202, 203))
OCCURRENT_IDS = frozenset(
    "BFO_%07d" % n for n in (3, 8, 11, 15, 35, 38, 148, 182, 202, 203))
# BFO 2.0 part-of: split by the subject's category (continuant vs occurrent)
# rather than blindly aliased, since BFO 2020 has distinct relations.
PART_OF_SPLIT = {"BFO_0000050": ("BFO_0000176", "BFO_0000132"),
                 "BFO_0000051": ("BFO_0000178", "BFO_0000117")}

_STD_CURIE = re.compile(r"^(rdf|rdfs|owl):([A-Za-z]+)$")
_UPPER_FRAG = re.compile(r"^(?:(?:bfo|ro|iao|obo):)?((?:BFO|RO|IAO|OBI)_\d+)$", re.I)
_TEXTY = re.compile(r"[\s\[\]<>\"{}|\\^`()]")
_COMPLEMENT = re.compile(r"^(?:owl:)?complementOf\s+(\S+)$|^not\s+(\S+)$", re.I)

STEPS = ("1_namespace_unification", "2_mangled_predicates",
         "3_upper_ids_and_aliases", "4_restriction_text", "5_sentence_iris",
         "6_punned_triples", "7_bnode_label_iris")


class Text:
    """Marker for an IRI that is really free text (steps 4/5/7)."""
    __slots__ = ("text", "label")

    def __init__(self, text: str, label: bool):
        self.text, self.label = text, label


class Migration:
    def __init__(self, src: Path):
        self.src = Path(src)
        self.g = rdflib.Graph()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.g.parse(str(self.src))
        self.counts: dict[str, collections.Counter] = {
            s: collections.Counter() for s in STEPS}
        self.ledger: list[dict] = []
        self.out: set[tuple] = set()
        self.migration_axioms: list[dict] = []  # restorations, for gating
        self.decls_added = 0
        # Predicates used on the ontology header (dcterms:license, ...) are
        # ontology annotations, never object properties.
        self.header_annotations: set = set()

    # ---------------------------------------------------------------- terms
    def _unify(self, s: str) -> str:
        """Step 1 (and working: CURIEs): collapse every non-canonical local
        base onto WORKING#."""
        orig = s
        if s.startswith(W) and s[len(W):].startswith("file:"):
            s = W + s.rsplit("#", 1)[-1]
        elif s.startswith("file:"):
            s = W + (s.rsplit("#", 1)[-1] if "#" in s else s.rsplit("/", 1)[-1])
        elif s.startswith("http://anonymous#"):
            s = W + s[len("http://anonymous#"):]
        if s.startswith(W) and s[len(W):].startswith("working:"):
            s = W + s[len(W) + len("working:"):]
        if s != orig:
            self.counts["1_namespace_unification"]["terms"] += 1
        return s

    def norm(self, t):
        if not isinstance(t, URIRef):
            return t
        s = self._unify(str(t))
        if s in STD_FIXUPS:
            self.counts["2_mangled_predicates"][s.rsplit("#", 1)[-1]] += 1
            return URIRef(STD_FIXUPS[s])
        frag = s[len(W):] if s.startswith(W) else None
        if frag is not None:
            m = _STD_CURIE.match(frag)
            if m:
                pre, name = m.group(1), m.group(2)
                full = STD_BY_PREFIX[pre] + name
                full = STD_FIXUPS.get(full, full)
                self.counts["2_mangled_predicates"][f"{pre}:{name}"] += 1
                return URIRef(full)
            m = _UPPER_FRAG.match(frag)
            if m:
                self.counts["3_upper_ids_and_aliases"]["local_upper_id"] += 1
                s = OBO + m.group(1).upper()
                frag = None
        if s.startswith(OBO) and not _TEXTY.search(s):
            oid = s[len(OBO):]
            if oid in rv.RELATION_ALIASES and oid not in PART_OF_SPLIT:
                self.counts["3_upper_ids_and_aliases"][f"{oid}->"
                                                       f"{rv.RELATION_ALIASES[oid]}"] += 1
                return URIRef(OBO + rv.RELATION_ALIASES[oid])
            return URIRef(s)
        body = frag if frag is not None else (
            s[len(OBO):] if s.startswith(OBO) else None)
        if body is not None and (_TEXTY.search(body) or ":" in body):
            return Text(body, label=body.startswith("_:") and not _TEXTY.search(body))
        return URIRef(s)

    # -------------------------------------------------------------- helpers
    def led(self, step: str, s, p, o, reason: str) -> None:
        self.ledger.append({"step": step, "subject": _txt(s), "predicate": _txt(p),
                            "object": _txt(o), "reason": reason})

    def resolve(self, tok: str) -> URIRef | None:
        iri = mp.resolve_term(tok, W)
        if iri is None:
            return None
        iri = self._unify(iri)
        if iri.startswith(OBO):
            oid = iri[len(OBO):]
            iri = OBO + rv.RELATION_ALIASES.get(oid, oid) \
                if oid not in PART_OF_SPLIT else iri
        return URIRef(iri)

    def restriction(self, prop: URIRef, quant: str, filler, neg: bool = False):
        r = BNode()
        self.out.add((r, RDF.type, OWL.Restriction))
        self.out.add((r, OWL.onProperty, prop))
        if neg:
            c = BNode()
            self.out.add((c, RDF.type, OWL.Class))
            self.out.add((c, OWL.complementOf, filler))
            filler = c
        key = {"some": OWL.someValuesFrom, "only": OWL.allValuesFrom,
               "value": OWL.hasValue}[quant]
        self.out.add((r, key, filler))
        return r

    def annotate_axiom(self, s, p, o, prop, value) -> None:
        a = BNode()
        self.out.update({(a, RDF.type, OWL.Axiom), (a, OWL.annotatedSource, s),
                         (a, OWL.annotatedProperty, p), (a, OWL.annotatedTarget, o),
                         (a, prop, value)})

    def add_restriction_axiom(self, step, s, pred, prop, quant, filler,
                              neg=False, defaulted=False, origin=None):
        if str(s).startswith(OBO):
            # Never mutate an imported BFO/RO/IAO kernel class (same rule as
            # OntologyManager._add_relation); the claim goes to the ledger.
            self.counts[step]["kernel_subject_refused"] += 1
            self.led(step, s, prop, filler,
                     f"restriction on kernel class refused ({quant}); "
                     f"anchor a local subclass instead")
            return False
        r = self.restriction(prop, quant, filler, neg)
        self.out.add((s, pred, r))
        if defaulted:
            self.annotate_axiom(s, pred, r, BFOAGENT.quantifierDefaulted,
                                Literal(True))
        self.migration_axioms.append({
            "step": step, "subject": str(s), "predicate": str(pred),
            "restriction": (str(prop), quant, _txt(filler), neg),
            "origin": origin, "node": r})
        return True

    def comment(self, s, text: str, from_pred) -> None:
        lit = Literal(text)
        self.out.add((s, RDFS.comment, lit))
        self.annotate_axiom(s, RDFS.comment, lit, BFOAGENT.migratedFrom,
                            Literal(str(from_pred)))

    # ------------------------------------------------------- steps 1-5, 7
    def pass_terms(self) -> None:
        subj_has_restriction = collections.defaultdict(bool)
        for s, p, o in self.g:
            if isinstance(o, BNode) and (o, RDF.type, OWL.Restriction) in self.g \
                    and p in (RDFS.subClassOf, OWL.equivalentClass):
                subj_has_restriction[self._unify(str(s))] = True
        known = {str(x)[len(W):] for x in self._declared_classes_raw()}

        for s, p, o in sorted(self.g, key=_triple_key):
            if p == RDF.type and o == OWL.Ontology:
                if str(s).startswith(OBO):
                    self.out.add((s, p, o))
                continue  # local header re-added in declarations()
            if (s, RDF.type, OWL.Ontology) in self.g:
                # Only the local header is rebased; an imported ontology's
                # header merged into the file (e.g. bfo.owl) keeps its IRI.
                local = not str(s).startswith(OBO)
                no = self.norm(o)
                if not isinstance(no, Text):
                    self.out.add((URIRef(WORKING_BASE) if local else s, p, no))
                    if p != OWL.imports and not _is_std(p):
                        self.header_annotations.add(p)
                continue
            if _aliased_semantics(s, p, o):
                # An RO declaration's domain/range/characteristics must not be
                # carried onto the BFO IRI it aliases to: bfo.owl governs those.
                self.counts["3_upper_ids_and_aliases"]["alias_declaration_dropped"] += 1
                self.led("3_upper_ids_and_aliases", s, p, o,
                         "aliased relation semantics left to the BFO import")
                continue
            ns, np_, no = self.norm(s), self.norm(p), self.norm(o)
            if isinstance(np_, Text):
                self.counts["5_sentence_iris"]["predicate_refused"] += 1
                self.led("5_sentence_iris", s, p, o, "free text in predicate position")
                continue
            if isinstance(ns, Text):
                step = "7_bnode_label_iris" if ns.label else "5_sentence_iris"
                self.counts[step]["subject_dropped"] += 1
                self.led(step, s, p, o, "text/label IRI in subject position")
                continue
            if np_ == RDFS.subClassOf and isinstance(no, URIRef) and \
                    str(no).startswith(str(RDF)):
                self.counts["2_mangled_predicates"]["nonsense_object"] += 1
                self.led("2_mangled_predicates", s, p, o,
                         "subClassOf an rdf: vocabulary term")
                continue
            if not isinstance(no, Text):
                if ns == no and np_ in (RDFS.subClassOf, OWL.equivalentClass):
                    # e.g. working#BFO_0000040 subClassOf BFO_0000040: a
                    # tautology after step 3, and a cycle owlready2 warns on.
                    self.counts["3_upper_ids_and_aliases"]["self_subclass_dropped"] += 1
                    continue
                self.out.add((ns, np_, no))
                continue
            self._text_object(ns, np_, no, s, p, o, known, subj_has_restriction)

    def _text_object(self, ns, np_, no: Text, s, p, o, known, has_r) -> None:
        if not isinstance(ns, URIRef):
            self.led("4_restriction_text", s, p, o,
                     "text object under an anonymous subject")
            self.counts["4_restriction_text"]["ledgered"] += 1
            return
        pred = np_ if np_ in (RDFS.subClassOf, OWL.equivalentClass, RDF.type) \
            else RDFS.subClassOf
        if no.label:
            hit = mp.parse_bnode_label(no.text, known)
            if hit:
                rel, frag = hit
                if self.add_restriction_axiom(
                    "7_bnode_label_iris", ns, pred, URIRef(OBO + rel), "some",
                    URIRef(W + frag), defaulted=True, origin=no.text):
                    self.counts["7_bnode_label_iris"]["recovered"] += 1
            elif has_r.get(str(ns)):
                self.counts["7_bnode_label_iris"]["resolved_to_existing"] += 1
            else:
                self.counts["7_bnode_label_iris"]["dropped"] += 1
                self.led("7_bnode_label_iris", s, p, o,
                         "blank-node label names no restriction")
            return
        text = no.text.strip()
        m = _COMPLEMENT.match(text)
        if m:
            f = self.resolve(m.group(1) or m.group(2))
            if f is not None:
                c = BNode()
                self.out.update({(c, RDF.type, OWL.Class), (c, OWL.complementOf, f)})
                self.out.add((ns, pred, c))
                self.migration_axioms.append({
                    "step": "4_restriction_text", "subject": str(ns),
                    "predicate": str(pred), "restriction": ("not", str(f)),
                    "origin": text, "node": c})
                self.counts["4_restriction_text"]["complement"] += 1
                return
        parsed = mp.parse_restriction_text(text)
        if parsed:
            prop, filler = self.resolve(parsed["prop"]), self.resolve(parsed["filler"])
            if prop is not None and filler is not None and \
                    prop[len(OBO):] not in BFO_CLASS_IDS:
                if self.add_restriction_axiom(
                    "4_restriction_text", ns, pred, prop, parsed["quant"], filler,
                    neg=parsed["neg"], defaulted=parsed["defaulted"], origin=text):
                    self.counts["4_restriction_text"]["restored"] += 1
                if pred != np_:
                    self.counts["4_restriction_text"]["via_other_predicate"] += 1
                return
        self.comment(ns, text, np_)
        if mp.RESTRICTION_HINT.search(text):
            self.counts["4_restriction_text"]["unparseable_to_comment"] += 1
            self.led("4_restriction_text", s, p, o,
                     "restriction-like text not parseable; kept as rdfs:comment")
        else:
            self.counts["5_sentence_iris"]["to_comment"] += 1

    def _declared_classes_raw(self) -> set[URIRef]:
        return {URIRef(self._unify(str(c))) for c in self.g.subjects(RDF.type, OWL.Class)
                if isinstance(c, URIRef)}

    # ------------------------------------------------------------- step 6
    def pass_punning(self) -> None:
        out = self.out
        decl_c = {s for s, p, o in out if p == RDF.type and o == OWL.Class
                  and isinstance(s, URIRef)}
        decl_i = {s for s, p, o in out if p == RDF.type and o == OWL.NamedIndividual}
        ann_props = {s for s, p, o in out if p == RDF.type and o in
                     (OWL.AnnotationProperty, OWL.DatatypeProperty)}
        for hp in self.header_annotations:
            if hp not in ann_props:
                out.add((hp, RDF.type, OWL.AnnotationProperty))
                ann_props.add(hp)
                self.decls_added += 1
        inferred_c = set()
        for s, p, o in out:
            if p in (RDFS.subClassOf, OWL.equivalentClass, OWL.disjointWith):
                inferred_c.update(x for x in (s, o) if isinstance(x, URIRef))
            elif p in (OWL.someValuesFrom, OWL.allValuesFrom) and isinstance(o, URIRef):
                inferred_c.add(o)
            elif p == RDF.type and isinstance(o, URIRef) and not _is_std(o):
                inferred_c.add(o)
        inferred_i = {s for s, p, o in out if p == RDF.type and isinstance(s, URIRef)
                      and isinstance(o, URIRef) and not _is_std(o)}

        decl_i0 = frozenset(decl_i)  # kind() must not see declarations added below

        def kind(x) -> str:
            if str(x).startswith(OBO):
                return "C" if str(x)[len(OBO):] in BFO_CLASS_IDS or x in decl_c else "?"
            c, i = x in decl_c, x in decl_i0
            if c and not i:
                return "C"
            if i and not c:
                return "I"
            if c and i:
                return "?"
            if x in inferred_c and x not in inferred_i:
                return "C"
            if x in inferred_i and x not in inferred_c:
                return "I"
            return "?"

        parents = collections.defaultdict(set)
        for s, p, o in out:
            if p in (RDFS.subClassOf, RDF.type) and isinstance(o, URIRef):
                parents[s].add(o)
        occ_cache: dict = {}

        def occurrent(x) -> bool:
            # Full-closure BFS: memoising partial results inside a hierarchy
            # cycle would make the answer depend on visit order.
            if x not in occ_cache:
                seen, todo = {x}, [x]
                while todo:
                    for q in parents.get(todo.pop(), ()):
                        if q not in seen:
                            seen.add(q)
                            todo.append(q)
                occ_cache[x] = any(str(q).startswith(OBO) and
                                   str(q)[len(OBO):] in OCCURRENT_IDS for q in seen)
            return occ_cache[x]

        for t in sorted(out, key=_triple_key):
            s, p, o = t
            if not isinstance(s, URIRef) or not isinstance(o, URIRef) or _is_std(p) \
                    or p in ANNOTATION_PREDICATES or p in ann_props \
                    or str(p).startswith(rv.BFOAGENT_NS):
                continue
            out.discard(t)
            pid = str(p)[len(OBO):] if str(p).startswith(OBO) else None
            if pid in BFO_CLASS_IDS:
                self.counts["6_punned_triples"]["class_as_predicate_refused"] += 1
                self.led("6_punned_triples", s, p, o, "BFO class used as a predicate")
                continue
            if pid in PART_OF_SPLIT:
                cont, occ = PART_OF_SPLIT[pid]
                new = occ if occurrent(s) else cont
                self.counts["3_upper_ids_and_aliases"][f"{pid}->{new}"] += 1
                p = URIRef(OBO + new)
            ks, ko = kind(s), kind(o)
            if ks == "I" and ko == "I":
                out.add((s, p, o))
                for x in (s, o):  # typed-but-undeclared individuals
                    if x not in decl_i:
                        out.add((x, RDF.type, OWL.NamedIndividual))
                        decl_i.add(x)
                        self.decls_added += 1
            elif ks == "C" and ko == "C":
                if self.add_restriction_axiom("6_punned_triples", s, RDFS.subClassOf,
                                           p, "some", o, defaulted=True,
                                           origin="class-class triple"):
                    self.counts["6_punned_triples"]["class_class_to_some"] += 1
            elif ks == "C" and ko == "I":
                if self.add_restriction_axiom("6_punned_triples", s, RDFS.subClassOf,
                                           p, "value", o, defaulted=True,
                                           origin="class-individual triple"):
                    self.counts["6_punned_triples"]["class_individual_to_hasValue"] += 1
            else:
                reason = ("individual related to a class; needs an individual of "
                          "that class or a class-level restriction"
                          if (ks, ko) == ("I", "C") else
                          f"cannot classify subject/object ({ks}->{ko})")
                self.counts["6_punned_triples"][f"refused_{ks}_{ko}"] += 1
                self.led("6_punned_triples", s, p, o, reason)

    # ------------------------------------------------------------- finish
    def declarations(self) -> None:
        out = self.out
        declared = {(s, o) for s, p, o in out if p == RDF.type}
        typed = {s for s, _ in declared}
        props = {p for s, p, o in out if isinstance(s, URIRef) and isinstance(o, URIRef)
                 and not _is_std(p) and p not in ANNOTATION_PREDICATES
                 and not str(p).startswith(rv.BFOAGENT_NS)}
        props |= {o for s, p, o in out if p == OWL.onProperty}
        ann = {p for s, p, o in out if str(p).startswith(rv.BFOAGENT_NS)}
        if any(p == URIRef(rv.IAO_DEFINITION) for s, p, o in out):
            ann.add(URIRef(rv.IAO_DEFINITION))
        seed = rdflib.Graph()
        seed.parse(data=rv.seed_turtle(), format="turtle")
        for pr in sorted(props, key=str):
            if (pr, OWL.AnnotationProperty) in declared or \
                    (pr, OWL.DatatypeProperty) in declared:
                continue
            if (pr, OWL.ObjectProperty) not in declared:
                out.add((pr, RDF.type, OWL.ObjectProperty))
                self.decls_added += 1
            for t in seed.triples((pr, None, None)):
                out.add(t)
        for a in sorted(ann, key=str):
            if (a, OWL.AnnotationProperty) not in declared:
                out.add((a, RDF.type, OWL.AnnotationProperty))
                self.decls_added += 1
        # Fillers and parents must be declared entities (OWL 2 DL).
        for s, p, o in list(out):
            if not isinstance(o, URIRef) or str(o).startswith(STD_NS) or \
                    str(o).startswith(OBO):
                continue
            if p in (RDFS.subClassOf, OWL.someValuesFrom, OWL.allValuesFrom,
                     OWL.complementOf, OWL.equivalentClass, OWL.disjointWith) \
                    or (p == RDF.type and not _is_std(o)):
                if o not in typed:
                    out.add((o, RDF.type, OWL.Class))
                    typed.add(o)
                    self.decls_added += 1
            elif p == OWL.hasValue and o not in typed:
                out.add((o, RDF.type, OWL.NamedIndividual))
                typed.add(o)
                self.decls_added += 1
        onto = URIRef(WORKING_BASE)
        out.add((onto, RDF.type, OWL.Ontology))
        if not any(s == onto and p == OWL.imports for s, p, o in out):
            out.add((onto, OWL.imports, BFO_OWL_IMPORT))

    def drop_empty_restrictions(self) -> None:
        """Restriction nodes with no property or no filler are inert; drop
        them and any edge pointing at them, and ledger it."""
        out = self.out
        fill = {OWL.someValuesFrom, OWL.allValuesFrom, OWL.hasValue, OWL.onClass,
                OWL.cardinality, OWL.minCardinality, OWL.maxCardinality,
                OWL.qualifiedCardinality, OWL.minQualifiedCardinality,
                OWL.maxQualifiedCardinality}
        restr = {s for s, p, o in out if p == RDF.type and o == OWL.Restriction
                 and isinstance(s, BNode)}
        has_p = {s for s, p, o in out if p == OWL.onProperty and s in restr}
        has_f = {s for s, p, o in out if p in fill and s in restr}
        empty = restr - (has_p & has_f)
        if not empty:
            return
        for t in sorted([t for t in out if t[0] in empty or t[2] in empty],
                        key=_triple_key):
            out.discard(t)
            if t[2] in empty:
                self.led("7_bnode_label_iris", t[0], t[1], "[empty restriction]",
                         "restriction with no property or filler")
        self.counts["7_bnode_label_iris"]["empty_restriction_dropped"] += len(empty)
        _drop_orphans(out)

    def run(self) -> None:
        self.pass_terms()
        self.pass_punning()
        self.drop_empty_restrictions()
        self.declarations()

    # ------------------------------------------------------------ gating
    def exclude(self, axioms: list[dict], reason: str) -> int:
        """Faithful gating: move clashing restorations out of the coherent
        file into the ledger (kept, never silently dropped)."""
        nodes = {ax["node"] for ax in axioms}
        axiom_nodes = {s for s, p, o in self.out
                       if p == OWL.annotatedTarget and o in nodes}
        for t in [t for t in self.out
                  if t[0] in nodes or t[2] in nodes or t[0] in axiom_nodes]:
            self.out.discard(t)
        _drop_orphans(self.out)
        for ax in axioms:
            self.ledger.append({"step": ax["step"], "subject": ax["subject"],
                                "predicate": ax["predicate"],
                                "object": json.dumps(ax["restriction"]),
                                "reason": reason})
        self.migration_axioms = [a for a in self.migration_axioms
                                 if a["node"] not in nodes]
        return len(axioms)

    def serialize(self) -> bytes:
        return serialize_deterministic(self.out)

    def report(self) -> dict:
        return {
            "input": str(self.src),
            "input_sha256": hashlib.sha256(self.src.read_bytes()).hexdigest(),
            "input_triples": len(self.g),
            "output_triples": len(self.out),
            "working_base": WORKING_BASE,
            "steps": {s: dict(sorted(c.items())) | {"total": sum(c.values())}
                      for s, c in self.counts.items()},
            "declarations_added": self.decls_added,
            "restorations": len(self.migration_axioms),
            "ledger_entries": len(self.ledger),
            "ledger_by_step": dict(sorted(collections.Counter(
                e["step"] for e in self.ledger).items())),
        }


# --------------------------------------------------------------- utilities
def _txt(t) -> str:
    if isinstance(t, Text):
        return t.text
    return str(t)


_CHARACTERISTICS = {OWL.FunctionalProperty, OWL.InverseFunctionalProperty,
                    OWL.TransitiveProperty, OWL.SymmetricProperty,
                    OWL.AsymmetricProperty, OWL.ReflexiveProperty,
                    OWL.IrreflexiveProperty}


def _aliased_semantics(s, p, o) -> bool:
    if not isinstance(s, URIRef):
        return False
    m = _UPPER_FRAG.match(re.split(r"[#/]", str(s))[-1])
    if not m or m.group(1).upper() not in rv.RELATION_ALIASES:
        return False
    return p in (RDFS.domain, RDFS.range) or (p == RDF.type and o in _CHARACTERISTICS)


def _is_std(t) -> bool:
    return str(t).startswith(STD_NS)


def _triple_key(t):
    return tuple((type(x).__name__, str(x),
                  str(getattr(x, "datatype", None) or ""),
                  str(getattr(x, "language", None) or "")) for x in t)


def _drop_orphans(out: set) -> None:
    """Remove blank nodes no longer reachable from any named subject or
    top-level axiom node (after a restriction was excluded)."""
    while True:
        referenced = {o for s, p, o in out if isinstance(o, BNode)}
        roots = {s for s, p, o in out if isinstance(s, BNode) and p == RDF.type
                 and o in (OWL.Axiom, OWL.AllDisjointClasses, OWL.AllDifferent,
                           OWL.NegativePropertyAssertion, OWL.AllDisjointProperties)}
        dead = {s for s, p, o in out if isinstance(s, BNode)
                and s not in referenced and s not in roots}
        # an Axiom whose target vanished is dead too
        dead |= {s for s, p, o in out if p == OWL.annotatedTarget
                 and isinstance(o, BNode) and not any(t[0] == o for t in out)}
        if not dead:
            return
        for t in [t for t in out if t[0] in dead]:
            out.discard(t)


def _canonical_bnodes(triples: set) -> dict:
    """Content-derived blank-node ids (QS-C2). A bnode's id hashes its own
    outgoing content (recursively) and the context that references it, so
    the labelling is independent of parse order and of rdflib's random ids."""
    outgoing = collections.defaultdict(list)
    incoming = collections.defaultdict(list)
    for s, p, o in triples:
        if isinstance(s, BNode):
            outgoing[s].append((p, o))
        if isinstance(o, BNode):
            incoming[o].append((s, p))
    content: dict = {}

    def rep(x, stack=()):
        return content_hash(x, stack) if isinstance(x, BNode) else \
            f"{type(x).__name__}:{x}" + (
                f"^^{x.datatype}@{x.language}" if isinstance(x, Literal) else "")

    def content_hash(b, stack=()):
        if b in content:
            return content[b]
        if b in stack:
            return "cycle"
        items = sorted(f"{p}|{rep(o, stack + (b,))}" for p, o in outgoing[b])
        content[b] = hashlib.sha1("\n".join(items).encode()).hexdigest()
        return content[b]

    ids: dict = {}

    def bid(b, stack=()):
        if b in ids:
            return ids[b]
        if b in stack:
            return content_hash(b)
        ctx = []
        for s, p in incoming[b]:
            sid = bid(s, stack + (b,)) if isinstance(s, BNode) else str(s)
            ctx.append(f"{sid}|{p}")
        ctx = min(ctx) if ctx else "root"
        ids[b] = "b" + hashlib.sha1(f"{ctx}#{content_hash(b)}".encode()).hexdigest()[:20]
        return ids[b]

    for b in set(outgoing) | set(incoming):
        bid(b)
    return ids


_NCNAME_TAIL = re.compile(r"[A-Za-z_][\w.\-]*$")
_KNOWN_PREFIXES = (("rdf", str(RDF)), ("rdfs", str(RDFS)), ("owl", str(OWL)),
                   ("xsd", str(XSD)), ("obo", OBO), ("working", W),
                   ("bfoagent", rv.BFOAGENT_NS))


def _xml_escape(s: str, attr: bool = False) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace('"', "&quot;") if attr else s


def serialize_deterministic(triples: set) -> bytes:
    """Flat RDF/XML with subjects and properties in sorted order. rdflib's own
    serializer walks subjects in hash order, which PYTHONHASHSEED makes differ
    between processes, so it cannot give a byte-identical rerun (QS-C2)."""
    ids = _canonical_bnodes(triples)

    def m(x):
        return BNode(ids[x]) if isinstance(x, BNode) else x

    rows = sorted({(m(s), m(p), m(o)) for s, p, o in triples}, key=_triple_key)
    ns_prefix = {ns: pre for pre, ns in _KNOWN_PREFIXES}
    extra = set()
    for _, p, _ in rows:
        mt = _NCNAME_TAIL.search(str(p))
        if not mt:
            raise ValueError(f"predicate {p} has no XML-serializable local name")
        ns = str(p)[:mt.start()]
        if ns not in ns_prefix:
            extra.add(ns)
    for i, ns in enumerate(sorted(extra), 1):
        ns_prefix[ns] = f"ns{i}"
    used = {str(p)[:_NCNAME_TAIL.search(str(p)).start()] for _, p, _ in rows}

    def qname(p) -> str:
        loc = _NCNAME_TAIL.search(str(p))
        return f"{ns_prefix[str(p)[:loc.start()]]}:{loc.group(0)}"

    out = ['<?xml version="1.0" encoding="utf-8"?>', "<rdf:RDF"]
    decls = sorted({(ns_prefix[ns], ns) for ns in used | {str(RDF)}})
    out += [f'   xmlns:{pre}="{_xml_escape(ns, True)}"' for pre, ns in decls]
    out[-1] += ">"
    subj = None
    for s, p, o in rows:
        if s != subj:
            if subj is not None:
                out.append("  </rdf:Description>")
            ref = (f'rdf:nodeID="{s}"' if isinstance(s, BNode)
                   else f'rdf:about="{_xml_escape(str(s), True)}"')
            out.append(f"  <rdf:Description {ref}>")
            subj = s
        q = qname(p)
        if isinstance(o, BNode):
            out.append(f'    <{q} rdf:nodeID="{o}"/>')
        elif isinstance(o, URIRef):
            out.append(f'    <{q} rdf:resource="{_xml_escape(str(o), True)}"/>')
        else:
            attrs = ""
            if o.language:
                attrs = f' xml:lang="{o.language}"'
            elif o.datatype:
                attrs = f' rdf:datatype="{_xml_escape(str(o.datatype), True)}"'
            out.append(f"    <{q}{attrs}>{_xml_escape(str(o))}</{q}>")
    if subj is not None:
        out.append("  </rdf:Description>")
    out.append("</rdf:RDF>")
    return ("\n".join(out) + "\n").encode("utf-8")


# ------------------------------------------------------------ verification
def _scratch(owl_bytes: bytes, bfo_path: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="qs_migrate_"))
    (tmp / "working.owl").write_bytes(owl_bytes)
    shutil.copy(bfo_path, tmp / "bfo.owl")
    return tmp


def hermit_consistent(owl_bytes: bytes, bfo_path: Path) -> bool | None:
    """Fast consistency probe for the gating search: one guarded HermiT run in
    a fresh owlready2 World over a scratch copy. None on timeout/error."""
    import owlready2
    from app import ontology_manager as om

    tmp = _scratch(owl_bytes, bfo_path)
    try:
        world = owlready2.World()
        owlready2.onto_path.append(str(tmp))
        try:
            world.get_ontology(str(tmp / "bfo.owl")).load()
            onto = world.get_ontology((tmp / "working.owl").as_uri()).load()
            with om._REASONER_LOCK, onto:
                om._sync_reasoner_guarded(world)
            return True
        except owlready2.OwlReadyInconsistentOntologyError:
            return False
        except Exception:  # noqa: BLE001 -- timeout / java error: unknown
            logging.getLogger(__name__).warning("consistency probe failed",
                                                exc_info=True)
            return None
        finally:
            owlready2.onto_path.remove(str(tmp))
            world.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify(owl_bytes: bytes, bfo_path: Path, fol: bool = True) -> dict:
    """QS-C3: HermiT certificate via OntologyManager.verify_full on a scratch
    copy (consistency + unsatisfiable classes), plus the FOL gate audit when
    its binaries are present."""
    res: dict = {}
    tmp = _scratch(owl_bytes, bfo_path)
    try:
        wp = tmp / "working.owl"
        try:
            from app.ontology_manager import OntologyManager
            v = OntologyManager(tmp / "bfo.owl", wp).verify_full()
            detail = str(v["detail"] or "")
            res["hermit"] = {
                "consistent": "inconsistent ontology" not in detail.lower(),
                "ok": bool(v["ok"]),
                "unsat_classes": sorted(v["unsat_classes"] or []),
                "detail": detail[:500], "duration_ms": v["duration_ms"]}
        except Exception as e:  # noqa: BLE001 -- report, don't crash
            res["hermit"] = {"error": f"{type(e).__name__}: {e}"[:500]}
        if fol:
            try:
                from app import fol_gate
                if fol_gate.binaries_available():
                    rec = fol_gate.audit(wp, mode="A")
                    res["fol_gate"] = {k: rec.get(k) for k in
                                       ("verdict", "consistent", "status",
                                        "summary", "duration_ms") if k in rec}
                else:
                    res["fol_gate"] = {"skipped": "prover9/mace4 not installed"}
            except Exception as e:  # noqa: BLE001
                res["fol_gate"] = {"error": f"{type(e).__name__}: {e}"[:300]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return res


def _axiom_closures(mig: Migration) -> list[set]:
    """For each restoration, the exact triples it contributed: the edge to its
    anonymous node, that node's blank-node closure, and any bfoagent:
    annotation axiom targeting it."""
    by_subj = collections.defaultdict(list)
    for t in mig.out:
        by_subj[t[0]].append(t)
    ann_of = collections.defaultdict(list)
    for s, p, o in mig.out:
        if p == OWL.annotatedTarget and isinstance(o, BNode):
            ann_of[o].append(s)
    closures = []
    for ax in mig.migration_axioms:
        node = ax["node"]
        tri = {(URIRef(ax["subject"]), URIRef(ax["predicate"]), node)}
        todo = [node] + ann_of.get(node, [])
        seen = set()
        while todo:
            b = todo.pop()
            if b in seen:
                continue
            seen.add(b)
            for t in by_subj.get(b, ()):
                tri.add(t)
                if isinstance(t[2], BNode):
                    todo.append(t[2])
        closures.append(tri)
    return closures


_DECL_TYPES = {OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty,
               OWL.AnnotationProperty, OWL.NamedIndividual, OWL.Ontology,
               OWL.FunctionalProperty, OWL.TransitiveProperty}


def _bnode_closure(b, by_subj: dict) -> set:
    out, todo, seen = set(), [b], set()
    while todo:
        x = todo.pop()
        if x in seen:
            continue
        seen.add(x)
        for t in by_subj.get(x, ()):
            out.add(t)
            if isinstance(t[2], BNode):
                todo.append(t[2])
    return out


def _gating_units(mig: Migration) -> tuple[list[dict], set]:
    """Split the output into a *base* of inert triples (declarations and
    annotations) and gating *units*: "text" units the input asserted verbatim
    and units the migration introduced or rewrote. The text's visible axioms
    are units too, because an input can pass HermiT only because malformed
    triples hid a clash, and the gated output must be consistent. A unit is a whole axiom: a
    restoration's closure; a named triple that changed; or a pre-existing
    anonymous axiom (the edge to it plus its blank-node closure) any part of
    which changed -- e.g. a restriction whose RO_0000052 became BFO_0000197
    and so picked up BFO's domain and range. Only units are gating
    candidates; the base is the text as the reasoner could already see it."""
    closures = _axiom_closures(mig)
    in_closure = set().union(*closures) if closures else set()
    units = [{"kind": "restoration", "axiom": ax, "triples": c}
             for ax, c in zip(mig.migration_axioms, closures)]
    rest = mig.out - in_closure
    by_subj = collections.defaultdict(list)
    referenced = set()
    for t in rest:
        if isinstance(t[0], BNode):
            by_subj[t[0]].append(t)
        if isinstance(t[2], BNode):
            referenced.add(t[2])

    def inert(t) -> bool:
        s, p, o = t
        return (p in ANNOTATION_PREDICATES or str(p).startswith(rv.BFOAGENT_NS)
                or (p == RDF.type and o in _DECL_TYPES))

    base, grouped = set(), set()
    # anonymous axioms hanging off a named subject, and unreferenced roots
    # (owl:AllDisjointClasses, owl:Axiom annotations)
    anchors = [t for t in rest if not isinstance(t[0], BNode) and isinstance(t[2], BNode)]
    anchors += [(None, None, b) for b in by_subj if b not in referenced]
    for a in sorted(anchors, key=lambda t: _triple_key(tuple(x or "" for x in t))):
        clo = _bnode_closure(a[2], by_subj)
        tri = clo | ({a} if a[0] is not None else set())
        grouped |= tri
        if all(inert(t) for t in tri):
            base |= tri
        elif all(t in mig.g or inert(t) for t in tri):
            units.append({"kind": "text", "triples": tri})
        else:
            units.append({"kind": "axiom", "triples": tri})
    for t in rest - grouped:
        if inert(t):
            base.add(t)
        elif t in mig.g:
            units.append({"kind": "text", "triples": {t}})
        else:
            units.append({"kind": "axiom", "triples": {t}})
    return units, base


def _unit_key(u: dict) -> str:
    return json.dumps(sorted(_triple_key(t) for t in u["triples"]
                             if not isinstance(t[0], BNode)), default=str)


def isolate_clashes(mig: Migration, bfo_path: Path, log=None) -> tuple[list, dict]:
    """Deterministically find the migration's own changes (restorations and
    rewrites) whose addition makes the source's own axioms inconsistent.
    FG-0: the source's asserted axioms ("text" units) are the fixed base and
    are never candidates. Changes are added in sorted chunks; a chunk that
    breaks consistency is bisected down to single offenders. Returns
    (offending units, stats). If the base alone is inconsistent the source is
    incoherent once visible: nothing is gated and base_consistent is False."""
    units, inert = _gating_units(mig)
    base = inert | set().union(*(u["triples"] for u in units if u["kind"] == "text"))
    changes = sorted((u for u in units if u["kind"] != "text"), key=_unit_key)
    stats = {"units": len(changes), "restorations": sum(
        u["kind"] == "restoration" for u in changes), "reasoner_runs": 0, "unknown": 0}

    def cons(idx: list) -> bool | None:
        extra = set().union(*(changes[i]["triples"] for i in idx)) if idx else set()
        stats["reasoner_runs"] += 1
        r = hermit_consistent(serialize_deterministic(base | extra), bfo_path)
        if r is None:
            stats["unknown"] += 1
        return r

    if cons([]) is not True:
        stats["base_consistent"] = False
        return [], stats
    stats["base_consistent"] = True
    kept: list = []
    bad: list = []

    def add(items: list) -> None:
        if not items:
            return
        r = cons(kept + items)
        if r is True:
            kept.extend(items)
            return
        if len(items) == 1:
            bad.append(items[0])  # inconsistent, or undecidable in budget
            return
        mid = len(items) // 2
        add(items[:mid])
        add(items[mid:])

    k = max(8, len(changes) // 24)
    for n in range(0, len(changes), k):
        add(list(range(n, min(n + k, len(changes)))))
        if log:
            log(f"  gating: {min(n + k, len(changes))}/{len(changes)} changes "
                f"checked, {len(bad)} clashing, {stats['reasoner_runs']} runs")
    return [changes[i] for i in sorted(bad)], stats


def source_culprits(mig: Migration, bfo_path: Path, log=None,
                    max_runs: int = 400) -> tuple[list, dict]:
    """Evidence only (FG-0): a minimal set of the source's own axioms that is
    inconsistent once made visible (QuickXplain over the text units, with the
    inert declarations as background). Nothing found here is removed."""
    units, inert = _gating_units(mig)
    text = sorted((u for u in units if u["kind"] == "text"), key=_unit_key)
    stats = {"text_units": len(text), "reasoner_runs": 0, "complete": True}

    def cons(idx) -> bool:
        if stats["reasoner_runs"] >= max_runs:
            stats["complete"] = False
            raise TimeoutError
        stats["reasoner_runs"] += 1
        extra = set().union(*(text[i]["triples"] for i in idx)) if idx else set()
        return hermit_consistent(serialize_deterministic(inert | extra), bfo_path) is True

    def qx(bg: list, delta: bool, cand: list) -> list:
        if delta and not cons(bg):
            return []
        if len(cand) == 1:
            return cand
        mid = len(cand) // 2
        c1, c2 = cand[:mid], cand[mid:]
        d2 = qx(bg + c1, bool(c1), c2)
        d1 = qx(bg + d2, bool(d2), c1)
        return d1 + d2

    try:
        found = qx([], False, list(range(len(text)))) if text and not cons(
            list(range(len(text)))) else []
    except TimeoutError:
        found = []
    if log:
        log(f"  source culprits: {len(found)} axioms ({stats['reasoner_runs']} runs)")
    return [text[i] for i in found], stats


def _primary_unsat_units(mig: Migration, units: list, new_unsat: set) -> list:
    """Migration changes to gate for newly unsat classes, root causes only. A
    class unsat merely because a named ancestor or one of its own restriction
    fillers is unsat is not a root: gating its axioms would discard innocent
    content. The source's own axioms are never candidates (FG-0)."""
    named_parents = collections.defaultdict(set)
    fillers = collections.defaultdict(set)
    by_subj = collections.defaultdict(list)
    for t in mig.out:
        by_subj[t[0]].append(t)
    for s, p, o in mig.out:
        if isinstance(s, BNode):
            continue
        if p in (RDFS.subClassOf, OWL.equivalentClass):
            if isinstance(o, URIRef):
                named_parents[str(s)].add(str(o))
            elif isinstance(o, BNode):
                for t in _bnode_closure(o, by_subj):
                    if isinstance(t[2], URIRef) and t[1] in (
                            OWL.someValuesFrom, OWL.allValuesFrom, OWL.complementOf):
                        fillers[str(s)].add(str(t[2]))

    def ancestors(c):
        seen, todo = set(), [c]
        while todo:
            for q in named_parents.get(todo.pop(), ()):
                if q not in seen:
                    seen.add(q)
                    todo.append(q)
        return seen

    roots = {c for c in new_unsat if not (ancestors(c) & new_unsat)}
    primary = {c for c in roots if not (fillers.get(c, set()) & new_unsat)} or roots
    return [u for u in units if u["kind"] != "text"
            and any(not isinstance(t[0], BNode) and str(t[0]) in primary
                    for t in u["triples"])]


def _source_forms(mig: Migration) -> dict:
    """Output triple -> the source's reasoner-visible triple(s) it rewrites
    (alias / namespace / upper-id rewrites of axioms the source reasoner could
    already see). Used to revert a gated rewrite to the source's own form."""
    saved = {k: collections.Counter(v) for k, v in mig.counts.items()}
    rev = collections.defaultdict(set)
    for t in mig.g:
        if not _source_visible(mig, t):
            continue
        n = tuple(mig.norm(x) for x in t)
        if any(isinstance(x, Text) for x in n) or n == t:
            continue
        rev[n].add(t)
    mig.counts = saved
    return rev


def _source_visible(mig: Migration, t) -> bool:
    """Would a reasoner loading the *source* have seen this triple as an
    axiom? (Not a mangled predicate, not a malformed/text IRI, and not a
    punned class-level use of an object property.)"""
    s, p, o = t
    for x in t:
        if isinstance(x, URIRef):
            frag = str(x).split("#", 1)[1] if "#" in str(x) else ""
            if _TEXTY.search(frag) or ":" in frag:
                return False
    if str(p) in STD_FIXUPS:
        return False
    if _is_std(p) or p in ANNOTATION_PREDICATES:
        return True
    if isinstance(s, BNode):
        return True
    if not hasattr(mig, "_src_inds"):
        mig._src_inds = set(mig.g.subjects(RDF.type, OWL.NamedIndividual))
    return s in mig._src_inds and o in mig._src_inds


def missing_source_axioms(mig: Migration) -> list:
    """Source reasoner-visible axioms present in the output neither verbatim
    nor as a migration rewrite (the deployability check)."""
    saved = {k: collections.Counter(v) for k, v in mig.counts.items()}
    missing = []
    for t in sorted(mig.g, key=_triple_key):
        if isinstance(t[0], BNode) or not _source_visible(mig, t):
            continue  # anonymous structure is checked through its anchor
        if t[1] == RDF.type and t[2] == OWL.Ontology:
            continue
        if _aliased_semantics(*t):
            continue  # RO domain/range deliberately left to the BFO import
        n = tuple(mig.norm(x) for x in t)
        if (t[0], RDF.type, OWL.Ontology) in mig.g and not str(t[0]).startswith(OBO):
            n = (URIRef(WORKING_BASE),) + n[1:]  # local header is rebased
        cands = {n}
        pid = str(t[1])[len(OBO):] if str(t[1]).startswith(OBO) else None
        if pid in PART_OF_SPLIT:  # BFO 2.0 part-of split by subject category
            cands = {(n[0], URIRef(OBO + q), n[2]) for q in PART_OF_SPLIT[pid]}
        if t in mig.out or cands & mig.out:
            continue
        if n[0] == n[2] and n[1] in (RDFS.subClassOf, OWL.equivalentClass):
            continue  # tautology collapsed by step 3
        if t[1] == RDF.type and n[0] == URIRef(WORKING_BASE):
            continue
        missing.append(t)
    mig.counts = saved
    return missing


def _exclude_units(mig: Migration, units: list, reason: str,
                   source_forms: dict | None = None) -> int:
    """Faithful gating: keep clashing migration changes out of the coherent
    file and in the ledger (never silently dropped). A gated rewrite of an
    axiom the source reasoner could already see is reverted to the source's
    own form, so no source axiom is ever lost (FG-0)."""
    assert all(u["kind"] != "text" for u in units), "source axioms are never gated"
    restorations = [u["axiom"] for u in units if u["kind"] == "restoration"]
    n = mig.exclude(restorations, reason) if restorations else 0
    source_forms = source_forms if source_forms is not None else _source_forms(mig)
    for u in units:
        if u["kind"] == "restoration":
            continue
        back = set()
        for t in u["triples"]:
            mig.out.discard(t)
            if t in mig.g:
                back.add(t)  # untouched part of a rewritten anonymous axiom
            back |= source_forms.get(t, set())
        rewritten = any(t in source_forms for t in u["triples"])
        if rewritten:
            mig.out |= back
        named = sorted((t for t in u["triples"] if not isinstance(t[0], BNode)),
                       key=_triple_key) or sorted(u["triples"], key=_triple_key)[:1]
        for t in named:
            mig.led("gated", t[0], t[1], t[2],
                    reason + ("; reverted to the source's own form" if rewritten else ""))
        n += 1
    _drop_orphans(mig.out)
    return n


def default_bfo_path() -> Path:
    """ontology/bfo.owl is untracked (gitignored), so a worktree nested under
    the main checkout (.claude/worktrees/...) has none: walk up to the first
    ancestor checkout that does."""
    for d in (ROOT, *ROOT.parents):
        p = d / "ontology" / "bfo.owl"
        if p.exists():
            return p
    raise FileNotFoundError(f"{ROOT / 'ontology' / 'bfo.owl'} not found; pass --bfo")


def migrate(src, dst, report_path=None, ledger_path=None, do_verify=True,
            bfo_path=None, fol=True, log=None) -> dict:
    """Migrate ``src`` to ``dst``, gated by default (QS-C3, FG-0).

    Only migration changes (restorations, rewrites, aliases) are ever gated
    out, and a gated rewrite of an axiom the source reasoner already saw is
    reverted to the source's own form. The source's asserted axioms are never
    removed. The report carries ``deployable``: true only when the output is
    HermiT-consistent AND every source reasoner-visible axiom is present
    verbatim or as a rewrite. If the source's own axioms are inconsistent once
    made visible, the output is written, marked ``source_inconsistent`` and
    not deployable, with a minimal culprit set in the ledger as evidence.
    ``do_verify=False`` skips reasoning (syntactic steps only; never
    deployable)."""
    mig = Migration(Path(src))
    mig.run()
    rep = mig.report()
    rep["gated_out"] = 0
    if do_verify:
        bfo_path = Path(bfo_path) if bfo_path else default_bfo_path()
        forms = _source_forms(mig)
        v = verify(mig.serialize(), bfo_path, fol=False)
        rep["verify_before_gating"] = v
        if v.get("hermit", {}).get("consistent") is False:
            clash, stats = isolate_clashes(mig, bfo_path, log=log)
            rep["gating_search"] = stats
            if not stats["base_consistent"]:
                rep["source_inconsistent"] = True
                culprits, cstats = source_culprits(mig, bfo_path, log=log)
                rep["source_culprit_search"] = cstats
                rep["source_culprits"] = len(culprits)
                for u in culprits:
                    for t in sorted((t for t in u["triples"] if not isinstance(t[0], BNode)),
                                    key=_triple_key):
                        mig.led("source_inconsistent", t[0], t[1], t[2],
                                "member of a minimal inconsistent set of the source's "
                                "own axioms (evidence only; kept in the file)")
            elif clash:
                rep["gated_out"] += _exclude_units(
                    mig, clash, "migration change makes the ontology inconsistent; "
                                "kept out of the coherent file (faithful mode)", forms)
            v = verify(mig.serialize(), bfo_path, fol=False)
        h = v.get("hermit", {})
        # Migration changes that make a class newly unsat (relative to what the
        # input itself classifies as unsat) are gated too, root causes first.
        if h.get("consistent") and h.get("unsat_classes"):
            b = verify(Path(src).read_bytes(), bfo_path, fol=False)
            base_unsat = {mig._unify(c) for c in
                          b.get("hermit", {}).get("unsat_classes") or []}
            rep["unsat_source"] = len(base_unsat)
            rep["unsat_gating_rounds"] = 0
            for _ in range(25):
                new = set(h.get("unsat_classes") or []) - base_unsat
                if not new or not h.get("consistent"):
                    break
                units, _inert = _gating_units(mig)
                clashing = _primary_unsat_units(mig, units, new)
                if not clashing:
                    break
                rep["unsat_gating_rounds"] += 1
                rep["gated_out"] += _exclude_units(
                    mig, clashing, "migration change makes a class unsatisfiable; "
                                   "kept out of the coherent file (faithful mode)", forms)
                if log:
                    log(f"  unsat gating round {rep['unsat_gating_rounds']}: "
                        f"{len(new)} new unsat, gated {len(clashing)} changes")
                v = verify(mig.serialize(), bfo_path, fol=False)
                h = v.get("hermit", {})
            rep["unsat_new_remaining"] = sorted(
                set(h.get("unsat_classes") or []) - base_unsat)
        if fol:
            v = verify(mig.serialize(), bfo_path, fol=fol)
        rep["verify"] = v
        rep["restorations"] = len(mig.migration_axioms)
    missing = missing_source_axioms(mig)
    rep["missing_source_axioms"] = len(missing)
    for t in missing:
        mig.led("missing_source_axiom", t[0], t[1], t[2],
                "source axiom absent from the output (blocks deployment)")
    final = rep.get("verify", {}).get("hermit", {})
    rep["consistent"] = final.get("consistent") if do_verify else None
    rep["unsat"] = len(final.get("unsat_classes") or []) if do_verify else None
    rep["deployable"] = bool(do_verify and final.get("consistent") is True and not missing)
    rep["ledger_entries"] = len(mig.ledger)
    rep["ledger_by_step"] = dict(sorted(collections.Counter(
        e["step"] for e in mig.ledger).items()))
    data = mig.serialize()
    Path(dst).write_bytes(data)
    rep["output"] = str(dst)
    rep["output_triples"] = len(mig.out)
    rep["output_sha256"] = hashlib.sha256(data).hexdigest()
    ledger = sorted(mig.ledger, key=lambda e: json.dumps(e, sort_keys=True, default=str))
    if ledger_path:
        Path(ledger_path).write_text(json.dumps(ledger, indent=1) + "\n")
    if report_path:
        Path(report_path).write_text(json.dumps(rep, indent=2, default=str) + "\n")
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("dst", type=Path)
    ap.add_argument("--report", type=Path)
    ap.add_argument("--ledger", type=Path)
    ap.add_argument("--verify", action="store_true",
                    help="accepted for compatibility; gating is the default")
    ap.add_argument("--unverified", action="store_true",
                    help="skip HermiT gating (syntactic steps only; the output "
                         "is NOT certified consistent)")
    ap.add_argument("--bfo", type=Path, default=None)
    ap.add_argument("--no-fol", action="store_true")
    a = ap.parse_args(argv)
    if a.src.resolve() == a.dst.resolve():
        ap.error("refusing to overwrite the input (the migration is read-only)")
    report = a.report or a.dst.with_suffix(".migration.json")
    ledger = a.ledger or a.dst.with_suffix(".ledger.json")
    logging.basicConfig(level=logging.WARNING)
    rep = migrate(a.src, a.dst, report, ledger, not a.unverified, a.bfo,
                  fol=not a.no_fol,
                  log=lambda m: print(m, file=sys.stderr, flush=True))
    print(json.dumps({"steps": {k: v["total"] for k, v in rep["steps"].items()},
                      "ledger_entries": rep["ledger_entries"],
                      "consistent": rep["consistent"], "deployable": rep["deployable"],
                      "output": rep["output"]}, indent=1))
    # 0 deployable; 3 written but not deployable (see report); unverified -> 0
    return 0 if rep["deployable"] or a.unverified else 3


if __name__ == "__main__":
    sys.exit(main())
