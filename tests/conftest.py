"""Test setup: isolate the accounts DB and crypto key from any real data.

Must run before app.config is imported, so env is set at module import time
(pytest imports conftest before collecting test modules).
"""
import os
import tempfile

from cryptography.fernet import Fernet

# Throwaway SQLite file (absolute path overrides config's ROOT-relative default).
_db_fd, _db_path = tempfile.mkstemp(prefix="bfo_test_", suffix=".db")
os.close(_db_fd)
os.environ.setdefault("DB_PATH", _db_path)

# Deterministic-per-run crypto + session secrets so BYOK tests can run.
os.environ.setdefault("BYOK_ENCRYPTION_KEY", Fernet.generate_key().decode())
os.environ.setdefault("SECRET_KEY", "test-secret")
