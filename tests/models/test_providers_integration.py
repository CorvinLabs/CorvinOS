"""
Integration Tests: External Providers (ADR-0607, ADR-0643)

Tests for OpenAI, Ollama, and OpenRouter providers.
Tests health checks, invocation, and timeout handling.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from core.models.providers import (
    OpenAIProvider,
    OllamaProvider,
    OpenRouterProvider,
)
from core.models.provider_interface import ModelProviderConfig, ModelResponse


class TestOpenAIProvider:
    """OpenAI provider tests."""

    def setup_method(self):
        self.config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=30,
        )
        self.provider = OpenAIProvider(self.config)

    def test_get_default_model(self):
        """Test default model selection."""
        assert self.provider.get_default_model() == "gpt-4-turbo"

    def test_provider_name(self):
        """Test provider name property."""
        assert self.provider.name == "openai"

    @pytest.mark.asyncio
    async def test_check_availability(self):
        """Test model availability check."""
        # These should be available
        assert await self.provider.check_availability("gpt-4")
        assert await self.provider.check_availability("gpt-3.5-turbo")

        # Unknown models
        assert not await self.provider.check_availability("unknown-model")

    @pytest.mark.asyncio
    async def test_invoke_success(self):
        """Test successful model invocation."""
        messages = [{"role": "user", "content": "Hello"}]

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{"message": {"content": "Hello there!"}}],
                "usage": {"total_tokens": 20},
            })
            mock_post.return_value.__aenter__.return_value = mock_response

            result = await self.provider.invoke(
                model="gpt-4",
                messages=messages,
            )

            assert result.content == "Hello there!"
            assert result.model == "gpt-4"
            assert result.usage_tokens == 20
            assert result.cost_usd is not None

    @pytest.mark.asyncio
    async def test_invoke_malformed_response(self):
        """Test handling of malformed API response."""
        messages = [{"role": "user", "content": "Hello"}]

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={"invalid": "response"})
            mock_post.return_value.__aenter__.return_value = mock_response

            with pytest.raises(Exception):
                await self.provider.invoke(
                    model="gpt-4",
                    messages=messages,
                )


class TestOllamaProvider:
    """Ollama provider tests."""

    def setup_method(self):
        self.config = ModelProviderConfig(
            name="ollama",
            base_url="http://localhost:11434",
            timeout_s=30,
        )
        self.provider = OllamaProvider(self.config)

    def test_get_default_model(self):
        """Test default model selection."""
        assert self.provider.get_default_model() == "mistral:7b"

    def test_provider_name(self):
        """Test provider name property."""
        assert self.provider.name == "ollama"

    @pytest.mark.asyncio
    async def test_check_availability(self):
        """Test model availability check."""
        # Ollama always returns True for availability check
        assert await self.provider.check_availability("mistral:7b")
        assert await self.provider.check_availability("any-model")

    @pytest.mark.asyncio
    async def test_invoke_success(self):
        """Test successful Ollama invocation."""
        messages = [{"role": "user", "content": "Hello"}]

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "message": {"content": "Hello world"},
            })
            mock_post.return_value.__aenter__.return_value = mock_response

            result = await self.provider.invoke(
                model="mistral:7b",
                messages=messages,
            )

            assert result.content == "Hello world"
            assert result.model == "mistral:7b"
            assert result.cost_usd == 0.0  # Ollama is free

    @pytest.mark.asyncio
    async def test_invoke_with_custom_url(self):
        """Test invocation with custom Ollama URL."""
        config = ModelProviderConfig(
            name="ollama",
            base_url="http://ollama.custom.local:11434",
        )
        provider = OllamaProvider(config)

        messages = [{"role": "user", "content": "Test"}]

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "message": {"content": "Response"},
            })
            mock_post.return_value.__aenter__.return_value = mock_response

            await provider.invoke(model="mistral:7b", messages=messages)

            # Verify custom URL was used
            call_args = mock_post.call_args
            assert "ollama.custom.local" in str(call_args)


class TestOpenRouterProvider:
    """OpenRouter provider tests."""

    def setup_method(self):
        self.config = ModelProviderConfig(
            name="openrouter",
            api_key="test-key",
            timeout_s=30,
        )
        self.provider = OpenRouterProvider(self.config)

    def test_get_default_model(self):
        """Test default model selection."""
        assert self.provider.get_default_model() == "anthropic/claude-opus"

    def test_provider_name(self):
        """Test provider name property."""
        assert self.provider.name == "openrouter"

    @pytest.mark.asyncio
    async def test_check_availability(self):
        """Test model availability check."""
        # OpenRouter always returns True for availability
        assert await self.provider.check_availability("anthropic/claude-opus")
        assert await self.provider.check_availability("any-model")

    @pytest.mark.asyncio
    async def test_invoke_success(self):
        """Test successful OpenRouter invocation."""
        messages = [{"role": "user", "content": "Hello"}]

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{"message": {"content": "Hello!"}}],
                "usage": {"total_tokens": 15},
                "cost": 0.005,
            })
            mock_post.return_value.__aenter__.return_value = mock_response

            result = await self.provider.invoke(
                model="anthropic/claude-opus",
                messages=messages,
            )

            assert result.content == "Hello!"
            assert result.model == "anthropic/claude-opus"
            assert result.usage_tokens == 15
            assert result.cost_usd == 0.005


class TestProviderConfigValidation:
    """Test provider configuration validation."""

    def test_openai_config_required_keys(self):
        """Test OpenAI configuration requires API key."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=30,
        )
        provider = OpenAIProvider(config)

        assert provider.config.api_key == "test-key"
        assert provider.config.timeout_s == 30

    def test_ollama_config_base_url(self):
        """Test Ollama configuration with base URL."""
        config = ModelProviderConfig(
            name="ollama",
            base_url="http://custom:11434",
            timeout_s=30,
        )
        provider = OllamaProvider(config)

        assert provider.config.base_url == "http://custom:11434"

    def test_default_timeout_value(self):
        """Test default timeout value (ADR-0643)."""
        config = ModelProviderConfig(name="test")
        assert config.timeout_s == 30  # Default is 30 seconds


