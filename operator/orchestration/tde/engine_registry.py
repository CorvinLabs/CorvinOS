"""ADR-0214: Engine Registry (Phase 2).

Central registry of all Agentic Compute Engines (TDE, ACS, Claude-Code).
Registers REAL engines (tde_engine.py) — no fake-success placeholders.

Naming note: operator/bridges/shared/engine_registry.py is a DIFFERENT,
lower-level registry (concrete WorkerEngine builders: claude_code/codex_cli/
opencode binaries). This one maps agentic-compute strategies onto plans.
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
        plan: Any,  # InitialAnalysisRequest or GlobalPlan (ADR-0210)
        context: dict[str, Any],
        **kwargs,
    ) -> dict[str, Any]:
        """Execute plan in this engine's context."""
        ...


class EngineRegistry:
    """Registry of Agentic Compute Engines."""

    def __init__(self, *, real_ipc: bool = False):
        """Initialize with built-in engines.

        Args:
            real_ipc: forwarded to TieredDelegationEngine — True enables real
                subprocess LLM delegation for TDE steps.
        """
        self.engines: Dict[str, EngineInterface] = {}
        self._register_builtin_engines(real_ipc=real_ipc)

    def _register_builtin_engines(self, *, real_ipc: bool = False):
        """Register built-in engines (TDE, ACS, Claude-Code)."""
        from .tde_engine import (
            AcsEngineBridge,
            ClaudeCodeLocalEngine,
            TieredDelegationEngine,
        )

        self.engines["tiered_delegation"] = TieredDelegationEngine(real_ipc=real_ipc)
        self.engines["acs"] = AcsEngineBridge()
        self.engines["claude_code"] = ClaudeCodeLocalEngine()

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


# Global registry singleton
_registry: Optional[EngineRegistry] = None


def get_registry() -> EngineRegistry:
    """Get or create global registry."""
    global _registry
    if _registry is None:
        _registry = EngineRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the singleton (test hook)."""
    global _registry
    _registry = None
