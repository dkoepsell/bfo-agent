"""Fernet encryption for BYOK Anthropic keys.

The Fernet secret comes from config.BYOK_ENCRYPTION_KEY (env only, never the
DB). Generate one once with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

and put it in .env as BYOK_ENCRYPTION_KEY.
"""
from __future__ import annotations

from cryptography.fernet import Fernet

from . import config


def _fernet() -> Fernet:
    if not config.BYOK_ENCRYPTION_KEY:
        raise RuntimeError(
            "BYOK_ENCRYPTION_KEY not set; cannot store/read BYOK keys. "
            "Generate one with cryptography.fernet.Fernet.generate_key()."
        )
    return Fernet(config.BYOK_ENCRYPTION_KEY.encode())


def encrypt_key(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode())


def decrypt_key(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode()


def key_hint(plaintext: str) -> str:
    """Last 4 chars for non-secret display, e.g. '…aA9z'."""
    return plaintext[-4:] if len(plaintext) >= 4 else "????"
