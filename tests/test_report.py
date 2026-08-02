"""The full report bundle, and the repair that must not become an edit.

The repair is the part worth guarding. Its whole licence is that it adds no
claim and removes no axiom, so most of what follows is checking that it stays
inside that licence rather than checking that it fixes things.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest
import rdflib
from rdflib import OWL, RDF, RDFS, URIRef

from app.report import repair as repair_mod
from app.report.bundle import Report, Section, report_markdown, write_bundle
from app.report.repair import repair_for_digestibility, serialise

HEADER = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:owl="http://www.w3.org/2002/07/owl#"
  xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#">
"""
FOOTER = "</rdf:RDF>\n"


def owl(body: str) -> rdflib.Graph:
    g = rdflib.Graph()
    g.parse(data=HEADER + body + FOOTER, format="xml")
    return g


# --------------------------------------------------------------------------
# The licence: no claim added, no axiom removed, original untouched
# --------------------------------------------------------------------------

def test_the_source_graph_is_never_mutated():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <rdf:Description rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
      </rdf:Description>
    """)
    before = len(g)
    result = repair_for_digestibility(g)
    assert len(g) == before
    assert len(result.graph) > before


def test_no_axiom_is_ever_removed():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
      </owl:Class>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
    """)
    result = repair_for_digestibility(g)
    for triple in g:
        assert triple in result.graph, triple


def test_repair_is_idempotent():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <rdf:Description rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
      </rdf:Description>
    """)
    once = repair_for_digestibility(g)
    twice = repair_for_digestibility(once.graph)
    assert twice.total == 0
    assert len(twice.graph) == len(once.graph)


def test_a_clean_file_is_left_alone():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
      </owl:Class>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
    """)
    result = repair_for_digestibility(g)
    assert result.total == 0
    assert len(result.graph) == len(g)


def test_builtin_vocabulary_is_never_declared():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://www.w3.org/2002/07/owl#Thing"/>
      </owl:Class>
    """)
    result = repair_for_digestibility(g)
    assert (OWL.Thing, RDF.type, OWL.Class) not in result.graph


# --------------------------------------------------------------------------
# Declarations, the commonest reason a file will not load
# --------------------------------------------------------------------------

def test_an_undeclared_class_is_declared():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <rdf:Description rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
      </rdf:Description>
    """)
    result = repair_for_digestibility(g)
    for local in ("Alpha", "Beta"):
        assert (URIRef(f"http://example.org/t#{local}"), RDF.type, OWL.Class) \
            in result.graph
    declared = next(r for r in result.repairs if r.id == "declare-class")
    assert declared.count == 2


def test_an_undeclared_property_is_declared_as_an_object_property():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <rdf:Description rdf:about="http://example.org/t#rel">
        <rdfs:domain rdf:resource="http://example.org/t#Alpha"/>
      </rdf:Description>
    """)
    result = repair_for_digestibility(g)
    assert (URIRef("http://example.org/t#rel"), RDF.type, OWL.ObjectProperty) \
        in result.graph


def test_a_property_used_only_with_literals_is_declared_as_data():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <rdf:Description rdf:about="http://example.org/t#label2">
        <rdfs:domain rdf:resource="http://example.org/t#Alpha"/>
      </rdf:Description>
      <owl:NamedIndividual rdf:about="http://example.org/t#a">
        <rdf:type rdf:resource="http://example.org/t#Alpha"/>
      </owl:NamedIndividual>
    """)
    g.add((URIRef("http://example.org/t#a"),
           URIRef("http://example.org/t#label2"), rdflib.Literal("x")))
    result = repair_for_digestibility(g)
    assert (URIRef("http://example.org/t#label2"), RDF.type, OWL.DatatypeProperty) \
        in result.graph


def test_an_iri_used_as_a_property_is_not_also_declared_a_class():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <rdf:Description rdf:about="http://example.org/t#rel">
        <rdfs:domain rdf:resource="http://example.org/t#Alpha"/>
        <rdfs:subClassOf rdf:resource="http://example.org/t#Alpha"/>
      </rdf:Description>
    """)
    result = repair_for_digestibility(g)
    rel = URIRef("http://example.org/t#rel")
    assert (rel, RDF.type, OWL.Class) not in result.graph


def test_an_untyped_restriction_is_typed():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:ObjectProperty rdf:about="http://example.org/t#rel"/>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf>
          <rdf:Description>
            <owl:onProperty rdf:resource="http://example.org/t#rel"/>
            <owl:someValuesFrom rdf:resource="http://example.org/t#Beta"/>
          </rdf:Description>
        </rdfs:subClassOf>
      </owl:Class>
    """)
    result = repair_for_digestibility(g)
    assert len(list(result.graph.subjects(RDF.type, OWL.Restriction))) == 1


