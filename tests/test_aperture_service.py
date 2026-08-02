"""The HTTP surface, end to end through a real Flask test client.

Covers the profile boundary, the two refusals a caller can hit, and the
hash-chained disclosure log.
"""
from __future__ import annotations

import json

import pytest

from app.aperture import audit as audit_log
from app.aperture import authz
from app.aperture.redaction import find_leaks

ONTOLOGY = "NFIP_SFIP_v1"

FULL_LINKS = {
    "authority": "external",
    "criteria": "performed",
    "assessor": "external",
    "facts": "external",
    "act": "external",
    "effect": "performed",
    "remedy": "external",
}
BASIS = "44 CFR 61 NFIP, appendix A, and the entry file annotations"


@pytest.fixture(scope="module")
def client():
    from app.orchestrator import create_app

    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def isolated_audit_log(tmp_path, monkeypatch):
    monkeypatch.setenv("APERTURE_OUT", str(tmp_path / "out"))
    yield


@pytest.fixture
def restore_manifest():
    """Tests that declare a chain write to the real library manifest.

    Restore it afterwards. A test suite that leaves a chain declaration behind
    would also leave the next run resolving against a declaration nobody made.
    """
    from pathlib import Path

    from app import config

    path = Path(config.LIBRARY_ROOT) / ONTOLOGY / "manifest.json"
    before = path.read_text() if path.exists() else None
    try:
        yield
    finally:
        if before is not None:
            path.write_text(before)
            # Drop the in-process cache so a later test sees the restored file.
            try:
                from app.orchestrator import _get_registry

                registry = _get_registry()
                registry._manifests[ONTOLOGY] = json.loads(before)
            except Exception:
                pass


def available(client) -> bool:
    return client.get(f"/ontologies/{ONTOLOGY}/aperture/preconditions").status_code == 200


# --------------------------------------------------------------------------
# The manifest endpoint honours the profile
# --------------------------------------------------------------------------

def test_manifest_internal_carries_identifiers(client):
    body = client.get("/aperture/manifest").get_json()
    assert body["profile"] == "internal"
    assert len(body["failures"]) == 12
    assert body["digest"].startswith("sha256:")
    assert any(f["id"] == "K-A1" for f in body["failures"])


def test_manifest_client_carries_labels_only(client):
    body = client.get("/aperture/manifest?profile=client").get_json()
    assert body["profile"] == "client"
    assert "failures" not in body and "digest" not in body and "version" not in body
    assert len(body["defect_classes"]) == 12
    assert find_leaks(json.dumps(body)) == []


def test_an_unknown_profile_is_refused_not_downgraded(client):
    res = client.get("/aperture/manifest?profile=public")
    assert res.status_code == 403
    assert "unknown profile" in res.get_json()["error"]


# --------------------------------------------------------------------------
# Profile resolution narrows and never widens
# --------------------------------------------------------------------------

def test_a_request_may_narrow_to_client():
    assert authz.resolve_profile("client") == authz.CLIENT


def test_a_request_cannot_widen_a_client_default(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "APERTURE_DEFAULT_PROFILE", "client")
    with pytest.raises(authz.ProfileRefused):
        authz.resolve_profile("internal")


def test_a_client_role_pins_the_profile():
    assert authz.resolve_profile(None, caller_role="client") == authz.CLIENT
    assert authz.resolve_profile("client", caller_role="client") == authz.CLIENT


def test_a_client_role_cannot_ask_its_way_up():
    """The role wins, and the request is refused rather than quietly honoured."""
    with pytest.raises(authz.ProfileRefused):
        authz.resolve_profile("internal", caller_role="client")


def test_an_unrecognised_default_falls_back_to_internal(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "APERTURE_DEFAULT_PROFILE", "nonsense")
    assert authz.default_profile() == authz.INTERNAL


# --------------------------------------------------------------------------
# The phases, over a real ontology in the library
# --------------------------------------------------------------------------

def test_preconditions_report_all_eight(client):
    if not available(client):
        pytest.skip(f"{ONTOLOGY} is not in the library")
    body = client.get(f"/ontologies/{ONTOLOGY}/aperture/preconditions").get_json()
    assert len(body["preconditions"]) == 8
    assert body["order"][0] == "formal_theory"
    for pid, blob in body["preconditions"].items():
        assert blob["evidence"], pid


def test_an_unknown_ontology_is_a_404(client):
    assert client.get("/ontologies/no-such-thing/aperture/preconditions").status_code == 404


def test_resolve_refuses_an_undeclared_chain(client):
    if not available(client):
        pytest.skip(f"{ONTOLOGY} is not in the library")
    res = client.post(f"/ontologies/{ONTOLOGY}/aperture/resolve")
    if res.status_code == 200:
        pytest.skip("this ontology already carries a chain declaration")
    assert res.status_code == 409
    assert res.get_json()["reason"] in ("incomplete", "thickness_contradiction")


