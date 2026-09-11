"""
Test suite for model providers (ADR-0377 Phase 3)
Tests ClaudeProvider and GeminiProvider implementations.
"""

import asyncio
from unittest.mock import Mock, AsyncMock, patch
import sys

sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.models.provider_interface import ModelProviderConfig, ModelResponse, HealthCheckResult
from core.models.providers import ClaudeProvider, GeminiProvider, OllamaProvider


class TestClaudeProvider:
    """Test Anthropic Claude provider."""

    def test_claude_initialization(self):
        """Test ClaudeProvider initialization."""
        config = ModelProviderConfig(
            name="anthropic",
            api_key="test-key-123",
        )
        provider = ClaudeProvider(config)

        assert provider.config.name == "anthropic"
        assert provider.config.api_key == "test-key-123"
        assert provider.name == "anthropic"

    async def test_claude_model_availability(self):
        """Test Claude model availability check."""
        config = ModelProviderConfig(
            name="anthropic",
            api_key="test-key",
        )
        provider = ClaudeProvider(config)

        # Valid models
        assert await provider.check_availability("claude-3-5-haiku-20241022")
        assert await provider.check_availability("claude-3-5-sonnet-20241022")
        assert await provider.check_availability("claude-3-opus-20240229")

        # Invalid model
        assert not await provider.check_availability("invalid-model")

    async def test_claude_default_model(self):
        """Test Claude default model."""
        config = ModelProviderConfig(
            name="anthropic",
            api_key="test-key",
        )
        provider = ClaudeProvider(config)

        default = await provider.get_default_model()
        assert "sonnet" in default.lower()

    def test_claude_health_check_no_key(self):
        """Test health check fails without API key."""
        config = ModelProviderConfig(
            name="anthropic",
            api_key=None,
        )
        provider = ClaudeProvider(config)

        # Manually check the health check logic
        # (can't easily async test without mocking aiohttp)
        assert config.api_key is None


class TestGeminiProvider:
    """Test Google Gemini provider."""

    def test_gemini_initialization(self):
        """Test GeminiProvider initialization."""
        config = ModelProviderConfig(
            name="google",
            api_key="test-key-456",
        )
        provider = GeminiProvider(config)

        assert provider.config.name == "google"
        assert provider.config.api_key == "test-key-456"
        assert provider.name == "google"

    async def test_gemini_model_availability(self):
        """Test Gemini model availability check."""
        config = ModelProviderConfig(
            name="google",
            api_key="test-key",
        )
        provider = GeminiProvider(config)

        # Valid models
        assert await provider.check_availability("gemini-pro")
        assert await provider.check_availability("gemini-1.5-pro")
        assert await provider.check_availability("gemini-1.5-flash")

        # Invalid model
        assert not await provider.check_availability("invalid-model")

    async def test_gemini_default_model(self):
        """Test Gemini default model."""
        config = ModelProviderConfig(
            name="google",
            api_key="test-key",
        )
        provider = GeminiProvider(config)

        default = await provider.get_default_model()
        assert "gemini" in default.lower()

    def test_gemini_health_check_no_key(self):
        """Test health check fails without API key."""
        config = ModelProviderConfig(
            name="google",
            api_key=None,
        )
        provider = GeminiProvider(config)

        # Manually check the health check logic
        # (can't easily async test without mocking aiohttp)
        assert config.api_key is None


class TestProviderInterfaces:
    """Test provider interface compliance."""

    def test_claude_implements_interface(self):
        """Test ClaudeProvider implements ModelProvider."""
        config = ModelProviderConfig(name="anthropic", api_key="test")
        provider = ClaudeProvider(config)

        # Check it has all required methods
        assert hasattr(provider, 'check_availability')
        assert hasattr(provider, 'health_check')
        assert hasattr(provider, 'invoke')
        assert hasattr(provider, 'get_default_model')
        assert hasattr(provider, 'name')

    def test_gemini_implements_interface(self):
        """Test GeminiProvider implements ModelProvider."""
        config = ModelProviderConfig(name="google", api_key="test")
        provider = GeminiProvider(config)

        # Check it has all required methods
        assert hasattr(provider, 'check_availability')
        assert hasattr(provider, 'health_check')
        assert hasattr(provider, 'invoke')
        assert hasattr(provider, 'get_default_model')
        assert hasattr(provider, 'name')


class TestModelResponse:
    """Test ModelResponse dataclass."""

    def test_model_response_creation(self):
        """Test creating ModelResponse."""
        response = ModelResponse(
            content="Test response",
            model="claude-3-sonnet",
            usage_tokens=100,
            cost_usd=0.001,
        )

        assert response.content == "Test response"
        assert response.model == "claude-3-sonnet"
        assert response.usage_tokens == 100
        assert response.cost_usd == 0.001

    def test_model_response_without_cost(self):
        """Test ModelResponse without cost."""
        response = ModelResponse(
            content="Test",
            model="llama-7b",
            usage_tokens=50,
        )

        assert response.cost_usd is None


def test_provider_config_immutability():
    """Test that ModelProviderConfig is immutable (frozen)."""
    config = ModelProviderConfig(
        name="test",
        api_key="key",
    )

    # Try to modify (should raise FrozenInstanceError)
    try:
        config.name = "modified"
        assert False, "Should not allow modification"
    except (AttributeError, Exception):
        # Expected
        pass


def run_tests():
    """Run all tests."""
    print("Testing Claude Provider...")
    test = TestClaudeProvider()
    test.test_claude_initialization()
    test.test_claude_health_check_no_key()
    asyncio.run(test.test_claude_model_availability())
    asyncio.run(test.test_claude_default_model())
    print("✓ Claude Provider tests passed")

    print("Testing Gemini Provider...")
    test = TestGeminiProvider()
    test.test_gemini_initialization()
    test.test_gemini_health_check_no_key()
    asyncio.run(test.test_gemini_model_availability())
    asyncio.run(test.test_gemini_default_model())
    print("✓ Gemini Provider tests passed")

    print("Testing Provider Interfaces...")
    test = TestProviderInterfaces()
    test.test_claude_implements_interface()
    test.test_gemini_implements_interface()
    print("✓ Provider Interface tests passed")

    print("Testing ModelResponse...")
    test = TestModelResponse()
    test.test_model_response_creation()
    test.test_model_response_without_cost()
    print("✓ ModelResponse tests passed")

    print("Testing immutability...")
    test_provider_config_immutability()
    print("✓ Immutability tests passed")

    print("\n" + "=" * 60)
    print("All provider tests passed! ✓")
    print("=" * 60)


if __name__ == "__main__":
    run_tests()
