"""At-rest encryption for stored provider secrets (e.g. the Alpaca key/secret in market_data.json).

Secrets are encrypted with Fernet (AES-128-CBC + HMAC) so they are never written to disk in
plaintext. The encryption key comes from:

* the ``OFT_SECRET_KEY`` env var if set (operator-managed — survives a data-dir wipe and lets you
  share one key across machines), else
* a locally generated key file (``<data_dir>/.secret_key``), created once with owner-only
  permissions. The data dir is gitignored, so the key never lands in version control.

Encrypted values are stored with an ``enc:`` prefix, so any legacy plaintext value still reads back
(and is transparently re-encrypted the next time it is saved). A value that can't be decrypted
(wrong/rotated key) reads back as empty rather than crashing the app.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_ENC_PREFIX = "enc:"


def _key_file() -> Path:
    # Lazy import keeps this module free of an import cycle with config.
    from app.config import get_terminal_settings

    return get_terminal_settings().data_dir / ".secret_key"


@lru_cache
def _fernet() -> Fernet:
    """The process-wide Fernet, keyed from OFT_SECRET_KEY or a generated owner-only key file."""
    env = os.environ.get("OFT_SECRET_KEY", "").strip()
    if env:
        return Fernet(env.encode())
    p = _key_file()
    if p.exists():
        key = p.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(key)
        try:
            os.chmod(p, 0o600)  # owner-only (best-effort; Windows honors the read-only bit)
        except OSError:
            pass
    return Fernet(key)


def encrypt_secret(plaintext: str) -> str:
    """Return an ``enc:``-prefixed Fernet token for a non-empty secret (empty stays empty)."""
    plaintext = (plaintext or "").strip()
    if not plaintext:
        return ""
    return _ENC_PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(stored: str) -> str:
    """Decrypt a stored secret. Values without the ``enc:`` prefix are treated as legacy plaintext."""
    stored = stored or ""
    if not stored:
        return ""
    if not stored.startswith(_ENC_PREFIX):
        return stored  # legacy plaintext — re-encrypted on the next save
    try:
        return _fernet().decrypt(stored[len(_ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        return ""  # wrong/rotated key — treat as unset instead of crashing
