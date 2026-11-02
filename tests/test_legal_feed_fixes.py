"""Fixes from the full 2018/1725 feed (EUpersonalData2, 1090 claims):

* legal_kernel wrote every literal (definiens, scope, labels, sources) as
  an IRI made of its text, and wildcard removes were no-ops;
* recitals had no locator, were fed first, auto-approved, and a recital's
  wording became the definition of 'profiling' and 'biometric data';
* ODP-003 positions were unconnected (no holder, no grounding), every
  position was said to be realized, duties inhered in roles, and generic
  duties were single individuals;
* individuals had no provenance; proposals were truncated at 4000 tokens.
"""
from pathlib import Path

import pytest
from rdflib import Literal, URIRef

from app import jobs, legal_kernel, legal_positions, legal_source
from app import legal_drafting as ld
from app.schema import Proposal

ROOT = Path(__file__).resolve().parent.parent
BFO = ROOT / "ontology" / "bfo.owl"
needs_bfo = pytest.mark.skipif(not BFO.exists(), reason="needs ontology/bfo.owl")

PREAMBLE = """REGULATION (EU) 2018/1725 OF THE EUROPEAN PARLIAMENT AND OF THE COUNCIL

Whereas:

(1)

The protection of natural persons in relation to the processing of personal data is a fundamental right.

(2) Biometric data are covered by the definition of biometric data only when processed through a specific technical means.

HAVE ADOPTED THIS REGULATION:

Article 3

Definitions

(18) ‘biometric data’ means personal data resulting from specific technical processing relating to the physical, physiological or behavioural characteristics of a natural person;
"""


@pytest.fixture
def mgr(tmp_path):
    from app.ontology_manager import OntologyManager
    wp = tmp_path / "Legal" / "working.owl"
    wp.parent.mkdir()
    return OntologyManager(bfo_path=BFO, working_path=wp)


def _proposal(entities, relations=()):
    return Proposal.model_validate({
        "session_id": "s", "utterance": "u",
        "entities": [{"bfo_label": "", "rationale": "", "is_new": True, **e} for e in entities],
        "relations": [{"rationale": "", **r} for r in relations]})


def _claim(**kw):
    c = {"id": 1, "genre": "legislation", "act_id": "32018R1725",
         "act_title": "Regulation (EU) 2018/1725", "locator": "art_33/par_1",
         "quote_verified": True, "clause_type": "norm", "quantifier": "every",
         "modality": "obligation", "claim": "x",
         "source_quote": "the controller shall notify the personal data breach"}
    c.update(kw)
    return c


# ------------------------------------------------------ literals as IRIs

@needs_bfo
def test_kernel_writes_literals_not_iris(mgr):
    legal_kernel.merge_lof(mgr)
    p = _proposal([{"label": "biometric data", "iri_suggestion": "working:BiometricData",
                    "bfo_type": "BFO_0000031", "kind": "class"}])
    mgr.commit_proposal(p, verify=False)
    claim = _claim(clause_type="definition", locator="art_3/pnt_18", subject="biometric data",
                   source_quote="‘biometric data’ means personal data resulting from specific "
                                "technical processing")
    legal_kernel.record_definition(mgr, p, claim)
    g = mgr.world.as_rdflib_graph()
    cls = URIRef(legal_kernel._entity_iri(p.entities[0]))
    defs = list(g.objects(cls, URIRef(legal_kernel.IAO_DEFINITION)))
    assert defs and all(isinstance(d, Literal) for d in defs)
    bad = [o for _, _, o in g if isinstance(o, URIRef) and " " in str(o)]
    assert bad == []


@needs_bfo
def test_wildcard_remove_removes(mgr):
    legal_kernel.merge_lof(mgr)
    s = legal_kernel.LEGAL_ROLE
    p = legal_kernel._working_meta("note")
    legal_kernel._put(mgr, s, p, legal_kernel._lit("a", "en"))
    legal_kernel._put(mgr, s, p, legal_kernel._lit("b"))
    legal_kernel._remove(mgr, s, p, None)
    assert legal_kernel._objects(mgr, s, p) == []


# ------------------------------------------------------------- recitals

