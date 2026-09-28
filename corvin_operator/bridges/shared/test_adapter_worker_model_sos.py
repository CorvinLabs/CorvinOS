"""Test: Bridge reads Tenant YAML worker_model (Single Source of Truth).

Tests that _build_spawn_env reads worker_model from Console-configured Tenant YAML
and injects it as CORVIN_ACS_WORKER_MODEL for ACS spawn.

Updated 2026-09-28 (adversarial review round 5): ``_build_spawn_env`` no longer
takes ``engine_id=`` / ``channel=``. The engine is derived from the chat's
``/engine`` overlay (``engine_switch.env_overlay(bridge, chat_key)``, default
``claude_code``), so the tests pin the engine through that overlay — the same
path production takes — instead of a kwarg that does not exist.
"""
import sys
from pathlib import Path

import pytest
import yaml

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))


@pytest.fixture
def tenant_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    tenant_dir = tmp_path / "tenants" / "_default" / "global"
    tenant_dir.mkdir(parents=True, exist_ok=True)
    return tenant_dir


def _pin_engine(monkeypatch, engine_id):
    """Pin the chat's worker engine the way ``/engine`` does (overlay env)."""
    import engine_switch  # type: ignore

    overlay = {"CORVIN_DELEGATE_PREF_ENGINE": engine_id} if engine_id else {}
    monkeypatch.setattr(engine_switch, "env_overlay", lambda _b, _c: dict(overlay))


def _spawn_env(profile=None):
    from adapter import _build_spawn_env  # type: ignore

    return _build_spawn_env(
        bridge="console",
        chat_key="test-chat",
        base={},
        profile=profile,
        tenant_id="_default",
    )


def test_bridge_reads_tenant_yaml_worker_model_per_engine(tenant_home, monkeypatch):
    """Test: Bridge reads per-engine worker_model from Tenant YAML."""
    (tenant_home / "tenant.corvin.yaml").write_text(yaml.dump({
        "spec": {
            "engine_models": {
                "claude_code": {
                    "os_model": "claude-haiku-4-5-20251001",
                    "worker_model": "claude-opus-5",
                }
            }
        }
    }))
    _pin_engine(monkeypatch, None)  # no /engine pin → claude_code

    env = _spawn_env()

    assert env.get("CORVIN_ENGINE_ID") == "claude_code"
    assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-opus-5", \
        f"Expected 'claude-opus-5', got {env.get('CORVIN_ACS_WORKER_MODEL')}"


def test_bridge_reads_tenant_yaml_worker_model_global_fallback(tenant_home, monkeypatch):
    """Test: Bridge falls back to global default_worker_model when per-engine not set."""
    (tenant_home / "tenant.corvin.yaml").write_text(yaml.dump({
        "spec": {
            "engine_models": {"claude_code": {"worker_model": "claude-opus-5"}},
            "default_worker_model": "claude-sonnet-5",
        }
    }))
    _pin_engine(monkeypatch, "codex_cli")  # engine with no engine_models entry

    env = _spawn_env()

    assert env.get("CORVIN_ENGINE_ID") == "codex_cli"
    assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-sonnet-5", \
        f"Expected 'claude-sonnet-5', got {env.get('CORVIN_ACS_WORKER_MODEL')}"


def test_bridge_persona_priority_over_tenant_yaml(tenant_home, monkeypatch):
    """Test: Bridge prefers persona per-engine setting over Tenant YAML."""
    (tenant_home / "tenant.corvin.yaml").write_text(yaml.dump({
        "spec": {"engine_models": {"claude_code": {"worker_model": "claude-opus-5"}}}
    }))
    _pin_engine(monkeypatch, None)

    persona = {
        "engine_models": {
            "claude_code": {"worker_model": "claude-haiku-4-5-20251001"}
        }
    }
    env = _spawn_env(profile=persona)

    # Persona (Haiku) should win over Console (Opus 5)
    assert env.get("CORVIN_ACS_WORKER_MODEL") == "claude-haiku-4-5-20251001", \
        f"Expected Persona Haiku, got {env.get('CORVIN_ACS_WORKER_MODEL')}"


def test_provider_qualified_console_pin_is_normalised(tenant_home, monkeypatch):
    """The Console persists pins provider-qualified; the worker must never get
    ``anthropic/...`` (the API answers 404 model_not_found)."""
    (tenant_home / "tenant.corvin.yaml").write_text(yaml.dump({
        "spec": {"engine_models": {"claude_code": {"worker_model": "anthropic/claude-opus-5"}}}
    }))
    _pin_engine(monkeypatch, None)

    env = _spawn_env()

    wm = env.get("CORVIN_ACS_WORKER_MODEL", "")
    assert wm and "/" not in wm, wm


def test_no_worker_model_anywhere_pops_inherited_value(tenant_home, monkeypatch):
    """An empty resolution must not leak a stale parent value into the spawn."""
    _pin_engine(monkeypatch, None)
    from adapter import _build_spawn_env  # type: ignore

    env = _build_spawn_env(
        bridge="console", chat_key="test-chat",
        base={"CORVIN_ACS_WORKER_MODEL": "stale-model"},
        profile=None, tenant_id="_default",
    )
    assert "CORVIN_ACS_WORKER_MODEL" not in env


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
