"""Genre-independent source grounding (app/grounding.py) and the grounded
drafting tier of app/legal_drafting.py.

Regression cases come from the second EU personal-data draft (2018/1725):
near-quotes that drop a word, restatements stored as spans, a modal verb
lost before the axiom was written, and alternatives read as a conjunction.
No network: the extractor runs against a stub client.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from app import grounding as gr
from app import legal_drafting as ld
from app.schema import Proposal

RECITALS = """(18) Natural persons may be associated with online identifiers provided by their devices, applications, tools and protocols, such as internet protocol addresses, cookie identifiers or other identifiers such as radio frequency identification tags. This may leave traces which, in particular when combined with unique identifiers and other information received by the servers, may be used to create profiles of the natural persons and identify them.

(19) Consent should be given by a clear affirmative act establishing a freely given, specific, informed and unambiguous indication of the data subject’s agreement to the processing of personal data relating to him or her, such as by a written statement, including by electronic means, or an oral statement. Silence, pre-ticked boxes or inactivity should not therefore constitute consent.
"""

PHILOSOPHY = """A person is a substance that persists through change. Persons can
have expertise, and some persons have great experience, but neither belongs to
the nature of a person. A role exists because its bearer is in some special
natural, social or institutional circumstance. """ * 20


# ------------------------------------------------------------ ground_quote

def test_exact_quote_is_verbatim():
    g = gr.ground_quote("Silence, pre-ticked boxes or inactivity should not therefore "
                        "constitute consent.", RECITALS)
    assert g.status == "verbatim" and g.verified


def test_near_quote_is_snapped_to_the_source_words():
    g = gr.ground_quote("online identifiers provided by devices, applications, tools "
                        "and protocols", RECITALS)
    assert g.status == "snapped"
    assert g.quote == "online identifiers provided by their devices, applications, tools and protocols"
    assert g.quote in RECITALS


def test_restatement_is_not_laundered_into_a_quote():
    for q in ("Genetic data is a kind of personal data.",
              "Controllers are entities that process personal data.",
              "Natural persons may bear the relation of association with online identifiers"):
        assert gr.ground_quote(q, RECITALS).status == "unverified", q


def test_ellipsis_and_short_quotes_are_exact_only():
    assert gr.ground_quote("Consent should be given ... affirmative act", RECITALS).status == "unverified"
    # four words, one changed: too short to snap
    assert gr.ground_quote("pre-ticked box or inactivity", RECITALS).status == "unverified"


def test_snap_never_crosses_a_sentence():
    q = ("identification tags. This may leave traces which, in particular when "
         "combined with unique identifiers")
    g = gr.ground_quote(q.replace("unique", "special"), RECITALS)
    # Either refused, or snapped to text inside ONE sentence.
    assert g.status in ("unverified", "snapped")
    if g.status == "snapped":
        assert "tags. This" not in g.quote and g.quote in RECITALS


def test_ground_claim_keeps_model_wording_and_sentence():
    c = gr.ground_claim({"source_quote": "Consent is a clear affirmative act establishing a "
                         "freely given, specific, informed and unambiguous indication of a "
                         "data subject's agreement to processing."}, RECITALS)
    assert c["quote_verified"] and c["quote_grounding"] == "snapped"
    assert c["source_quote"] in RECITALS
    assert c["quote_model"].startswith("Consent is")
    # the governing modal is in view for the proposer
    assert c["source_sentence"].startswith("Consent should be given by")


# ------------------------------------------------------------ genre

def test_detect_genre():
    src = Path(__file__).parent / "fixtures" / "legal" / "sources" / "32018R1725.txt"
    assert gr.detect_genre(src.read_text(encoding="utf-8")) == "legislation"
    assert gr.detect_genre(PHILOSOPHY) == "philosophy"
    assert gr.detect_genre("") == "philosophy"


def test_resolve_genre_explicit_choice_wins():
    src = (Path(__file__).parent / "fixtures" / "legal" / "sources" / "32018R1725.txt").read_text(
        encoding="utf-8")
    assert gr.resolve_genre("philosophy", src) == "philosophy"
    assert gr.resolve_genre("auto", src) == "legislation"
    assert gr.resolve_genre(None, PHILOSOPHY) == "philosophy"
    assert gr.resolve_genre("nonsense", PHILOSOPHY) == "philosophy"


# ------------------------------------------- philosophy extraction profile

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


def test_philosophy_extraction_grounds_quotes_and_keeps_quantifier():
    ex = _extractor({"claims": [
        {"claim": "Persons can have expertise.",
         "source_quote": "Persons can have expertise",
         "subject": "person", "quantifier": "can", "confidence": "high"},
        {"claim": "Roles exist because of circumstances.",
         "source_quote": "Roles exist because of the bearer's circumstances.",
         "quantifier": "every", "confidence": "high",
         "alternatives": ["natural", "social", "institutional"]},
    ]})
    out = ex.extract_chunk(PHILOSOPHY[:600], section="ch1")
    a, b = out
    assert a["quote_verified"] and a["quantifier"] == "can" and a["subject"] == "person"
    assert a["confidence"] == "high"
    # an ungroundable quote: kept, flagged, never auto-approved
    assert b["quote_verified"] is False and b["quote_grounding"] == "unverified"
    assert b["confidence"] == "low" and "not found" in b["note"]
    assert b["alternatives"] == ["natural", "social", "institutional"]
    prompt = ex.client.calls[0]["messages"][0]["content"]
    assert "IN HIS OWN VOICE" in prompt and "QUANTIFIER" in prompt


def test_jobs_persist_grounding_fields(tmp_path, monkeypatch):
    from app import jobs
    monkeypatch.setattr(jobs, "_job_path", lambda jid: tmp_path / f"{jid}.json")
    job = jobs.create_job("t")
    job = jobs.append_claims(job["job_id"], [{
        "claim": "x", "source_quote": "q", "confidence": "high",
        "quantifier": "can", "quote_verified": True, "quote_grounding": "snapped",
        "source_sentence": "s", "quote_model": "m", "alternatives": ["a", "b"]}])
    c = job["claims"][0]
    for k in ("quantifier", "quote_verified", "quote_grounding", "source_sentence",
              "quote_model", "alternatives"):
        assert k in c, k


# ------------------------------------------- grounded drafting tier

def _claim(**kw):
    base = {"id": 1, "claim": "x", "quote_verified": True, "quote_grounding": "verbatim",
            "source_quote": "Persons can have expertise", "quantifier": "some"}
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


def test_pre_grounding_claims_are_untouched():
    claim = {"id": 1, "claim": "Courts are institutions.", "source_quote": "Courts ..."}
    assert not ld.is_grounded_claim(claim)
    assert ld.proposer_utterance(claim) == "Courts are institutions."
    assert ld.claim_rules(claim) == ""


def test_grounded_utterance_and_rules():
    c = _claim(quantifier="can", subject="person", alternatives=["a", "b"],
               source_sentence="Persons can have expertise, and some persons have ...")
    u = ld.proposer_utterance(c)
    assert 'SOURCE QUOTE (verbatim): "Persons can have expertise"' in u
    assert "SOURCE SENTENCE" in u and "QUANTIFIER: can; SUBJECT: person" in u
    assert "ALTERNATIVES" in u
    assert "G-4" in ld.claim_rules(c)
    assert ld.provenance_text(c) == "Persons can have expertise"
    unverified = _claim(quote_verified=False, quote_grounding="unverified")
    assert "not verified" in ld.proposer_utterance(unverified)
    assert ld.provenance_text(unverified) == "x"


def test_D1_any_genre_can_claim_gives_no_universal():
    p = _proposal(
        [_ent("Person", "BFO_0000040"), _ent("Expertise", "BFO_0000016")],
        [{"s": "working:Person", "p": "rdfs:subClassOf",
          "o": "bfo:BFO_0000196 some working:Expertise"}])
    findings = ld.check(p, _claim(quantifier="can"))
    assert [f["check"] for f in findings] == ["modal-universal"]
    ld.repair(p, findings)
    assert p.relations == []


def test_modal_verb_before_the_quote_counts():
    """'That officer should be | a person with expert knowledge ...': the
    extractor said 'every', the source sentence says 'should be'."""
    claim = _claim(quantifier="some",
                   source_quote="a person with expert knowledge of data protection law",
                   source_sentence="That officer should be a person with expert knowledge "
                                   "of data protection law and practices.")
    p = _proposal(
        [_ent("Officer", "BFO_0000023"), _ent("Knowledge", "BFO_0000016")],
        [{"s": "working:Officer", "p": "rdfs:subClassOf",
          "o": "bfo:BFO_0000054 some working:Knowledge"}])
    assert [f["check"] for f in ld.check(p, claim)] == ["modal-universal"]


def test_modal_elsewhere_in_the_sentence_does_not_count():
    claim = _claim(quantifier="every", source_quote="A person is a substance",
                   source_sentence="A person is a substance that persists, and you may "
                                   "read this however you like.")
    p = _proposal(
        [_ent("Person", "BFO_0000040"), _ent("Persistence", "BFO_0000015")],
        [{"s": "working:Person", "p": "rdfs:subClassOf",
          "o": "bfo:BFO_0000056 some working:Persistence"}])
    assert ld.check(p, claim) == []


def test_alternatives_are_not_a_conjunction():
    """Provision has_participant some Device AND some Application AND some
    Protocol, from 'devices, applications, tools and protocols'."""
    ents = [_ent("Provision", "BFO_0000015"), _ent("Device", "BFO_0000040"),
            _ent("Application", "BFO_0000031"), _ent("Protocol", "BFO_0000031")]
    rels = [{"s": "working:Provision", "p": "rdfs:subClassOf",
             "o": f"bfo:BFO_0000057 some working:{x}"} for x in ("Device", "Application", "Protocol")]
    p = _proposal(ents, rels)
    claim = _claim(quantifier="every",
                   alternatives=["devices", "applications", "tools", "protocols"])
    findings = ld.check(p, claim)
    assert [f["check"] for f in findings] == ["alternatives-as-conjunction"]
    notes = ld.repair(p, findings)
    assert p.relations == [] and notes
    prov = next(e for e in p.entities if e.label == "Provision")
    assert prov.possible_relations == ["BFO_0000057 some (Device or Application or Protocol)"]
    assert any("alternatives" in q for q in p.open_questions)


def test_single_restriction_to_one_alternative_is_fine():
    p = _proposal([_ent("Provision", "BFO_0000015"), _ent("Device", "BFO_0000040")],
                  [{"s": "working:Provision", "p": "rdfs:subClassOf",
                    "o": "bfo:BFO_0000057 some working:Device"}])
    claim = _claim(quantifier="every", alternatives=["devices", "applications"])
    assert ld.check(p, claim) == []


def test_stamp_provenance_any_genre():
    claim = _claim(source_sentence="Persons can have expertise, and some persons have great experience")
    p = _proposal([_ent("Person", "BFO_0000040", source_span="Persons can have experience."),
                   _ent("Expertise", "BFO_0000016", source_span="have expertise"),
                   _ent("Experience", "BFO_0000016", source_span="some persons have great experience")],
                  [])
    ld.stamp_provenance(p, claim)
    spans = {e.label: e.source_span for e in p.entities}
    assert spans["Person"] == "Persons can have expertise"        # paraphrase replaced
    assert spans["Expertise"] == "have expertise"                   # excerpt of the quote
    assert spans["Experience"] == "some persons have great experience"  # excerpt of the sentence
    assert getattr(p.entities[0], "source_locator", None) in (None, "")  # no legal locator


def test_stamp_provenance_unverified_claim_stores_no_span():
    claim = _claim(quote_verified=False, quote_grounding="unverified")
    p = _proposal([_ent("Person", "BFO_0000040", source_span="Persons are things.")], [])
    ld.stamp_provenance(p, claim)
    assert p.entities[0].source_span is None
