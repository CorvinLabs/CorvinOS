"""test_provider_keys.py — resolver precedence for provider_keys.py, the
single canonical BYOK/API-key resolution module (see its own module
docstring for the path-audit-class bug it consolidates).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import provider_keys as pk  # type: ignore


def test_resolve_by_env_var_known_canonical_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-known")
    assert pk.resolve_by_env_var("OPENROUTER_API_KEY") == "sk-or-known"


def test_resolve_by_env_var_unregistered_name_still_resolves_from_process_env(monkeypatch):
    """Regression (adversarial review, 2026-07-14): resolve_by_env_var used
    to return None outright for any env-var name not in the small
    CANONICAL_ENV_VAR set, even though the caller (ADR-0181 provider routing)
    passes whatever `credential_env` a provider declares in its own config —
    not limited to the hardcoded set. A provider with e.g. credential_env
    "MY_CUSTOM_PROVIDER_KEY" silently lost key resolution even though the
    env var was genuinely set in the process environment."""
    monkeypatch.delenv("MY_CUSTOM_PROVIDER_KEY", raising=False)
    assert pk.resolve_by_env_var("MY_CUSTOM_PROVIDER_KEY") is None
    monkeypatch.setenv("MY_CUSTOM_PROVIDER_KEY", "sk-custom-value")
    assert pk.resolve_by_env_var("MY_CUSTOM_PROVIDER_KEY") == "sk-custom-value"


def test_resolve_by_env_var_unregistered_name_falls_back_to_service_env(tmp_path, monkeypatch):
    """Same regression as above, but via the service.env file path (the
    other half of the env-then-service.env precedence chain that registered
    names already get through resolve_key)."""
    monkeypatch.delenv("MY_CUSTOM_PROVIDER_KEY", raising=False)
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "service.env").write_text("MY_CUSTOM_PROVIDER_KEY=sk-from-file\n", encoding="utf-8")
    assert pk.resolve_by_env_var("MY_CUSTOM_PROVIDER_KEY") == "sk-from-file"


def test_resolve_by_env_var_process_env_beats_service_env_for_unregistered_name(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "service.env").write_text("MY_CUSTOM_PROVIDER_KEY=sk-from-file\n", encoding="utf-8")
    monkeypatch.setenv("MY_CUSTOM_PROVIDER_KEY", "sk-from-env")
    assert pk.resolve_by_env_var("MY_CUSTOM_PROVIDER_KEY") == "sk-from-env"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
