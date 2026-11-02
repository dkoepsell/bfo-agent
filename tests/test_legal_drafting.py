"""SPEC-legal-drafting.md: source units, verbatim provenance, quantifier
discipline (L1, 3.3, L3) and the legal extraction profile (L2).

The baseline defects D1, D2 and D5 of the spec are the regression cases.
No network: the extractor runs against a stub client.
"""
import json
from types import SimpleNamespace

import pytest

from app import legal_drafting as ld
from app import legal_source as ls
from app.schema import Proposal

GDPR_ART4 = """REGULATION (EU) 2016/679 OF THE EUROPEAN PARLIAMENT AND OF THE COUNCIL

Article 4
Definitions

For the purposes of this Regulation:

(1) ‘personal data’ means any information relating to an identified or identifiable natural person (‘data subject’);

(2) ‘processing’ means any operation or set of operations which is performed on personal data or on sets of personal data;

(7) ‘controller’ means the natural or legal person, public authority, agency or other body which, alone or jointly with others, determines the purposes and means of the processing of personal data;

Article 37
Designation of the data protection officer

1. The controller and the processor shall designate a data protection officer in any case where:

(a) the processing is carried out by a public authority or body;

5. The data protection officer shall be designated on the basis of professional qualities and, in particular, expert knowledge of data protection law and practices.
"""


# ------------------------------------------------------------- L1 reader

def test_detect_act_celex():
    assert ls.detect_act(GDPR_ART4)[0] == "32016R0679"
    assert ls.detect_act("Regulation (EU) 2018/1725 of 23 October 2018")[0] == "32018R1725"
    assert ls.detect_act("Council Directive 93/13/EEC of 5 April 1993")[0] == "31993L0013"
    assert ls.detect_act("no act named here") == ("", "")


def test_split_units_locators():
    units = ls.split_units(GDPR_ART4, act_id="32016R0679")
    locs = [u.locator for u in units]
    assert "art_4/pnt_7" in locs
    assert "art_37/par_1" in locs
    assert "art_37/par_1/pnt_a" in locs
    assert "art_37/par_5" in locs
    ctrl = next(u for u in units if u.locator == "art_4/pnt_7")
    assert ctrl.kind == "definition"
    assert ctrl.act_id == "32016R0679"
    # offsets point back into the source
    s, e = ctrl.offsets
    assert "determines the purposes and means" in GDPR_ART4[s:e]


def test_unstructured_text_is_locator_unknown():
    units = ls.split_units("Some prose with no headings at all.\n\nMore prose.")
    assert units and not ls.has_structure(units)
    assert {u.locator for u in units} == {"unknown"}


def test_verbatim_match_normalizes_only_whitespace_and_typography():
    quote = "'controller' means the natural or legal person,   public authority"
    assert ls.quote_is_verbatim(quote, GDPR_ART4)
    # paraphrase and ellipsis do not match (D5)
    assert not ls.quote_is_verbatim("Persons can have experience.", GDPR_ART4)
    assert not ls.quote_is_verbatim("'controller' means ... public authority", GDPR_ART4)


def test_locate_returns_the_unit():
    units = ls.split_units(GDPR_ART4)
    u = ls.locate("determines the purposes and means of the processing of personal data", units)
    assert u is not None and u.locator == "art_4/pnt_7"


def test_chunk_units_are_exact_slices_on_article_boundaries():
    chunks = ls.chunk_units(GDPR_ART4, chunk_chars=700)
    assert len(chunks) >= 2
    for c in chunks:
        assert GDPR_ART4.replace("\r\n", "\n")[c["offset"]:c["offset"] + len(c["text"])] == c["text"]
    assert any(c["text"].lstrip().startswith("Article 37") for c in chunks)
    assert any(c["locator"] == "art_37" for c in chunks)


# ------------------------------------------------- L2 extraction profile

class _StubClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.payload))],
                               usage=None)


def _extractor(payload):
    from app.extractor import ClaimExtractor
    ex = ClaimExtractor.__new__(ClaimExtractor)
    ex.client = _StubClient(payload)
    ex.model = "stub"
    ex._on_usage = None
    return ex


