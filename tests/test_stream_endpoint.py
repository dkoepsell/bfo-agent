"""Smoke test for the read-only live SSE stream endpoint.

Builds the app via ``create_app()``, creates a real job, writes a couple of
synthetic events into that job's main + gate session logs (via the storage
path helpers so the streamer and loader agree on the location), then hits
``GET /jobs/<job_id>/stream`` with a bounded streaming read. Asserts the SSE
``hello`` frame plus at least one backlog ``data:`` event with the right shape.

The generator tails forever, so the read is capped to the backlog phase and
the response is closed explicitly -- the test must not hang.
"""
import json

import pytest

from app import jobs as jobs_store
from app import storage
from app.orchestrator import create_app


@pytest.fixture()
def app():
    return create_app()


def _write_events(session_id):
    main = storage.session_path(session_id)
    gate = storage.gate_log_path(session_id)
    main.parent.mkdir(parents=True, exist_ok=True)
    with main.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": "2026-07-07T00:00:00Z", "session_id": session_id,
            "event_type": "propose",
            "payload": {
                "proposal_id": "prop_test1", "claim_id": "c1", "job_id": "j",
                "proposal": {
                    "proposal_id": "prop_test1",
                    "utterance": "Vanessa is a person",
                    "entities": [{"label": "Vanessa", "kind": "individual",
                                  "bfo_type": "BFO_0000040", "parent_class": None}],
                    "relations": [],
                },
            },
        }) + "\n")
        f.write(json.dumps({
            "ts": "2026-07-07T00:00:02Z", "session_id": session_id,
            "event_type": "claim_timing",
            "payload": {"proposal_id": "prop_test1", "claim_id": "c1",
                        "verdict": "consistent", "committed": True,
                        "ms": {"propose": 5, "total": 42},
                        "stats": {"classes": 3, "individuals": 1}},
        }) + "\n")
    with gate.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "ts": "2026-07-07T00:00:01Z", "session_id": session_id,
            "proposal_id": "prop_test1", "tier": "construction",
            "outcome": "ACCEPT", "reason": None, "attempt": 0,
        }) + "\n")
    return main, gate


def test_stream_hello_and_backlog(app):
    job = jobs_store.create_job("stream-smoke-test")
    job_id = job["job_id"]
    session_id = job["session_id"]
    main_path, gate_path = _write_events(session_id)
    try:
        client = app.test_client()
        resp = client.get(f"/jobs/{job_id}/stream")
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"

        # Bounded read of the backlog phase: pull frames until we've seen
        # hello + a data event, or a small cap, then close so we never hang.
        it = resp.response  # streaming iterator over the generator
        chunks = []
        seen_hello = False
        seen_data = False
        for i, raw in enumerate(it):
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            chunks.append(text)
            if text.startswith("event: hello"):
                seen_hello = True
            if text.startswith("data: "):
                seen_data = True
            if (seen_hello and seen_data) or i > 20:
                break
        it.close()  # triggers GeneratorExit -> clean stop, no spin

        blob = "".join(chunks)
        assert seen_hello, f"no hello frame: {blob[:200]}"
        assert seen_data, f"no backlog data frame: {blob[:400]}"

        # At least one backlog data frame parses to the {"src", "event"} shape.
        found = False
        for line in blob.splitlines():
            if line.startswith("data: "):
                payload = line[len("data: "):]
                if not payload.strip():
                    continue
                obj = json.loads(payload)
                if "src" in obj and "event" in obj:
                    found = True
                    assert obj["src"] in ("main", "gate")
                    break
        assert found, f"no well-shaped backlog event: {blob[:400]}"
    finally:
        jobs_store.delete_job(job_id)
        main_path.unlink(missing_ok=True)
        gate_path.unlink(missing_ok=True)


def test_stream_unknown_job_404(app):
    client = app.test_client()
    resp = client.get("/jobs/job_does_not_exist/stream")
    assert resp.status_code == 404
    assert "error" in resp.get_json()