class TestProviderInvokeOptions:
    """Test provider invoke options."""

    @pytest.mark.asyncio
    async def test_temperature_parameter(self):
        """Test temperature parameter in invocation."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{"message": {"content": "Response"}}],
                "usage": {"total_tokens": 10},
            })
            mock_post.return_value.__aenter__.return_value = mock_response

            await provider.invoke(
                model="gpt-4",
                messages=[{"role": "user", "content": "test"}],
                temperature=0.3,
            )

            # Verify temperature was passed
            call_args = mock_post.call_args
            json_data = call_args[1]["json"]
            assert json_data["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_max_tokens_parameter(self):
        """Test max_tokens parameter in invocation."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value={
                "choices": [{"message": {"content": "Response"}}],
                "usage": {"total_tokens": 100},
            })
            mock_post.return_value.__aenter__.return_value = mock_response

            await provider.invoke(
                model="gpt-4",
                messages=[{"role": "user", "content": "test"}],
                max_tokens=500,
            )

            # Verify max_tokens was passed
            call_args = mock_post.call_args
            json_data = call_args[1]["json"]
            assert json_data["max_tokens"] == 500


class TestProviderErrorHandling:
    """Test provider error handling."""

    @pytest.mark.asyncio
    async def test_api_error_response(self):
        """Test handling of API error responses."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_response = AsyncMock()
            mock_response.status = 429  # Rate limit
            mock_post.return_value.__aenter__.return_value = mock_response

            with pytest.raises(Exception):
                await provider.invoke(
                    model="gpt-4",
                    messages=[{"role": "user", "content": "test"}],
                )

    @pytest.mark.asyncio
    async def test_timeout_during_invoke(self):
        """Test timeout during model invocation."""
        config = ModelProviderConfig(
            name="openai",
            api_key="test-key",
            timeout_s=1,
        )
        provider = OpenAIProvider(config)

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_post.side_effect = asyncio.TimeoutError()

            with pytest.raises(asyncio.TimeoutError):
                await provider.invoke(
                    model="gpt-4",
                    messages=[{"role": "user", "content": "test"}],
                )
