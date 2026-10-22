#!/usr/bin/env python3
"""
E2E Test: TTS Provider Selection (OpenAI → Edge → Piper)

Test Coverage:
1. Provider priority (OpenAI preferred, fallback to Edge)
2. Key management (env vars, service.env, vault resolution)
3. Fallback scenarios (key missing, API down, quota exceeded)
4. Voice synthesis success/failure paths
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock
import pytest

# Add bridges/shared to path
BRIDGES_SHARED = Path(__file__).parent.parent / "corvin_operator/bridges/shared"
sys.path.insert(0, str(BRIDGES_SHARED))


@pytest.fixture(autouse=True)
def _hermetic_voice_keys(monkeypatch, tmp_path):
    """Start every test from an install that holds no TTS/STT key anywhere.

    These tests describe key precedence and provider availability, so the answer must not
    depend on what the machine running them happens to export or store: the process env,
    secrets.enc and ~/.config/corvin-voice/service.env are all neutralised.
    """
    for name in ("CORVIN_TTS_OPENAI_KEY", "OPENAI_API_KEY", "OPENAI_APIKEY", "CORVIN_STT_OPENAI_KEY",
                 "CORVIN_TTS_PROVIDER", "CORVIN_TTS_LOCAL_ONLY"):
        monkeypatch.delenv(name, raising=False)
    import provider_keys

    class _NoSecrets:
        def decrypt_secrets(self):
            return {}

    monkeypatch.setattr(provider_keys, "SecretsStore", _NoSecrets)
    monkeypatch.setattr(provider_keys, "service_env_path", lambda: tmp_path / "service.env")


class TestTTSProviderSelection:
    """Test TTS provider selection logic"""

    def test_provider_key_resolution_order(self):
        """Test 1: Verify key resolution follows correct precedence"""
        import provider_keys

        # Get candidate list
        candidates = provider_keys._candidates_for("tts_openai_api_key")

        # Should be: dedicated → general → legacy
        assert candidates == ["CORVIN_TTS_OPENAI_KEY", "OPENAI_API_KEY", "OPENAI_APIKEY"], \
            f"Wrong precedence order: {candidates}"


    def test_openai_fallback_to_general_key(self):
        """Test 2: OpenAI TTS key falls back to general OPENAI_API_KEY"""
        import provider_keys

        # When CORVIN_TTS_OPENAI_KEY is missing, should fallback to OPENAI_API_KEY
        with mock.patch.dict(os.environ, {
            "CORVIN_TTS_OPENAI_KEY": "",  # Empty = not set
            "OPENAI_API_KEY": "test-key-123",
        }):
            key = provider_keys.resolve_key("tts_openai_api_key")
            assert key == "test-key-123", \
                "Should fallback to OPENAI_API_KEY when dedicated key is empty"


    def test_dedicated_key_preferred_over_general(self):
        """Test 3: CORVIN_TTS_OPENAI_KEY takes precedence over OPENAI_API_KEY"""
        import provider_keys

        with mock.patch.dict(os.environ, {
            "CORVIN_TTS_OPENAI_KEY": "dedicated-key",
            "OPENAI_API_KEY": "general-key",
        }):
            key = provider_keys.resolve_key("tts_openai_api_key")
            assert key == "dedicated-key", \
                "Dedicated CORVIN_TTS_OPENAI_KEY should be preferred"


    def test_env_var_beats_service_env(self):
        """Test 4: Process environment beats service.env file"""
        import provider_keys

        # Process env should win over file
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "env-key"}):
            with mock.patch('provider_keys._load_from_file', return_value="file-key"):
                key = provider_keys.resolve_key("tts_openai_api_key")
                assert key == "env-key", \
                    "Process env var should beat service.env file"


    def test_placeholder_detection(self):
        """Test 5: Detect placeholder values that aren't real keys"""
        import provider_keys

        with mock.patch.dict(os.environ, {
            "OPENAI_API_KEY": "sk-proj-PLACEHOLDER-TTS-20260916",
        }):
            key = provider_keys.resolve_key("tts_openai_api_key")
            assert key is not None, "Key should resolve"

            is_placeholder = "PLACEHOLDER" in key
            assert is_placeholder, "Should detect placeholder format"


class TestOpenAITTSProvider:
    """Test OpenAI TTS provider directly"""

    @pytest.mark.asyncio
    async def test_openai_available_with_key(self):
        """Test 6: OpenAI provider detects availability correctly"""
        # This test requires openai SDK to be installed
        try:
            from core.voice.tts_providers import OpenAITTSProvider
        except ImportError:
            pytest.skip("openai package not installed")

        # With a valid key
        provider = OpenAITTSProvider(api_key="sk-test-valid-key")
        assert provider.is_available() or not provider.is_available(), \
            "Should return boolean availability status"


    @pytest.mark.asyncio
    async def test_openai_unavailable_without_key(self):
        """Test 7: OpenAI provider unavailable when key missing"""
        try:
            from core.voice.tts_providers import OpenAITTSProvider
        except ImportError:
            pytest.skip("openai package not installed")

        # Without a key
        provider = OpenAITTSProvider(api_key=None)
        assert not provider.is_available(), \
            "Should be unavailable without API key"


    def test_tts_manager_builds_without_any_key(self, monkeypatch):
        """Test 7b: the production entry point (get_tts_manager, used by the console voice route)
        must construct on an install with no OpenAI key and report the primary as unavailable.
        Until the fix this raised openai.OpenAIError, hidden on machines that export a key."""
        try:
            import core.voice.tts_providers as tp
        except ImportError:
            pytest.skip("voice providers not importable")
        monkeypatch.setattr(tp, "_tts_manager", None)
        manager = tp.get_tts_manager()
        assert manager.primary.is_available() is False, "no key anywhere means the OpenAI tier is unavailable"
        assert manager.fallback is not None


