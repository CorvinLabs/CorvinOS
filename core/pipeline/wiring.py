"""
Entry-Point Wiring — ADR-0301

Decorators and factories for wiring DualGatePipeline into 45+ entry points
across all transport layers (Flask, CLI, async, WebSocket, bridge, plugin).

This module provides high-level wiring APIs that adapters use to guard routes.

Fail-closed: any gate failure logs audit and denies the operation.
"""

import asyncio
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
    """
    Get pipeline from FastAPI app.state (fallback if global not set).

    DEPRECATED: Use get_global_pipeline() instead. This function is kept for
    backward compatibility but all current code paths use the global pipeline.
    """
    if app_state is None:
        return get_global_pipeline()
    if not hasattr(app_state, "pipeline"):
        return get_global_pipeline()
    return app_state.pipeline


# ============================================================================
# FastAPI Middleware — Automatic Dual-Gate Protection for All Routes
# ============================================================================


def create_dual_gate_middleware(skip_paths: Optional[list[str]] = None):
    """Create middleware that applies dual-gate pipeline to all Console API requests."""
    if skip_paths is None:
        skip_paths = ['/healthz', '/static/', '/ws-live/', '/.well-known/']

    def should_skip(path: str) -> bool:
        for skip_pattern in skip_paths:
            if path.startswith(skip_pattern):
                return True
        return False

    async def dual_gate_middleware(request, call_next):
        try:
            if should_skip(request.url.path):
                return await call_next(request)

            # Ship-dark: when the DualGatePipeline feature is off (the default),
            # instantiate_pipeline() leaves the pipeline as None. The middleware
            # is registered unconditionally, so it must degrade to a transparent
            # pass-through — the pre-feature code path — rather than crash.
            # "Off must be a quiet path, never an error." Note get_global_pipeline()
            # RAISES RuntimeError on a None pipeline (it treats None as "not
            # initialized"); in the ship-dark world None is the legitimate off
            # state, so that RuntimeError means "feature off" → pass through.
            try:
                pipeline = get_global_pipeline()
            except RuntimeError:
                return await call_next(request)
            if pipeline is None:
                return await call_next(request)

            actor = request.headers.get("X-User-ID", "")
            if not actor:
                # Extract session ID (first 8 chars, with bounds checking)
                sid = request.cookies.get("sid", "unknown")
                actor = sid[:8] if len(sid) >= 8 else sid
            actor = actor or "unknown"

            tenant_id = request.headers.get("X-Tenant-ID", "_default")
            capability = _infer_capability_from_request(request)
            action = _infer_action_from_request(request)
            resource = _infer_resource_from_request(request)

            # Gate 1: Capability Check (fail-closed)
            try:
                has_cap = pipeline.check_capability(
                    actor=actor,
                    capability=capability,
                    tenant_id=tenant_id,
                )
                if not has_cap:
                    logger.warning(
                        f"Capability denied: {actor} lacks {capability} for {action}"
                    )
                    # Record denial in audit
                    try:
                        pipeline.record_audit(
                            event_type="capability_denied",
                            actor=actor,
                            action=action,
                            resource=resource,
                            result="failure",
                            tenant_id=tenant_id,
                            details={
                                "reason": "capability_denied",
                                "method": request.method,
                                "path": request.url.path,
                            },
                        )
                    except Exception as audit_err:
                        logger.exception(f"Failed to audit denial: {audit_err}")

                    from starlette.responses import JSONResponse
                    return JSONResponse({"error": "Unauthorized"}, status_code=403)
            except Exception as gate_err:
                logger.exception(f"Capability gate error: {gate_err}")
                from starlette.responses import JSONResponse
                return JSONResponse({"error": "Unauthorized"}, status_code=403)

            # Proceed to route handler
            response = await call_next(request)

            # Post-Audit: Record successful access (fail-closed on audit error)
            try:
                pipeline.record_audit(
                    event_type="route_access",
                    actor=actor,
                    action=action,
                    resource=resource,
                    result="success" if 200 <= response.status_code < 300 else "failure",
                    tenant_id=tenant_id,
                    details={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": response.status_code,
                    },
                )
            except Exception as audit_err:
                logger.exception(f"Audit recording FAILED: {audit_err}")
                # Fail-closed: deny access if audit fails (GDPR Art. 30, 32)
                from starlette.responses import JSONResponse
                return JSONResponse({"error": "Audit system error"}, status_code=500)

            return response

        except Exception as e:
            logger.exception(f"Middleware error: {e}")
            from starlette.responses import JSONResponse
            return JSONResponse({"error": "Server error"}, status_code=500)

    return dual_gate_middleware


def _infer_capability_from_request(request) -> str:
    """
    Infer capability from HTTP method + path.

    Uses prefix-based matching (not substring) to avoid over-granting capabilities.
    Falls back to generic read/write only for unclassified endpoints.

    Fail-closed: if path is ambiguous, request generic capability instead of
    guessing a specialized one.
    """
    method = request.method
    path = request.url.path.lower()

    # Path segment matching (prefix-based, not substring)
    path_segments = path.strip("/").split("/")
    first_segment = path_segments[0] if path_segments else ""

    if method == "GET":
        # Read operations - check path prefix (first segment) for specificity
        if first_segment == "audit":
            return "read_audit_log"
        elif first_segment == "chat":
            return "read_chat_sessions"
        elif first_segment == "tasks":
            return "read_tasks"
        elif first_segment == "plugins":
            return "read_plugins"
        else:
            # Generic read - fail-closed fallback
            return "read"
    elif method in ("POST", "PUT"):
        # Write operations - check path prefix
        if first_segment == "chat":
            return "write_chat_sessions"
        elif first_segment == "tasks":
            return "write_tasks"
        elif first_segment == "plugins":
            return "write_plugins"
        else:
            # Generic write - fail-closed fallback
            return "write"
    elif method == "DELETE":
        return "delete"
    else:
        # Unknown method - fail-closed (deny by default)
        return "unknown"


