"""Forge/MCP tool adapter for security pipeline."""

import asyncio
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class ForgeSecurityAdapter:
    """Wrapper for Forge/MCP tools."""

    def __init__(self, pipeline):
        self.pipeline = pipeline

    def require_security(
        self,
        action: str,
        resource: str,
        capability_required: str,
    ):
        """Decorator for Forge tools."""

        def decorator(f):
            async def wrapper(*args, **kwargs):
                try:
                    actor = kwargs.get('actor', 'system:forge')
                    input_data = kwargs.get('input_data', {})

                    success, result, context = await self.pipeline.execute_with_security(
                        actor=actor,
                        action=action,
                        resource=resource,
                        capability_required=capability_required,
                        transport='forge_tool',
                        input_data=input_data,
                        handler_fn=lambda: self._call_handler(f, *args, **kwargs),
                    )

                    if not success:
                        logger.error(f"[Forge] Access denied: {context.error}")
                        return {"error": context.error, "decision_hash": context.decision_record_hash}

                    return result

                except Exception as e:
                    logger.exception(f"[Forge] Error: {e}")
                    return {"error": str(e)}

            return wrapper

        return decorator

    async def _call_handler(self, f: Callable, *args, **kwargs):
        """Call handler, detecting async vs sync."""
        if asyncio.iscoroutinefunction(f):
            return await f(*args, **kwargs)
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, f, *args, **kwargs)
