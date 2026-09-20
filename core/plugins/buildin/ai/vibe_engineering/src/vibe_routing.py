"""Vibe Engineering Router — L5 deterministic + active agentic routing."""
from __future__ import annotations

import json
from typing import Any, Optional
from dataclasses import dataclass

from corvin_plugins import BasePlugin


@dataclass
class VibeRoutingDecision:
    """Result of a Vibe routing decision."""
    target_engine: str
    confidence: float
    mode: str  # "brief" or "active"
    reasoning: str


class VibeRouter(BasePlugin):
    """Routes requests via CEL brief (deterministic) or LLM (active) mode.

    - Brief (deterministic): Uses CEL + learned patterns, fast, always-on
    - Active (LLM): Uses full LLM reasoning + SkillForge, slower, opt-in
    """

    async def initialize(self) -> None:
        """Load Vibe models + context adapters."""
        self.active_mode = self.config.get("active_mode", False)
        self.logger.info(f"Vibe Engineering initialized (active_mode={self.active_mode})")

    async def route_request(self, request: dict[str, Any]) -> VibeRoutingDecision:
        """Classify + route via Vibe (brief or active)."""
        if self.active_mode:
            return await self._route_active(request)
        else:
            return await self._route_brief(request)

    async def _route_brief(self, request: dict[str, Any]) -> VibeRoutingDecision:
        """Deterministic CEL-based routing."""
        # Placeholder: In real implementation, would use CEL + learned patterns
        return VibeRoutingDecision(
            target_engine="opus",
            confidence=0.85,
            mode="brief",
            reasoning="CEL pattern match: request type=task, complexity=medium"
        )

    async def _route_active(self, request: dict[str, Any]) -> VibeRoutingDecision:
        """LLM-powered active routing (full agentic decision-making)."""
        # Placeholder: In real implementation, would call LLM + SkillForge
        return VibeRoutingDecision(
            target_engine="claude-opus-5",
            confidence=0.92,
            mode="active",
            reasoning="LLM analysis: request requires nuanced understanding + tool use"
        )

    def is_enabled(self) -> bool:
        """Check if this plugin is enabled in tenant config."""
        return getattr(self, "_enabled", True)
