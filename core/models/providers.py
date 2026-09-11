"""
Model Provider Implementations (ADR-0607, ADR-0377 Phase 3)
OpenAI, Ollama, OpenRouter, Claude (Anthropic), Gemini (Google)
"""

import asyncio
import aiohttp
import logging
import time
import os
from typing import List, Dict, Optional
from .provider_interface import ModelProvider, ModelProviderConfig, ModelResponse, HealthCheckResult

logger = logging.getLogger(__name__)


class OpenAIProvider(ModelProvider):
    """OpenAI (gpt-4, gpt-3.5-turbo via official API)."""

    async def check_availability(self, model: str) -> bool:
        """Check if model exists in OpenAI."""
        # In real impl: call /models endpoint
        return model in ["gpt-4", "gpt-4-turbo", "gpt-3.5-turbo"]

    async def health_check(self) -> HealthCheckResult:
        """Health check for OpenAI API (ADR-0643)."""
        if not self.config.api_key:
            return HealthCheckResult(
                healthy=False,
                message="API key not configured",
                latency_ms=0.0,
            )

        try:
            start = time.time()
            url = "https://api.openai.com/v1/models"
            headers = {"Authorization": f"Bearer {self.config.api_key}"}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_s),
                ) as resp:
                    latency_ms = (time.time() - start) * 1000

                    if resp.status != 200:
                        return HealthCheckResult(
                            healthy=False,
                            message=f"API returned {resp.status}",
                            latency_ms=latency_ms,
                        )

                    data = await resp.json()
                    models = [m.get("id") for m in data.get("data", [])]
                    return HealthCheckResult(
                        healthy=True,
                        message="OpenAI API is healthy",
                        latency_ms=latency_ms,
                        available_models=models,
                    )
        except asyncio.TimeoutError:
            return HealthCheckResult(
                healthy=False,
                message=f"Health check timeout (>{self.config.timeout_s}s)",
                latency_ms=self.config.timeout_s * 1000,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Health check failed: {e}",
                latency_ms=0.0,
            )

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

    async def health_check(self) -> HealthCheckResult:
        """Health check for Ollama local service (ADR-0643)."""
        base_url = self.config.base_url or "http://localhost:11434"
        url = f"{base_url}/api/tags"

        try:
            start = time.time()
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_s),
                ) as resp:
                    latency_ms = (time.time() - start) * 1000

                    if resp.status != 200:
                        return HealthCheckResult(
                            healthy=False,
                            message=f"Ollama returned {resp.status}",
                            latency_ms=latency_ms,
                        )

                    data = await resp.json()
                    models = [m.get("name") for m in data.get("models", [])]
                    return HealthCheckResult(
                        healthy=True,
                        message="Ollama is healthy",
                        latency_ms=latency_ms,
                        available_models=models,
                    )
        except asyncio.TimeoutError:
            return HealthCheckResult(
                healthy=False,
                message=f"Ollama health check timeout (>{self.config.timeout_s}s)",
                latency_ms=self.config.timeout_s * 1000,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Ollama health check failed (is it running?): {e}",
                latency_ms=0.0,
            )

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

    async def health_check(self) -> HealthCheckResult:
        """Health check for OpenRouter API (ADR-0643)."""
        if not self.config.api_key:
            return HealthCheckResult(
                healthy=False,
                message="API key not configured",
                latency_ms=0.0,
            )

        try:
            start = time.time()
            url = "https://openrouter.ai/api/v1/models"
            headers = {"Authorization": f"Bearer {self.config.api_key}"}

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_s),
                ) as resp:
                    latency_ms = (time.time() - start) * 1000

                    if resp.status != 200:
                        return HealthCheckResult(
                            healthy=False,
                            message=f"OpenRouter returned {resp.status}",
                            latency_ms=latency_ms,
                        )

                    data = await resp.json()
                    models = [m.get("id") for m in data.get("data", [])]
                    return HealthCheckResult(
                        healthy=True,
                        message="OpenRouter is healthy",
                        latency_ms=latency_ms,
                        available_models=models,
                    )
        except asyncio.TimeoutError:
            return HealthCheckResult(
                healthy=False,
                message=f"OpenRouter health check timeout (>{self.config.timeout_s}s)",
                latency_ms=self.config.timeout_s * 1000,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"OpenRouter health check failed: {e}",
                latency_ms=0.0,
            )

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


