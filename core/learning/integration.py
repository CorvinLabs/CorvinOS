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
            from core.paths.tenant import tenant_home  # noqa: PLC0415
            store_path = tenant_home(tenant_id) / "learning"
        store_path = Path(store_path)
        self.tenant_id = tenant_id
        self.store = LearningEventStore(store_path)
        # Operator grades are persisted through the audit-first, hash-chained
        # ADR-0314 store (``<tenant_home>/learning/events/``) — never through the
        # unchained TreeOfThoughts JSONL above (F-L6). ``store_path`` is
        # ``<tenant_home>/learning``, so its parent is the tenant home.
        from .event_store import EventStore as _ChainedEventStore  # noqa: PLC0415
        self.event_store = _ChainedEventStore(store_path.parent, tenant_id=tenant_id)
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
        
        # ``text``/``voice`` are the TTS call's arguments — forward them to
        # ``tts_fn``; only ``text_length`` (never the text) enters the context.
        result = await self.execute_method_with_learning(
            pattern_id,
            tts_fn,
            {
                "provider": provider_id,
                "voice": voice,
                "text_length": len(text),
                **(context or {})
            },
            *args,
            text=text,
            voice=voice,
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
    
    def grade_pattern(self, pattern_id: str, grade: float, reason: str = "") -> str:
        """Manual operator grading of a pattern.

        Audit-first: the grade is committed to the core hash chain and the
        ADR-0314 event store BEFORE the in-memory confidence moves; if the chain
        write does not commit, ``RuntimeError`` propagates and nothing changes.
        The operator's free-text ``reason`` is NOT persisted — only whether one
        was given and how long it was (F-L6).

        Returns:
            The persisted learning event's id.
        """
        from .confidence import update_confidence
        from .learning_events import EventType, LearningEvent as _ChainedEvent
        from .models import LearningEvent

        if not isinstance(grade, (int, float)) or grade != grade:
            raise ValueError(f"grade must be a number, got {grade!r}")
        grade = float(max(-1.0, min(1.0, grade)))
        reason_text = reason.strip() if isinstance(reason, str) else ""

        chained = _ChainedEvent.create(
            event_type=EventType.FEEDBACK,
            skill_id=pattern_id,
            tenant_id=self.tenant_id,
            signal={
                "kind": "pattern_grade",
                "pattern_id": pattern_id,
                "grade": grade,
                "has_reason": bool(reason_text),
                "reason_length": len(reason_text),
                "source": "operator",
            },
            lom="core/learning/integration.py:grade_pattern",
        )
        self.event_store.write_event(chained)

        node = self.store.get_node(pattern_id)
        if node:
            update_confidence(
                node,
                LearningEvent(
                    subject_id=pattern_id,
                    event_type="graded",
                    confidence_delta=grade,
                    reason="",  # in-memory only; free text is never stored
                ),
            )
        return chained.event_id

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
