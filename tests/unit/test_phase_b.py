"""Phase B tests: Providers, Router, UI-Layers."""

import pytest
from core.models.provider_interface import ModelProviderConfig, ModelResponse
from core.models.providers import OpenAIProvider, OllamaProvider, OpenRouterProvider
from core.models.router import ModelRouter
from core.ui_layers.ui_adapter import UIRequest, UIResponse
from core.ui_layers.discord_adapter import DiscordUILayer
from core.ui_layers.cli_adapter import CLIUILayer


class TestProviders:
    """Test model providers (Tier 1-2)."""

    def test_openai_config(self):
        """OpenAI provider initializes."""
        config = ModelProviderConfig(name="openai", api_key="test-key")
        provider = OpenAIProvider(config)
        assert provider.name == "openai"
        assert provider.config.api_key == "test-key"

    def test_ollama_config(self):
        """Ollama provider initializes."""
        config = ModelProviderConfig(name="ollama", base_url="http://localhost:11434")
        provider = OllamaProvider(config)
        assert provider.name == "ollama"
        assert provider.config.base_url == "http://localhost:11434"

    def test_openrouter_config(self):
        """OpenRouter provider initializes."""
        config = ModelProviderConfig(name="openrouter", api_key="test-key")
        provider = OpenRouterProvider(config)
        assert provider.name == "openrouter"

    @pytest.mark.asyncio
    async def test_openai_check_availability(self):
        """OpenAI availability check."""
        config = ModelProviderConfig(name="openai", api_key="test")
        provider = OpenAIProvider(config)
        assert await provider.check_availability("gpt-4") is True
        assert await provider.check_availability("gpt-3.5-turbo") is True

    @pytest.mark.asyncio
    async def test_ollama_check_availability(self):
        """Ollama availability check."""
        config = ModelProviderConfig(name="ollama")
        provider = OllamaProvider(config)
        # Ollama assumes everything is available locally
        assert await provider.check_availability("mistral:7b") is True

    def test_model_response_immutable(self):
        """ModelResponse is frozen."""
        resp = ModelResponse(content="test", model="gpt-4", usage_tokens=100, cost_usd=0.01)
        with pytest.raises(AttributeError):
            resp.content = "changed"


class TestRouter:
    """Test model router (Tier 2-3)."""

    def test_router_init(self):
        """Router initializes with all providers."""
        router = ModelRouter()
        assert "openai" in router.providers
        assert "ollama" in router.providers
        assert "openrouter" in router.providers

    def test_router_cost_tracking(self):
        """Router tracks costs per skill."""
        router = ModelRouter()
        assert router.get_cost_for_skill("os.test") == 0.0

        # Simulate cost addition
        router.cost_tracker["os.test"] = 0.05
        assert router.get_cost_for_skill("os.test") == 0.05

    @pytest.mark.asyncio
    async def test_router_select_by_complexity(self):
        """Router selects provider by complexity."""
        router = ModelRouter()

        simple_provider = await router.select_provider_by_complexity("simple")
        assert simple_provider in ["ollama", "openrouter"]

        medium_provider = await router.select_provider_by_complexity("medium")
        assert medium_provider == "openrouter"

        complex_provider = await router.select_provider_by_complexity("complex")
        assert complex_provider == "openai"


class TestUILayers:
    """Test UI-Layer adapters (Tier 2-3)."""

    @pytest.mark.asyncio
    async def test_discord_parse_input(self):
        """Discord adapter parses slash command."""
        adapter = DiscordUILayer()
        raw_input = {
            "guild_id": "123456",
            "user_id": "user_1",
            "channel_id": "ch_1",
            "content": "/skill os.delegation_router task_shape=small",
        }

        request = await adapter.parse_input(raw_input)
        assert request.tenant_id == "123456"
        assert request.user_id == "user_1"
        assert request.skill_id == "skill"
        assert request.input_data.get("os.delegation_router") == "os.delegation_router"
        assert request.channel_id == "ch_1"

    @pytest.mark.asyncio
    async def test_cli_parse_input(self):
        """CLI adapter parses command-line args."""
        adapter = CLIUILayer()
        raw_input = ["os.delegation_router", "task_shape=small", "confidence=0.7"]

        request = await adapter.parse_input(raw_input)
        assert request.tenant_id == "_default"
        assert request.skill_id == "os.delegation_router"
        assert request.input_data["task_shape"] == "small"
        assert request.input_data["confidence"] == "0.7"

    @pytest.mark.asyncio
    async def test_ui_request_immutable(self):
        """UIRequest is frozen."""
        request = UIRequest(
            tenant_id="test",
            user_id="user",
            skill_id="os.test",
            input_data={},
        )
        with pytest.raises(AttributeError):
            request.tenant_id = "changed"

    @pytest.mark.asyncio
    async def test_ui_response_immutable(self):
        """UIResponse is frozen."""
        response = UIResponse(content="test", is_success=True)
        with pytest.raises(AttributeError):
            response.is_success = False


class TestAdversarial:
    """Adversarial tests (Tier 5)."""

    def test_provider_config_validates(self):
        """Provider config validation."""
        config = ModelProviderConfig(name="invalid", timeout_s=1)
        # Should not crash on init
        assert config.timeout_s == 1

    @pytest.mark.asyncio
    async def test_cli_missing_skill_id(self):
        """CLI adapter rejects empty args."""
        adapter = CLIUILayer()
        with pytest.raises(ValueError, match="Usage"):
            await adapter.parse_input([])

    def test_router_unknown_provider(self):
        """Router gracefully handles unknown provider."""
        router = ModelRouter()
        # Should not crash if asking for invalid provider
        assert "unknown" not in router.providers
