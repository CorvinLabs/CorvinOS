"""Bridge message handler adapter for security pipeline."""

import asyncio
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class BridgeSecurityAdapter:
    """Wrapper for bridge message handlers."""

    def __init__(self, pipeline):
        self.pipeline = pipeline

    async def wrap_handler(
        self,
        action: str,
        resource: str,
        capability_required: str,
        actor: str,
        input_data: dict,
        handler_fn: Callable,
    ):
        """Wrap a bridge handler with security checks."""
        try:
            success, result, context = await self.pipeline.execute_with_security(
                actor=actor,
                action=action,
                resource=resource,
                capability_required=capability_required,
                transport='bridge_handler',
                input_data=input_data,
                handler_fn=handler_fn,
            )

            if not success:
                logger.warning(f"[Bridge] Access denied: {context.error}")
                return None

            return result

        except Exception as e:
            logger.exception(f"[Bridge] Handler error: {e}")
            return None
