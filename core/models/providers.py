"""
Model Provider Implementations (ADR-0607)
OpenAI, Ollama, OpenRouter
"""

import aiohttp
import logging
from typing import List, Dict
from .provider_interface import ModelProvider, ModelProviderConfig, ModelResponse

logger = logging.getLogger(__name__)


class OpenAIProvider(ModelProvider):
    """OpenAI (gpt-4, gpt-3.5-turbo via official API)."""

    async def check_availability(self, model: str) -> bool:
        """Check if model exists in OpenAI."""
        # In real impl: call /models endpoint
        return model in ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]

    async def invoke(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        """Call OpenAI API."""
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=self.config.timeout_s)) as resp:
                if resp.status != 200:
                    raise Exception(f"OpenAI error: {resp.status}")
                data = await resp.json()

        # Extract response with defensive access
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise Exception(f"Malformed OpenAI response: {e}") from e
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)

        # Cost estimation (model-specific pricing)
        # gpt-4: $0.01/1K input, $0.03/1K output; gpt-3.5: $0.0005/1K input, $0.0015/1K output
        if "gpt-4" in model:
            cost = (tokens / 1000) * 0.04  # Average 0.01 in + 0.03 out
        else:
            cost = (tokens / 1000) * 0.001  # gpt-3.5 average

        return ModelResponse(
            content=content,
            model=model,
            usage_tokens=tokens,
            cost_usd=cost,
        )

    async def get_default_model(self) -> str:
        return "gpt-4-turbo"


class OllamaProvider(ModelProvider):
    """Ollama (local, free, fast)."""

    async def check_availability(self, model: str) -> bool:
        """Check if model is pulled in Ollama."""
        # In real impl: query Ollama /api/tags
        return True  # Assume available for now

    async def invoke(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        """Call Ollama API (local)."""
        base_url = self.config.base_url or "http://localhost:11434"
        url = f"{base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=self.config.timeout_s)) as resp:
                if resp.status != 200:
                    raise Exception(f"Ollama error: {resp.status}")
                data = await resp.json()

        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise Exception(f"Malformed Ollama response: {e}") from e
        # Ollama doesn't track tokens, estimate conservatively (count actual chars, not split)
        tokens = max(1, len(content) // 4)

        return ModelResponse(
            content=content,
            model=model,
            usage_tokens=tokens,
            cost_usd=0.0,  # Free
        )

    async def get_default_model(self) -> str:
        return "mistral:7b"


class OpenRouterProvider(ModelProvider):
    """OpenRouter (proxy to multiple providers, cost-effective)."""

    async def check_availability(self, model: str) -> bool:
        """Check if model available on OpenRouter."""
        # In real impl: call OpenRouter /models
        return True  # Assume available

    async def invoke(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        """Call OpenRouter API."""
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=self.config.timeout_s)) as resp:
                if resp.status != 200:
                    raise Exception(f"OpenRouter error: {resp.status}")
                data = await resp.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise Exception(f"Malformed OpenRouter response: {e}") from e
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        # OpenRouter provides actual cost; use it instead of estimate
        cost = data.get("cost", usage.get("cost", 0.001))

        return ModelResponse(
            content=content,
            model=model,
            usage_tokens=tokens,
            cost_usd=cost,
        )

    async def get_default_model(self) -> str:
        return "anthropic/claude-opus"
