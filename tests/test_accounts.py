"""Data-layer tests: DB bootstrap, BYOK crypto round-trip, usage metering,
and EngineContext key selection. No network / no LLM calls."""
from werkzeug.security import check_password_hash, generate_password_hash

from app import crypto
from app.db import init_db, session_scope
from app.engine_context import byok_context, owner_context
from app.models import Job, Usage, User


def setup_module(_module):
    init_db()


def _make_user(email):
    with session_scope() as s:
        u = User(email=email, password_hash=generate_password_hash("pw12345"))
        s.add(u)
        s.flush()
        return u.id


def test_password_hash_roundtrip():
    h = generate_password_hash("pw12345")
    assert check_password_hash(h, "pw12345")
    assert not check_password_hash(h, "wrong")


def test_byok_crypto_roundtrip_and_hint():
    secret = "sk-ant-abcd1234WXYZ"
    blob = crypto.encrypt_key(secret)
    assert blob != secret.encode()           # actually encrypted
    assert crypto.decrypt_key(blob) == secret
    assert crypto.key_hint(secret) == "WXYZ"


def test_user_unique_email():
    _make_user("dup@example.com")
    raised = False
    try:
        _make_user("dup@example.com")
    except Exception:
        raised = True
    assert raised


def test_usage_metering_records_rows():
    uid = _make_user("meter@example.com")
    ctx = owner_context(user_id=uid, job_id="job_test1")
    ctx.record_usage("propose", "claude-sonnet-4-6", 100, 50)
    ctx.record_usage("gate", "claude-sonnet-4-6", 10, 5)
    with session_scope() as s:
        rows = s.query(Usage).filter_by(job_id="job_test1").all()
        assert len(rows) == 2
        assert {r.phase for r in rows} == {"propose", "gate"}
        assert all(r.billed_to == "owner" for r in rows)
        assert sum(r.input_tokens for r in rows) == 110


def test_owner_vs_byok_context_key_selection():
    owner = owner_context()
    assert owner.billed_to == "owner"
    byok = byok_context("sk-ant-userkey", user_id=1, job_id="j")
    assert byok.billed_to == "byok"
    assert byok.api_key == "sk-ant-userkey"


def test_usage_hook_feeds_record_usage():
    uid = _make_user("hook@example.com")
    ctx = owner_context(user_id=uid, job_id="job_hook")
    hook = ctx.usage_hook("extract")
    hook("claude-haiku-4-5", 7, 3)   # mimics LLMProposer/_record_usage shape
    with session_scope() as s:
        row = s.query(Usage).filter_by(job_id="job_hook").one()
        assert row.phase == "extract"
        assert row.output_tokens == 3


def test_job_ownership_row():
    uid = _make_user("owner@example.com")
    with session_scope() as s:
        s.add(Job(job_id="job_own", user_id=uid, ontology_name="u1_job_own",
                  kind="byok", source_chars=1234, status="queued"))
    with session_scope() as s:
        j = s.get(Job, "job_own")
        assert j.user_id == uid
        assert j.kind == "byok"
        assert j.status == "queued"
