# PyInstaller runtime hook — runs before run_desktop.py in the frozen .app.
#
# DISTRIBUTABLE build: bakes in NO API keys or shared secrets (this bundle is meant to be
# published). Instead:
#   * OFT_SECRET_KEY  — generated once per machine and persisted, so each install has its own
#                       key for encrypting saved credentials at rest (not a shared constant).
#   * LLM / broker keys — NOT baked in; the user configures them in the in-app Settings dialog.
import base64
import os
from pathlib import Path

_APPDIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OpenFinancialTerminal"

if "OFT_SECRET_KEY" not in os.environ:
    try:
        _APPDIR.mkdir(parents=True, exist_ok=True)
        _keyfile = _APPDIR / "secret.key"
        if _keyfile.is_file():
            _key = _keyfile.read_text().strip()
        else:
            _key = base64.urlsafe_b64encode(os.urandom(32)).decode()
            _keyfile.write_text(_key)
            try:
                _keyfile.chmod(0o600)
            except Exception:
                pass
        os.environ["OFT_SECRET_KEY"] = _key
    except Exception:
        # Fall back to an ephemeral key if the home dir isn't writable (encrypted settings
        # just won't persist across runs in that edge case).
        os.environ.setdefault("OFT_SECRET_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())

# SEC EDGAR fair-access User-Agent (users can override with SEC_USER_AGENT). MUST include a
# contact email — EDGAR 403s UAs without one, which broke every filings fetch in the frozen app.
os.environ.setdefault("SEC_USER_AGENT", "open-financial-terminal research@example.com")
