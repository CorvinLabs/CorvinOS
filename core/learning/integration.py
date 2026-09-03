"""Integration: wire active_loop into method execution (Phase 3).

Phase 8: Integrated anomaly detection and auto-recovery.
Phase 9: Pattern discovery from production failures.
"""
from __future__ import annotations
from .active_loop import ActiveLearningLoop
from .storage import LearningEventStore
from .metrics import ExecutionMetrics, ExecutionMetricsRecorder
from .anomaly_detector import AnomalyDetector
from .pattern_discovery import FailureClusterer, DiscoveredPattern
from pathlib import Path
import time
from typing import Callable, Any, Optional


class LearningIntegration:
    """Bridge between execution (chat_runtime, say.py) and learning loop.

    Includes Phase 8 anomaly detection and auto-recovery features.
    Includes Phase 9 pattern discovery from production failures.
    """

    def __init__(self, store_path: Path = None, tenant_id: str = "_default"):
        if store_path is None:
            from forge.tenants import tenant_home  # type: ignore[import-not-found]
            store_path = Path(tenant_home(tenant_id)) / "learning"
        self.tenant_id = tenant_id
        self.store = LearningEventStore(store_path)
        self.metrics = ExecutionMetricsRecorder(self.store)
        self.loop = ActiveLearningLoop(self.store)
        # Operator-feedback anomaly detector (window-based; takes window_size,
        # NOT a store — passing the store made deque(maxlen=<store>) raise).
        self.anomaly_detector = AnomalyDetector()
        self.pattern_clusterer = FailureClusterer(self.store)
    
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

    # Phase 8 note: the former check_anomaly/get_alerts/get_latest_alert/
    # clear_alerts_before wrappers delegated to a file-based detector that the
    # v0.6 rewrite replaced with the feedback-window ``AnomalyDetector``
    # (record_feedback / get_health_status). They were unreachable — this
    # constructor crashed before them — and are gone rather than kept as dead API.

    # Phase 9: Pattern Discovery

    def record_failure(
        self,
        subject_id: str,
        error_type: str,
        context: dict = None,
    ) -> None:
        """Record a failure for pattern discovery clustering.

        This is typically called after a method fails (e2e execution).
        The FailureClusterer accumulates these and discovers patterns when
        there are >=50 samples.

        Args:
            subject_id: Pattern or method that failed
            error_type: Type of error (e.g., "timeout", "rate_limit", "auth_failed")
            context: Context dict from the failure (provider, endpoint, etc.)
        """
        if context is None:
            context = {}
        self.pattern_clusterer.add_failure(subject_id, error_type, context)

    def discover_patterns(self) -> list[DiscoveredPattern]:
        """Discover new patterns from accumulated failures.

        Clusters failures by error_type and context, infers when/anti_when
        conditions, and auto-registers patterns with 0.5 baseline confidence
        when a cluster has >=50 samples.

        Returns:
            List of newly discovered patterns.
        """
        return self.pattern_clusterer.discover_patterns(integration=self)

    def get_failure_clusters(self):
        """Get all discovered failure clusters."""
        return self.pattern_clusterer.get_clusters()

    def get_discovered_patterns(self) -> list[DiscoveredPattern]:
        """Get all successfully discovered patterns."""
        return self.pattern_clusterer.get_discoveries()
