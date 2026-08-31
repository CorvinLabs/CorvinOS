"""Flask adapter for security pipeline (Finding #7: async/sync detection)."""

import asyncio
import inspect
import logging
from functools import wraps
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class FlaskSecurityAdapter:
    """Decorator for Flask routes."""

    def __init__(self, pipeline):
        self.pipeline = pipeline

    def require_security(
        self,
        action: str,
        resource: str,
        capability_required: str,
        input_schema: Optional[dict] = None,
    ):
        """Decorator for Flask routes."""

        def decorator(f):
            @wraps(f)
            async def wrapper(*args, **kwargs):
                try:
                    # Simulate request context (real Flask would provide this)
                    actor = kwargs.get('current_user_id', 'anonymous')
                    input_data = kwargs.get('input_data', {})

                    # Run pipeline
                    success, result, context = await self.pipeline.execute_with_security(
                        actor=actor,
                        action=action,
                        resource=resource,
                        capability_required=capability_required,
                        transport='flask_route',
                        input_data=input_data,
                        handler_fn=lambda: self._call_handler(f, *args, **kwargs),
                        input_schema=input_schema,
                    )

                    if not success:
                        return {
                            'error': context.error,
                            'decision_hash': context.decision_record_hash,
                        }, 403

                    # Enrich response
                    if isinstance(result, dict):
                        result['_security'] = {
                            'decision_hash': context.decision_record_hash,
                            'pii_detected': len(context.pii_detected),
                        }

                    return result, 200

                except Exception as e:
                    logger.exception(f"[FlaskAdapter] Error: {e}")
                    return {'error': str(e)}, 500

            return wrapper

        return decorator

    async def _call_handler(self, f: Callable, *args, **kwargs):
        """Call handler, detecting async vs sync (Finding #7)."""
        if asyncio.iscoroutinefunction(f):
            return await f(*args, **kwargs)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, f, *args, **kwargs)