def test_legal_extraction_keeps_verbatim_drops_paraphrase():
    payload = {"claims": [
        {"claim": "A controller determines purposes and means of processing.",
         "source_quote": "‘controller’ means the natural or legal person, public authority, "
                         "agency or other body which, alone or jointly with others, determines the "
                         "purposes and means of the processing of personal data",
         "clause_type": "definition", "subject": "controller",
         "quantifier": "definitional", "modality": "none", "confidence": "high"},
        {"claim": "Persons can have experience.",
         "source_quote": "Persons can have experience.",
         "clause_type": "other", "quantifier": "can", "confidence": "medium"},
    ]}
    ex = _extractor(payload)
    rejected = []
    out = ex.extract_legal_chunk(GDPR_ART4, genre="legislation", rejected_out=rejected)
    assert len(out) == 1 and len(rejected) == 1
    c = out[0]
    assert c["act_id"] == "32016R0679"
    assert c["locator"] == "art_4/pnt_7"
    assert c["quantifier"] == "definitional" and c["clause_type"] == "definition"
    assert c["quote_verified"] is True and c["genre"] == "legislation"
    assert rejected[0]["rejected"] == "paraphrase-provenance"
    # the legal prompt, not the philosophy one
    assert "IN HIS OWN VOICE" not in ex.client.calls[0]["messages"][0]["content"]


# ------------------------------------------- 3.3 + L3 legal discipline

def _claim(**kw):
    base = {"id": 1, "claim": "x", "genre": "legislation", "act_id": "32016R0679",
            "locator": "art_4/pnt_7", "quote_verified": True,
            "source_quote": "the natural or legal person, public authority, agency or other body",
            "clause_type": "other", "quantifier": "some", "modality": "none"}
    base.update(kw)
    return base


def _ent(name, bfo, kind="class", **kw):
    d = {"label": name, "iri_suggestion": f"working:{name}", "bfo_type": bfo,
         "bfo_label": "", "kind": kind, "rationale": "", "is_new": True}
    d.update(kw)
    return d


def _proposal(entities, relations):
    relations = [{"rationale": "", **r} for r in relations]
    return Proposal.model_validate({"session_id": "s", "utterance": "u",
                                    "entities": entities, "relations": relations})


def test_philosophy_claims_are_untouched():
    claim = {"id": 1, "claim": "Courts are institutions."}
    assert ld.proposer_utterance(claim) == "Courts are institutions."
    assert ld.provenance_text(claim) == "Courts are institutions."
    p = _proposal([_ent("Person", "BFO_0000040")],
                  [{"s": "working:Person", "p": "bfo:BFO_0000196",
                    "o": "working:Expertise", "quantifier": "some"}])
    assert ld.check(p, claim) == []


def test_legal_utterance_carries_quote_and_metadata():
    u = ld.proposer_utterance(_claim(quantifier="can", modality="permission"))
    assert "SOURCE QUOTE" in u and "art_4/pnt_7" in u
    assert "QUANTIFIER: can" in u and "MODALITY: permission" in u


def test_D1_can_claim_gives_no_bearer_universal():
    """D1: 'persons can have expertise' must not become
    NaturalPerson SubClassOf bearer_of some Expertise."""
    p = _proposal(
        [_ent("NaturalPerson", "BFO_0000040"), _ent("Expertise", "BFO_0000016")],
        [{"s": "working:NaturalPerson", "p": "rdfs:subClassOf",
          "o": "bfo:BFO_0000196 some working:Expertise"}])
    claim = _claim(quantifier="can")
    findings = ld.check(p, claim)
    assert [f["check"] for f in findings] == ["modal-universal"]
    notes = ld.repair(p, findings)
    assert p.relations == []
    np = next(e for e in p.entities if e.label == "NaturalPerson")
    assert np.possible_relations == ["bfo:BFO_0000196 some working:Expertise"]
    assert notes


def test_bearer_side_universal_flipped_to_dependent_side():
    p = _proposal(
        [_ent("NaturalPerson", "BFO_0000040"), _ent("DataProtectionOfficer", "BFO_0000023")],
        [{"s": "working:NaturalPerson", "p": "bfo:BFO_0000196",
          "o": "working:DataProtectionOfficer", "quantifier": "some"}])
    findings = ld.check(p, _claim(quantifier="some"))
    assert [f["check"] for f in findings] == ["bearer-universal"]
    ld.repair(p, findings)
    assert len(p.relations) == 1
    r = p.relations[0]
    assert r.s == "working:DataProtectionOfficer"
    assert r.o == "bfo:BFO_0000197 some working:NaturalPerson"