def _infer_action_from_request(request) -> str:
    """Infer action from HTTP method + path."""
    method = request.method
    path = request.url.path.split("?")[0]
    last_segment = path.rstrip("/").split("/")[-1] or "root"
    return f"{method.lower()}_{last_segment}"


def _infer_resource_from_request(request) -> str:
    """Infer resource type from path."""
    path = request.url.path.split("?")[0]
    if "chat" in path:
        return "chat_session"
    elif "audit" in path:
        return "audit_log"
    elif "tasks" in path:
        return "task"
    elif "plugins" in path:
        return "plugin"
    else:
        return "resource"


def _normalize_cli_actor(actor: str) -> str:
    """
    Normalize CLI actor name for consistency with capability checker.

    CLI actors come from os.getenv("USER"), which may be in different formats:
    - Local: "admin"
    - Domain: "admin@company.com"
    - LDAP: "cn=admin,o=company"

    Attempts to detect and preserve the format, with fallback to bare username.
    Note: Capability checker may need to implement its own format normalization
    if multiple formats are expected (e.g., LDAP directory lookup).

    Args:
        actor: Raw CLI user name (from getenv("USER"))

    Returns:
        Normalized actor name safe for capability checking
    """
    if not actor:
        return "unknown"

    # Already in domain format or LDAP format - return as-is
    if "@" in actor or "=" in actor or "," in actor:
        return actor

    # Bare username (most common from getenv("USER"))
    # Leave as-is and let capability checker handle domain resolution
    # This allows the checker to implement site-specific normalization
    # (e.g., add company domain, query LDAP, etc.)
    return actor


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
# Utility Functions
# ============================================================================


def _extract_context_from_starlette_request(
    args: Any,
    kwargs: Any,
    capability: str,
    action: str,
    resource: str,
    resource_extractor: Optional[Callable] = None,
) -> "PipelineContext":
    """
    Extract pipeline context from Starlette/FastAPI request (shared by async/sync wrappers).

    Args:
        args: Positional arguments from wrapper function
        kwargs: Keyword arguments from wrapper function
        capability: Required capability
        action: Action name for audit
        resource: Default resource type
        resource_extractor: Optional function to override resource

    Returns:
        PipelineContext ready for pipeline execution
    """
    from starlette.requests import Request
    from core.pipeline.dual_gate import PipelineContext

    # Extract context from Starlette request (FastAPI compatible)
    request = None
    for arg in args:
        if isinstance(arg, Request):
            request = arg
            break

    if request is None:
        # Fallback: check kwargs
        request = kwargs.get("request")

    actor = "unknown"
    tenant_id = "_default"

    if request:
        # Extract user from request headers or session
        actor = request.headers.get("X-User-ID", "unknown")
        tenant_id = request.headers.get("X-Tenant-ID", "_default")

    resource = resource_extractor() if resource_extractor else resource

    ctx = PipelineContext(
        actor=actor,
        capability=capability,
        action=action,
        resource=resource,
        tenant_id=tenant_id,
        details={
            "method": request.method if request else "unknown",
            "path": request.url.path if request else "unknown",
        },
    )
    return ctx


# ============================================================================
# CLI Command Wiring
# ============================================================================


def fastapi_route_guarded(
    capability: str,
    action: str,
    resource_extractor: Optional[Callable] = None,
):
    """
    Decorator for FastAPI routes with dual-gate protection.

    Usage:
        @router.get('/chat/sessions')
        @fastapi_route_guarded(
            capability='read_chat_sessions',
            action='list_sessions',
            resource_extractor=lambda: 'chat_sessions'
        )
        def list_sessions(request: Request):
            return {...}

    Args:
        capability: Required capability (e.g., 'read_users', 'write_config')
        action: Action name for audit log
        resource_extractor: Optional function to extract resource ID from scope/request
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                pipeline = get_global_pipeline()

                # Extract context from Starlette request using shared helper
                ctx = _extract_context_from_starlette_request(
                    args, kwargs, capability, action, "unknown", resource_extractor
                )

                return await pipeline.execute_guarded_async(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"FastAPI async route guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        # For async functions, use the async wrapper
        if asyncio.iscoroutinefunction(func):
            return async_wrapper

        # For sync functions, create and return a sync wrapper
        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                pipeline = get_global_pipeline()

                # Extract context from Starlette request using shared helper
                ctx = _extract_context_from_starlette_request(
                    args, kwargs, capability, action, "unknown", resource_extractor
                )

                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                logger.exception(
                    f"FastAPI sync route guard failed: {capability} on {action}. Error: {e}"
                )
                raise

        return sync_wrapper

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
                # Note: actor format may differ from web auth (local vs LDAP)
                # Capability checker should handle both formats
                actor = os.getenv("USER", "cli_user")
                actor = _normalize_cli_actor(actor)  # Normalize for consistency
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