def test_recitals_get_locators_and_kind():
    units = legal_source.split_units(PREAMBLE, "32018R1725")
    by = {u.locator: u for u in units}
    assert by["rec_1"].kind == "recital" and "fundamental right" in by["rec_1"].text
    assert by["rec_2"].kind == "recital"
    # after the enacting formula, (18) is a point of Article 3, not a recital
    assert "art_3/pnt_18" in by and by["art_3/pnt_18"].kind == "definition"
    assert legal_source.locate("covered by the definition of biometric data", units).locator == "rec_2"


def test_chunks_start_inside_the_right_recital():
    text = PREAMBLE.replace("(2) Biometric", "\n".join(["(2) Filler."] * 1) + "\n\n(3) Biometric")
    chunks = legal_source.chunk_units(text, chunk_chars=120)
    locs = [c["locator"] for c in chunks]
    assert any(l.startswith("rec_") for l in locs)


class _Stub:
    def __init__(self, payload):
        import json
        from types import SimpleNamespace
        self._r = SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))], usage=None)
        self.messages = self

    def create(self, **kw):
        return self._r


def test_recital_definition_is_not_a_definition():
    from app.extractor import ClaimExtractor
    ex = ClaimExtractor.__new__(ClaimExtractor)
    ex.client = _Stub({"claims": [
        {"claim": "Biometric data are covered only when technically processed.",
         "source_quote": "Biometric data are covered by the definition of biometric data only "
                         "when processed through a specific technical means.",
         "clause_type": "definition", "subject": "biometric data",
         "quantifier": "definitional", "confidence": "high"},
        {"claim": "Biometric data means personal data from technical processing.",
         "source_quote": "‘biometric data’ means personal data resulting from specific technical "
                         "processing",
         "clause_type": "definition", "subject": "biometric data",
         "quantifier": "definitional", "confidence": "high"}]})
    ex.model, ex._on_usage = "stub", None
    rec, art = ex.extract_legal_chunk(PREAMBLE, genre="legislation")
    assert rec["locator"] == "rec_2" and rec["provision"] == "recital"
    assert rec["clause_type"] == "other" and "not a definition" in rec["note"]
    assert art["provision"] == "operative" and art["clause_type"] == "definition"


@needs_bfo
def test_operative_definition_supersedes_a_recital_one(mgr):
    legal_kernel.merge_lof(mgr)
    p = _proposal([{"label": "biometric data", "iri_suggestion": "working:BiometricData",
                    "bfo_type": "BFO_0000031", "kind": "class"}])
    mgr.commit_proposal(p, verify=False)
    first = legal_kernel.record_definition(mgr, p, _claim(
        clause_type="definition", locator="unknown", subject="biometric data",
        source_quote="they are covered by the definition of biometric data only when processed"))
    assert first["action"] == "defined"
    second = legal_kernel.record_definition(mgr, p, _claim(
        clause_type="definition", locator="art_3/pnt_18", subject="biometric data",
        source_quote="‘biometric data’ means personal data resulting from specific technical "
                     "processing"))
    assert second["action"] == "defined-from-operative-provision"
    cls = second["class"]
    defs = [str(d) for d in legal_kernel._objects(mgr, cls, legal_kernel.IAO_DEFINITION)]
    assert defs == ["‘biometric data’ means personal data resulting from specific technical processing"]
    old = [str(d) for d in legal_kernel._objects(mgr, cls, legal_kernel._working_meta("supersededDefinition"))]
    assert old and old[0].startswith("they are covered")
    # and a later recital-like definition does not displace it
    third = legal_kernel.record_definition(mgr, p, _claim(
        clause_type="definition", locator="unknown", subject="biometric data",
        source_quote="biometric data is something else entirely"))
    assert third["action"] != "defined-from-operative-provision"


# -------------------------------------------------------- jobs and order

@pytest.fixture
def jobdir(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "_job_path", lambda jid: tmp_path / f"{jid}.json")
    return tmp_path


def test_recitals_not_auto_approved(jobdir):
    job = jobs.create_job("t")
    job = jobs.append_claims(job["job_id"], [
        {"claim": "a", "confidence": "high", "provision": "recital"},
        {"claim": "b", "confidence": "high", "provision": "operative"}])
    a, b = job["claims"]
    assert a["approved"] is False and a["provision"] == "recital"
    assert b["approved"] is True


