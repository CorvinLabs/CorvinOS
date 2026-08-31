"""GET /byok/secrets/{key_name}/value — reveal a previously-saved BYOK key.

Explicit user request (2026-07-14): the "eye" button on Settings -> API Keys
only ever toggled visibility of a value being typed; it never showed an
already-saved key, because list_secrets() deliberately returns presence only,
never plaintext (ADR-0047's original write-only stance). This endpoint is the
one intentional exception: the authenticated owner of a self-hosted instance
can read back their own saved key (same trust model as a password manager
letting its owner view a saved secret). Hosted mode explicitly does not
support this yet and must return 501, not silently proxy or guess.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import byok as B


class _FakeRec:
    tenant_id = "_default"
    sid_fingerprint = "abcd1234"


def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_HOSTED_MODE", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def test_reveals_a_well_known_key_saved_via_service_env(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    voice_dir = tmp_path / "corvin-voice"
    voice_dir.mkdir(parents=True)
    (voice_dir / "service.env").write_text(
        "ANTHROPIC_API_KEY=sk-ant-real-saved-value\n", encoding="utf-8",
    )

    result = B.get_secret_value("anthropic_api_key", rec=_FakeRec())
    assert result["value"] == "sk-ant-real-saved-value"
    assert result["key_name"] == "anthropic_api_key"


def test_reveals_a_custom_key_from_the_vault(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "operator" / "bridges" / "shared"))
    import vault as _vault_mod  # type: ignore
    _vault_mod.set_item("custom_stripe_key", "sk_live_stripe_secret", tags=["byok"])

    result = B.get_secret_value("custom_stripe_key", rec=_FakeRec())
    assert result["value"] == "sk_live_stripe_secret"


def test_missing_key_returns_404(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        B.get_secret_value("anthropic_api_key", rec=_FakeRec())
    assert exc_info.value.status_code == 404


def test_hosted_mode_returns_501_not_a_silent_proxy(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    monkeypatch.setenv("CORVIN_HOSTED_MODE", "true")
    with pytest.raises(HTTPException) as exc_info:
        B.get_secret_value("anthropic_api_key", rec=_FakeRec())
    assert exc_info.value.status_code == 501


def test_invalid_key_name_returns_400(monkeypatch, tmp_path):
    _isolated(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        B.get_secret_value("../../etc/passwd", rec=_FakeRec())
    assert exc_info.value.status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
