"""Extraction scorecard (scripts/extraction_scorecard.py) on hand-built
fixtures with known counts: one defect of each kind the scorecard reports."""
from __future__ import annotations

import json

import pytest
from rdflib import BNode, Graph, Literal, OWL, RDF, RDFS, URIRef

from scripts import extraction_scorecard as sc

W = "http://davidkoepsell.com/bfo-agent/working"
WN = W + "#"
OBO = "http://purl.obolibrary.org/obo/"
META = "http://davidkoepsell.com/bfo-agent/meta#"
ME, PROC, QUAL, GDC = (URIRef(OBO + b) for b in
                       ("BFO_0000040", "BFO_0000015", "BFO_0000019", "BFO_0000031"))
PART_IN = URIRef(OBO + "BFO_0000056")
LOC_IN = URIRef(OBO + "BFO_0000176")


def cls(g, local, label, parent, span=None, definition=None):
    u = URIRef(WN + local)
    g.add((u, RDF.type, OWL.Class))
    g.add((u, RDFS.label, Literal(label)))
    g.add((u, RDFS.subClassOf, parent))
    if span:
        g.add((u, URIRef(META + "sourceSpan"), Literal(span)))
    if definition:
        g.add((u, URIRef(OBO + "IAO_0000115"), Literal(definition)))
    else:
        g.add((u, URIRef(META + "definitionStatus"), Literal("absent-in-source")))
    return u


def some(g, c, prop, filler, kind=OWL.someValuesFrom):
    b = BNode()
    g.add((b, RDF.type, OWL.Restriction))
    g.add((b, OWL.onProperty, prop))
    g.add((b, kind, filler))
    g.add((c, RDFS.subClassOf, b))


def run_graph() -> Graph:
    g = Graph()
    g.add((URIRef(W), RDF.type, OWL.Ontology))
    method = cls(g, "DietMethod", "diet method", PROC, definition="A process that ...")
    cls(g, "FecalSampling", "fecal sampling", method, definition="A diet method that ...")
    study = cls(g, "IsotopeStudy", "isotope study", PROC)                       # flat
    feather = cls(g, "Feather", "feather", ME,
                  span="feathers can be used in stable isotope analysis")       # flat
    some(g, feather, PART_IN, study)                                            # false universal, modal
    bird = cls(g, "Bird", "bird", ME)                                           # flat
    some(g, bird, PART_IN, method)                                              # false universal
    cls(g, "Species", "species", ME)                                            # taxon as material
    cls(g, "Telescopes", "high-powered telescopes", ME)                         # plural
    cls(g, "VolEst", "volumetric estimate", GDC,
        definition="A GDC that gives greater importance to large items.")      # caveat as definition
    rb = cls(g, "ReadAbundance", "read abundance", QUAL, definition="A quality ...")
    g.add((rb, URIRef(META + "distinctFrom"), QUAL))                            # distinct from parent
    coll = URIRef(WN + "PatuxentCollection")
    g.add((coll, RDF.type, OWL.NamedIndividual))
    g.add((coll, RDFS.label, Literal("Patuxent collection")))
    card = cls(g, "Card", "card", ME, definition="A material entity ...")
    some(g, card, LOC_IN, coll, kind=OWL.hasValue)                             # hasValue in TBox
    lead = URIRef(WN + "Lead")
    g.add((lead, RDF.type, OWL.NamedIndividual))
    g.add((lead, RDFS.label, Literal("lead")))                                  # kind-like individual
    return g


def previous_graph() -> Graph:
    g = Graph()
    g.add((URIRef(W), RDF.type, OWL.Ontology))
    cls(g, "ReadAbundance", "read abundance", GDC, definition="A GDC ...")       # category conflict
    cls(g, "Bird", "bird", ME)
    cls(g, "IsotopeAnalysis", "isotope study", PROC)                           # re-minted
    cls(g, "Courtship", "courtship", PROC)                                     # dropped
    return g


@pytest.fixture
def paths(tmp_path):
    run, prev = tmp_path / "run.owl", tmp_path / "prev.owl"
    run_graph().serialize(str(run), format="xml")
    previous_graph().serialize(str(prev), format="xml")
    return run, prev


def test_structure_and_strength(paths):
    card = sc.scorecard(paths[0], with_construction=False)
    h = card["headline"]
    assert h["classes"] == 10
    # every class but FecalSampling hangs directly off a BFO category
    assert card["structure"]["flat_classes"] == 9
    assert h["definition_absent_share"] == 0.5
    assert h["caveat_as_definition"] == 1
    assert h["material_participation"] == 2
    assert card["strength"]["material_participation_modal"] == 1
    assert h["modal_span_axioms"] == 1


def test_category_and_surface(paths):
    card = sc.scorecard(paths[0], with_construction=False)
    c, s = card["category"], card["surface"]
    assert c["kind_like_individuals"] == ["lead"]
    assert c["has_value_in_class_axioms"] == ["card"]
    assert c["taxa_as_material_entities"] == ["species"]
    assert c["distinct_from_self_or_parent"] == ["read abundance"]
    assert s["plural_labels"] == ["high-powered telescopes"]


def test_drift(paths):
    d = sc.scorecard(paths[0], previous=paths[1], with_construction=False)["drift"]
    assert d["shared"] == 2 and d["dropped"] == 2
    assert d["category_conflicts"] == [{"class": "ReadAbundance",
                                        "previous": "generically dependent continuant",
                                        "current": "quality"}]
    assert d["reminted"] == [{"previous": "IsotopeAnalysis", "current": "IsotopeStudy"}]


def test_scope_and_gold(paths, tmp_path):
    scope = tmp_path / "scope.json"
    scope.write_text(json.dumps({"terms": ["diet method", "feather", "bird"],
                                 "questions": ["Which samples support a diet record?"]}))
    gold = tmp_path / "gold.json"
    gold.write_text(json.dumps({
        "classes": [{"label": "fecal sampling", "bfo": "BFO_0000015"},
                    {"label": "crop contents"}],
        "axioms": [{"subject": "fecal sampling", "property": "subClassOf",
                    "filler": "diet method"}]}))
    card = sc.scorecard(paths[0], scope_file=str(scope), gold_file=str(gold),
                        with_construction=False)
    assert "high-powered telescopes" in card["scope"]["out_of_scope_classes"]
    assert "diet method" not in card["scope"]["out_of_scope_classes"]
    g = card["gold"]
    assert g["class_recall"] == 0.5 and g["class_category_accuracy"] == 1.0
    assert g["axiom_recall"] == 1.0
    assert g["missed_classes"] == ["crop contents"]


def test_gates(paths):
    card = sc.scorecard(paths[0], with_construction=False)
    assert sc.gate_failures(card, ["flat_share>0.5"]) == ["flat_share>0.5 (got 0.9)"]
    assert sc.gate_failures(card, ["material_participation>5"]) == []