def test_prioritize_pending_orders_and_keeps_watermark_sound(jobdir):
    job = jobs.create_job("t")
    jid = job["job_id"]
    jobs.append_claims(jid, [
        {"claim": "recital", "confidence": "high", "provision": "recital"},
        {"claim": "norm", "confidence": "high", "provision": "operative", "clause_type": "norm"},
        {"claim": "done", "confidence": "high", "provision": "operative", "clause_type": "norm"},
        {"claim": "def", "confidence": "high", "provision": "operative", "clause_type": "definition"},
        {"claim": "est", "confidence": "high", "provision": "operative", "clause_type": "establishment"}])
    jobs.update_claim_status(jid, 2, "committed", verdict="consistent")
    res = jobs.prioritize_pending(jid)
    job = jobs.load_job(jid)
    order = [(c["id"], c["claim"]) for c in job["claims"]]
    committed = [i for i, n in order if n == "done"]
    assert committed == [2]
    pend = [n for i, n in sorted(order) if n != "done"]
    assert pend == ["def", "est", "norm", "recital"]
    # every pending id is above every non-pending id
    assert min(i for i, n in order if n != "done") > 2
    assert res["order"] == {0: 1, 1: 1, 2: 1, 3: 1}


def test_prioritize_refused_while_feeding(jobdir):
    job = jobs.create_job("t")
    job["status"] = "feeding"
    jobs.save_job(job)
    with pytest.raises(RuntimeError):
        jobs.prioritize_pending(job["job_id"])


# -------------------------------------------------------------- positions

def _duty_proposal(*relations, kind="class", extra=()):
    ents = [{"label": "breach notification duty", "iri_suggestion": "working:NotifyDuty",
             "bfo_type": "BFO_0000020", "kind": kind, "parent_class": "lof:ODP003_0001002"
             if kind == "class" else None},
            {"label": "breach notification", "iri_suggestion": "working:Notification",
             "bfo_type": "BFO_0000015", "kind": "class"},
            {"label": "controller", "iri_suggestion": "working:Controller",
             "bfo_type": "BFO_0000023", "kind": "class"},
            {"label": "European Data Protection Supervisor", "iri_suggestion": "working:EDPS",
             "bfo_type": "BFO_0000040", "kind": "individual"}, *extra]
    rels = list(relations)
    if kind == "individual":
        rels.append({"s": "working:NotifyDuty", "p": "rdf:type", "o": "lof:ODP003_0001002"})
    return _proposal(ents, rels)


def test_realization_some_becomes_only():
    p = _duty_proposal({"s": "working:NotifyDuty", "p": "rdfs:subClassOf",
                        "o": "bfo:BFO_0000054 some working:Notification"})
    f = legal_positions.check(p, _claim())
    assert [x["check"] for x in f] == ["realization-universal"]
    legal_positions.repair(p, f)
    r = [x for x in p.relations if "0000054" in x.p]
    assert len(r) == 1 and r[0].quantifier == "only" and r[0].o == "working:Notification"


def test_role_inherence_goes_through_a_holder():
    p = _duty_proposal({"s": "working:NotifyDuty", "p": "rdfs:subClassOf",
                        "o": "bfo:BFO_0000197 some working:Controller"})
    f = legal_positions.check(p, _claim())
    assert [x["check"] for x in f] == ["role-inherence"]
    legal_positions.repair(p, f)
    holder = next(e for e in p.entities if e.iri_suggestion == "working:ControllerHolder")
    assert holder.bfo_type == "BFO_0000040" and holder.kind == "class"
    assert any(r.s == "working:ControllerHolder" and "RO_0000087 some working:Controller" in r.o
               for r in p.relations)
    assert any(r.p == "lof:ODP003_0001100" and r.o == "working:ControllerHolder"
               and r.quantifier == "some" for r in p.relations)
    assert not any("0000197 some working:Controller" in (r.o or "") for r in p.relations)


