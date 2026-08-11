"""Async task decorators."""

import functools
from typing import Callable, Any, Optional

from core.pipeline import DualGatePipeline, PipelineContext


def async_requires_capability(
    capability: str,
    get_resource: Optional[Callable[..., str]] = None,
):
    """
    Async task decorator: dual-gate (capability + audit).

    Usage:
        @async_requires_capability('write', get_resource=lambda: 'cache')
        async def refresh_cache():
            await asyncio.sleep(1)
            return 'refreshed'

    Args:
        capability: Required capability
        get_resource: Function to extract resource identifier
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                # For async, actor is typically "system" (background task)
                actor = "system"
                tenant_id = "default"
                resource = get_resource() if get_resource else func.__name__

                # Create context
                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=func.__name__,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "async", "task": func.__name__},
                )

                # Get pipeline (must be injected)
                from core.pipeline import get_pipeline_from_context

                pipeline = get_pipeline_from_context()
                if not pipeline:
                    raise RuntimeError("Pipeline not configured for async")

                # Execute through pipeline (async variant)
                return await pipeline.execute_guarded_async(
                    ctx, func, *args, **kwargs
                )

            except Exception as e:
                # Pipeline will have audited gate failures
                raise

        return wrapper

    return decorator
