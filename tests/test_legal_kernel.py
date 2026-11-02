"""SPEC-legal-drafting.md L4-L8 and section 8: the LOF kernel, acts in the
graph, scoped definitions, identity, readers, the draft report, finishing,
and the D1-D8 measurements on the frozen baseline draft."""
import json
import shutil
from pathlib import Path

import pytest

from app import legal_eval, legal_kernel, legal_report, legal_source
from app import legal_drafting as ld
from app.schema import Proposal

ROOT = Path(__file__).resolve().parent.parent
BFO = ROOT / "ontology" / "bfo.owl"
FIX = ROOT / "tests" / "fixtures" / "legal"
GDPR = (FIX / "sources" / "32016R0679.txt").read_text(encoding="utf-8")
EUI = (FIX / "sources" / "32018R1725.txt").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(not BFO.exists(), reason="needs ontology/bfo.owl")


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
    c = {"id": 1, "genre": "legislation", "act_id": "32016R0679",
         "act_title": "Regulation (EU) 2016/679", "locator": "art_4/pnt_7",
         "quote_verified": True, "clause_type": "definition", "subject": "controller",
         "quantifier": "definitional", "modality": "none", "claim": "x",
         "source_quote": "'controller' means the natural or legal person, public authority, "
                         "agency or other body which, alone or jointly with others, determines "
                         "the purposes and means of the processing of personal data"}
    c.update(kw)
    return c


# ---------------------------------------------------------------- L5

def test_merge_lof_is_idempotent_and_anchored(mgr):
    assert legal_kernel.merge_lof(mgr) is True
    role = mgr.world[legal_kernel.LEGAL_ROLE]
    assert role is not None
    assert any("BFO_0000023" in getattr(p, "iri", "") for p in role.is_a)
    assert legal_kernel.merge_lof(mgr) is False


def test_lof_prefix_resolves():
    from app.ontology_manager import WORKING_IRI, _resolve_iri
    assert _resolve_iri("lof:ODP002_0001000", WORKING_IRI) == legal_kernel.LEGAL_ROLE


def test_kernel_rules_and_candidates_name_legal_role():
    assert "lof:ODP002_0001000" in legal_kernel.kernel_rules()
    assert any(c["iri"] == legal_kernel.LEGAL_ROLE for c in legal_kernel.candidates())


def test_commit_under_legal_role(mgr):
    legal_kernel.merge_lof(mgr)
    p = _proposal([{"label": "controller", "iri_suggestion": "working:Controller",
                    "bfo_type": "BFO_0000023", "kind": "class",
                    "parent_class": "lof:ODP002_0001000"}])
    mgr.commit_proposal(p, verify=False)
    from app.ontology_manager import WORKING_IRI
    ctl = mgr.world[WORKING_IRI + "#Controller"]
    assert ctl is not None
    assert legal_kernel.LEGAL_ROLE in {getattr(a, "iri", "") for a in ctl.ancestors()}


# --------------------------------------------------------------- 2.2

def test_ensure_act_creates_content_enactment_document(mgr):
    legal_kernel.merge_lof(mgr)
    iris = legal_kernel.ensure_act(mgr, "32016R0679", "Regulation (EU) 2016/679")
    w = mgr.world
    content, enact, doc = (w[iris[k]] for k in ("content", "enactment", "document"))
    assert content is not None and enact is not None and doc is not None
    assert legal_kernel.LEGAL_CONTENT in {c.iri for c in content.is_a}
    assert legal_kernel.ensure_act(mgr, "32016R0679") == iris      # idempotent
    assert legal_kernel.ensure_act(mgr, "") is None


# ---------------------------------------------------------------- L4

def _define(mgr, claim, label="controller", name="Controller", definition=None):
    p = _proposal([{"label": label, "iri_suggestion": f"working:{name}",
                    "bfo_type": "BFO_0000023", "kind": "class",
                    "parent_class": "lof:ODP002_0001000",
                    **({"definition": definition} if definition else {})}])
    mgr.commit_proposal(p, verify=False)
    return legal_kernel.record_definition(mgr, p, claim)


def test_D4_definition_is_verbatim_and_sourced(mgr):
    legal_kernel.merge_lof(mgr)
    rec = _define(mgr, _claim(), definition="A legal role that determines purposes.")
    assert rec["action"] == "defined"
    defs = legal_kernel._objects(mgr, rec["class"], legal_kernel.IAO_DEFINITION)
    assert [str(d) for d in defs] == [_claim()["source_quote"]]
    derived = legal_kernel._objects(mgr, rec["class"], legal_kernel._working_meta("derivedDefinition"))
    assert [str(d) for d in derived] == ["A legal role that determines purposes."]
    src = legal_kernel._objects(mgr, rec["class"], legal_kernel.IAO_DEFINITION_SOURCE)
    assert "32016R0679" in str(src[0]) and "art_4/pnt_7" in str(src[0])
    # the definition individual is part of the act's content
    parts = legal_kernel._objects(mgr, rec["definition_individual"], legal_kernel.CONTINUANT_PART_OF)
    assert str(parts[0]) == legal_kernel.act_iris("32016R0679")["content"]


