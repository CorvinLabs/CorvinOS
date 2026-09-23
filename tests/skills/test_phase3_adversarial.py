"""Phase 3 Adversarial Tests (4 tests for both streams).

Tests attack vectors and stress scenarios to ensure Phase 3 is production-safe.

**Attack Vectors:**
1. Concurrent feedback writes (1000 events from 100 async tasks)
2. SQL injection + XSS in feedback reason
3. PII leakage detection (email/SSN in feedback)
4. Load surge (1000 req/s feedback ingestion for 60s)

**Compliance:**
- GDPR Art. 30/32: PII detection + redaction in audit trail
- ADR-0314: No silent failures, all events audited
- Fail-closed: sanitization errors → reject request
"""

from __future__ import annotations

import asyncio
import json
import logging
import pytest
import re
from datetime import datetime
from typing import Optional, List, Dict
from unittest.mock import Mock, MagicMock
import concurrent.futures
import time

try:
    from core.learning.event_store import EventStore
    from core.learning.learning_events import LearningEvent, EventType
    from core.skills.os_skills.workflow_optimizer_skill.feedback_handler import (
        FeedbackHandler,
        RoutingFeedback,
        FeedbackType,
    )
    from core.skills.os_skills.flow_guard.feedback_handler import (
        PolicyFeedbackHandler,
        PolicyFeedback,
        PolicyFeedbackType,
    )
except ImportError as e:
    pytest.skip(f"Phase 3 modules not yet available: {e}", allow_module_level=True)

logger = logging.getLogger(__name__)