def test_unheld_individual_becomes_a_class():
    p = _duty_proposal({"s": "working:NotifyDuty", "p": "bfo:BFO_0000054",
                        "o": "working:Notification"}, kind="individual")
    f = legal_positions.check(p, _claim())
    assert [x["check"] for x in f] == ["unheld-position-individual"]
    legal_positions.repair(p, f)
    duty = p.entities[0]
    assert duty.kind == "class" and duty.parent_class == "lof:ODP003_0001002"
    real = next(r for r in p.relations if r.s == "working:NotifyDuty" and "0000054" in r.p)
    assert real.quantifier == "only"
    assert any(r.p == "rdfs:subClassOf" and r.o == "lof:ODP003_0001002" for r in p.relations)


def test_named_holder_individual_is_kept_and_gets_held_by():
    p = _duty_proposal({"s": "working:NotifyDuty", "p": "bfo:BFO_0000197", "o": "working:EDPS"},
                       kind="individual")
    assert legal_positions.check(p, _claim()) == []
    notes = legal_positions.complete(p, _claim())
    assert any(r.p == "lof:ODP003_0001100" and r.o == "working:EDPS" and r.quantifier is None
               for r in p.relations)
    content = legal_kernel.act_iris("32018R1725")["content"]
    assert any(r.p == "lof:ODP003_0001101" and r.o == content for r in p.relations)
    assert notes


def test_class_position_grounded_by_value_and_remedy_typed():
    p = _duty_proposal()
    legal_positions.complete(p, _claim(clause_type="remedy"))
    content = legal_kernel.act_iris("32018R1725")["content"]
    g = next(r for r in p.relations if r.p == "lof:ODP003_0001101")
    assert g.o == content and g.quantifier == "value"
    assert any(r.p == "rdfs:subClassOf" and r.o == "lof:ODP004_0001002" for r in p.relations)


def test_non_positions_untouched():
    p = _proposal([{"label": "notification", "iri_suggestion": "working:Notification",
                    "bfo_type": "BFO_0000015", "kind": "class"}],
                  [{"s": "working:Notification", "p": "rdfs:subClassOf",
                    "o": "bfo:BFO_0000057 some working:Notification"}])
    assert legal_positions.check(p, _claim()) == []
    assert legal_positions.complete(p, _claim()) == []


@needs_bfo
def test_repaired_duty_commits_consistently(mgr):
    """The repaired forms (only, value, held by some holder, has_role) go
    through the real write path and the reasoner."""
    legal_kernel.merge_lof(mgr)
    legal_kernel.ensure_act(mgr, "32018R1725", "Regulation (EU) 2018/1725")
    mgr.commit_proposal(_proposal([{"label": "controller", "iri_suggestion": "working:Controller",
                                    "bfo_type": "BFO_0000023", "kind": "class",
                                    "parent_class": "lof:ODP002_0001000"}]), verify=False)
    p = _duty_proposal({"s": "working:NotifyDuty", "p": "rdfs:subClassOf",
                        "o": "bfo:BFO_0000054 some working:Notification"},
                       {"s": "working:NotifyDuty", "p": "rdfs:subClassOf",
                        "o": "bfo:BFO_0000197 some working:Controller"})
    p.entities = [e for e in p.entities if e.iri_suggestion != "working:Controller"]
    p.entities.append(Proposal.model_validate({"session_id": "s", "utterance": "u", "entities": [{
        "label": "controller", "iri_suggestion": "working:Controller", "bfo_type": "BFO_0000023",
        "bfo_label": "", "kind": "class", "rationale": "", "is_new": False,
        "existing_iri": "http://davidkoepsell.com/bfo-agent/working#Controller"}]}).entities[0])
    claim = _claim()
    legal_positions.complete(p, claim, mgr)
    findings = ld.check(p, claim, mgr)
    checks = sorted(f["check"] for f in findings)
    assert checks == ["realization-universal", "role-inherence"]
    ld.repair(p, findings, mgr)
    mgr.commit_proposal(p, verify=True)          # raises on inconsistency
    g = mgr.world.as_rdflib_graph()
    from rdflib.namespace import OWL
    duty = URIRef("http://davidkoepsell.com/bfo-agent/working#NotifyDuty")
    assert (None, OWL.allValuesFrom, URIRef("http://davidkoepsell.com/bfo-agent/working#Notification")) in g
    assert (None, OWL.hasValue, URIRef(legal_kernel.act_iris("32018R1725")["content"])) in g


