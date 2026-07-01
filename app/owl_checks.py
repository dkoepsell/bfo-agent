"""
app/owl_checks.py
=================
Canonical OWL-fragment emitters + validators for bfo-agent (PC-7 / PC-8).

This module is the SINGLE SOURCE OF TRUTH for the lexical OWL checks: it is
imported both by the agent-side construction linter (self-lint, PC-7/PC-8 over a
proposal's IRIs) and by the gate client (emit-time, run_all over the serialized
fragment + import closure). Keeping one copy guarantees self-lint == gate.

Promoted verbatim from the reference ``sool_owl_checks.py`` (kept at repo root
as the standalone CI shim). Derived from defects found in AristotleCategories.owl
(refurbished bfo-agent output).

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
# Part 2 — VALIDATORS  (gate-side)
# ---------------------------------------------------------------------------

# Boolean / restriction operators that must never appear inside an IRI fragment.
_EXPR_TOKENS = [" and ", " or ", " not ", " some ", " only ", " value ",
                " min ", " max ", " exactly "]
_EXPR_BRACKET = re.compile(r'rdf:(?:about|resource)="[^"]*[\[\]][^"]*"')
_IRI_ATTR = re.compile(r'rdf:(?:about|resource)="([^"]*)"')

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
        return not self.findings
    def add(self, code, detail):
        self.findings.append(Finding(code, detail))


def check_expression_iris(raw_text: str) -> Report:
    """A7 / E_EXPR_IRI — class expression baked into an IRI. Pure lexical."""
    rep = Report()
    for m in _IRI_ATTR.finditer(raw_text):
        frag = m.group(1)
        if "[" in frag or "]" in frag or any(t in frag for t in _EXPR_TOKENS):
            rep.add("E_EXPR_IRI", frag.strip())
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
    for iri in iris:
        frag = (iri or "").strip()
        if not frag:
            continue
        if "[" in frag or "]" in frag or any(t in frag for t in _EXPR_TOKENS):
            rep.add("E_EXPR_IRI", frag)
        for hit in _ANTIPATTERN.findall(frag):
            if hit not in seen_anti:
                seen_anti.add(hit)
                rep.add("E_ANTIPATTERN", hit)
    return rep


def run_all(path: str, imported: Graph | None = None) -> Report:
    raw = open(path, encoding="utf-8").read()
    rep = Report()
    for sub in (check_expression_iris(raw), check_antipatterns_all(raw),
                check_bnode_iris(raw), check_dangling_and_remint(path, imported)):
        rep.findings.extend(sub.findings)
    return rep


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "AristotleCategories.owl"
    r = run_all(target)
    if r.passed:
        print(f"PASS — {target}: no A7/A6/B4 findings")
    else:
        print(f"FAIL — {target}:")
        for f in r.findings:
            print(f"  [{f.code}] {f.detail}")
        # non-zero exit so this works as a CI gate
        sys.exit(1)