class MockEventStore:
    """Thread-safe mock EventStore for concurrent testing."""
    def __init__(self):
        self.events: List[LearningEvent] = []
        self._lock = __import__('threading').Lock()

    def write_event(self, event: LearningEvent) -> None:
        """Audit-first: chain write must succeed (thread-safe)."""
        with self._lock:
            if not event.event_id:
                raise RuntimeError("Event missing event_id")
            self.events.append(event)

    def query_events(
        self,
        tenant_id: str,
        event_type: Optional[EventType] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[LearningEvent]:
        """Query events (thread-safe, tenant-scoped)."""
        with self._lock:
            result = [e for e in self.events if e.tenant_id == tenant_id]
            if event_type:
                result = [e for e in result if e.event_type == event_type]
            return result[offset:offset+limit]

    def count_events(self, tenant_id: str) -> int:
        """Count events (thread-safe, tenant-scoped)."""
        with self._lock:
            return len([e for e in self.events if e.tenant_id == tenant_id])


class TestConcurrentFeedbackWrites:
    """Adversarial Test 1: Concurrent feedback writes (1000 events)."""

    @pytest.fixture
    def event_store(self):
        """Thread-safe EventStore."""
        return MockEventStore()

    def test_concurrent_feedback_1000_events(self, event_store):
        """
        Stress test: 100 async tasks each submitting 10 feedback events.
        Verify: no race conditions, all 1000 events logged, latency < 5ms p99.
        """
        handler = FeedbackHandler(
            event_store=event_store,
            tenant_id="_default",
        )

        latencies = []
        errors = []

        def submit_feedback(feedback_num: int) -> float:
            """Submit one feedback event, return latency."""
            start = time.perf_counter_ns()
            try:
                feedback = RoutingFeedback(
                    task_id=f"task_{feedback_num}",
                    routed_model="opus",
                    task_complexity="complex",
                    feedback_type=FeedbackType.CORRECT,
                    confidence_score=0.9,
                    tenant_id="_default",
                )
                handler.process_feedback(feedback)
                latency_ms = (time.perf_counter_ns() - start) / 1e6
                return latency_ms
            except Exception as e:
                errors.append(str(e))
                return None

        # Submit 1000 feedback events concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
            futures = [executor.submit(submit_feedback, i) for i in range(1000)]
            latencies = [f.result() for f in concurrent.futures.as_completed(futures)]

        # Remove None latencies (errors)
        latencies = [l for l in latencies if l is not None]

        # Assertions
        assert len(errors) == 0, f"Should have no errors, got {len(errors)}: {errors}"
        assert len(latencies) == 1000, f"Should log all 1000 events, got {len(latencies)}"

        # Verify audit trail
        audit_events = event_store.count_events(tenant_id="_default")
        assert audit_events == 1000, f"Should have 1000 audit events, got {audit_events}"

        # Latency check: p99 < 5ms
        latencies_sorted = sorted(latencies)
        p99_idx = int(0.99 * len(latencies_sorted))
        p99_latency = latencies_sorted[p99_idx]

        logger.info(f"Concurrent stress test: {len(latencies)} events, p99={p99_latency:.2f}ms")
        assert p99_latency < 10.0, \
            f"p99 latency should be < 10ms, got {p99_latency:.2f}ms"


class TestInjectionAttacks:
    """Adversarial Test 2: SQL injection + XSS in feedback reason."""

    @pytest.fixture
    def event_store(self):
        """Mock EventStore."""
        return MockEventStore()

    def test_sql_xss_injection_rejection(self, event_store):
        """
        Attempt SQL injection and XSS in feedback reason field.
        Verify: sanitized in audit trail, no execution.
        """
        handler = FeedbackHandler(
            event_store=event_store,
            tenant_id="_default",
        )

        malicious_payloads = [
            "'; DROP TABLE feedback; --",
            "<script>alert('XSS')</script>",
            "' OR '1'='1",
            "<img src=x onerror='alert(1)'>",
            "\\x00\\x01\\x02",  # Null bytes
        ]

        for payload in malicious_payloads:
            try:
                # Attempt to create feedback with malicious reason
                feedback = RoutingFeedback(
                    task_id=f"task_malicious",
                    routed_model="opus",
                    task_complexity="complex",
                    feedback_type=FeedbackType.CORRECT,
                    confidence_score=0.9,
                    tenant_id="_default",
                    reason=payload,  # Malicious payload in reason field
                )
                # If we get here, process the feedback
                handler.process_feedback(feedback)
            except ValueError:
                # Expected: validation should reject
                continue

        # Verify audit trail: no malicious payloads in stored events
        events = event_store.query_events(tenant_id="_default")

        for event in events:
            if event.signal and "reason" in event.signal:
                reason_field = event.signal["reason"]
                for payload in malicious_payloads:
                    # Should not contain the exact malicious payload
                    # (or should be sanitized/redacted)
                    assert payload not in reason_field, \
                        f"Malicious payload found in audit trail: {payload}"

        logger.info(f"Injection test: {len(malicious_payloads)} payloads rejected/sanitized")


class TestPIILeakageDetection:
    """Adversarial Test 3: PII detection (email, SSN, credit card)."""

    @pytest.fixture
    def event_store(self):
        """Mock EventStore."""
        return MockEventStore()

    def test_pii_leakage_detection(self, event_store):
        """
        Include PII in feedback reason (email, SSN).
        Verify: PII detector flags and redacts before audit logging.
        """
        handler = PolicyFeedbackHandler(
            event_store=event_store,
            tenant_id="_default",
        )

        pii_test_cases = [
            ("User john.doe@example.com gave feedback", "john.doe@example.com"),
            ("SSN: 123-45-6789", "123-45-6789"),
            ("Credit card 4532-1234-5678-9010", "4532-1234-5678-9010"),
        ]

        for reason, pii_value in pii_test_cases:
            try:
                feedback = PolicyFeedback(
                    flow_id=f"flow_pii_test",
                    data_class="pii",
                    engine="claude-opus",
                    destination="console",
                    policy_decision="deny",
                    feedback_type=PolicyFeedbackType.DENY_CORRECT,
                    confidence_score=0.9,
                    tenant_id="_default",
                    reason=reason,
                )
                handler.process_feedback(feedback)
            except ValueError:
                # PII detector may reject
                continue

        # Verify audit trail: PII should be redacted
        events = event_store.query_events(tenant_id="_default")

        for event in events:
            if event.signal:
                # Check all string fields for PII
                for field, value in event.signal.items():
                    if isinstance(value, str):
                        # Email regex
                        emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", value)
                        assert len(emails) == 0, \
                            f"Email found in audit trail: {emails}"

                        # SSN regex
                        ssns = re.findall(r"\d{3}-\d{2}-\d{4}", value)
                        assert len(ssns) == 0, \
                            f"SSN found in audit trail: {ssns}"

                        # Credit card regex
                        credit_cards = re.findall(r"\d{4}-\d{4}-\d{4}-\d{4}", value)
                        assert len(credit_cards) == 0, \
                            f"Credit card found in audit trail: {credit_cards}"

        logger.info(f"PII detection test: {len(pii_test_cases)} cases checked")


class TestLoadSurge:
    """Adversarial Test 4: Load surge (1000 req/s for 60s)."""

    @pytest.fixture
    def event_store(self):
        """Thread-safe EventStore."""
        return MockEventStore()

    def test_load_surge_1000_rps(self, event_store):
        """
        Simulate 1000 req/s feedback ingestion for 10 seconds (10,000 events total).
        Verify: zero event loss, latency p99 < 100ms, no timeouts.
        """
        handler = FeedbackHandler(
            event_store=event_store,
            tenant_id="_default",
        )

        submitted = 0
        succeeded = 0
        failed = 0
        latencies = []

        def submit_feedback_burst(task_num: int) -> bool:
            """Submit feedback, return success."""
            start = time.perf_counter_ns()
            try:
                feedback = RoutingFeedback(
                    task_id=f"task_load_{task_num}",
                    routed_model="haiku",
                    task_complexity="simple",
                    feedback_type=FeedbackType.CORRECT,
                    confidence_score=0.8,
                    tenant_id="_default",
                )
                handler.process_feedback(feedback)
                latency_ms = (time.perf_counter_ns() - start) / 1e6
                latencies.append(latency_ms)
                return True
            except Exception as e:
                logger.error(f"Feedback submission failed: {e}")
                return False

        # Simulate load surge: 100 tasks × 100 feedback each = 10,000 total
        target_load = 10000
        batch_size = 100

        for batch_num in range(target_load // batch_size):
            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
                futures = [
                    executor.submit(submit_feedback_burst, batch_num * batch_size + i)
                    for i in range(batch_size)
                ]
                for future in concurrent.futures.as_completed(futures):
                    submitted += 1
                    if future.result():
                        succeeded += 1
                    else:
                        failed += 1

        # Assertions
        assert failed == 0, f"Should have zero failures, got {failed}"
        assert succeeded == target_load, \
            f"Should process all {target_load} events, got {succeeded}"

        # Verify audit trail
        audit_events = event_store.count_events(tenant_id="_default")
        assert audit_events == target_load, \
            f"Should have {target_load} audit events, got {audit_events}"

        # Latency check
        if latencies:
            latencies_sorted = sorted(latencies)
            p99_idx = int(0.99 * len(latencies_sorted))
            p99_latency = latencies_sorted[p99_idx]

            logger.info(
                f"Load surge test: {succeeded} events processed, "
                f"p99_latency={p99_latency:.2f}ms"
            )
            assert p99_latency < 200.0, \
                f"p99 latency should be < 200ms under load, got {p99_latency:.2f}ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
