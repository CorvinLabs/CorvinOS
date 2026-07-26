"""Template: custom RouterBackend plugin (ADR-0033).

Copy this file, rename the class, fill in the TODOs, then install via:
    spec.plugins.installed:
      - id: "com.example.my-router"
        class_path: "my_package.my_module:MyRouterPlugin"
        config:
          # e.g. LDAP group → persona mapping, custom keyword list, etc.
          dept_map:
            engineering: "coder"
            sales: "research"
"""
from __future__ import annotations

from corvin_plugins.protocol import (  # noqa: F401 - CorvinPlugin is the
    CorvinPlugin,  # structural protocol this class satisfies; kept for reference.
    HealthStatus,
    PluginContext,
)


class MyRouterPlugin:
    """Replace with your actual class name."""

    plugin_id    = "com.example.my-router"
    plugin_type  = "router_backend"
    version      = "1.0.0"
    display_name = "My Router Backend"

    def __init__(self) -> None:
        self._config: dict = {}

    # ── CorvinPlugin lifecycle ───────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        if ctx.router_registry is not None:
            ctx.router_registry.set_active(self)

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, message="ok")

    # ── RouterBackend capability ──────────────────────────────────────────────

    def route(
        self,
        text: str,
        personas: list[dict],
        *,
        min_confidence: float = 0.5,
        tenant_id: str = "_default",
    ) -> dict | None:
        # Must NOT raise — return None on no-match or any error.
        # Return: {"persona": "<name>", "confidence": <0.0-1.0>, "why": "<reason>"}
        try:
            # TODO: implement your routing logic
            # Example: LDAP group lookup, custom keyword patterns, embedding model, …
            raise NotImplementedError
        except Exception:
            return None