def test_D4_two_acts_two_scoped_senses(mgr):
    legal_kernel.merge_lof(mgr)
    _define(mgr, _claim())
    eui = _claim(act_id="32018R1725", act_title="Regulation (EU) 2018/1725",
                 locator="art_3/pnt_8",
                 source_quote="'controller' means the Union institution or body, the "
                              "directorate-general or any other organisational entity")
    p = _proposal([{"label": "controller", "iri_suggestion": "working:Controller",
                    "bfo_type": "BFO_0000023", "kind": "class", "is_new": False,
                    "existing_iri": "http://davidkoepsell.com/bfo-agent/working#Controller"}])
    rec = legal_kernel.record_definition(mgr, p, eui)
    assert rec["action"] == "scoped-senses"
    assert len(rec["senses"]) == 2
    labels = [str(legal_kernel._objects(mgr, s, legal_kernel.RDFS_LABEL)[0]) for s in rec["senses"]]
    assert any("2018/1725" in l for l in labels)
    # the earlier definition was not overwritten
    defs = [str(d) for d in legal_kernel._objects(mgr, rec["class"], legal_kernel.IAO_DEFINITION)]
    assert _claim()["source_quote"] in defs


def test_same_definition_twice_adds_a_source(mgr):
    legal_kernel.merge_lof(mgr)
    _define(mgr, _claim())
    again = _claim(act_id="32016R0680", locator="art_3/pnt_8")
    p = _proposal([{"label": "controller", "iri_suggestion": "working:Controller",
                    "bfo_type": "BFO_0000023", "kind": "class", "is_new": False,
                    "existing_iri": "http://davidkoepsell.com/bfo-agent/working#Controller"}])
    assert legal_kernel.record_definition(mgr, p, again)["action"] == "same-definition-added-source"


def test_scope_phrase_detected():
    assert legal_kernel.detect_scope("For the purposes of this Regulation: (1)").startswith(
        "For the purposes of this Regulation")


# ---------------------------------------------------------------- L6

def test_D3_class_and_individual_with_one_label_are_distinguished(mgr):
    legal_kernel.merge_lof(mgr)
    mgr.commit_proposal(_proposal([{
        "label": "European Data Protection Supervisor", "iri_suggestion": "working:EDPS",
        "bfo_type": "BFO_0000040", "kind": "individual"}]), verify=False)
    p = _proposal([{"label": "European Data Protection Supervisor",
                    "iri_suggestion": "working:EDPSRole", "bfo_type": "BFO_0000023",
                    "bfo_label": "role", "kind": "class"}])
    notes = ld.resolve_identity(p, mgr)
    ent = p.entities[0]
    assert ent.label == "European Data Protection Supervisor (role)"
    assert ent.distinct_from.endswith("#EDPS")
    assert notes


def test_same_kind_same_label_is_reused(mgr):
    legal_kernel.merge_lof(mgr)
    p = _proposal([{"label": "duty", "iri_suggestion": "working:Duty",
                    "bfo_type": "BFO_0000020", "kind": "class"}],
                  [{"s": "working:Duty", "p": "rdfs:subClassOf", "o": "bfo:BFO_0000020"}])
    ld.resolve_identity(p, mgr)
    assert p.entities[0].is_new is False
    assert p.entities[0].existing_iri == "https://w3id.org/lof/ODP003_0001002"
    assert p.relations[0].s == "https://w3id.org/lof/ODP003_0001002"


# ------------------------------------------------------------ readers

FORMEX = """<?xml version="1.0"?><ACT><ENACTING.TERMS><ARTICLE IDENTIFIER="004">
<TI.ART>Article 4</TI.ART><STI.ART><P>Definitions</P></STI.ART>
<ALINEA>For the purposes of this Regulation:
<LIST><ITEM><NP><NO.P>(7)</NO.P><TXT>‘controller’ means the natural or legal person</TXT></NP></ITEM></LIST>
</ALINEA></ARTICLE><ARTICLE IDENTIFIER="037"><TI.ART>Article 37</TI.ART>
<PARAG IDENTIFIER="037.001"><NO.PARAG>1.</NO.PARAG><ALINEA>The controller shall designate a data protection officer.</ALINEA></PARAG>
</ARTICLE></ENACTING.TERMS></ACT>"""

AKN = """<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act><body>
<article eId="art_4"><num>Article 4</num><heading>Definitions</heading>
<paragraph><list><point eId="art_4__point_7"><num>(7)</num><content><p>'controller' means the natural or legal person</p></content></point></list></paragraph>
</article><article eId="art_37"><num>Article 37</num><paragraph eId="art_37__para_1"><num>1.</num>
<content><p>The controller shall designate a data protection officer.</p></content></paragraph></article>
</body></act></akomaNtoso>"""

