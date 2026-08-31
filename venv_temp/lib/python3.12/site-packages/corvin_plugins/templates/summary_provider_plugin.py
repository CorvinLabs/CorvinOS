"""Template: custom SummaryProvider plugin (ADR-0033).

Copy this file, rename the class, fill in the TODOs, then install via:
    spec.plugins.installed:
      - id: "com.example.my-summary"
        class_path: "my_package.my_module:MySummaryPlugin"
        config:
          endpoint: "https://llm.internal/v1/chat"
          model: "llama-3-70b-instruct"
          # API key goes in vault, never in config block
"""
from __future__ import annotations

from corvin_plugins.protocol import (  # noqa: F401 - CorvinPlugin is the
    CorvinPlugin,  # structural protocol this class satisfies; kept for reference.
    HealthStatus,
    PluginContext,
)


class MySummaryPlugin:
    """Replace with your actual class name."""

    plugin_id    = "com.example.my-summary"
    plugin_type  = "summary_provider"
    version      = "1.0.0"
    display_name = "My Summary Provider"

    def __init__(self) -> None:
        self._config: dict = {}

    # ── CorvinPlugin lifecycle ───────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        # TODO: initialize your LLM client (read API key from vault)
        if ctx.summary_registry is not None:
            ctx.summary_registry.set_active(self)

    def on_unload(self) -> None:
        # TODO: close connections
        pass

    def health_check(self) -> HealthStatus:
        # TODO: probe your LLM endpoint
        return HealthStatus(ok=True, message="ok")

    # ── SummaryProvider capability ────────────────────────────────────────────

    def summarize(
        self,
        text: str,
        *,
        lang: str = "de",
        max_chars: int = 400,
        tenant_id: str = "_default",
    ) -> str:
        # Must NOT raise — return a truncated fallback on any error.
        # Must NOT import the anthropic SDK directly.
        # Output must be plain prose (no Markdown, no code tokens).
        try:
            # TODO: call your LLM endpoint
            raise NotImplementedError
        except Exception:
            return text[:max_chars]
