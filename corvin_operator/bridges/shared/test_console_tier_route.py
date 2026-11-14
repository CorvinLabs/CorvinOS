"""A Console Models tier row naming OpenRouter/Ollama must route the turn there."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


def _all_tiers(model, provider):
    # The classifier may already be loaded by an earlier test, so any tier can be picked.
    return {t: {"selected_model": model, "provider": provider}
            for t in ("SIMPLE", "MEDIUM", "COMPLEX")}


def _home(tmp_path, monkeypatch, rows):
    g = tmp_path / "tenants" / "_default" / "global"
    g.mkdir(parents=True)
    (g / "model_selection_config.json").write_text(
        json.dumps({"tenant_id": "_default", "models": rows}))
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.delenv("CORVIN_OS_MODEL_OVERRIDE", raising=False)


def test_turn_route_returns_non_native_row(tmp_path, monkeypatch):
    import console_selection as cs
    _home(tmp_path, monkeypatch, _all_tiers("x/y:free", "openrouter"))
    assert cs.turn_route("hi", tenant_id="_default") == ("openrouter", "x/y:free")


def test_turn_route_ignores_native_row(tmp_path, monkeypatch):
    import console_selection as cs
    _home(tmp_path, monkeypatch, _all_tiers("claude-sonnet-5", "anthropic"))
    assert cs.turn_route("hi", tenant_id="_default") is None


def test_override_killswitch_wins(tmp_path, monkeypatch):
    import console_selection as cs
    _home(tmp_path, monkeypatch, _all_tiers("x/y", "openrouter"))
    monkeypatch.setenv("CORVIN_OS_MODEL_OVERRIDE", "claude-opus-5")
    assert cs.turn_route("hi", tenant_id="_default") is None


def test_apply_env_redirects_to_provider(tmp_path, monkeypatch):
    import engine_models as em
    _home(tmp_path, monkeypatch, _all_tiers("x/y:free", "openrouter"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    env: dict = {}
    assert em.apply_claude_code_provider_env(env, "_default", task_input="hi") is None
    assert env["CORVIN_CC_PROVIDER"] == "openrouter"
    assert env["ANTHROPIC_BASE_URL"].startswith("http://127.0.0.1")