JLT = """<Law><LawBody><MainProvision><Article Num="4"><ArticleCaption>(Definitions)</ArticleCaption>
<ArticleTitle>Article 4</ArticleTitle><Paragraph Num="1"><ParagraphNum>(1)</ParagraphNum>
<ParagraphSentence><Sentence>The term "controller" means the natural or legal person.</Sentence></ParagraphSentence>
</Paragraph></Article></MainProvision></LawBody></Law>"""

XHTML = """<html><body><p class="ti-art">Article 4</p><p class="sti-art">Definitions</p>
<div class="grid"><div class="c1"><p>(7)</p></div><div class="c2"><p>‘controller’ means the natural or legal person</p></div></div>
<p class="ti-art">Article 37</p><div><p>1.   The controller shall designate a data protection officer.</p></div></body></html>"""


@pytest.mark.parametrize("raw,fmt,locs", [
    (FORMEX, "formex", {"art_4/pnt_7", "art_37/par_1"}),
    (AKN, "akn", {"art_4/pnt_7", "art_37/par_1"}),
    (JLT, "jlt", {"art_4/pnt_1"}),
    (XHTML, "xhtml", {"art_4/pnt_7", "art_37/par_1"}),
])
def test_structured_readers(raw, fmt, locs):
    text, detected = legal_source.to_text(raw)
    assert detected == fmt
    found = {u.locator for u in legal_source.split_units(text)}
    assert locs <= found, (text, found)
    assert legal_source.quote_is_verbatim("controller", text)


def test_real_eurlex_excerpts_have_their_definitions():
    units = legal_source.split_units(GDPR)
    assert sum(u.locator.startswith("art_4/pnt_") for u in units) >= 26
    units = legal_source.split_units(EUI)
    assert any(u.locator == "art_3/pnt_8" and "controller" in u.text for u in units)


# ------------------------------------------------------- section 8.1

def test_baseline_reproduces_D1_to_D8():
    m = legal_eval.measure(FIX / "eu_personal_data_baseline.owl",
                           source_text=GDPR + EUI, bfo_path=BFO)
    assert m["classes"] == 27 and m["individuals"] == 4
    d1 = {(a.split("#")[-1], f.split("#")[-1]) for a, _, f in m["D1_bearer_universal"]["items"]}
    assert {("NaturalPerson", "DataProtectionOfficer"), ("NaturalPerson", "Expertise"),
            ("NaturalPerson", "Experience")} <= d1
    assert [c.split("#")[-1] for c, _ in m["D2_multi_bearer"]["items"]] == ["Independence"]
    assert [l for l, _ in m["D3_shared_labels"]["items"]] == ["european data protection supervisor"]
    assert m["D4_definitions"]["with_source_act"] == 0
    assert m["D5_provenance"]["rate"] < 0.5
    assert any(c.endswith("#DataProcessing") for c in m["D6_isolated"]["items"])
    assert m["D7_legal_structure"]["legal_content"] == 0
    assert m["D8_finishing"]["untagged_labels"] == m["D8_finishing"]["labels"]
    assert m["D8_finishing"]["working_namespace"] is True


# ------------------------------------------------------- L7 and L8

def test_report_and_finish(mgr, tmp_path):
    legal_kernel.merge_lof(mgr)
    rec = _define(mgr, _claim())
    claims = [dict(_claim(), status="committed"),
              _claim(id=2, clause_type="norm", quantifier="every", modality="obligation",
                     subject="controller", locator="art_37/par_1", status="committed",
                     source_quote="The controller and the processor shall designate a data "
                                  "protection officer in any case where")]
    job = {"job_id": "j", "name": "t", "claims": claims}
    from app.ontology_manager import WORKING_IRI
    mgr.save()
    r = legal_report.write_report(job, Path(mgr.working_path), WORKING_IRI)
    d = Path(mgr.working_path).parent
    assert (d / "draft-report.md").exists() and (d / "draft-report.json").exists()
    assert r["provenance"]["verbatim_rate"] == 1.0
    row = r["mlc_coverage"][0]
    assert {"source", "norm", "addressee", "position", "conduct", "condition"} <= set(row["present"])
    assert "breach" in row["missing"]
    assert r["audit"]["status"] in ("not-run", "not-configured")
    md = (d / "draft-report.md").read_text()
    assert "## MLC coverage" in md and "—" not in md

    rel = legal_report.finish(Path(mgr.working_path), BFO,
                              {"name": "Legal", "namespace": "https://example.org/legal/"},
                              [{"act_id": "32016R0679", "title": "GDPR"}], WORKING_IRI)
    m = legal_eval.measure(rel["artifact"])
    assert m["D8_finishing"]["untagged_labels"] == 0
    assert all(m["D8_finishing"]["header"].values())
    assert m["D8_finishing"]["working_namespace"] is False
    assert (Path(rel["catalog"]).parent / "bfo.owl").exists()
    assert "obo/bfo.owl" in Path(rel["catalog"]).read_text()