def test_a_missing_ontology_header_is_added():
    g = owl("""
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
    """)
    result = repair_for_digestibility(g)
    assert result.ontology_iri
    assert list(result.graph.subjects(RDF.type, OWL.Ontology))


def test_an_existing_header_is_not_duplicated():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
    """)
    result = repair_for_digestibility(g)
    assert len(list(result.graph.subjects(RDF.type, OWL.Ontology))) == 1
    assert result.ontology_iri == "http://example.org/t"


# --------------------------------------------------------------------------
# The IRI-form mismatch found in SOoL_v1
# --------------------------------------------------------------------------

MIXED = """
  <owl:Ontology rdf:about="http://example.org/t"/>
  <owl:Class rdf:about="file:///lib/working.owl#Alpha"/>
  <owl:Class rdf:about="http://example.org/t#Alpha">
    <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
  </owl:Class>
  <owl:Class rdf:about="http://example.org/t#Beta"/>
"""


def test_a_file_scheme_artifact_is_reconciled_with_the_real_namespace():
    """Regression against the real SOoL_v1 shape: classes declared under file://
    while the axioms use the ontology's own namespace, so every audit counts the
    same entity twice."""
    g = owl(MIXED)
    result = repair_for_digestibility(g)
    assert not [n for n in result.graph.all_nodes()
                if isinstance(n, URIRef) and str(n).startswith("file://")]
    canonical = next(r for r in result.repairs if r.id == "canonicalise-iri")
    assert canonical.count == 1


def test_a_file_scheme_iri_with_no_counterpart_is_left_alone():
    """The rewrite reconciles, it does not invent. Without a same-named entity in
    the real namespace there is nothing to reconcile it with."""
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="file:///lib/working.owl#Orphan"/>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
    """)
    result = repair_for_digestibility(g)
    assert URIRef("file:///lib/working.owl#Orphan") in set(result.graph.all_nodes())
    canonical = next(r for r in result.repairs if r.id == "canonicalise-iri")
    assert canonical.count == 0


# --------------------------------------------------------------------------
# What it refuses to do
# --------------------------------------------------------------------------

def test_every_kernel_primitive_is_listed_as_not_repaired():
    from app import recognition as rec

    assert set(repair_mod.NOT_REPAIRED) == set(rec.KERNEL)


def test_a_subclass_cycle_is_not_broken():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
      </owl:Class>
      <owl:Class rdf:about="http://example.org/t#Beta">
        <rdfs:subClassOf rdf:resource="http://example.org/t#Alpha"/>
      </owl:Class>
    """)
    result = repair_for_digestibility(g)
    a, b = URIRef("http://example.org/t#Alpha"), URIRef("http://example.org/t#Beta")
    assert (a, RDFS.subClassOf, b) in result.graph
    assert (b, RDFS.subClassOf, a) in result.graph


def test_a_missing_definition_is_not_invented():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
    """)
    result = repair_for_digestibility(g)
    assert not list(result.graph.objects(None, OWL.equivalentClass))