# ------------------------------------------------- individual provenance

def test_individual_provenance_written():
    from rdflib import Graph
    from app import content_quality
    from types import SimpleNamespace
    g = Graph()
    ent = SimpleNamespace(source_span="the controller shall notify", source_locator="art_33/par_1",
                          source_act="32018R1725", claim_text="x")
    added = content_quality.annotate_new_individual(g, "http://x.org/i", ent)
    assert len(added) == 4 and all(isinstance(t[2], Literal) for t in added)


def test_proposer_budget_is_configurable():
    from app import config
    assert config.PROPOSER_MAX_TOKENS >= 8000


# ------------------------------------------------ the gate cannot go blind

@needs_bfo
def test_reasoner_refuses_malformed_iris():
    """A text IRI made HermiT unable to parse the ontology; owlready2 then
    returned normally and every later check passed as consistent."""
    import owlready2
    from app.ontology_manager import ReasonerInputError, _sync_reasoner_guarded
    w = owlready2.World()
    w.get_ontology(BFO.as_uri()).load()
    o = w.get_ontology("http://t.org/t#")
    g = w.as_rdflib_graph()
    with o:
        g.add((URIRef("http://t.org/t#A"), URIRef(legal_kernel.IAO_DEFINITION),
               URIRef("http://t.org/'a' means something")))
    with pytest.raises(ReasonerInputError):
        _sync_reasoner_guarded(w)


@needs_bfo
def test_reasoner_reads_hermit_output_for_parse_failures(monkeypatch):
    """Second line of defence: even if the pre-flight misses it, HermiT's own
    'could not parse' output raises instead of passing."""
    import owlready2
    from app import ontology_manager as om
    monkeypatch.setattr(om, "_preflight_iris", lambda target: None)
    w = owlready2.World()
    w.get_ontology(BFO.as_uri()).load()
    o = w.get_ontology("http://t.org/t#")
    g = w.as_rdflib_graph()
    with o:
        g.add((URIRef("http://t.org/t#A"), URIRef(legal_kernel.IAO_DEFINITION),
               URIRef("http://t.org/'a' means something")))
    with pytest.raises(om.ReasonerInputError):
        om._sync_reasoner_guarded(w)


@needs_bfo
def test_reasoner_still_passes_a_clean_world():
    import owlready2
    from app.ontology_manager import _sync_reasoner_guarded
    w = owlready2.World()
    w.get_ontology(BFO.as_uri()).load()
    _sync_reasoner_guarded(w)


@needs_bfo
def test_rdf_type_relation_declares_the_individual(mgr):
    from rdflib.namespace import OWL, RDF
    mgr.commit_proposal(_proposal([{"label": "union body", "iri_suggestion": "working:UnionBody",
                                    "bfo_type": "BFO_0000040", "kind": "class"}]), verify=False)
    mgr.commit_proposal(_proposal([], [{"s": "working:DefaultingBody", "p": "rdf:type",
                                        "o": "working:UnionBody"}]), verify=False)
    g = mgr.world.as_rdflib_graph()
    x = URIRef("http://davidkoepsell.com/bfo-agent/working#DefaultingBody")
    assert (x, RDF.type, OWL.NamedIndividual) in g


def test_legal_annotation_properties_are_declared_in_vocab():
    from app import relation_vocab
    for n in ("sourceLocator", "sourceAct", "claimText", "definiens", "scope",
              "definedIn", "supersededDefinition"):
        assert n in relation_vocab.BFOAGENT_ANNOTATIONS


# ------------------------------------------------------- repair script

DRAFT = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:obo="http://purl.obolibrary.org/obo/"
         xmlns:meta="http://davidkoepsell.com/bfo-agent/meta#">
<owl:Class rdf:about="https://w3id.org/lof/ODP003_0001002"><rdfs:label>duty</rdfs:label></owl:Class>
<owl:Class rdf:about="http://purl.obolibrary.org/obo/BFO_0000023"/>
<owl:Class rdf:about="https://w3id.org/lof/ODP001_0001001"/>
<owl:NamedIndividual rdf:about="http://davidkoepsell.com/bfo-agent/working#Act_32018R1725_Content">
  <rdf:type rdf:resource="https://w3id.org/lof/ODP001_0001001"/></owl:NamedIndividual>