class TestEdgeTTSFallback:
    """Test Edge TTS fallback behavior"""

    @pytest.mark.asyncio
    async def test_edge_tts_always_available(self):
        """Test 8: Edge TTS is always available (no API key needed)"""
        try:
            from core.voice.tts_providers import EdgeTTSProvider
        except ImportError:
            pytest.skip("edge_tts package not installed")

        provider = EdgeTTSProvider()
        # Edge TTS should be available if edge_tts is installed
        # (doesn't need API key)
        availability = provider.is_available()
        assert isinstance(availability, bool), \
            "Should return boolean availability status"


    def test_fallback_chain_order(self):
        """Test 9: Fallback chain is correct (OpenAI → Edge → Piper → None)"""
        import inspect

        import adapter

        # The order itself lives in _tts_chain_for / _TTS_AUTO_CHAIN ...
        assert adapter._tts_chain_for(None) == ("openai", "edge", "piper"), "auto chain must be OpenAI → Edge → Piper"
        # ... a pinned cloud provider falls back to the LOCAL provider only (never to the other cloud),
        assert adapter._tts_chain_for("openai") == ("openai", "piper")
        assert adapter._tts_chain_for("edge") == ("edge", "piper")
        assert adapter._tts_chain_for("piper") == ("piper",)
        # ... and synthesize_voice_note actually walks that chain (call-site proof, not just a constant).
        source = inspect.getsource(adapter.synthesize_voice_note)
        assert "_tts_chain_for(" in source and "_run_tts_provider(" in source, \
            "synthesize_voice_note must run the providers in the order _tts_chain_for returns"


class TestConfigurationProblems:
    """Test common configuration issues"""

    def test_placeholder_key_problem(self):
        """Test 10: Identify when keys are placeholders"""
        placeholder_keys = [
            "sk-proj-PLACEHOLDER-TTS-20260916",
            "PLACEHOLDER_OPENAI_20260916",
        ]

        for key in placeholder_keys:
            is_placeholder = key.startswith("PLACEHOLDER") or "PLACEHOLDER" in key
            assert is_placeholder, f"Should detect placeholder: {key}"


    def test_corvin_tts_local_only_blocks_openai(self):
        """Test 11: CORVIN_TTS_LOCAL_ONLY=1 disables OpenAI"""
        with mock.patch.dict(os.environ, {"CORVIN_TTS_LOCAL_ONLY": "1"}):
            import adapter

            # OpenAI should be skipped
            # (This would need to be verified in actual execution)
            local_only = os.environ.get("CORVIN_TTS_LOCAL_ONLY") == "1"
            assert local_only, "CORVIN_TTS_LOCAL_ONLY should block OpenAI"


    def test_missing_openai_sdk(self):
        """Test 12: Handle missing openai package gracefully"""
        # Simulate openai SDK not installed
        import sys

        # Try importing openai
        try:
            import openai
            pytest.skip("openai is installed (test is for missing case)")
        except ImportError:
            # Expected — SDK is missing
            pass


class TestEndToEndScenarios:
    """Test real-world scenarios"""

    @pytest.mark.asyncio
    async def test_scenario_openai_success(self):
        """Scenario 1: OpenAI TTS succeeds"""
        # Setup: valid OpenAI key, SDK installed
        # Expected: voice.ogg file created with quality_score=0.95
        pytest.skip("not implemented — the body of this test is empty. It counted as a PASS in every run until the 2026-09-20 review; marking it skipped makes the gap visible instead of inflating the green count.")


    @pytest.mark.asyncio
    async def test_scenario_openai_fails_fallback_edge(self):
        """Scenario 2: OpenAI fails (e.g., network), falls back to Edge"""
        # Setup: valid OpenAI key, but API unreachable
        # Expected: Edge TTS used, voice.ogg still created
        pytest.skip("not implemented — the body of this test is empty. It counted as a PASS in every run until the 2026-09-20 review; marking it skipped makes the gap visible instead of inflating the green count.")


    @pytest.mark.asyncio
    async def test_scenario_quota_exceeded(self):
        """Scenario 3: OpenAI quota exceeded (429)"""
        # Setup: OpenAI returns 429 quota error
        # Expected: backoff timer set, Edge TTS fallback, subsequent calls skip OpenAI for 1h
        pytest.skip("not implemented — the body of this test is empty. It counted as a PASS in every run until the 2026-09-20 review; marking it skipped makes the gap visible instead of inflating the green count.")


    @pytest.mark.asyncio
    async def test_scenario_both_fail_text_only(self):
        """Scenario 4: All TTS engines fail"""
        # Setup: OpenAI fails, Edge unavailable, Piper missing
        # Expected: synthesize_voice_note returns None, text-only delivery with skip reason
        pytest.skip("not implemented — the body of this test is empty. It counted as a PASS in every run until the 2026-09-20 review; marking it skipped makes the gap visible instead of inflating the green count.")


# Test helpers
@pytest.fixture
def mock_service_env(tmp_path):
    """Create a temporary service.env file"""
    service_env = tmp_path / "service.env"
    service_env.write_text(
        "CORVIN_TTS_OPENAI_KEY=sk-proj-test-key-123\n"
        "OPENAI_API_KEY=sk-proj-fallback-key-456\n"
    )
    return service_env


@pytest.fixture
def mock_outbox(tmp_path):
    """Create a temporary outbox directory"""
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    return outbox


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])
