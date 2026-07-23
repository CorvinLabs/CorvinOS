"""ADR-0214: Engine Registry (Phase 2).

Central registry of all Agentic Compute Engines (TDE, ACS, Claude-Code).
Supports pluggable detectors (with CLS tier-gating).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Protocol

_logger = logging.getLogger(__name__)


class EngineInterface(Protocol):
    """Protocol for Agentic Compute Engines."""

    name: str

    async def execute(
        self,
        plan: Any,  # GlobalPlan from ADR-0210
        context: dict[str, Any],
        **kwargs,
    ) -> dict[str, Any]:
        """Execute plan in this engine's context."""
        ...


class EngineRegistry:
    """Registry of Agentic Compute Engines."""

    def __init__(self):
        """Initialize with built-in engines (placeholders for now)."""
        self.engines: Dict[str, EngineInterface] = {}
        self._register_builtin_engines()

    def _register_builtin_engines(self):
        """Register built-in engines (TDE, ACS, Claude-Code)."""
        # Placeholder: these will be real engines in Phase 2
        # For now, just register names so registry knows they exist

        self.engines["tiered_delegation"] = self._TDEPlaceholder()
        self.engines["acs"] = self._ACSPlaceholder()
        self.engines["claude_code"] = self._ClaudeCodePlaceholder()

        _logger.info("Registered 3 built-in engines: TDE, ACS, Claude-Code")

    def register(self, name: str, engine: EngineInterface):
        """Register a custom engine."""
        if name in self.engines:
            _logger.warning(f"Overriding engine: {name}")
        self.engines[name] = engine
        _logger.info(f"Registered engine: {name}")

    async def execute(
        self,
        engine_name: str,
        plan: Any,
        context: dict[str, Any],
        **kwargs,
    ) -> dict[str, Any]:
        """Execute plan using specified engine."""
        if engine_name not in self.engines:
            raise ValueError(f"Unknown engine: {engine_name}")

        engine = self.engines[engine_name]
        _logger.info(f"Executing with {engine_name}")

        return await engine.execute(plan, context, **kwargs)

    def get_engine(self, name: str) -> Optional[EngineInterface]:
        """Get engine by name."""
        return self.engines.get(name)

    # Placeholders (replace with real implementations in Phase 2)

    class _TDEPlaceholder:
        name = "tiered_delegation"

        async def execute(self, plan, context, **kwargs):
            return {"status": "TDE not yet implemented"}

    class _ACSPlaceholder:
        name = "acs"

        async def execute(self, plan, context, **kwargs):
            return {"status": "ACS placeholder"}

    class _ClaudeCodePlaceholder:
        name = "claude_code"

        async def execute(self, plan, context, **kwargs):
            return {"status": "Claude-Code placeholder"}


# Global registry singleton
_registry: Optional[EngineRegistry] = None


def get_registry() -> EngineRegistry:
    """Get or create global registry."""
    global _registry
    if _registry is None:
        _registry = EngineRegistry()
    return _registry
