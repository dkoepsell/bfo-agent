"""Tests for the validation gate client (app/gate_client.py).

Backend selection, fragment serialization, file-level owl_checks parity, and
remote-response mapping -- all without a network or external service.
"""
from types import SimpleNamespace as NS

from app import gate_client as gc
from app.coherence_gate import GateOutcome, GateTier


def _ent(**kw):
    base = dict(kind="class", existing_iri="", label="X", bfo_type="BFO_0000040",
                parent_class="", iri_suggestion="working#X")
    base.update(kw)
    return NS(**base)


def test_backend_selection():
    assert gc.GateClient(url="").backend == "local"
    assert gc.GateClient(url="http://gate.local").backend == "remote"


def test_serialize_fragment_preserves_iris():
    prop = NS(entities=[_ent(label="Contract", iri_suggestion="working#Contract")],
              relations=[])
    xml = gc.serialize_fragment(prop)
    assert "Contract" in xml
    assert "BFO_0000040" in xml  # bfo_type resolved to its obo IRI


def test_file_level_checks_catch_expression_and_privation():
    ent = _ent(bfo_type="BFO_0000019",
               iri_suggestion="working#[ Quantity and not ( p some working:NonQuantity ) ]")
    xml = gc.serialize_fragment(NS(entities=[ent], relations=[]))
    rules = {f["rule"] for f in gc._file_level_findings(xml)}
    assert "PC-7" in rules   # expression baked into IRI
    assert "PC-8" in rules   # NonQuantity privation operand


def test_clean_fragment_has_no_file_level_findings():
    prop = NS(entities=[_ent(label="Norm", bfo_type="BFO_0000020",
                             iri_suggestion="working#Norm")],
              relations=[])
    xml = gc.serialize_fragment(prop)
    assert gc._file_level_findings(xml) == []


def test_remote_response_mapping():
    gr = gc.GateClient._map_response({
        "outcome": "reject", "tier": "reasoner", "reason": "boom",
        "unsat_classes": ["http://x#C"],
    })
    assert gr.outcome == GateOutcome.REJECT
    assert gr.tier == GateTier.REASONER
    assert gr.unsat_classes == ["http://x#C"]


def test_remote_unknown_tier_falls_back_to_none():
    gr = gc.GateClient._map_response({"outcome": "accept", "tier": "weird"})
    assert gr.outcome == GateOutcome.ACCEPT
    assert gr.tier == GateTier.NONE
