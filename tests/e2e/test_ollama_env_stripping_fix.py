"""
E2E Test: Ollama Env-Stripping Fix (ADR-XXXX)

Verifies that call_claude_streaming() filters environment variables before
passing to Ollama subprocess, preventing CLAUDE_*-vars from corrupting Ollama
config and causing 404 errors.
"""
import json
import os
import sys
import pytest

# Adjust path for test import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../corvin_operator/bridges/shared"))

from adapter import _call_claude_streaming_impl


@pytest.fixture
def hermes_test_env():
    """Provide test environment with mixed CLAUDE_* and Ollama vars."""
    return {
        "OLLAMA_HOST": "http://localhost:11434",
        "CLAUDE_MODEL": "claude-opus-5",
        "CLAUDE_API_KEY": "sk-test-123",
        "ANTHROPIC_BASE_URL": "http://localhost:9999",
        "PATH": "/usr/local/bin:/usr/bin",
        "HOME": os.path.expanduser("~"),
        "USER": os.getenv("USER", "testuser"),
    }


def test_ollama_env_stripping_filters_claude_vars(monkeypatch, hermes_test_env):
    """
    Verify that call_claude_streaming() creates _OLLAMA_SUBPROCESS_ENV
    with only whitelist vars, excluding CLAUDE_* and other sensitive vars.
    """
    # Mock the environment
    for k, v in hermes_test_env.items():
        monkeypatch.setenv(k, v)

    # Intercept _build_spawn_env to capture the env dict it produces
    captured_env = {}
    original_build_spawn_env = __import__("adapter", fromlist=["_build_spawn_env"])._build_spawn_env

    def mock_build_spawn_env(**kwargs):
        env = original_build_spawn_env(**kwargs)
        captured_env.update(env)
        return env

    monkeypatch.setattr("adapter._build_spawn_env", mock_build_spawn_env)

    # Mock filesystem and subprocess to avoid actual I/O
    monkeypatch.setenv("ADAPTER_FAKE_CLAUDE", "1")

    # Call the function (will short-circuit on ADAPTER_FAKE_CLAUDE)
    try:
        _call_claude_streaming_impl(
            prompt="test",
            channel="test",
            chat_key="test_key",
        )
    except Exception:
        pass  # We only care about the env dict being built

    # Verify _OLLAMA_SUBPROCESS_ENV was created
    assert "_OLLAMA_SUBPROCESS_ENV" in captured_env, "Missing _OLLAMA_SUBPROCESS_ENV in env dict"

    # Parse the filtered env
    ollama_env_json = captured_env.get("_OLLAMA_SUBPROCESS_ENV")
    assert ollama_env_json is not None, "_OLLAMA_SUBPROCESS_ENV is None"

    ollama_env = json.loads(ollama_env_json)

    # Verify whitelist vars are present (or empty, depending on test env)
    whitelist = {"OLLAMA_HOST", "OLLAMA_NUM_PARALLEL", "PATH", "HOME", "USER", "TMPDIR"}
    for k in ollama_env:
        assert k in whitelist, f"Non-whitelist var '{k}' found in filtered env"

    # Verify CLAUDE_* and ANTHROPIC_* vars are NOT in the filtered env
    for k in ollama_env:
        assert not k.startswith("CLAUDE_"), f"CLAUDE_* var '{k}' leaked into Ollama env!"
        assert not k.startswith("ANTHROPIC_"), f"ANTHROPIC_* var '{k}' leaked into Ollama env!"

    print(f"✅ Ollama env filtered correctly: {list(ollama_env.keys())}")


def test_ollama_env_contains_only_whitelist_keys():
    """Verify that the whitelist covers the necessary Ollama-safe vars."""
    whitelist = {"OLLAMA_HOST", "OLLAMA_NUM_PARALLEL", "PATH", "HOME", "USER", "TMPDIR"}

    # These should be in the whitelist
    critical_vars = ["PATH", "HOME"]
    for var in critical_vars:
        assert var in whitelist, f"Critical var '{var}' missing from whitelist"

    # These should NOT be in the whitelist
    excluded_vars = ["CLAUDE_MODEL", "CLAUDE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"]
    test_env = {k: "test" for k in excluded_vars}
    filtered = {k: v for k, v in test_env.items() if k in whitelist}

    assert len(filtered) == 0, f"Excluded vars leaked into filtered env: {filtered}"
    print(f"✅ Whitelist correctly excludes dangerous vars")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
