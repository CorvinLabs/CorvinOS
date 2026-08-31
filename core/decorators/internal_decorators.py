"""Internal function decorators."""

import functools
from typing import Callable, Any, Optional

from core.pipeline import DualGatePipeline, PipelineContext


def internal_requires_capability(
    capability: str,
    resource: str = "internal",
):
    """
    Internal function decorator: dual-gate (capability + audit).

    Usage:
        @internal_requires_capability('write', resource='config:theme')
        def update_theme(theme: str):
            return f'theme set to {theme}'

    Args:
        capability: Required capability
        resource: Resource identifier
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                from core.pipeline import get_current_actor, get_current_tenant_id

                # Use current context (set by outer request/transport)
                actor = get_current_actor() or "internal"
                tenant_id = get_current_tenant_id() or "default"

                # Create context
                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=func.__name__,
                    resource=resource,
                    tenant_id=tenant_id,
                    details={"transport": "internal", "function": func.__name__},
                )

                # Get pipeline
                from core.pipeline import get_pipeline_from_context

                pipeline = get_pipeline_from_context()
                if not pipeline:
                    raise RuntimeError("Pipeline not configured for internal")

                # Execute through pipeline
                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                # Pipeline will have audited gate failures
                raise

        return wrapper

    return decorator
