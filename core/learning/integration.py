"""Integration: wire active_loop into method execution (Phase 3)."""
from __future__ import annotations
from .active_loop import ActiveLearningLoop
from .storage import LearningEventStore
from .metrics import ExecutionMetrics, MetricsCollector
from pathlib import Path
import time
from typing import Callable, Any


class LearningIntegration:
    """Bridge between execution (chat_runtime, say.py) and learning loop."""
    
    def __init__(self, store_path: Path = None):
        if store_path is None:
            store_path = Path.home() / ".corvin" / "learning"
        self.store = LearningEventStore(store_path)
        self.metrics = MetricsCollector(self.store)
        self.loop = ActiveLearningLoop(self.store)
    
    async def execute_method_with_learning(
        self,
        method_id: str,
        method_fn: Callable,
        context: dict[str, Any] = None,
        *args,
        **kwargs
    ) -> dict:
        """Execute a method with full learning pipeline.
        
        Emits:
        - LearningEvent (success/failure)
        - ExecutionMetrics (latency, cost)
        - Auto-suggestions if confidence drops
        - Antipattern warnings
        """
        start = time.time()
        result = await self.loop.execute_with_learning(
            method_id=method_id,
            method_fn=method_fn,
            context=context or {},
            *args,
            **kwargs
        )
        latency_ms = (time.time() - start) * 1000
        
        # Record metrics
        metrics = ExecutionMetrics(
            subject_id=method_id,
            latency_ms=latency_ms,
            success=result.get("success", False),
            error_type=result.get("error_type"),
            context=context or {}
        )
        self.metrics.record(metrics)
        
        return result
    
    async def execute_tts_with_learning(
        self,
        provider_id: str,
        tts_fn: Callable,
        text: str,
        voice: str = "alloy",
        context: dict[str, Any] = None,
        *args,
        **kwargs
    ) -> dict:
        """Execute TTS call with learning tracking.
        
        Emits:
        - LearningEvent (success/failure)
        - ExecutionMetrics (latency, cost)
        """
        # Pattern ID: "pattern_tts_{provider}"
        pattern_id = f"pattern_tts_{provider_id}"
        
        result = await self.execute_method_with_learning(
            method_id=pattern_id,
            method_fn=tts_fn,
            context={
                "provider": provider_id,
                "voice": voice,
                "text_length": len(text),
                **(context or {})
            },
            *args,
            **kwargs
        )
        
        return result
    
    def register_pattern(self, pattern_id: str, name: str, when: list[str], anti_when: list[str] = None):
        """Register a pattern before using it."""
        from .models import TreeNode
        
        node = TreeNode(
            id=pattern_id,
            level="pattern",
            name=name,
            when=when,
            anti_when=anti_when or [],
        )
        self.store.register_node(node)
    
    def get_pattern_confidence(self, pattern_id: str) -> float:
        """Retrieve current confidence for a pattern."""
        node = self.store.get_node(pattern_id)
        return node.confidence if node else 0.0
    
    def grade_pattern(self, pattern_id: str, grade: float, reason: str = ""):
        """Manual operator grading of a pattern."""
        from .models import LearningEvent
        
        event = LearningEvent(
            subject_id=pattern_id,
            event_type="graded",
            confidence_delta=grade,
            reason=reason,
        )
        self.store.append_event(pattern_id, event)
        
        node = self.store.get_node(pattern_id)
        if node:
            from .confidence import update_confidence
            update_confidence(node, event)
