"""
Model Router with Automatic Fallback (ADR-0609)
Cost-optimized provider selection + resilience.
"""

import logging
from typing import List, Dict, Optional
from .provider_interface import ModelProvider, ModelProviderConfig, ModelResponse
from .providers import OpenAIProvider, OllamaProvider, OpenRouterProvider

logger = logging.getLogger(__name__)


class ModelRouter:
    """Route Skill invocations to optimal provider."""

    def __init__(self):
        import os
        # Initialize providers (load secrets from env, validate)
        openai_key = os.environ.get("OPENAI_API_KEY")
        openrouter_key = os.environ.get("OPENROUTER_API_KEY")

        if not openai_key:
            logger.warning("OPENAI_API_KEY not configured; OpenAI provider will fail")
        if not openrouter_key:
            logger.warning("OPENROUTER_API_KEY not configured; OpenRouter provider will fail")

        self.providers = {
            "openai": OpenAIProvider(ModelProviderConfig(name="openai", api_key=openai_key)),
            "ollama": OllamaProvider(ModelProviderConfig(name="ollama", base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"))),
            "openrouter": OpenRouterProvider(ModelProviderConfig(name="openrouter", api_key=openrouter_key)),
        }
        self.cost_tracker = {}  # skill_id → cost

    async def invoke_with_fallback(
        self,
        skill_id: str,
        model_preference: str,  # "openai" | "ollama" | "openrouter"
        fallback_chain: List[str],  # ["openrouter", "openai"]
        messages: List[Dict[str, str]],
        **kwargs,
    ) -> ModelResponse:
        """Invoke model, fallback to next provider on failure."""

        providers_to_try = [model_preference] + fallback_chain
        last_error = None

        for provider_name in providers_to_try:
            if provider_name not in self.providers:
                logger.warning(f"Provider {provider_name} not registered, skipping")
                continue

            provider = self.providers[provider_name]

            try:
                logger.info(f"Trying {provider_name} for {skill_id}")
                model = kwargs.get("model") or await provider.get_default_model()
                response = await provider.invoke(
                    model=model,
                    messages=messages,
                    temperature=kwargs.get("temperature", 0.7),
                    max_tokens=kwargs.get("max_tokens", 2048),
                )

                # Track cost (distinguish 0.0 from None)
                if skill_id not in self.cost_tracker:
                    self.cost_tracker[skill_id] = 0.0
                if response.cost_usd is not None:
                    self.cost_tracker[skill_id] += response.cost_usd

                logger.info(f"✓ {provider_name} succeeded for {skill_id}")
                return response

            except Exception as e:
                logger.warning(f"✗ {provider_name} failed: {e}")
                last_error = e
                continue  # Try next provider

        # All providers exhausted
        raise Exception(f"All providers exhausted for {skill_id}. Last error: {last_error}")

    def get_cost_for_skill(self, skill_id: str) -> float:
        """Get accumulated cost for a Skill."""
        return self.cost_tracker.get(skill_id, 0.0)

    async def select_provider_by_complexity(self, complexity: str) -> str:
        """Select provider based on Skill complexity."""
        if complexity == "simple":
            # Use cheap/local first
            if await self.providers["ollama"].check_availability("mistral:7b"):
                return "ollama"
            return "openrouter"

        if complexity == "medium":
            # Balanced cost/quality
            return "openrouter"

        if complexity == "complex":
            # Use best model
            return "openai"

        return "openrouter"  # Default