class ClaudeProvider(ModelProvider):
    """Anthropic Claude (Haiku, Sonnet, Opus via official API) — ADR-0377 Phase 3."""

    async def check_availability(self, model: str) -> bool:
        """Check if Claude model exists."""
        valid_models = [
            "claude-3-5-haiku-20241022",
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
            "claude-3-haiku-20240307",
            "claude-3-sonnet-20240229",
        ]
        return model in valid_models

    async def health_check(self) -> HealthCheckResult:
        """Health check for Anthropic API (ADR-0643)."""
        if not self.config.api_key:
            return HealthCheckResult(
                healthy=False,
                message="API key not configured",
                latency_ms=0.0,
            )

        try:
            start = time.time()
            url = "https://api.anthropic.com/v1/messages"
            headers = {
                "x-api-key": self.config.api_key,
                "anthropic-version": "2023-06-01",
            }
            # Simple health check: short completion request
            payload = {
                "model": "claude-3-haiku-20240307",
                "max_tokens": 10,
                "messages": [{"role": "user", "content": "Hi"}],
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_s),
                ) as resp:
                    latency_ms = (time.time() - start) * 1000

                    if resp.status not in [200, 201]:
                        return HealthCheckResult(
                            healthy=False,
                            message=f"API returned {resp.status}",
                            latency_ms=latency_ms,
                        )

                    data = await resp.json()
                    return HealthCheckResult(
                        healthy=True,
                        message="Anthropic API is healthy",
                        latency_ms=latency_ms,
                        available_models=[
                            "claude-3-5-haiku-20241022",
                            "claude-3-5-sonnet-20241022",
                            "claude-3-opus-20240229",
                        ],
                    )
        except asyncio.TimeoutError:
            return HealthCheckResult(
                healthy=False,
                message=f"Health check timeout (>{self.config.timeout_s}s)",
                latency_ms=self.config.timeout_s * 1000,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Health check failed: {e}",
                latency_ms=0.0,
            )

    async def invoke(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        """Call Anthropic Claude API."""
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout_s),
            ) as resp:
                if resp.status not in [200, 201]:
                    raise Exception(f"Anthropic error: {resp.status}")
                data = await resp.json()

        # Extract response
        try:
            content = data["content"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise Exception(f"Malformed Anthropic response: {e}") from e

        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens

        # Cost estimation (Claude 3.5 pricing as of 2024)
        # Haiku: $0.80/M input, $4.00/M output
        # Sonnet: $3.00/M input, $15.00/M output
        # Opus: $15.00/M input, $75.00/M output
        cost = 0.0
        if "haiku" in model.lower():
            cost = (input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000
        elif "sonnet" in model.lower():
            cost = (input_tokens * 3.00 + output_tokens * 15.00) / 1_000_000
        elif "opus" in model.lower():
            cost = (input_tokens * 15.00 + output_tokens * 75.00) / 1_000_000
        else:
            cost = (input_tokens * 3.00 + output_tokens * 15.00) / 1_000_000  # Default to Sonnet

        return ModelResponse(
            content=content,
            model=model,
            usage_tokens=total_tokens,
            cost_usd=cost,
        )

    async def get_default_model(self) -> str:
        return "claude-3-5-sonnet-20241022"


class GeminiProvider(ModelProvider):
    """Google Gemini (gemini-pro, gemini-1.5 via Google Generative AI API) — ADR-0377 Phase 3."""

    async def check_availability(self, model: str) -> bool:
        """Check if model exists in Gemini."""
        valid_models = [
            "gemini-pro",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-2.0-flash",
        ]
        return model in valid_models

    async def health_check(self) -> HealthCheckResult:
        """Health check for Google Generative AI API (ADR-0643)."""
        api_key = self.config.api_key or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return HealthCheckResult(
                healthy=False,
                message="Google API key not configured (GOOGLE_API_KEY env var)",
                latency_ms=0.0,
            )

        try:
            start = time.time()
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout_s),
                ) as resp:
                    latency_ms = (time.time() - start) * 1000

                    if resp.status != 200:
                        return HealthCheckResult(
                            healthy=False,
                            message=f"API returned {resp.status}",
                            latency_ms=latency_ms,
                        )

                    data = await resp.json()
                    models = [m.get("name", "").split("/")[-1] for m in data.get("models", [])]
                    return HealthCheckResult(
                        healthy=True,
                        message="Google Generative AI API is healthy",
                        latency_ms=latency_ms,
                        available_models=models,
                    )
        except asyncio.TimeoutError:
            return HealthCheckResult(
                healthy=False,
                message=f"Health check timeout (>{self.config.timeout_s}s)",
                latency_ms=self.config.timeout_s * 1000,
            )
        except Exception as e:
            return HealthCheckResult(
                healthy=False,
                message=f"Health check failed: {e}",
                latency_ms=0.0,
            )

    async def invoke(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        """Call Google Generative AI API."""
        api_key = self.config.api_key or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise Exception("Google API key not configured")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        # Convert messages to Gemini format (simpler than Claude)
        # For now, use only the last user message
        last_user_msg = None
        for msg in reversed(messages):
            if msg.get("role") == "user":
                last_user_msg = msg.get("content")
                break

        if not last_user_msg:
            raise Exception("No user message found")

        payload = {
            "contents": [{"parts": [{"text": last_user_msg}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }

        headers = {"Content-Type": "application/json"}

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self.config.timeout_s),
            ) as resp:
                if resp.status not in [200]:
                    error_text = await resp.text()
                    raise Exception(f"Gemini error: {resp.status} - {error_text}")
                data = await resp.json()

        # Extract response
        try:
            content = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise Exception(f"Malformed Gemini response: {e}") from e

        # Gemini API doesn't always return token count; estimate
        usage_metadata = data.get("usageMetadata", {})
        input_tokens = usage_metadata.get("promptTokenCount", len(last_user_msg) // 4)
        output_tokens = usage_metadata.get("candidatesTokenCount", len(content) // 4)
        total_tokens = input_tokens + output_tokens

        # Cost estimation (Gemini 1.5 pricing as of 2024)
        # Flash: $0.075/M input, $0.30/M output
        # Pro: $1.50/M input, $6.00/M output
        cost = 0.0
        if "flash" in model.lower():
            cost = (input_tokens * 0.075 + output_tokens * 0.30) / 1_000_000
        elif "pro" in model.lower():
            cost = (input_tokens * 1.50 + output_tokens * 6.00) / 1_000_000
        else:
            cost = (input_tokens * 1.50 + output_tokens * 6.00) / 1_000_000  # Default to Pro

        return ModelResponse(
            content=content,
            model=model,
            usage_tokens=total_tokens,
            cost_usd=cost,
        )

    async def get_default_model(self) -> str:
        return "gemini-1.5-pro"
