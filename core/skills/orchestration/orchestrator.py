"""Plugin Orchestrator: Selection Methods + Audit Integration (ADR-0612)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from core.plugins.corvin_plugins.capability_registry import get_registry
from core.plugins.corvin_plugins.manifest_capabilities import Capability

log = logging.getLogger(__name__)


class SelectionMethod(str, Enum):
    """Plugin selection method."""
    DETERMINISTIC = "deterministic"
    LLM_GUIDED = "llm_guided"
    LEARNED = "learned"


@dataclass
class OrchestrationResult:
    """Result of plugin invocation via orchestration."""

    invocation_id: str
    status: str  # "ok" | "failed"
    plugin_id: Optional[str] = None
    capability_id: Optional[str] = None
    selection_method: Optional[str] = None
    latency_ms: int = 0
    output: Any = None
    error: Optional[str] = None


class PluginOrchestrator:
    """Unified plugin orchestration for ACP Skills."""

    def __init__(self):
        """Initialize orchestrator."""
        self.registry = get_registry()

    def invoke_with_orchestration(
        self,
        skill_id: str,
        capability_id: str,
        input_data: dict[str, Any],
        selection_method: str = "deterministic",
        allowed_plugins: Optional[list[str]] = None,
        **kwargs,
    ) -> OrchestrationResult:
        """Main entry point for skill→plugin invocation with full tracking."""
        invocation_id = f"inv_{uuid.uuid4().hex[:12]}"

        try:
            # 1. Select plugin
            plugin_id, capability = self._select_plugin(
                capability_id=capability_id,
                allowed_plugins=allowed_plugins,
                selection_method=selection_method,
            )

            if not plugin_id:
                raise ValueError(f"No plugin selected for capability {capability_id}")

            # 2. Invoke plugin (mock)
            result = {
                "invocation_id": invocation_id,
                "success": True,
                "data": input_data,
                "latency_ms": 42,
            }

            # 3. Audit event
            log.debug(f"Audit: skill_invokes_capability {skill_id} → {plugin_id}:{capability_id}")

            # 4. Learning event
            log.debug(f"Learning: {skill_id}:{plugin_id} outcome=success latency_ms={result['latency_ms']}")

            return OrchestrationResult(
                invocation_id=invocation_id,
                status="ok",
                plugin_id=plugin_id,
                capability_id=capability_id,
                selection_method=selection_method,
                latency_ms=result["latency_ms"],
                output=result,
            )

        except Exception as e:
            log.error(f"Orchestration failed: {e}")
            return OrchestrationResult(
                invocation_id=invocation_id,
                status="failed",
                error=str(e),
                selection_method=selection_method,
            )

    def _select_plugin(
        self,
        capability_id: str,
        allowed_plugins: Optional[list[str]] = None,
        selection_method: str = "deterministic",
    ) -> tuple[Optional[str], Optional[Capability]]:
        """Select a plugin for a capability."""
        if selection_method == "deterministic":
            return self._select_deterministic(capability_id, allowed_plugins)
        elif selection_method == "llm_guided":
            return self._select_llm_guided(capability_id, allowed_plugins)
        elif selection_method == "learned":
            return self._select_learned(capability_id, allowed_plugins)
        else:
            raise ValueError(f"Unknown selection method: {selection_method}")

    def _select_deterministic(
        self,
        capability_id: str,
        allowed_plugins: Optional[list[str]] = None,
    ) -> tuple[Optional[str], Optional[Capability]]:
        """Rule-based selection: pick first allowed plugin."""
        if not allowed_plugins:
            return None, None

        for plugin_id in allowed_plugins:
            cap = self.registry.get_capability(plugin_id, capability_id)
            if cap:
                return plugin_id, cap

        return None, None

    def _select_llm_guided(
        self,
        capability_id: str,
        allowed_plugins: Optional[list[str]] = None,
    ) -> tuple[Optional[str], Optional[Capability]]:
        """LLM picks from whitelist (mock: just delegates to deterministic)."""
        return self._select_deterministic(capability_id, allowed_plugins)

    def _select_learned(
        self,
        capability_id: str,
        allowed_plugins: Optional[list[str]] = None,
    ) -> tuple[Optional[str], Optional[Capability]]:
        """Model-based selection (mock: just delegates to deterministic)."""
        return self._select_deterministic(capability_id, allowed_plugins)
