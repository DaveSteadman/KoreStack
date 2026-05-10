# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# Encryption helpers for sensitive values stored in the database.
#
# Uses Fernet symmetric encryption.  The key file lives alongside the KoreComms data
# directory and is git-ignored.  If the key file is absent it is generated on first use.
#
# Public API:
#   encrypt(plaintext: str) -> str   -- returns base64-encoded ciphertext
#   decrypt(ciphertext: str) -> str  -- returns original plaintext
#   _get_fernet()                    -- loads or generates the Fernet key (LRU-cached)
#
# Related modules:
#   - app/database.py              -- stores encrypted config_json values
#   - app/interfaces/discord/adapter.py  -- encrypts/decrypts bot tokens
#   - app/interfaces/gmail/adapter.py    -- encrypts/decrypts OAuth credentials
# ====================================================================================================
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet

from .config import cfg

_KEY_PATH = Path(cfg["data_dir"]) / "secret.key"
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _KEY_PATH.exists():
        key = _KEY_PATH.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        _KEY_PATH.write_bytes(key)
        # Restrict permissions on POSIX systems.
        try:
            os.chmod(_KEY_PATH, 0o600)
        except AttributeError:
            pass  # Windows — no-op
    _fernet = Fernet(key)
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt *plaintext* and return a base64 token string."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt` and return the plaintext."""
    return _get_fernet().decrypt(token.encode()).decode()
