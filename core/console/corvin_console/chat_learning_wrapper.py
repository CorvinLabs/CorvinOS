"""Learning integration wrapper for chat_runtime (Phase 7c)."""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, AsyncIterator

from core.learning import LearningIntegration, ExecutionMetrics

_logger = logging.getLogger(__name__)


class ChatLearningWrapper:
    """Wraps chat execution with learning event tracking."""
    
    def __init__(self, tenant_id: str = "default"):
        self.tenant_id = tenant_id
        store_path = Path.home() / ".corvin" / "tenants" / tenant_id / "learning"
        self.integration = LearningIntegration(store_path)
        self._turn_count = 0
    
    async def stream_turn_with_learning(
        self,
        stream_turn_fn,
        chat_key: str,
        messages: list,
        system_prompt: str,
        **kwargs
    ) -> AsyncIterator[dict]:
        """Wrap stream_turn with learning tracking.
        
        Yields the same events as stream_turn, but tracks:
        - Execution latency
        - Success/failure
        - Token metrics (if available)
        
        Updates confidence for the "method_chat_turn" pattern.
        """
        self._turn_count += 1
        start = time.time()
        
        # Register pattern if first turn
        if self._turn_count == 1:
            self.integration.register_pattern(
                "pattern_chat_turn_execution",
                "Chat Turn Execution",
                when=["user asks a question"],
                anti_when=["invalid input"]
            )
        
        error_occurred = False
        error_type = None
        token_count = 0
        
        try:
            # Stream through original function
            async for event in stream_turn_fn(chat_key, messages, system_prompt, **kwargs):
                # Count tokens if available (heuristic: ~4 chars per token)
                if "text" in event:
                    token_count += len(event.get("text", "")) // 4
                
                yield event
        
        except Exception as e:
            error_occurred = True
            error_type = type(e).__name__
            _logger.error(f"Chat turn failed: {error_type}: {e}", exc_info=True)
            raise
        
        finally:
            # Record metrics
            latency_ms = (time.time() - start) * 1000
            
            metrics = ExecutionMetrics(
                subject_id="pattern_chat_turn_execution",
                latency_ms=latency_ms,
                cost_tokens=token_count,
                success=not error_occurred,
                error_type=error_type,
                context={
                    "chat_key": chat_key,
                    "message_count": len(messages),
                    "tenant_id": self.tenant_id,
                }
            )
            
            try:
                self.integration.metrics.record(metrics)
            except Exception as e:
                _logger.warning(f"Failed to record chat metrics: {e}")


# Global singleton per tenant
_wrappers: dict[str, ChatLearningWrapper] = {}


def get_chat_learning_wrapper(tenant_id: str = "default") -> ChatLearningWrapper:
    """Get or create a wrapper for this tenant."""
    if tenant_id not in _wrappers:
        _wrappers[tenant_id] = ChatLearningWrapper(tenant_id)
    return _wrappers[tenant_id]
