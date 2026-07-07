"""Unit tests for job export/import (app/job_transfer.py).

Covers envelope construction, the export->import round-trip through the real
jobs.py store (claims land feed-ready), validation/rejection paths, and that
import provenance is recorded in meta.
"""
import pytest

from app import job_transfer, jobs


@pytest.fixture
def jobs_dir(tmp_path, monkeypatch):
    """Point the jobs store at a throwaway dir so tests never touch real jobs."""
    d = tmp_path / "jobs"
    d.mkdir()
    monkeypatch.setattr(jobs, "JOBS_DIR", d)
    return d


def _sample_job():
    """A job as it exists on disk, with mixed feed state to prove export drops it."""
    return {
        "job_id": "job_source0001",
        "name": "SOoL Ch.4",
        "session_id": "feed_sool_ch4_source0",
        "status": "feeding",
        "meta": {"source_file": "ch4.txt", "model": "claude-x", "chunk_chars": 8000},
        "claims": [
            {
                "id": 0,
                "claim": "A contract is a legal object.",
                "source_quote": "A contract ...",
                "confidence": "high",
                "note": "existence claim",
                "section": "Ch.4",
                "chunk_index": 2,
                "approved": True,
                "status": "committed",
                "proposal_id": "prop_abc",
                "verdict": "consistent",
                "updated_at": "2026-07-07T00:00:00+00:00",
            },
            {
                "id": 1,
                "claim": "A contract depends on the parties.",
                "source_quote": "... depends on ...",
                "confidence": "low",
                "note": "dependence claim",
                "section": "Ch.4",
                "chunk_index": 2,
                "approved": False,
                "status": "pending",
                "proposal_id": None,
                "verdict": None,
                "updated_at": "2026-07-07T00:00:00+00:00",
            },
        ],
    }


def test_build_export_envelope_shape_and_field_projection():
    env = job_transfer.build_export_envelope(_sample_job(), box="hetzner")

    assert env[job_transfer.FORMAT_MARKER] is True
    assert env["version"] == job_transfer.FORMAT_VERSION
    assert env["source"]["job_id"] == "job_source0001"
    assert env["source"]["box"] == "hetzner"
    assert env["source"]["model"] == "claude-x"
    assert env["job"]["name"] == "SOoL Ch.4"

    # ALL claims exported regardless of status.
    assert len(env["claims"]) == 2
    for c in env["claims"]:
        # Only the extraction-relevant fields; feed-state dropped.
        assert set(c.keys()) == set(job_transfer.EXPORT_CLAIM_FIELDS)
        assert "status" not in c and "proposal_id" not in c and "id" not in c
    # approved preserved verbatim.
    assert env["claims"][0]["approved"] is True
    assert env["claims"][1]["approved"] is False


def test_export_filename():
    assert job_transfer.export_filename({"name": "SOoL Ch.4"}) == "sool_ch_4_claims.json"


def test_round_trip_import_is_feedable(jobs_dir):
    env = job_transfer.build_export_envelope(_sample_job(), box="dgx")

    name, meta, claims = job_transfer.parse_import_envelope(env)
    job = jobs.create_job(name, meta=meta)
    job = jobs.append_claims(job["job_id"], claims)

    # Fresh job id, distinct from the source.
    assert job["job_id"] != "job_source0001"
    assert job["name"] == "SOoL Ch.4"

    got = job["claims"]
    assert [c["id"] for c in got] == [0, 1]  # ids reassigned sequentially
    for c in got:
        assert c["status"] == "pending"  # feed state reset
        assert c["proposal_id"] is None
        assert c["verdict"] is None
    # approved carried through: the high-confidence one stays approved.
    assert got[0]["approved"] is True
    assert got[1]["approved"] is False

    # The approved+pending claim is what the feeder would pick up.
    pending = jobs.next_pending(job["job_id"], limit=10)
    assert [c["id"] for c in pending] == [0]


def test_import_records_provenance(jobs_dir):
    env = job_transfer.build_export_envelope(_sample_job(), box="dgx")
    _, meta, _ = job_transfer.parse_import_envelope(env)

    assert meta["imported_from"] == "dgx"
    assert meta["source_session_id"] == "feed_sool_ch4_source0"
    assert meta["original_job_id"] == "job_source0001"
    assert meta["import_format_version"] == job_transfer.FORMAT_VERSION
    assert "imported_at" in meta
    # Original meta preserved (informational source model passes through).
    assert meta["model"] == "claude-x"
    assert meta["source_file"] == "ch4.txt"


@pytest.mark.parametrize(
    "payload",
    [
        "not a dict",
        {},  # missing marker
        {job_transfer.FORMAT_MARKER: True},  # missing version
        {job_transfer.FORMAT_MARKER: True, "version": job_transfer.FORMAT_VERSION + 1,
         "job": {"name": "x"}, "claims": [{"claim": "c"}]},  # too new
        {job_transfer.FORMAT_MARKER: True, "version": 1, "job": {"name": ""},
         "claims": [{"claim": "c"}]},  # empty name
        {job_transfer.FORMAT_MARKER: True, "version": 1, "job": {"name": "x"},
         "claims": []},  # no claims
        {job_transfer.FORMAT_MARKER: True, "version": 1, "job": {"name": "x"},
         "claims": "nope"},  # claims not a list
    ],
)
def test_parse_import_envelope_rejections(payload):
    with pytest.raises(ValueError):
        job_transfer.parse_import_envelope(payload)


def test_parse_import_envelope_rejects_oversized(monkeypatch):
    monkeypatch.setattr(job_transfer, "MAX_IMPORT_CLAIMS", 3)
    env = {
        job_transfer.FORMAT_MARKER: True,
        "version": 1,
        "job": {"name": "big"},
        "claims": [{"claim": f"c{i}"} for i in range(4)],
    }
    with pytest.raises(ValueError):
        job_transfer.parse_import_envelope(env)
