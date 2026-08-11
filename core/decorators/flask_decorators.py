"""Flask request decorators."""

import functools
from typing import Callable, Any, Optional

from core.pipeline import DualGatePipeline, PipelineContext


def requires_auth_capability(
    capability: str,
    resource_extractor: Optional[Callable[..., str]] = None,
):
    """
    Flask route decorator: dual-gate (capability + audit).

    Usage:
        @bp.route('/api/users/<user_id>')
        @requires_auth_capability('read_users', resource_extractor=lambda: 'users')
        def get_user(user_id):
            return {...}

    Args:
        capability: Required capability
        resource_extractor: Function to extract resource identifier
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                from flask import request, g, current_app
                from core.capabilities import get_registry

                # Extract context
                actor = getattr(g, "user_id", "unknown")
                tenant_id = getattr(g, "tenant_id", "default")
                resource = (
                    resource_extractor() if resource_extractor else request.path
                )

                # Get pipeline from app config
                pipeline = current_app.config.get("pipeline")
                if not pipeline:
                    raise RuntimeError("Pipeline not configured in Flask app")

                # Create context
                ctx = PipelineContext(
                    actor=actor,
                    capability=capability,
                    action=f"{request.method} {request.path}",
                    resource=resource,
                    tenant_id=tenant_id,
                    details={
                        "method": request.method,
                        "path": request.path,
                        "content_type": request.content_type,
                    },
                )

                # Execute through pipeline
                return pipeline.execute_guarded(ctx, func, *args, **kwargs)

            except Exception as e:
                # Pipeline will have audited any gate failures
                raise

        return wrapper

    return decorator


def flask_audit_log(event_type: str):
    """
    Flask route decorator: manual audit logging (no capability gate).

    Usage:
        @bp.route('/health')
        @flask_audit_log('health_check')
        def health():
            return {'status': 'ok'}

    Args:
        event_type: Type of event to log
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                from flask import request, g, current_app

                # Get pipeline
                pipeline = current_app.config.get("pipeline")
                if not pipeline:
                    return func(*args, **kwargs)  # No audit if pipeline missing

                # Log the request
                actor = getattr(g, "user_id", "unknown")
                tenant_id = getattr(g, "tenant_id", "default")

                pipeline.record_audit(
                    event_type=event_type,
                    actor=actor,
                    action=f"{request.method} {request.path}",
                    resource=request.path,
                    result="success",
                    tenant_id=tenant_id,
                    details={
                        "method": request.method,
                        "path": request.path,
                    },
                )

                return func(*args, **kwargs)

            except Exception as e:
                # Log failure
                try:
                    from flask import request, g, current_app

                    pipeline = current_app.config.get("pipeline")
                    if pipeline:
                        actor = getattr(g, "user_id", "unknown")
                        tenant_id = getattr(g, "tenant_id", "default")

                        pipeline.record_audit(
                            event_type=event_type,
                            actor=actor,
                            action=f"{request.method} {request.path}",
                            resource=request.path,
                            result="failure",
                            tenant_id=tenant_id,
                            details={"error": str(e)},
                        )
                except:
                    pass

                raise

        return wrapper

    return decorator
