"""CLI command decorators."""

import functools
import os
from typing import Callable, Any, Optional

from core.pipeline import DualGatePipeline, PipelineContext


def cli_requires_capability(
    capability: str,
    get_resource: Optional[Callable[..., str]] = None,
):
    """
    CLI command decorator: dual-gate (capability + audit).

    Usage:
        @cli.command()
        @cli_requires_capability('admin', get_resource=lambda: 'config')
        def setup_system():
            return 'setup complete'

    Args:
        capability: Required capability
        get_resource: Function to extract resource identifier
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                # Get pipeline from context (must be set by CLI framework)
                from core.pipeline import _current_actor, _current_tenant_id

                # For CLI, actor is typically the OS user
                actor = os.getenv("USER", "cli_user")
                tenant_id = "default"
                resource = get_resource() if get_resource else func.__name__

                # Create context
                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=func.__name__,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "cli", "command": func.__name__},
                )

                # Get pipeline (must be injected)
                # For now, import at runtime to avoid circular deps
                from core.pipeline import get_pipeline_from_context

                pipeline = get_pipeline_from_context()
                if not pipeline:
                    raise RuntimeError("Pipeline not configured for CLI")

                # Execute through pipeline
                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                # Pipeline will have audited gate failures
                raise

        return wrapper

    return decorator
