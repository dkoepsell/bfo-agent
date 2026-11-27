"""Load-time guard: no seed may narrow BFO 2020 bearer-of / inheres-in.

Library seeds re-declared BFO_0000196 ("bearer of" in BFO 2020) as
"has disposition" with rdfs:range Disposition. Because bearer-of is the
inverse of inheres-in, every quality that inheres in anything was then forced
to be a Disposition, and Quality is disjoint from Disposition, so one ordinary
assertion made the whole ontology inconsistent (Tractatus, 2026-11-27).
"""
import re
from pathlib import Path

import pytest
from owlready2 import World, sync_reasoner, OwlReadyInconsistentOntologyError

from app.ontology_manager import OntologyManager

REPO = Path(__file__).resolve().parents[1]
BFO_PATH = REPO / "ontology" / "bfo.owl"
O = "http://purl.obolibrary.org/obo/"

BAD_SEED_PLUS_QUALITY = f"""<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:obo="{O}"
         xml:base="http://example.org/sigtest">
<owl:Ontology rdf:about="http://example.org/sigtest"/>
<owl:ObjectProperty rdf:about="{O}BFO_0000196">
  <rdfs:label>has disposition</rdfs:label>
  <rdfs:domain rdf:resource="{O}BFO_0000004"/>
  <rdfs:range rdf:resource="{O}BFO_0000016"/>
</owl:ObjectProperty>
<owl:NamedIndividual rdf:about="http://example.org/sigtest#Thing1">
  <rdf:type rdf:resource="{O}BFO_0000040"/>
</owl:NamedIndividual>
<owl:NamedIndividual rdf:about="http://example.org/sigtest#Q1">
  <rdf:type rdf:resource="{O}BFO_0000019"/>
  <obo:BFO_0000197 rdf:resource="http://example.org/sigtest#Thing1"/>
</owl:NamedIndividual>
</rdf:RDF>
"""


def _world(tmp_path):
    f = tmp_path / "sig.owl"
    f.write_text(BAD_SEED_PLUS_QUALITY)
    w = World()
    w.get_ontology(str(BFO_PATH)).load()
    w.get_ontology(f.as_uri()).load()
    return w


def _consistent(w):
    try:
        sync_reasoner(w, infer_property_values=False, debug=0)
        return True
    except OwlReadyInconsistentOntologyError:
        return False


def test_bad_seed_makes_quality_inherence_inconsistent(tmp_path):
    """Pins the bug: without the guard, one quality inhering in a material
    entity is enough to make the ontology inconsistent."""
    assert not _consistent(_world(tmp_path))


def test_guard_restores_consistency_and_bfo_signature(tmp_path):
    w = _world(tmp_path)
    mgr = OntologyManager.__new__(OntologyManager)
    mgr._sanitize_bfo_relation_signatures(world=w)
    g = w.as_rdflib_graph()
    from rdflib import RDFS, URIRef
    p196 = URIRef(O + "BFO_0000196")
    assert {str(o) for o in g.objects(p196, RDFS.range)} == {O + "BFO_0000020"}
    assert {str(o) for o in g.objects(p196, RDFS.label)} == {"bearer of"}
    assert _consistent(w)


def test_seed_templates_do_not_narrow_bearer_of():
    """Guard the seed files directly, like the disjointness seed test."""
    block = re.compile(r"obo:BFO_0000196\s+a\s+owl:ObjectProperty\s*;(.*?)\.\s*\n",
                       re.S)
    offenders = []
    for ttl in REPO.glob("ontology/library/*/seed/*.ttl"):
        for body in block.findall(ttl.read_text(encoding="utf-8")):
            if "has disposition" in body or re.search(
                    r"rdfs:range\s+obo:BFO_00000(?!20\b)\d+", body):
                offenders.append(f"{ttl.parent.parent.name}/{ttl.name}")
    assert not offenders, "seeds narrow BFO_0000196: " + ", ".join(offenders)


def test_repair_script_handles_owlready_description_form():
    """owlready2 writes the property as <rdf:Description>, not
    <owl:ObjectProperty>; the on-disk repair must match both."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fixer", REPO / "scripts" / "fix_bfo0000196_seeds.py")
    fixer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixer)
    for el in ("rdf:Description", "owl:ObjectProperty"):
        xml = (f'<{el} rdf:about="{O}BFO_0000196">\n'
               f'  <rdfs:domain rdf:resource="{O}BFO_0000004"/>\n'
               f'  <rdfs:range rdf:resource="{O}BFO_0000016"/>\n'
               '  <rdfs:label rdf:datatype="http://www.w3.org/2001/XMLSchema'
               '#string">has disposition</rdfs:label>\n'
               f'</{el}>\n'
               f'<{el} rdf:about="{O}BFO_0000040">\n'
               f'  <rdfs:range rdf:resource="{O}BFO_0000016"/>\n'
               f'</{el}>\n')
        out, n = fixer.fix_xml(xml)
        assert n == 2, el
        assert "has disposition" not in out
        assert out.count("BFO_0000016") == 1  # other subjects untouched
        assert "BFO_0000004" in out
    ttl = ("obo:BFO_0000196 a owl:ObjectProperty ;\n"
           "    rdfs:label \"has disposition\" ;\n"
           "    rdfs:domain obo:BFO_0000004 ;\n"
           "    rdfs:range obo:BFO_0000016 .\n\nobo:X a owl:Class .\n")
    out, n = fixer.fix_ttl(ttl)
    assert n == 1 and "bearer of" in out and "obo:X a owl:Class" in out