def test_a_chain_without_a_basis_is_refused(client, restore_manifest):
    if not available(client):
        pytest.skip(f"{ONTOLOGY} is not in the library")
    res = client.put(f"/ontologies/{ONTOLOGY}/aperture/chain",
                     json={"links": FULL_LINKS, "basis": ""})
    assert res.status_code == 409
    assert res.get_json()["reason"] == "incomplete"


def test_a_partial_chain_is_refused(client, restore_manifest):
    if not available(client):
        pytest.skip(f"{ONTOLOGY} is not in the library")
    res = client.put(f"/ontologies/{ONTOLOGY}/aperture/chain",
                     json={"links": {"act": "external"}, "basis": BASIS})
    assert res.status_code == 409
    assert res.get_json()["reason"] == "incomplete"


def test_declare_then_resolve_then_render(client, restore_manifest):
    if not available(client):
        pytest.skip(f"{ONTOLOGY} is not in the library")

    put = client.put(f"/ontologies/{ONTOLOGY}/aperture/chain",
                     json={"links": FULL_LINKS, "basis": BASIS,
                           "declared_by": "test", "declared_on": "2026-08-02"})
    if put.status_code == 409:
        pytest.skip(f"chain contradicts the stored profile: {put.get_json()}")
    assert put.status_code == 200
    assert put.get_json()["usable"] is True

    internal = client.post(f"/ontologies/{ONTOLOGY}/aperture/resolve").get_json()
    assert internal["profile"] == "internal"
    assert len(internal["rows"]) == 12
    assert "CLEAN" not in {r["verdict"] for r in internal["rows"]}

    clientside = client.post(
        f"/ontologies/{ONTOLOGY}/aperture/resolve?profile=client").get_json()
    assert clientside["profile"] == "client"
    assert find_leaks(json.dumps(clientside)) == []

    scope = client.get(f"/ontologies/{ONTOLOGY}/aperture/scope?profile=client").get_json()
    assert scope["profile"] == "client"
    assert find_leaks(scope["markdown"]) == []
    assert "Scope of review" in scope["markdown"]

    internal_scope = client.get(
        f"/ontologies/{ONTOLOGY}/aperture/scope").get_json()
    assert "K-" in internal_scope["markdown"]


# --------------------------------------------------------------------------
# The disclosure log
# --------------------------------------------------------------------------

def test_an_empty_log_verifies(client):
    assert client.get("/aperture/audit/verify").get_json()["ok"] is True


def test_records_chain_and_verify(tmp_path, monkeypatch):
    monkeypatch.setenv("APERTURE_OUT", str(tmp_path / "out"))
    for n in range(3):
        audit_log.record(profile="client", engagement="e",
                         artifact_sha256="a" * 64, chain_sha256="b" * 64,
                         manifest_version="1.0.0", output=f"body {n}")
    result = audit_log.verify()
    assert result.ok is True
    assert result.lines == 3


def test_a_deleted_line_is_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("APERTURE_OUT", str(tmp_path / "out"))
    for n in range(4):
        audit_log.record(profile="internal", engagement="e",
                         artifact_sha256="a" * 64, chain_sha256="b" * 64,
                         manifest_version="1.0.0", output=f"body {n}")
    path = next((tmp_path / "out" / "audit").glob("*.jsonl"))
    lines = path.read_text().splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n")

    result = audit_log.verify()
    assert result.ok is False
    assert result.first_break["line"] == 2
    assert "chain broken" in result.first_break["problem"]


def test_an_altered_line_is_detected(tmp_path, monkeypatch):
    monkeypatch.setenv("APERTURE_OUT", str(tmp_path / "out"))
    for n in range(3):
        audit_log.record(profile="client", engagement="e",
                         artifact_sha256="a" * 64, chain_sha256="b" * 64,
                         manifest_version="1.0.0", output=f"body {n}")
    path = next((tmp_path / "out" / "audit").glob("*.jsonl"))
    lines = path.read_text().splitlines()
    entry = json.loads(lines[0])
    entry["profile"] = "internal"
    lines[0] = json.dumps(entry, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")

    assert audit_log.verify().ok is False


def test_the_log_records_what_was_disclosed(tmp_path, monkeypatch):
    monkeypatch.setenv("APERTURE_OUT", str(tmp_path / "out"))
    entry = audit_log.record(profile="client", engagement="example-engagement",
                             artifact_sha256="a" * 64, chain_sha256="b" * 64,
                             manifest_version="1.0.0", output="the table")
    assert entry["profile"] == "client"
    assert entry["engagement"] == "example-engagement"
    assert entry["output_sha256"].startswith("sha256:")
    assert entry["prev"] == audit_log.GENESIS


def test_a_write_failure_does_not_break_the_render(tmp_path, monkeypatch):
    """A disclosure that happened must not be undone by a logging problem."""
    monkeypatch.setenv("APERTURE_OUT", "/proc/definitely-not-writable")
    entry = audit_log.record(profile="client", engagement="e",
                             artifact_sha256="a" * 64, chain_sha256="b" * 64,
                             manifest_version="1.0.0", output="x")
    assert entry["profile"] == "client"
