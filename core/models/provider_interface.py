"""
Model Provider Interface (ADR-0607)
Unified contract for OpenAI, Ollama, OpenRouter.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, List


@dataclass(frozen=True)
class ModelProviderConfig:
    """Provider configuration (immutable, frozen)."""
    name: str  # "openai", "ollama", "openrouter"
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    timeout_s: int = 30


@dataclass
class ModelResponse:
    """Response from model (immutable)."""
    content: str
    model: str
    usage_tokens: int
    cost_usd: Optional[float] = None


class ModelProvider(ABC):
    """Abstract base for model providers."""

    def __init__(self, config: ModelProviderConfig):
        self.config = config

    @abstractmethod
    async def check_availability(self, model: str) -> bool:
        """Check if model is available."""
        pass

    @abstractmethod
    async def invoke(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        """Invoke model with messages."""
        pass

    @abstractmethod
    async def get_default_model(self) -> str:
        """Return provider's default model."""
        pass

    @property
    def name(self) -> str:
        return self.config.name
