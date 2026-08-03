"""The norm extractor has to be reachable from the path the UI actually drives.

Everything in chainlab is useless to a user if `/extract/chunk` still runs only
the entity-relation extractor: FRCP returns 0 claims, forever, and that reads as
a broken pipeline rather than a schema mismatch. These tests pin the wiring.

No LLM calls — both extractors are stubbed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app import orchestrator  # noqa: E402
from app.orchestrator import create_app  # noqa: E402
from chainlab.model import Modality, NormTuple  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    app = create_app()
    app.config["TESTING"] = True

    class StubClaims:
        calls = 0

        def extract_chunk(self, chunk, section=""):
            StubClaims.calls += 1
            return []           # FRCP's actual behaviour: no ontological claims

    class StubNorms:
        calls = 0

        def extract_chunk(self, chunk, corpus_id, chunk_index=0, default_locator=""):
            StubNorms.calls += 1
            return [NormTuple(
                corpus_id=corpus_id, source_locator="Rule 4(b)", chunk_index=chunk_index,
                bearer="the clerk", bearer_role="clerk", modality=Modality.DUTY,
                action="sign, seal, and issue the summons",
                chain_link="recognition_act", confidence="high",
            )]

    StubClaims.calls = 0
    StubNorms.calls = 0
    monkeypatch.setattr(orchestrator, "_get_extractor", lambda: StubClaims())
    monkeypatch.setattr(orchestrator, "_get_norm_extractor", lambda: StubNorms())
    with app.test_client() as c:
        c.stub_claims = StubClaims
        c.stub_norms = StubNorms
        yield c


def test_frcp_chunk_runs_both_extractors(client):
    r = client.post("/extract/chunk", json={
        "chunk": "The clerk must sign, seal, and issue it.", "corpus_id": "frcp"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["extractors"] == ["entity_relation", "norm"]
    assert body["claims"] == []
    assert len(body["norm_tuples"]) == 1
    assert body["norm_tuples"][0]["modality"] == "duty"
    assert client.stub_claims.calls == 1
    assert client.stub_norms.calls == 1


def test_no_corpus_id_keeps_the_historical_behaviour(client):
    """An existing caller that never heard of corpora must not start paying for
    a second model call."""
    r = client.post("/extract/chunk", json={"chunk": "Some philosophy text."})
    assert r.status_code == 200
    body = r.get_json()
    assert body["extractors"] == ["entity_relation"]
    assert body["norm_tuples"] == []
    assert client.stub_norms.calls == 0


def test_icd11_does_not_get_the_norm_extractor(client):
    r = client.post("/extract/chunk", json={"chunk": "Diabetes is a disorder.",
                                            "corpus_id": "icd11"})
    assert r.get_json()["norm_tuples"] == []
    assert client.stub_norms.calls == 0


def test_prepare_reports_the_route_and_the_trim(client):
    text = ("COMMITTEE ON RULES\nHon. Someone\n\nFOREWORD\nBlah.\n\n"
            "RULES OF CIVIL PROCEDURE\nFOR THE\nUNITED STATES DISTRICT COURTS\n\n"
            "Rule 1. Scope.\nThese rules govern the procedure.\n")
    r = client.post("/extract/prepare", json={"text": text, "corpus_id": "frcp"})
    body = r.get_json()
    assert body["extractors"] == ["entity_relation", "norm"]
    assert body["retained_span"]["dropped_chars"] > 0
    assert body["total_chars"] == len(text)
    assert "FOREWORD" not in "".join(body["chunks"])
    assert "Rule 1. Scope." in "".join(body["chunks"])


def test_prepare_without_corpus_id_does_not_trim(client):
    text = "COMMITTEE ON RULES\n\nFOREWORD\nSome text that must survive.\n"
    body = client.post("/extract/prepare", json={"text": text}).get_json()
    assert body["retained_span"] is None
    assert "FOREWORD" in "".join(body["chunks"])


def test_kd3_endpoint_runs_over_the_whole_corpus(client):
    """A capacity in Rule 60(b) with its pathway in Rule 60(c) must come out
    pathway_found — which only works if the detector sees every chunk at once."""
    tuples = [
        {"corpus_id": "frcp", "source_locator": "Rule 60(b)", "chunk_index": 0,
         "bearer": "the court", "bearer_role": "court", "modality": "power",
         "action": "relieve a party from a final judgment", "chain_link": "remedy",
         "confidence": "high"},
        {"corpus_id": "frcp", "source_locator": "Rule 60(c)(1)", "chunk_index": 7,
         "bearer": "a party", "bearer_role": "party", "modality": "duty",
         "action": "make a motion for relief from a final judgment",
         "counterparty": "the court", "deadline": "no more than a year after entry",
         "chain_link": "remedy", "confidence": "high"},
    ]
    r = client.post("/extract/kd3", json={"norm_tuples": tuples, "corpus_id": "frcp"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["summary"]["no_pathway"] == 0
    assert body["findings"] == []
    assert body["run_id"]


def test_extraction_runs_against_a_finalized_active_ontology(client, monkeypatch):
    """Reading a corpus is not a write. Every ontology in the library can be
    finalized and you must still be able to extract from a new source — which is
    the normal state of affairs, since finalizing is the point of the workflow."""
    monkeypatch.setattr(orchestrator, "_active_is_finalized", lambda: True)

    prep = client.post("/extract/prepare", json={"text": "Rule 1. Scope.\nThese rules govern."})
    assert prep.status_code == 200

    chunk = client.post("/extract/chunk", json={"chunk": "The clerk must sign it.",
                                                "corpus_id": "frcp"})
    assert chunk.status_code == 200
    assert len(chunk.get_json()["norm_tuples"]) == 1


def test_writes_are_still_refused_against_a_finalized_ontology(client, monkeypatch):
    """The guard belongs on the write, and must stay there."""
    monkeypatch.setattr(orchestrator, "_active_is_finalized", lambda: True)
    r = client.post("/propose", json={"text": "anything"})
    assert r.status_code in (409, 423), r.status_code
    assert "finalized" in r.get_data(as_text=True).lower()


def test_kd3_endpoint_rejects_an_empty_run(client):
    assert client.post("/extract/kd3", json={"corpus_id": "frcp"}).status_code == 400


def test_kd3_endpoint_rejects_malformed_tuples(client):
    r = client.post("/extract/kd3", json={"corpus_id": "frcp",
                                          "norm_tuples": [{"bearer": "x"}]})
    assert r.status_code == 400