def test_bearer_universal_allowed_when_text_says_every():
    p = _proposal(
        [_ent("Controller", "BFO_0000040"), _ent("Accountability", "BFO_0000023")],
        [{"s": "working:Controller", "p": "bfo:BFO_0000196",
          "o": "working:Accountability", "quantifier": "some"}])
    assert ld.check(p, _claim(quantifier="every")) == []


def test_D2_multi_bearer_quality_keeps_one_bearer():
    p = _proposal(
        [_ent("Independence", "BFO_0000019"),
         _ent("NationalSupervisoryAuthority", "BFO_0000040"),
         _ent("NaturalPerson", "BFO_0000040")],
        [{"s": "working:Independence", "p": "rdfs:subClassOf",
          "o": "bfo:BFO_0000197 some working:NationalSupervisoryAuthority"},
         {"s": "working:Independence", "p": "rdfs:subClassOf",
          "o": "bfo:BFO_0000197 some working:NaturalPerson"}])
    findings = ld.check(p, _claim(quantifier="every"))
    assert [f["check"] for f in findings] == ["multi-bearer"]
    ld.repair(p, findings)
    assert len(p.relations) == 1
    assert any("multi-bearer" in q for q in p.open_questions)


def test_unscoped_definition_rejects_in_curated_only():
    claim = _claim(clause_type="definition", quantifier="definitional", act_id="")
    p = _proposal([_ent("Controller", "BFO_0000023")], [])
    findings = ld.check(p, claim)
    assert [f["check"] for f in findings] == ["unscoped-definition"]
    assert ld.rejects(findings, faithful=False)
    assert not ld.rejects(findings, faithful=True)


def test_unverified_quote_rejects_in_both_modes():
    findings = ld.check(_proposal([], []), _claim(quote_verified=False))
    assert [f["check"] for f in findings] == ["paraphrase-provenance"]
    assert ld.rejects(findings, faithful=True) and ld.rejects(findings, faithful=False)


def test_D5_provenance_is_the_verbatim_quote():
    claim = _claim()
    p = _proposal([
        _ent("Controller", "BFO_0000023", source_span="Persons can be controllers."),
        _ent("PublicAuthority", "BFO_0000040", source_span="public authority"),
    ], [])
    ld.stamp_provenance(p, claim)
    ctrl, pa = p.entities
    assert ctrl.source_span == claim["source_quote"]      # paraphrase replaced
    assert pa.source_span == "public authority"            # verbatim excerpt kept
    assert ctrl.source_locator == "art_4/pnt_7"
    assert ctrl.source_act == "32016R0679"
    assert ctrl.claim_text == "x"


def test_record_findings_writes_ledger(tmp_path):
    working = tmp_path / "working.owl"
    working.write_text("")
    ld.record_findings(working, _claim(), [{"check": "multi-bearer"}],
                       "faithful", "committed-as-asserted", job_id="j1")
    line = (tmp_path / "legal_findings.jsonl").read_text().strip()
    rec = json.loads(line)
    assert rec["action"] == "committed-as-asserted" and rec["job_id"] == "j1"


def test_append_claims_keeps_legal_fields(tmp_path, monkeypatch):
    from app import jobs
    monkeypatch.setattr(jobs, "JOBS_DIR", tmp_path)
    job = jobs.create_job("t") if hasattr(jobs, "create_job") else None
    if job is None:
        pytest.skip("jobs.create_job not available")
    jid = job["job_id"]
    jobs.append_claims(jid, [
        {"claim": "a", "confidence": "high", **{k: v for k, v in _claim().items() if k != "id"}},
        {"claim": "b", "confidence": "high"},
    ])
    claims = jobs.load_job(jid)["claims"]
    assert claims[0]["locator"] == "art_4/pnt_7" and claims[0]["quantifier"] == "some"
    assert "genre" not in claims[1] and "locator" not in claims[1]
