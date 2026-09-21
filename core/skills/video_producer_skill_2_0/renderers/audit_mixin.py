"""Audit Trail Mixin — Compliance logging for all renderers"""

import logging
import time
import json
from typing import Dict

logger = logging.getLogger(__name__)


class AuditMixin:
    """Mixin for all renderers: emit audit events"""

    def emit_audit_event(self, event_type: str, renderer: str, payload: Dict) -> None:
        """Emit audit event for compliance

        Args:
            event_type: renderer_executed, renderer_failed, effect_applied, etc.
            renderer: renderer name (svg_renderer, effects_processor, etc.)
            payload: event data (duration_ms, success, error, etc.)
        """
        try:
            audit_entry = {
                "event_type": event_type,
                "renderer": renderer,
                "timestamp": time.time(),
                **payload
            }
            # Log as JSON for audit parsing
            logger.info(f"[AUDIT] {json.dumps(audit_entry)}")
        except Exception as e:
            logger.warning(f"Failed to emit audit event: {e}")

    def timed_execute(self, renderer_name: str, fn, *args, **kwargs):
        """Execute function with timing + audit

        Args:
            renderer_name: name of renderer
            fn: function to execute
            *args, **kwargs: arguments to function

        Returns:
            Function result

        Raises:
            Exception if fn raises
        """
        start = time.time()
        try:
            result = fn(*args, **kwargs)
            duration_ms = (time.time() - start) * 1000
            self.emit_audit_event("renderer_executed", renderer_name, {
                "duration_ms": duration_ms,
                "success": True,
            })
            return result
        except Exception as e:
            duration_ms = (time.time() - start) * 1000
            self.emit_audit_event("renderer_failed", renderer_name, {
                "duration_ms": duration_ms,
                "error": str(e),
                "success": False,
            })
            raise


__all__ = ["AuditMixin"]
