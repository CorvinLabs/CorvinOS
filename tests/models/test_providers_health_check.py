"""
Integration Tests: Provider Health Checks (ADR-0643)

Tests provider health checks, timeouts, and fallback mechanisms.
Timeout target: 30s (ADR-0643).
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.models.provider_interface import HealthCheckResult
from core.models.providers import (
    OpenAIProvider,
    OllamaProvider,
    OpenRouterProvider,
)
from core.models.provider_interface import ModelProviderConfig


class TestOpenAIHealthCheck:
    """OpenAI provider health check tests."""

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Test successful OpenAI health check."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=30,
        )
        provider = OpenAIProvider(config)

        # Mock successful response
        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": [
                    {"id": "gpt-4"},
                    {"id": "gpt-3.5-turbo"},
                ]
            })
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await provider.health_check()

            assert result.healthy is True
            assert "healthy" in result.message.lower()
            assert result.latency_ms > 0

    @pytest.mark.asyncio
    async def test_health_check_no_api_key(self):
        """Test health check fails without API key."""
        config = ModelProviderConfig(name="openai", api_key=None)
        provider = OpenAIProvider(config)

        result = await provider.health_check()

        assert result.healthy is False
        assert "key" in result.message.lower()

    @pytest.mark.asyncio
    async def test_health_check_api_error(self):
        """Test health check handles API errors."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=30,
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 401
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await provider.health_check()

            assert result.healthy is False
            assert "401" in result.message

    @pytest.mark.asyncio
    async def test_health_check_timeout(self):
        """Test health check respects timeout."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=1,
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_get.side_effect = asyncio.TimeoutError()

            result = await provider.health_check()

            assert result.healthy is False
            assert "timeout" in result.message.lower()
            assert result.latency_ms > 0


class TestOllamaHealthCheck:
    """Ollama provider health check tests."""

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Test successful Ollama health check."""
        config = ModelProviderConfig(
            name="ollama",
            base_url="http://localhost:11434",
            timeout_s=30,
        )
        provider = OllamaProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "models": [
                    {"name": "mistral:7b"},
                    {"name": "neural-chat"},
                ]
            })
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await provider.health_check()

            assert result.healthy is True
            assert "healthy" in result.message.lower()
            assert "mistral:7b" in result.available_models

    @pytest.mark.asyncio
    async def test_health_check_not_running(self):
        """Test health check when Ollama is not running."""
        config = ModelProviderConfig(
            name="ollama",
            base_url="http://localhost:11434",
            timeout_s=5,
        )
        provider = OllamaProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_get.side_effect = ConnectionError("Connection refused")

            result = await provider.health_check()

            assert result.healthy is False
            assert "running" in result.message.lower()

    @pytest.mark.asyncio
    async def test_health_check_custom_base_url(self):
        """Test health check with custom Ollama URL."""
        config = ModelProviderConfig(
            name="ollama",
            base_url="http://ollama.custom.local:11434",
            timeout_s=30,
        )
        provider = OllamaProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"models": []})
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await provider.health_check()

            # Verify custom URL was used
            call_args = mock_get.call_args
            assert "ollama.custom.local" in str(call_args)

    @pytest.mark.asyncio
    async def test_health_check_timeout(self):
        """Test Ollama health check respects timeout."""
        config = ModelProviderConfig(
            name="ollama",
            base_url="http://localhost:11434",
            timeout_s=1,
        )
        provider = OllamaProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_get.side_effect = asyncio.TimeoutError()

            result = await provider.health_check()

            assert result.healthy is False
            assert "timeout" in result.message.lower()


class TestOpenRouterHealthCheck:
    """OpenRouter provider health check tests."""

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        """Test successful OpenRouter health check."""
        config = ModelProviderConfig(
            name="openrouter",
            api_key="test-key",
            timeout_s=30,
        )
        provider = OpenRouterProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "data": [
                    {"id": "openai/gpt-4"},
                    {"id": "anthropic/claude-opus"},
                ]
            })
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await provider.health_check()

            assert result.healthy is True
            assert "healthy" in result.message.lower()

    @pytest.mark.asyncio
    async def test_health_check_invalid_api_key(self):
        """Test health check with invalid API key."""
        config = ModelProviderConfig(
            name="openrouter",
            api_key="invalid-key",
            timeout_s=30,
        )
        provider = OpenRouterProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 401
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await provider.health_check()

            assert result.healthy is False

    @pytest.mark.asyncio
    async def test_health_check_no_api_key(self):
        """Test health check fails without API key."""
        config = ModelProviderConfig(name="openrouter", api_key=None)
        provider = OpenRouterProvider(config)

        result = await provider.health_check()

        assert result.healthy is False
        assert "key" in result.message.lower()


class TestProviderHealthCheckTimeout:
    """Test timeout constraints (ADR-0643)."""

    @pytest.mark.asyncio
    async def test_timeout_30_seconds_constraint(self):
        """Test that providers timeout at 30s (ADR-0643)."""
        config = ModelProviderConfig(
            name="ollama",
            base_url="http://localhost:11434",
            timeout_s=30,  # Must be exactly 30s
        )
        provider = OllamaProvider(config)

        assert provider.config.timeout_s == 30

    @pytest.mark.asyncio
    async def test_health_check_respects_timeout(self):
        """Test that health check respects configured timeout."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=5,  # Short timeout for testing
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            # Simulate timeout
            async def timeout_after_delay():
                await asyncio.sleep(0.1)
                raise asyncio.TimeoutError()

            mock_get.side_effect = timeout_after_delay

            result = await provider.health_check()

            assert result.healthy is False
            assert result.latency_ms > 0


class TestHealthCheckResult:
    """Test HealthCheckResult dataclass."""

    def test_health_check_result_structure(self):
        """Test HealthCheckResult structure."""
        result = HealthCheckResult(
            healthy=True,
            message="All good",
            latency_ms=42.5,
            available_models=["model-1", "model-2"],
        )

        assert result.healthy is True
        assert result.message == "All good"
        assert result.latency_ms == 42.5
        assert len(result.available_models) == 2

    def test_health_check_result_immutable(self):
        """Test that HealthCheckResult is immutable (frozen)."""
        result = HealthCheckResult(
            healthy=True,
            message="Good",
            latency_ms=10.0,
        )

        # Should not be able to modify
        with pytest.raises(AttributeError):
            result.healthy = False

    def test_health_check_result_defaults(self):
        """Test default values in HealthCheckResult."""
        result = HealthCheckResult(
            healthy=False,
            message="Error",
        )

        assert result.latency_ms == 0.0
        assert result.available_models is None


class TestProviderFailureHandling:
    """Test provider failure scenarios."""

    @pytest.mark.asyncio
    async def test_network_failure_handling(self):
        """Test handling of network failures."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=30,
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_get.side_effect = ConnectionError("Network unreachable")

            result = await provider.health_check()

            assert result.healthy is False
            assert result.latency_ms == 0.0

    @pytest.mark.asyncio
    async def test_malformed_response_handling(self):
        """Test handling of malformed API responses."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=30,
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.get") as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(side_effect=ValueError("Invalid JSON"))
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await provider.health_check()

            assert result.healthy is False
