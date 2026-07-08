import json

import pytest

from app import config
from app.config import normalize_llm_base_url


def test_normalize_open_webui_root_to_openai_compatible_base():
    assert normalize_llm_base_url("http://localhost:3000") == "http://localhost:3000/openai/v1"


def test_normalize_keeps_explicit_openai_compatible_base():
    assert normalize_llm_base_url("http://localhost:3000/openai/v1") == "http://localhost:3000/openai/v1"
    assert normalize_llm_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"


# ── LLM provider override: key encrypted at rest ──────────────────────────────────
@pytest.fixture()
def clean_override():
    """Isolate llm_provider.json: blank it for the test, restore the original after."""
    path = config._llm_override_path()
    backup = path.read_bytes() if path.exists() else None
    path.unlink(missing_ok=True)
    config.get_engine_settings.cache_clear()
    try:
        yield path
    finally:
        if backup is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(backup)
        config.get_engine_settings.cache_clear()


def test_override_round_trip(clean_override):
    config.set_llm_override("https://api.openai.com/v1", "sk-123", "gpt-4o")
    ov = config.get_llm_override()
    assert ov["base_url"] == "https://api.openai.com/v1"
    assert ov["api_key"] == "sk-123" and ov["model"] == "gpt-4o"
    # the engine settings (consumed by every LLM caller) pick up the decrypted key
    config.get_engine_settings.cache_clear()
    eng = config.get_engine_settings()
    assert eng.llm_api_key == "sk-123"


def test_key_encrypted_at_rest(clean_override):
    config.set_llm_override("https://api.openai.com/v1", "sk-123", "gpt-4o")
    raw = clean_override.read_text("utf-8")
    # the plaintext key never appears on disk; the stored value is an enc:-prefixed token
    assert "sk-123" not in raw
    assert '"api_key": "enc:' in raw
    # …but it decrypts transparently on read
    assert config.get_llm_override()["api_key"] == "sk-123"


def test_blank_key_keeps_saved(clean_override):
    config.set_llm_override("https://api.openai.com/v1", "sk-123", "gpt-4o")
    # re-save same base_url with a blank key → previously saved key preserved (UI shows it masked)
    config.set_llm_override("https://api.openai.com/v1", "", "gpt-4o-mini")
    ov = config.get_llm_override()
    assert ov["api_key"] == "sk-123" and ov["model"] == "gpt-4o-mini"


def test_legacy_plaintext_key_reads_and_reencrypts(clean_override):
    # a pre-encryption file with a plaintext key still reads back…
    clean_override.write_text(
        json.dumps({"base_url": "https://api.openai.com/v1", "api_key": "plainkey", "model": "gpt-4o"}),
        "utf-8",
    )
    assert config.get_llm_override()["api_key"] == "plainkey"
    # …and the next save migrates it to an enc:-prefixed token on disk
    config.set_llm_override("https://api.openai.com/v1", "", "gpt-4o")
    raw = clean_override.read_text("utf-8")
    assert "plainkey" not in raw and '"api_key": "enc:' in raw
    assert config.get_llm_override()["api_key"] == "plainkey"

