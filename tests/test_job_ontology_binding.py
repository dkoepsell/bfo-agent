"""Job-ontology binding: a job's claims can only ever be written into the
ontology the job belongs to.

Regression for 2026-07-13: feeding wrote into whichever ontology was ACTIVE,
so an ICD-11 job poured 536 commits into Tractatus. Jobs now record their
ontology at creation; feeding, resume and the remedy endpoint refuse to run a
job while a different ontology is active; activation is refused while any
feed is running; and legacy (unbound) jobs never feed until bound explicitly.
"""
import pytest

from app import jobs as jobs_store
from app import job_runner
from app import orchestrator
from tests.test_checkpoint_verify import FakeManager, FakeProposer


class MutableRegistry:
    def __init__(self, active="Tractatus"):
        self.active = active
        self.activations = []
        self.manifests = {"Tractatus": {}, "ICD": {}}

    def active_name(self):
        return self.active

    def activate(self, name):
        if name not in self.manifests:
            raise orchestrator.OntologyNotFoundError(name) \
                if hasattr(orchestrator, "OntologyNotFoundError") else KeyError(name)
        prev, self.active = self.active, name
        self.activations.append(name)
        return name, prev

    def fidelity(self, name=None):
        return self.manifests.get(name or self.active, {}).get(
            "fidelity", "curated")

    def manifest(self, name):
        return dict(self.manifests[name])

    def set_manifest_fields(self, name, **fields):
        self.manifests[name].update(fields)
        return dict(self.manifests[name])


