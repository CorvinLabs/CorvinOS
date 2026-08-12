"""
Entry-Point Wiring — ADR-0301

Decorators and factories for wiring DualGatePipeline into 45+ entry points
across all transport layers (Flask, CLI, async, WebSocket, bridge, plugin).

This module provides high-level wiring APIs that adapters use to guard routes.

Fail-closed: any gate failure logs audit and denies the operation.
"""

import functools
import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Global pipeline instance (set during bootstrap)
_GLOBAL_PIPELINE: Optional[Any] = None


def set_global_pipeline(pipeline: Any) -> None:
    """Set the global DualGatePipeline instance (called by bootstrap)."""
    global _GLOBAL_PIPELINE
    _GLOBAL_PIPELINE = pipeline
    logger.info("Global pipeline instance set")


def get_global_pipeline() -> Any:
    """Get the global DualGatePipeline instance (used by all adapters)."""
    global _GLOBAL_PIPELINE
    if _GLOBAL_PIPELINE is None:
        raise RuntimeError(
            "DualGatePipeline not initialized. "
            "Did bootstrap_pipeline() run in app startup?"
        )
    return _GLOBAL_PIPELINE


def get_pipeline_from_app(app_state: Any) -> Any:
    """Get pipeline from FastAPI app.state (fallback if global not set)."""
    if app_state is None:
        return get_global_pipeline()
    if not hasattr(app_state, "pipeline"):
        return get_global_pipeline()
    return app_state.pipeline


# ============================================================================
# Flask Route Wiring
# ============================================================================


def flask_route_guarded(
    capability: str,
    action: str,
    resource_extractor: Optional[Callable] = None,
):
    """
    Decorator for Flask routes with dual-gate protection.

    Usage:
        @bp.route('/api/users', methods=['GET'])
        @flask_route_guarded(
            capability='read_users',
            action='list_users',
            resource_extractor=lambda: 'users'
        )
        def list_users():
            return {...}

    Args:
        capability: Required capability (e.g., 'read_users', 'write_config')
        action: Action name for audit log
        resource_extractor: Optional function to extract resource ID from request
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                from flask import request, g

                pipeline = get_global_pipeline()

                # Extract context from Flask g (set by auth middleware)
                actor = getattr(g, "user_id", "unknown")
                tenant_id = getattr(g, "tenant_id", "_default")
                resource = resource_extractor() if resource_extractor else "unknown"

                from core.pipeline.dual_gate import PipelineContext

                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=action,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={
                        "method": request.method,
                        "path": request.path,
                    },
                )

                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"Flask route guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        return wrapper

    return decorator


# ============================================================================
# CLI Command Wiring
# ============================================================================


def cli_command_guarded(
    capability: str,
    action: str,
    resource: str = "cli",
):
    """
    Decorator for CLI commands with dual-gate protection.

    Usage:
        @cli.command()
        @cli_command_guarded(
            capability='admin',
            action='audit_verify',
            resource='audit'
        )
        def audit_verify():
            return {...}

    Args:
        capability: Required capability (e.g., 'admin', 'read_config')
        action: Action name for audit log
        resource: Resource being accessed
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                import os

                pipeline = get_global_pipeline()

                # Extract context from environment (CLI is local)
                actor = os.getenv("USER", "cli_user")
                tenant_id = "_default"

                from core.pipeline.dual_gate import PipelineContext

                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=action,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "cli"},
                )

                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"CLI command guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        return wrapper

    return decorator


# ============================================================================
# Async Task Wiring
# ============================================================================


def async_task_guarded(
    capability: str,
    action: str,
    resource: str = "task",
):
    """
    Decorator for async background tasks with dual-gate protection.

    Usage:
        @async_task_guarded(
            capability='execute_tasks',
            action='background_sync',
            resource='data_sync'
        )
        async def sync_background_data():
            return {...}

    Args:
        capability: Required capability
        action: Action name for audit log
        resource: Resource being accessed
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                pipeline = get_global_pipeline()

                # Background tasks run as system
                actor = "system"
                tenant_id = "_default"

                from core.pipeline.dual_gate import PipelineContext

                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=action,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "async"},
                )

                return await pipeline.execute_guarded_async(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"Async task guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        return wrapper

    return decorator


# ============================================================================
# WebSocket Handler Wiring
# ============================================================================


def websocket_handler_guarded(
    capability: str,
    action: str,
    resource: str = "websocket",
):
    """
    Decorator for WebSocket handlers with dual-gate protection.

    Usage:
        @bp.websocket('/ws/chat')
        @websocket_handler_guarded(
            capability='read_write_chat',
            action='stream_chat_events'
        )
        async def chat_stream(ws):
            return {...}

    Args:
        capability: Required capability
        action: Action name for audit log
        resource: Resource being accessed
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                pipeline = get_global_pipeline()

                # WebSocket context (simplified)
                actor = "websocket_client"
                tenant_id = "_default"

                from core.pipeline.dual_gate import PipelineContext

                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=action,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "websocket"},
                )

                return await pipeline.execute_guarded_async(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"WebSocket guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        return wrapper

    return decorator


# ============================================================================
# Bridge Handler Wiring
# ============================================================================


def bridge_handler_guarded(
    capability: str,
    action: str,
    resource: str = "bridge_message",
):
    """
    Decorator for bridge message handlers with dual-gate protection.

    Usage:
        @bridge_handler_guarded(
            capability='relay_bridge_messages',
            action='process_message'
        )
        def process_bridge_message(msg):
            return {...}

    Args:
        capability: Required capability
        action: Action name for audit log
        resource: Resource being accessed
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                pipeline = get_global_pipeline()

                # Bridge context
                actor = "bridge"
                tenant_id = "_default"

                from core.pipeline.dual_gate import PipelineContext

                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=action,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "bridge"},
                )

                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"Bridge handler guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        return wrapper

    return decorator


# ============================================================================
# Plugin Wiring
# ============================================================================


def plugin_entry_guarded(
    capability: str,
    action: str,
    resource: str = "plugin",
):
    """
    Decorator for plugin entry points with dual-gate protection.

    Usage:
        @plugin_entry_guarded(
            capability='load_plugin',
            action='initialize_plugin'
        )
        def plugin_init():
            return {...}

    Args:
        capability: Required capability
        action: Action name for audit log
        resource: Resource being accessed
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                pipeline = get_global_pipeline()

                # Plugin context
                actor = "plugin_system"
                tenant_id = "_default"

                from core.pipeline.dual_gate import PipelineContext

                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=action,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "plugin"},
                )

                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"Plugin guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        return wrapper

    return decorator