<owl:Class rdf:about="http://davidkoepsell.com/bfo-agent/working#Controller">
  <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/BFO_0000023"/></owl:Class>
<owl:Class rdf:about="http://davidkoepsell.com/bfo-agent/working#Notification"/>
<owl:Class rdf:about="http://davidkoepsell.com/bfo-agent/working#BiometricData">
  <rdfs:label>biometric data</rdfs:label>
  <obo:IAO_0000115 rdf:resource="http://davidkoepsell.com/bfo-agent/they are covered by the definition of biometric data only when"/>
</owl:Class>
<owl:Class rdf:about="http://davidkoepsell.com/bfo-agent/working#NotifyDuty">
  <rdfs:subClassOf rdf:resource="https://w3id.org/lof/ODP003_0001002"/>
  <rdfs:subClassOf><owl:Restriction><owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/BFO_0000054"/>
    <owl:someValuesFrom rdf:resource="http://davidkoepsell.com/bfo-agent/working#Notification"/></owl:Restriction></rdfs:subClassOf>
  <rdfs:subClassOf><owl:Restriction><owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/BFO_0000197"/>
    <owl:someValuesFrom rdf:resource="http://davidkoepsell.com/bfo-agent/working#Controller"/></owl:Restriction></rdfs:subClassOf>
  <meta:sourceAct>32018R1725</meta:sourceAct>
</owl:Class>
<owl:NamedIndividual rdf:about="http://davidkoepsell.com/bfo-agent/working#GenericDuty">
  <rdf:type rdf:resource="https://w3id.org/lof/ODP003_0001002"/>
</owl:NamedIndividual>
</rdf:RDF>
"""


def test_repair_script_on_a_mini_draft(tmp_path):
    import importlib.util
    from rdflib import Graph
    from rdflib.namespace import OWL, RDF, RDFS
    spec = importlib.util.spec_from_file_location("repair", ROOT / "scripts" / "repair_legal_draft.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    src = tmp_path / "draft.owl"
    src.write_text(DRAFT, encoding="utf-8")
    act = tmp_path / "act.txt"
    act.write_text("(18) ‘biometric data’ means personal data resulting from specific technical "
                   "processing;\n", encoding="utf-8")
    out = tmp_path / "out.owl"
    assert mod.main([str(src), "-o", str(out), "--source", str(act)]) == 0
    g = Graph().parse(out)
    W = "http://davidkoepsell.com/bfo-agent/working#"
    O = "http://purl.obolibrary.org/obo/"
    # no text IRIs; operative definition installed, recital one superseded
    assert not [o for _, _, o in g if isinstance(o, URIRef) and " " in str(o)]
    d = [str(x) for x in g.objects(URIRef(W + "BiometricData"), URIRef(O + "IAO_0000115"))]
    assert d == ["‘biometric data’ means personal data resulting from specific technical processing"]
    # realization some -> only; role inherence -> holder; grounded in the act
    duty = URIRef(W + "NotifyDuty")
    rs = [r for r in g.objects(duty, RDFS.subClassOf) if (r, RDF.type, OWL.Restriction) in g]
    props = {(str(next(g.objects(r, OWL.onProperty))).rsplit("/", 1)[-1],
              [k for k in ("someValuesFrom", "allValuesFrom", "hasValue")
               if (r, getattr(OWL, k), None) in g][0],
              str(next(g.objects(r, getattr(OWL, [k for k in ("someValuesFrom", "allValuesFrom", "hasValue")
                                                    if (r, getattr(OWL, k), None) in g][0])))).rsplit("#", 1)[-1])
             for r in rs}
    assert ("BFO_0000054", "allValuesFrom", "Notification") in props
    assert ("BFO_0000197", "someValuesFrom", "ControllerHolder") in props
    assert ("ODP003_0001100", "someValuesFrom", "ControllerHolder") in props
    assert ("ODP003_0001101", "hasValue", "Act_32018R1725_Content") in props
    # an unheld generic duty individual becomes a class
    assert (URIRef(W + "GenericDuty"), RDF.type, OWL.Class) in g
