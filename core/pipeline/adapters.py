"""
Transport Adapters for Dual-Gate Pipeline — ADR-0301 helper

Auto-wiring for common transports: Flask, CLI, async.
"""

import functools
from typing import Any, Callable, Optional
from flask import request, g

from core.pipeline import DualGatePipeline, PipelineContext


class FlaskAdapter:
    """Adapter for Flask route wiring."""

    def __init__(self, pipeline: DualGatePipeline):
        """Initialize Flask adapter."""
        self.pipeline = pipeline

    def route_guarded(
        self,
        capability: str,
        action: str,
        resource_extractor: Optional[Callable] = None,
    ):
        """
        Decorator for Flask routes requiring dual-gate.

        Args:
            capability: Required capability (e.g., "read", "write", "admin")
            action: Action name (e.g., "GET /users", "POST /settings")
            resource_extractor: Function to extract resource from request

        Usage:
            @bp.route('/users/<user_id>', methods=['GET'])
            @adapter.route_guarded('read_users', 'fetch_user',
                                   resource_extractor=lambda: request.args.get('user_id'))
            def get_user(user_id):
                return {...}
        """

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    # Extract context from Flask g (set by auth middleware)
                    actor = getattr(g, "user_id", "unknown")
                    tenant_id = getattr(g, "tenant_id", "default")
                    resource = (
                        resource_extractor() if resource_extractor else "unknown"
                    )

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

                    return self.pipeline.execute_guarded(ctx, func, *args, **kwargs)

                except Exception as e:
                    # Pipeline will have recorded audit
                    raise

            return wrapper

        return decorator


class CLIAdapter:
    """Adapter for CLI command wiring."""

    def __init__(self, pipeline: DualGatePipeline):
        """Initialize CLI adapter."""
        self.pipeline = pipeline

    def command_guarded(
        self,
        capability: str,
        action: str,
        get_resource: Optional[Callable] = None,
    ):
        """
        Decorator for CLI commands requiring dual-gate.

        Args:
            capability: Required capability
            action: Action name
            get_resource: Function to extract resource from context

        Usage:
            @cli.command()
            @adapter.command_guarded('admin', 'config_set',
                                    get_resource=lambda: 'config')
            @click.option('--key', required=True)
            def set_config(key):
                return {...}
        """

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    import os

                    # Extract context from environment (CLI is local)
                    actor = os.getenv("USER", "cli_user")
                    tenant_id = "default"
                    resource = get_resource() if get_resource else action

                    ctx = PipelineContext(
                        actor=actor,
                        capability=capability,
                        action=action,
                        resource=resource,
                        tenant_id=tenant_id,
                        details={"transport": "cli"},
                    )

                    return self.pipeline.execute_guarded(ctx, func, *args, **kwargs)

                except Exception as e:
                    # Pipeline will have recorded audit
                    raise

            return wrapper

        return decorator


class AsyncAdapter:
    """Adapter for async function wiring."""

    def __init__(self, pipeline: DualGatePipeline):
        """Initialize async adapter."""
        self.pipeline = pipeline

    def task_guarded(
        self,
        capability: str,
        action: str,
        get_resource: Optional[Callable] = None,
    ):
        """
        Decorator for async task functions requiring dual-gate.

        Args:
            capability: Required capability
            action: Action name
            get_resource: Function to extract resource

        Usage:
            @adapter.task_guarded('write', 'background_sync')
            async def sync_data():
                return {...}
        """

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    # Extract context
                    actor = "system"  # Background tasks run as system
                    tenant_id = "default"
                    resource = get_resource() if get_resource else action

                    ctx = PipelineContext(
                        actor=actor,
                        capability=capability,
                        action=action,
                        resource=resource,
                        tenant_id=tenant_id,
                        details={"transport": "async"},
                    )

                    return await self.pipeline.execute_guarded_async(
                        ctx, func, *args, **kwargs
                    )

                except Exception as e:
                    # Pipeline will have recorded audit
                    raise

            return wrapper

        return decorator


class InternalFunctionAdapter:
    """Adapter for internal function wiring."""

    def __init__(self, pipeline: DualGatePipeline):
        """Initialize internal function adapter."""
        self.pipeline = pipeline

    def function_guarded(
        self,
        capability: str,
        action: str,
        resource: str = "internal",
    ):
        """
        Decorator for internal function calls requiring dual-gate.

        Args:
            capability: Required capability
            action: Action name
            resource: Resource being accessed

        Usage:
            @adapter.function_guarded('write', 'update_config', resource='config:theme')
            def update_theme_config(theme: str):
                return {...}
        """

        def decorator(func: Callable) -> Callable:
            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    # Extract context (use current context if available)
                    actor = (
                        self.pipeline.get_actor() or "internal"
                    )  # Fallback to "internal"
                    tenant_id = self.pipeline.get_tenant_id() or "default"

                    ctx = PipelineContext(
                        actor=actor,
                        capability=capability,
                        action=action,
                        resource=resource,
                        tenant_id=tenant_id,
                        details={"transport": "internal"},
                    )

                    return self.pipeline.execute_guarded(ctx, func, *args, **kwargs)

                except Exception as e:
                    # Pipeline will have recorded audit
                    raise

            return wrapper

        return decorator