def test_disjointness_is_never_added_or_removed():
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha">
        <owl:disjointWith rdf:resource="http://example.org/t#Beta"/>
      </owl:Class>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
    """)
    result = repair_for_digestibility(g)
    assert len(list(result.graph.triples((None, OWL.disjointWith, None)))) == 1


def test_the_result_states_its_guarantee_and_its_limits():
    blob = repair_for_digestibility(owl(
        '<owl:Ontology rdf:about="http://example.org/t"/>')).to_dict()
    assert "No claim about the domain was added" in blob["guarantee"]
    assert "separate artifact" in blob["guarantee"]
    assert len(blob["not_repaired"]) == 12


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------

def test_the_repaired_variant_reparses():
    result = repair_for_digestibility(owl(MIXED))
    text = serialise(result)
    again = rdflib.Graph()
    again.parse(data=text, format="xml")
    assert len(again) == len(result.graph)


def test_the_repaired_graph_never_has_a_blank_node_predicate():
    """Regression, found in production. The declaration pass iterated one of the
    graph's own generators while adding to it, which corrupts rdflib's indexes.
    The damage did not surface at the point of the bug: it surfaced later as a
    triple with a blank node in predicate position, which no serialiser can
    write."""
    g = owl("""
      <owl:Ontology rdf:about="http://example.org/t"/>
      <owl:Class rdf:about="http://example.org/t#Alpha"/>
      <owl:Class rdf:about="http://example.org/t#Beta"/>
      <rdf:Description rdf:about="http://example.org/t#a">
        <rdf:type rdf:resource="http://example.org/t#Alpha"/>
      </rdf:Description>
      <rdf:Description rdf:about="http://example.org/t#b">
        <rdf:type rdf:resource="http://example.org/t#Beta"/>
      </rdf:Description>
    """)
    result = repair_for_digestibility(g)
    for _s, p, _o in result.graph:
        assert isinstance(p, URIRef), p
    serialise(result)


@pytest.mark.parametrize("body", [
    '<owl:Ontology rdf:about="http://example.org/t"/>',
    MIXED,
    """<owl:Ontology rdf:about="http://example.org/t"/>
       <rdf:Description rdf:about="http://example.org/t#Alpha">
         <rdfs:subClassOf rdf:resource="http://example.org/t#Beta"/>
       </rdf:Description>""",
    """<owl:Class rdf:about="http://example.org/t#Alpha"/>""",
])
def test_every_repaired_variant_is_serialisable_and_reparses(body):
    """The whole point of the module. If the output will not parse, nothing else
    it does matters."""
    result = repair_for_digestibility(owl(body))
    again = rdflib.Graph()
    again.parse(data=serialise(result), format="xml")
    assert len(again) == len(result.graph)


def test_a_malformed_triple_is_dropped_and_counted():
    g = owl('<owl:Ontology rdf:about="http://example.org/t"/>')
    g.add((URIRef("http://example.org/t#Alpha"), rdflib.BNode(),
           URIRef("http://example.org/t#Beta")))
    result = repair_for_digestibility(g)
    dropped = next(r for r in result.repairs if r.id == "drop-nonconforming-triple")
    assert dropped.count == 1
    serialise(result)


def test_the_guarantee_admits_the_one_removal():
    blob = repair_for_digestibility(owl(
        '<owl:Ontology rdf:about="http://example.org/t"/>')).to_dict()
    assert "No claim about the domain was added" in blob["guarantee"]
    assert "drop-nonconforming-triple" in blob["guarantee"]


# --------------------------------------------------------------------------
# The bundle
# --------------------------------------------------------------------------

def make_report() -> Report:
    report = Report(ontology="example", generated_at="2026-08-02T00:00:00Z",
                    report_schema="1.0", artifact_sha256="a" * 64)
    report.sections = [
        Section("statistics", "Statistics", data={"classes": 10, "individuals": 2}),
        Section("kernel_audit", "Structural",
                data={"metrics": {"named_classes": 10}, "finding_counts": {"K-B1": 1}}),
        Section("coherence", "Reasoner", status="failed", error="RuntimeError: java"),
        Section("scope", "Scope of review", status="skipped",
                error="no usable chain declaration"),
        Section("repair", "Digestibility repairs",
                data=repair_for_digestibility(owl(MIXED)).to_dict()),
    ]
    return report


def test_a_failed_section_is_reported_not_swallowed():
    report = make_report()
    blob = report.to_dict()
    assert "coherence" in blob["sections_failed"]
    assert "scope" in blob["sections_skipped"]
    assert "statistics" in blob["sections_ok"]
    assert report.data("coherence") is None


def test_markdown_names_what_could_not_be_produced():
    text = report_markdown(make_report())
    assert "Sections that could not be produced" in text
    assert "RuntimeError: java" in text
    assert "not a clean result" in text


def test_markdown_carries_the_separation_caveat():
    text = report_markdown(make_report())
    assert "as delivered" in text
    assert "nothing here was" in text.lower() or "computed from it" in text


def test_bundle_contains_every_expected_member():
    report = make_report()
    blob = write_bundle(report, "<rdf:RDF/>", "client table")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = set(z.namelist())
        assert names == {"report.json", "report.md", "repaired.owl",
                         "repairs.json", "scope-client.md", "README.txt"}
        parsed = json.loads(z.read("report.json"))
        assert parsed["ontology"] == "example"
        readme = z.read("README.txt").decode()
        assert "not a corrected ontology" in readme


def test_the_client_table_is_omitted_when_there_is_no_chain():
    blob = write_bundle(make_report(), "<rdf:RDF/>", "")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        assert "scope-client.md" not in z.namelist()
