"""Template: custom RecallBackend plugin (ADR-0033).

Copy this file, rename the class, fill in the TODOs, then install via:
    spec.plugins.installed:
      - id: "com.example.my-recall"
        class_path: "my_package.my_module:MyRecallPlugin"
        config:
          dsn: "postgresql://..."   # connection string — DB password goes in vault
"""
from __future__ import annotations

from corvin_plugins.protocol import (  # noqa: F401 - CorvinPlugin is the
    CorvinPlugin,  # structural protocol this class satisfies; kept for reference.
    HealthStatus,
    PluginContext,
)


class MyRecallPlugin:
    """Replace with your actual class name."""

    plugin_id    = "com.example.my-recall"
    plugin_type  = "recall_backend"
    version      = "1.0.0"
    display_name = "My Recall Backend"

    def __init__(self) -> None:
        self._config: dict = {}

    # ── CorvinPlugin lifecycle ───────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        # TODO: open DB connection, run migrations
        if ctx.recall_registry is not None:
            ctx.recall_registry.set_active(self)

    def on_unload(self) -> None:
        # TODO: close connections
        pass

    def health_check(self) -> HealthStatus:
        # TODO: ping DB
        return HealthStatus(ok=True, message="ok")

    # ── RecallBackend capability ──────────────────────────────────────────────

    def index_turn(
        self,
        channel: str,
        chat_key: str,
        text: str,           # already PII-redacted by caller — do NOT re-introduce PII
        *,
        tenant_id: str = "_default",
    ) -> None:
        # TODO: insert into your store
        pass

    def search(
        self,
        query: str,
        *,
        channel: str = "",
        limit: int = 10,
        tenant_id: str = "_default",
    ) -> list[dict]:
        # TODO: full-text or semantic search
        # Return list of {"channel": str, "chat_key": str, "text": str, "ts": float}
        return []

    def forget(
        self,
        channel: str,
        chat_key: str,
        *,
        tenant_id: str = "_default",
    ) -> int:
        # TODO: delete rows; return count deleted (for audit)
        return 0
