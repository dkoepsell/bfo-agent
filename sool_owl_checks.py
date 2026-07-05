"""
sool_owl_checks.py
==================
Reference implementation for two SOoL tooling fixes, derived from defects found
in AristotleCategories.owl (refurbished bfo-agent output). Standalone CI shim;
the canonical copy lives at app/owl_checks.py and is synced here verbatim
(dsm-extraction-fix-spec.md extends both with G-1..G-4).

Part 1 — EMITTERS (bfo-agent serializer fix, PC-7 / §6.1):
    Build boolean & restriction class expressions as proper anonymous OWL
    constructs. Use these instead of templating an IRI like
    "#[ Quantity and not ( has_quality some Contrary ) ]".

Part 2 — VALIDATORS (owltesterservice checks A6/A7/B4):
    - check_expression_iris      -> E_EXPR_IRI   (lexical, pre-parse)
    - check_antipatterns_all     -> E_ANTIPATTERN (all fragments, not just decls)
    - check_dangling_and_remint  -> E_DANGLING_IRI / E_BFO_REMINT

Dependencies: rdflib (pip install rdflib --break-system-packages).
The lexical checks (A7, A6) run on raw text and need no parser, so they still
work on a file too malformed for rdflib to load.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from rdflib import Graph, BNode, URIRef, RDF, OWL, Literal
from rdflib.collection import Collection

# ---------------------------------------------------------------------------
# Part 1 — EMITTERS  (agent-side; produce real anonymous class constructs)
# ---------------------------------------------------------------------------

def _as_node(x):
    return x if isinstance(x, (URIRef, BNode)) else URIRef(x)

def union_of(g: Graph, members) -> BNode:
    """A or B or ...  ->  [ owl:unionOf ( A B ... ) ]"""
    node = BNode()
    g.add((node, RDF.type, OWL.Class))
    Collection(g, node_first := BNode(), [_as_node(m) for m in members])
    g.add((node, OWL.unionOf, node_first))
    return node

def intersection_of(g: Graph, members) -> BNode:
    """A and B and ...  ->  [ owl:intersectionOf ( A B ... ) ]"""
    node = BNode()
    g.add((node, RDF.type, OWL.Class))
    Collection(g, node_first := BNode(), [_as_node(m) for m in members])
    g.add((node, OWL.intersectionOf, node_first))
    return node

def complement_of(g: Graph, cls) -> BNode:
    """not A  ->  [ owl:complementOf A ]"""
    node = BNode()
    g.add((node, RDF.type, OWL.Class))
    g.add((node, OWL.complementOf, _as_node(cls)))
    return node

def some_values_from(g: Graph, prop, filler) -> BNode:
    """p some C  ->  [ owl:Restriction; owl:onProperty p; owl:someValuesFrom C ]"""
    node = BNode()
    g.add((node, RDF.type, OWL.Restriction))
    g.add((node, OWL.onProperty, _as_node(prop)))
    g.add((node, OWL.someValuesFrom, _as_node(filler)))
    return node

def all_values_from(g: Graph, prop, filler) -> BNode:
    """p only C"""
    node = BNode()
    g.add((node, RDF.type, OWL.Restriction))
    g.add((node, OWL.onProperty, _as_node(prop)))
    g.add((node, OWL.allValuesFrom, _as_node(filler)))
    return node

# Worked example — the exact axiom AristotleCategories.owl tried (and failed) to
# express as "#[ Quantity and not ( BFO_0000196 some Contrary ) ]".
def example_quantity_has_no_contrary(g, QUANTITY, HAS_QUALITY, CONTRARY) -> BNode:
    # Quantity and not (hasQuality some Contrary) — reuses Contrary, no NonQuantity
    return intersection_of(g, [
        QUANTITY,
        complement_of(g, some_values_from(g, HAS_QUALITY, CONTRARY)),
    ])

# ---------------------------------------------------------------------------
# Part 1b — SANCTIONED EXPRESSION MICRO-SYNTAX (relation objects)
# ---------------------------------------------------------------------------
# The proposer may put a class EXPRESSION (not a class IRI) as the object of a
# subClassOf edge, using exactly these forms (dsm-extraction-fix-spec.md X-1/X-2):
#
#     "PROP some FILLER"            -> existential restriction
#     "not CLASS"                   -> complement (A subClassOf not B)
#     "not (PROP some FILLER)"      -> complement of a restriction
#     "_:x PROP FILLER"             -> legacy bnode-restriction placeholder
#
# 'owl:complementOf CLASS' / 'complementOf CLASS' are normalized to the "not"
# form: that is the exact string the DSM run baked into fabricated IRIs, and
# the intent (a differential exclusion) is recoverable, so we recover it.
# Every consumer (self-lint, commit path, gate serializer) parses with THIS
# function, so a proposal is treated identically at all three layers.

_TERM_RE = r"(?:https?://[^\s()]+|[A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)"
_EXPR_NOT_SOME = re.compile(
    rf"^(?:not|(?:owl:)?complementOf)\s*\(\s*({_TERM_RE})\s+some\s+({_TERM_RE})\s*\)$")
_EXPR_NOT = re.compile(rf"^(?:not|(?:owl:)?complementOf)\s+({_TERM_RE})$")
_EXPR_SOME = re.compile(rf"^(?:_:\S+\s+)?({_TERM_RE})\s+(?:some\s+)?({_TERM_RE})$")


def parse_class_expression(text: str) -> dict | None:
    """Parse a sanctioned class-expression relation object. Returns
    ``{"op": "some"|"not"|"not_some", ...}`` or None if ``text`` is not an
    expression (i.e. it should be treated as a plain IRI reference)."""
    t = (text or "").strip()
    if not t:
        return None
    m = _EXPR_NOT_SOME.match(t)
    if m:
        return {"op": "not_some", "prop": m.group(1), "filler": m.group(2)}
    m = _EXPR_NOT.match(t)
    if m:
        return {"op": "not", "cls": m.group(1)}
    # bnode-some ("_:x P F") requires the _: prefix or the 'some' keyword;
    # a plain "A B" two-token string is NOT an expression.
    if t.startswith("_:") or re.search(r"\s+some\s+", t):
        m = _EXPR_SOME.match(t)
        if m:
            return {"op": "some", "prop": m.group(1), "filler": m.group(2)}
    return None


def expression_operands(expr: dict) -> list[str]:
    """The term operands of a parsed expression (for per-term lexical checks)."""
    return [v for k, v in expr.items() if k != "op"]


# H-1 / H-2: IRI fragments must be valid NCName-style tokens; display text
# belongs in rdfs:label, never in the IRI.
_VALID_FRAG = re.compile(r"^[A-Za-z_][\w.\-]*$")
_WORDLIKE = re.compile(r"^[A-Za-z][A-Za-z0-9'’\-]*$")


def slugify_fragment(frag: str) -> str | None:
    """Turn a label-like fragment into a valid CamelCase token (H-1).

    'Premenstrual Dysphoric Disorder' -> 'PremenstrualDysphoricDisorder'.
    Returns the fragment unchanged when already valid, and None when the
    string is not label-like (operator tokens, prefixed names, symbols) --
    callers must refuse those rather than guess."""
    frag = (frag or "").strip()
    if not frag:
        return None
    if _VALID_FRAG.match(frag):
        return frag
    tokens = frag.split()
    if tokens and all(_WORDLIKE.match(t) for t in tokens):
        slug = "".join(re.sub(r"[^A-Za-z0-9_.\-]", "", t[:1].upper() + t[1:])
                       for t in tokens)
        if _VALID_FRAG.match(slug):
            return slug
    return None


# ---------------------------------------------------------------------------
# Part 2 — VALIDATORS  (gate-side)
# ---------------------------------------------------------------------------

# Boolean / restriction operators that must never appear inside an IRI fragment.
_EXPR_TOKENS = [" and ", " or ", " not ", " some ", " only ", " value ",
                " min ", " max ", " exactly "]
_EXPR_BRACKET = re.compile(r'rdf:(?:about|resource)="[^"]*[\[\]][^"]*"')
_IRI_ATTR = re.compile(r'rdf:(?:about|resource)="([^"]*)"')

# G-1: OWL construct names that must never appear inside an IRI fragment --
# they mean a boolean construct was templated into an IRI instead of built as
# a proper anonymous class (the DSM 'owl#complementOf working:X' defect).
_OWL_CONSTRUCTS = ("complementOf", "unionOf", "intersectionOf",
                   "someValuesFrom", "allValuesFrom")
_OWL_NS = "http://www.w3.org/2002/07/owl#"
# G-1: a bare QName smuggled into what should be a full IRI (a colon in the
# fragment, e.g. '...#complementOf working:NeurocognitiveDisorder').
_HOST_PORT = re.compile(r"^[\w.\-]+:\d+$")
# G-2: characters illegal in an IRI (RFC 3987 excluded set + whitespace).
_BAD_IRI_CHARS = re.compile(r'[\s<>"{}|\\^`]')


def _fragment_of(iri: str) -> str:
    if "#" in iri:
        return iri.rsplit("#", 1)[-1]
    return iri.rsplit("/", 1)[-1]


def iri_is_malformed(iri: str) -> bool:
    """True when an already-resolved IRI carries whitespace/illegal characters
    or a templated OWL construct name. Commit-path guard (never persist)."""
    if _BAD_IRI_CHARS.search(iri):
        return True
    if iri.startswith(_OWL_NS):  # the owl vocabulary itself is fine
        return False
    frag = _fragment_of(iri)
    return any(t in frag for t in _OWL_CONSTRUCTS)

# Privation / compound anti-pattern (mirror of bfo-agent PC-1).
# Boundary includes ':' and whitespace so terms smuggled inside expression
# fragments (e.g. "... some working:NonQuantity ...") are caught, not just
# terms at a '#'/'/' boundary in a clean declaration.
_ANTIPATTERN = re.compile(
    r'(?:^|[#/:\s(])((?:AbsenceOf|Absence|Lack|Loss|Missing|Non|Un|Failure|Broken|'
    r'Invalid|Degraded|Collapsed|Residual|Partial)[A-Z][A-Za-z]*)')

# Upper-ontology IDs that must never be minted in a local namespace.
_UPPER_ID = re.compile(r'[#/]((?:BFO|RO|IAO)_\d+)$')


@dataclass
class Finding:
    code: str
    detail: str

@dataclass
class Report:
    findings: list = field(default_factory=list)
    @property
    def passed(self) -> bool:
        # WARN_* and INFO_* findings are advisory (G-3/G-4); only E_* fails.
        return not any(f.code.startswith("E_") for f in self.findings)
    def add(self, code, detail):
        self.findings.append(Finding(code, detail))


def check_expression_iris(raw_text: str) -> Report:
    """A7 / E_EXPR_IRI — class expression baked into an IRI. Pure lexical.

    G-1 (dsm-extraction-fix-spec.md): beyond the bracketed and operator-token
    cases, also reject an OWL construct name (complementOf, unionOf, ...) or a
    bare QName (``prefix:Name``) inside an rdf:about / rdf:resource value."""
    rep = Report()
    for m in _IRI_ATTR.finditer(raw_text):
        iri = m.group(1)
        frag = _fragment_of(iri)
        qname_in_frag = (":" in frag and not iri.startswith(_OWL_NS)
                         and not _HOST_PORT.match(frag))
        if ("[" in iri or "]" in iri
                or any(t in iri for t in _EXPR_TOKENS)
                or (not iri.startswith(_OWL_NS)
                    and any(t in frag for t in _OWL_CONSTRUCTS))
                or qname_in_frag):
            rep.add("E_EXPR_IRI", iri.strip())
    return rep


def check_bad_iris(raw_text: str) -> Report:
    """G-2 / E_BAD_IRI — an rdf:about / rdf:resource value containing
    whitespace or a character illegal in an IRI (H-1: slugify the label into
    the fragment; the display text belongs in rdfs:label)."""
    rep = Report()
    seen = set()
    for m in _IRI_ATTR.finditer(raw_text):
        iri = m.group(1)
        if _BAD_IRI_CHARS.search(iri) and iri not in seen:
            seen.add(iri)
            rep.add("E_BAD_IRI", iri.strip())
    return rep


def check_lost_complement(path: str, raw_text: str | None = None) -> Report:
    """G-3 / WARN_LOST_COMPLEMENT — the raw file mentions ``complementOf`` but
    the parsed graph yields zero valid owl:complementOf triples: the agent
    attempted a negation that failed to serialize and every reasoner will
    silently drop it."""
    rep = Report()
    raw = raw_text if raw_text is not None else open(path, encoding="utf-8").read()
    if "complementOf" not in raw:
        return rep
    try:
        g = Graph()
        g.parse(path)
    except Exception:
        return rep  # unparseable files are someone else's finding
    if not any(g.triples((None, OWL.complementOf, None))):
        rep.add("WARN_LOST_COMPLEMENT",
                "raw text contains 'complementOf' but no valid "
                "owl:complementOf triple parsed; an attempted negation "
                "was lost in serialization")
    return rep


def report_shared_fillers(path: str) -> Report:
    """G-4 / INFO_SHARED_FILLERS — comorbidity-readiness signal: how many
    domain (non-upper-ontology) restriction fillers are referenced by two or
    more classes. Zero means criterion overlap (CT-1) cannot yet compute."""
    from rdflib import RDFS
    rep = Report()
    try:
        g = Graph()
        g.parse(path)
    except Exception:
        return rep
    filler_subjects: dict[str, set[str]] = {}
    for restr, _, filler in g.triples((None, OWL.someValuesFrom, None)):
        if not isinstance(filler, URIRef) or "obolibrary.org/obo/" in str(filler):
            continue
        for subj in g.subjects(RDFS.subClassOf, restr):
            if isinstance(subj, URIRef):
                filler_subjects.setdefault(str(filler), set()).add(str(subj))
    shared = sorted(f for f, subs in filler_subjects.items() if len(subs) >= 2)
    rep.add("INFO_SHARED_FILLERS",
            f"{len(shared)} restriction filler(s) shared by >=2 classes"
            + (": " + ", ".join(_fragment_of(f) for f in shared[:20])
               if shared else ""))
    return rep


def check_antipatterns_all(raw_text: str) -> Report:
    """A6 / E_ANTIPATTERN — privation/compound term in ANY IRI fragment,
    including expression operands, not just class declarations."""
    rep = Report()
    seen = set()
    for m in _IRI_ATTR.finditer(raw_text):
        for hit in _ANTIPATTERN.findall(m.group(1)):
            if hit not in seen:
                seen.add(hit)
                rep.add("E_ANTIPATTERN", hit)
    return rep


def check_bnode_iris(raw_text: str) -> Report:
    """E_BNODE_IRI — a blank-node label (``_:``) baked into an rdf:about /
    rdf:resource value. This is always malformed: it means a restriction (or
    other anonymous node) was serialized as a dangling named IRI instead of a
    real ``owl:Restriction``. Blank nodes belong in ``rdf:nodeID``, never in an
    IRI attribute. Mirrors the commit-path guard in
    ``OntologyManager._add_relation``."""
    rep = Report()
    seen = set()
    for m in _IRI_ATTR.finditer(raw_text):
        frag = m.group(1)
        if "_:" in frag and frag not in seen:
            seen.add(frag)
            rep.add("E_BNODE_IRI", frag.strip())
    return rep


def check_dangling_and_remint(path: str, imported: Graph | None = None) -> Report:
    """B4 — (i) E_DANGLING_IRI: obo:/imported IRIs that resolve to nothing;
            (ii) E_BFO_REMINT: BFO/RO/IAO ids minted in the local namespace.
    `imported` is the merged import closure (bfo.owl + kernel). If None, the
    dangling half is reported as 'unverified' rather than silently skipped."""
    rep = Report()
    g = Graph(); g.parse(path)
    base = _ontology_base(g)

    declared = set(g.subjects(RDF.type, OWL.Class)) | \
               set(g.subjects(RDF.type, OWL.ObjectProperty)) | \
               set(g.subjects(RDF.type, OWL.DatatypeProperty))

    # (ii) local re-mint of an upper-ontology id
    for s in declared:
        s = str(s)
        if base and s.startswith(base):
            m = _UPPER_ID.search(s)
            if m:
                rep.add("E_BFO_REMINT", s)

    # (i) dangling imported IRIs
    obo_used = {str(o) for o in g.all_nodes()
                if isinstance(o, URIRef) and "obolibrary.org/obo/" in str(o)}
    if imported is None:
        if obo_used:
            rep.add("WARN_UNVERIFIED_IMPORT",
                    f"{len(obo_used)} obo: IRIs used; supply import closure to verify "
                    f"(e.g. BFO_0000196 must resolve in bfo.owl)")
    else:
        known = {str(s) for s in imported.subjects()}
        for iri in sorted(obo_used):
            if iri not in known:
                rep.add("E_DANGLING_IRI", iri)
    return rep


def _ontology_base(g: Graph) -> str | None:
    for s in g.subjects(RDF.type, OWL.Ontology):
        b = str(s)
        return b if b.endswith(("#", "/")) else b + "#"
    return None


def check_iri_strings(iris) -> Report:
    """Proposal-side PC-7/PC-8 — run the A7/A6 lexical checks over a list of
    bare IRI/fragment strings (an entity's ``iri_suggestion`` or a relation
    predicate), without needing a serialized file. Same regexes as the
    file-level validators, so self-lint == gate.

        E_EXPR_IRI    -> PC-7  (class expression baked into an IRI)
        E_ANTIPATTERN -> PC-8  (privation/compound term in any IRI fragment)
    """
    rep = Report()
    seen_anti = set()
    queue = list(iris)
    while queue:
        frag = (queue.pop(0) or "").strip()
        if not frag:
            continue
        # Sanctioned expression syntax ("not X", "P some F") is NOT an IRI:
        # lint its operand terms individually instead of the raw string.
        expr = parse_class_expression(frag)
        if expr is not None:
            queue.extend(expression_operands(expr))
            continue
        if ("[" in frag or "]" in frag
                or any(t in frag for t in _EXPR_TOKENS)
                or any(t in frag.split(":")[-1] for t in _OWL_CONSTRUCTS)):
            rep.add("E_EXPR_IRI", frag)
        elif _BAD_IRI_CHARS.search(frag) and slugify_fragment(
                frag.split(":", 1)[-1]) is None:
            # Whitespace that is neither an expression nor a slugifiable
            # label-like name can never become a valid IRI (H-1).
            rep.add("E_BAD_IRI", frag)
        for hit in _ANTIPATTERN.findall(frag):
            if hit not in seen_anti:
                seen_anti.add(hit)
                rep.add("E_ANTIPATTERN", hit)
    return rep


def run_all(path: str, imported: Graph | None = None) -> Report:
    raw = open(path, encoding="utf-8").read()
    rep = Report()
    for sub in (check_expression_iris(raw), check_antipatterns_all(raw),
                check_bnode_iris(raw), check_bad_iris(raw),
                check_dangling_and_remint(path, imported),
                check_lost_complement(path, raw),
                report_shared_fillers(path)):
        rep.findings.extend(sub.findings)
    return rep


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "AristotleCategories.owl"
    r = run_all(target)
    for f in r.findings:
        print(f"  [{f.code}] {f.detail}")
    if r.passed:
        print(f"PASS — {target}: no E_* findings")
    else:
        print(f"FAIL — {target}")
        # non-zero exit so this works as a CI gate
        sys.exit(1)
