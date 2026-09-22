"""Plugin Loader — Dual-running infrastructure for console plugins.

Loads marketplace plugins with console adapters, falling back to console routes
when plugins are unavailable. Enables canary deployment and zero-downtime migration.

ADR-0039 (Workflow Builder) — Phase 6: Plugin Integration.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter

_log = logging.getLogger(__name__)


class PluginLoaderError(Exception):
    """Plugin loading failed."""
    pass


class PluginLoader:
    """Loads marketplace plugins with console adapters; falls back to console routes."""

    def __init__(self):
        """Initialize plugin loader."""
        self._plugin_cache: dict[str, Any] = {}

    def load_workflows_plugin(
        self,
        *,
        force_fallback: bool = False,
        session_auth_module: Optional[Any] = None,
        audit_backend: Optional[Any] = None,
        storage_backend: Optional[Any] = None,
        license_backend: Optional[Any] = None,
        prompt_guard: Optional[Any] = None,
        scheduler_backend: Optional[Any] = None,
        forge_paths: Optional[Any] = None,
        spawn_gates: Optional[Any] = None,
    ) -> APIRouter:
        """Load WorkflowsPlugin with console adapters; fallback to console routes.

        Args:
            force_fallback: Skip plugin attempt, use console routes only.
            session_auth_module: Console session auth module (for adapter injection).
            audit_backend: Console audit backend (for adapter injection).
            storage_backend: Console storage backend (for adapter injection).
            license_backend: Console license backend (for adapter injection).
            prompt_guard: Console prompt guard (for adapter injection).
            scheduler_backend: Console scheduler backend (for adapter injection).
            forge_paths: Forge paths resolver (for adapter injection).
            spawn_gates: Spawn gates handler (for adapter injection).

        Returns:
            APIRouter with workflow routes (plugin or console fallback).

        Raises:
            PluginLoaderError: Both plugin and fallback failed (should never happen).
        """
        cache_key = "workflows_router"
        if cache_key in self._plugin_cache:
            return self._plugin_cache[cache_key]

        router: Optional[APIRouter] = None

        if not force_fallback:
            try:
                router = self._try_load_marketplace_plugin(
                    session_auth_module=session_auth_module,
                    audit_backend=audit_backend,
                    storage_backend=storage_backend,
                    license_backend=license_backend,
                    prompt_guard=prompt_guard,
                    scheduler_backend=scheduler_backend,
                    forge_paths=forge_paths,
                    spawn_gates=spawn_gates,
                )
                _log.info("✓ Workflows plugin loaded from marketplace")
            except Exception as e:
                _log.warning(f"Workflows plugin load failed ({type(e).__name__}); fallback to console routes: {e}")

        if router is None:
            try:
                router = self._fallback_console_routes()
                _log.info("✓ Workflows routes loaded from console (fallback)")
            except Exception as e:
                msg = f"Both plugin and console routes failed; cannot proceed: {e}"
                _log.error(msg)
                raise PluginLoaderError(msg) from e

        self._plugin_cache[cache_key] = router
        return router

    def _try_load_marketplace_plugin(
        self,
        *,
        session_auth_module: Optional[Any] = None,
        audit_backend: Optional[Any] = None,
        storage_backend: Optional[Any] = None,
        license_backend: Optional[Any] = None,
        prompt_guard: Optional[Any] = None,
        scheduler_backend: Optional[Any] = None,
        forge_paths: Optional[Any] = None,
        spawn_gates: Optional[Any] = None,
    ) -> APIRouter:
        """Attempt to load WorkflowsPlugin from marketplace.

        Raises:
            ImportError: Plugin not available.
            Exception: Plugin initialization failed.
        """
        try:
            from corvin_marketplace.plugins.buildin.orchestration.workflows.plugin_workflows.plugin import (
                WorkflowsPlugin,
            )
            from corvin_marketplace.plugins.buildin.orchestration.workflows.plugin_workflows.adapters import (
                ConsoleSessionBackend,
            )
        except ImportError as e:
            raise ImportError(f"Workflows plugin not available in marketplace: {e}") from e

        # Build adapters from console dependencies
        if session_auth_module is None:
            from .. import auth as session_auth
            session_auth_module = session_auth

        # Wrap console session auth in plugin's adapter
        session_backend = ConsoleSessionBackend(session_auth_module) if session_auth_module else None

        # Initialize plugin with injected adapters
        plugin = WorkflowsPlugin(
            session_backend=session_backend,
            audit_backend=audit_backend,
            storage_backend=storage_backend,
            license_backend=license_backend,
            prompt_guard=prompt_guard,
            scheduler_backend=scheduler_backend,
            forge_paths=forge_paths,
            spawn_gates=spawn_gates,
        )

        return plugin.router

    def _fallback_console_routes(self) -> APIRouter:
        """Fallback: load workflow routes from console.

        Returns:
            APIRouter with console workflow routes.

        Raises:
            ImportError: Console routes module not available.
        """
        from . import workflows as workflows_console

        if not hasattr(workflows_console, "router"):
            raise ImportError("Console workflows.router not found")

        return workflows_console.router


# ────────────────────────────────────────────────────────────────────────────
# Global singleton
# ────────────────────────────────────────────────────────────────────────────

_loader = PluginLoader()


def get_workflows_router(**adapter_kwargs: Any) -> APIRouter:
    """Get workflows router (plugin or fallback), with adapter injection.

    Convenience function for app.py.
    """
    return _loader.load_workflows_plugin(**adapter_kwargs)