@pytest.fixture
def env(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(jobs_store, "JOBS_DIR", jobs_dir)
    mgr = FakeManager(tmp_path)
    reg = MutableRegistry()
    events = []
    monkeypatch.setattr(orchestrator, "_get_manager", lambda: mgr)
    monkeypatch.setattr(orchestrator, "_get_registry", lambda: reg)
    monkeypatch.setattr(orchestrator, "_get_proposer", lambda: FakeProposer())
    monkeypatch.setattr(orchestrator, "_active_is_finalized", lambda: False)
    monkeypatch.setattr(orchestrator, "_active_fidelity", lambda: "curated")
    monkeypatch.setattr(orchestrator, "_apply_scaffolding", lambda p, m, s: [])
    monkeypatch.setattr(orchestrator, "_budget_and_kext_notes",
                        lambda p, m: [])
    monkeypatch.setattr(orchestrator, "git_commit_working_ontology",
                        lambda *a, **k: None)
    monkeypatch.setattr(orchestrator, "git_commit_library_change",
                        lambda *a, **k: None)
    monkeypatch.setattr(orchestrator.gate_mod, "scaffolding_directives",
                        lambda p, m: [])
    monkeypatch.setattr(orchestrator, "log_event",
                        lambda sid, kind, payload=None:
                        events.append((kind, payload)))
    monkeypatch.setattr(orchestrator.config, "ENABLE_COHERENCE_GATE", False)
    monkeypatch.setattr(orchestrator.config, "VERIFY_EVERY_COMMIT", False)
    monkeypatch.setattr(orchestrator.config, "FULL_VERIFY_EVERY_K", 100)
    return mgr, reg, events


def _job(ontology, n=2):
    job = jobs_store.create_job("j", ontology=ontology)
    jobs_store.append_claims(job["job_id"], [
        {"claim": f"claim {i}", "confidence": "high"} for i in range(n)])
    jobs_store.set_job_status(job["job_id"], "feeding")
    return job["job_id"]


def test_bound_job_feeds_its_own_ontology(env):
    mgr, reg, _ = env
    jid = _job("Tractatus")
    res = orchestrator._feed_one_core(jid)
    assert not res.get("fatal")
    assert len(mgr.commit_calls) == 1


def test_job_refuses_to_feed_after_active_ontology_switch(env):
    """The ICD-into-Tractatus scenario: an ICD job resumed while Tractatus
    is active must not write a single claim."""
    mgr, reg, events = env
    jid = _job("ICD")  # active is Tractatus
    res = orchestrator._feed_one_core(jid)
    assert res["fatal"] == "ontology_mismatch"
    assert mgr.commit_calls == []
    assert jobs_store.load_job(jid)["status"] == "paused"
    assert [k for k, _ in events if k == "feed_refused_wrong_ontology"]
    pend = [c for c in jobs_store.load_job(jid)["claims"]
            if c["status"] == "pending"]
    assert len(pend) == 2  # nothing consumed


def test_unbound_legacy_job_never_feeds(env):
    mgr, reg, _ = env
    jid = _job(None)
    res = orchestrator._feed_one_core(jid)
    assert res["fatal"] == "job_unbound"
    assert mgr.commit_calls == []


def test_switch_mid_run_stops_the_next_claim(env):
    mgr, reg, _ = env
    jid = _job("Tractatus", n=3)
    assert not orchestrator._feed_one_core(jid).get("fatal")
    reg.active = "ICD"  # e.g. activation from another tab
    assert orchestrator._feed_one_core(jid)["fatal"] == "ontology_mismatch"
    assert len(mgr.commit_calls) == 1


# ------------------------------------------------------------- routes

@pytest.fixture
def client(env, monkeypatch):
    monkeypatch.setattr(orchestrator.config, "AUTORESUME_JOBS", False)
    app = orchestrator.create_app()
    app.config["TESTING"] = True
    started = []
    monkeypatch.setattr(job_runner, "start",
                        lambda jid, *a, **k: started.append(jid) or {})
    with app.test_client() as c:
        yield c, env, started


def test_create_route_stamps_active_ontology(client):
    c, (mgr, reg, _), _ = client
    r = c.post("/jobs", json={"name": "new"})
    assert r.get_json()["ontology"] == "Tractatus"


def test_resume_wrong_ontology_is_409_with_health(client):
    c, (mgr, reg, _), started = client
    jid = _job("ICD")
    jobs_store.set_job_status(jid, "paused")
    r = c.post(f"/jobs/{jid}/resume", json={})
    assert r.status_code == 409
    body = r.get_json()
    assert body["health"]["state"] == "wrong_ontology"
    assert [m["id"] for m in body["health"]["remedies"]] == \
        ["activate_job_ontology"]
    assert started == []


def test_activation_refused_while_feed_running(client, monkeypatch):
    c, (mgr, reg, _), _ = client
    jid = _job("Tractatus")
    monkeypatch.setattr(job_runner, "running_job_ids", lambda: [jid])
    r = c.post("/ontologies/ICD/activate")
    assert r.status_code == 409
    assert r.get_json()["running_jobs"][0]["job_id"] == jid
    assert reg.active == "Tractatus"


def test_remedy_activate_job_ontology_then_resumes(client):
    c, (mgr, reg, _), started = client
    jid = _job("ICD")
    jobs_store.set_job_status(jid, "paused")
    r = c.post(f"/jobs/{jid}/remedy", json={"action": "activate_job_ontology"})
    assert r.status_code == 200
    assert reg.active == "ICD"
    assert started == [jid]


def test_remedy_bind_to_active_binds_and_resumes(client):
    c, (mgr, reg, _), started = client
    jid = _job(None)
    jobs_store.set_job_status(jid, "paused")
    r = c.post(f"/jobs/{jid}/remedy", json={"action": "bind_to_active"})
    assert r.status_code == 200
    assert jobs_store.load_job(jid)["ontology"] == "Tractatus"
    assert started == [jid]
    # binding is one-time: cannot be re-pointed via the remedy
    r = c.post(f"/jobs/{jid}/remedy", json={"action": "bind_to_active"})
    assert r.status_code == 409


def test_remedy_continue_faithful_targets_job_ontology(client):
    c, (mgr, reg, _), started = client
    jid = _job("Tractatus")
    jobs_store.set_job_status(jid, "paused")
    r = c.post(f"/jobs/{jid}/remedy", json={"action": "continue_faithful"})
    assert r.status_code == 200
    assert reg.manifests["Tractatus"]["fidelity"] == "faithful"
    assert reg.manifests["Tractatus"]["fidelity_switches"][0]["from"] == "curated"
    assert "fidelity" not in reg.manifests["ICD"]


def test_remedy_unknown_action_400(client):
    c, _, _ = client
    jid = _job("Tractatus")
    assert c.post(f"/jobs/{jid}/remedy", json={"action": "x"}).status_code == 400
