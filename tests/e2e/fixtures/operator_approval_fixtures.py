"""
Fixtures for Operator Approval Workflow E2E Tests.

Provides:
- Mock operators (operatorA, operatorB, operatorC)
- Approval decision contexts
- Audit event lineage generators
- WebSocket client simulation
- Live metrics streams
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any
from unittest.mock import AsyncMock, MagicMock

import pytest


logger = logging.getLogger(__name__)


# ============================================================================
# Enums & Constants
# ============================================================================


class OperatorDecision(str, Enum):
    """Operator approval decision."""
    APPROVE = "approve"
    DEFER = "defer"
    PAUSE = "pause"
    RESUME = "resume"
    ROLLBACK = "rollback"


class SkillStatus(str, Enum):
    """Skill deployment status."""
    GENERATING = "generating"
    VALIDATING = "validating"
    CANARY_10 = "canary_10"
    CANARY_HEALTHY = "canary_healthy"
    CANARY_FAILED = "canary_failed"
    RAMP_50 = "ramp_50"
    RAMP_HEALTHY = "ramp_healthy"
    ROLLBACK_INITIATED = "rollback_initiated"
    ROLLED_BACK = "rolled_back"
    FULL_100 = "full_100"


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class MockOperator:
    """Mock operator fixture."""
    operator_id: str
    name: str
    email: str
    tenant_id: str = "_default"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CanaryMetricsSnapshot:
    """Canary metrics snapshot for monitoring."""
    timestamp: int = field(default_factory=lambda: int(time.time()))
    latency_p99_ms: float = 200.0
    error_rate: float = 0.001  # 0.1%
    throughput_rps: float = 4300.0
    audit_integrity: float = 0.9995
    skill_version: str = "v1.2.3"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ApprovalContext:
    """Context for a single approval decision."""
    approval_id: str
    skill_id: str
    skill_version: str
    operator: MockOperator
    decision: OperatorDecision
    reason: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    metrics_before: CanaryMetricsSnapshot = field(default_factory=CanaryMetricsSnapshot)
    metrics_after: Optional[CanaryMetricsSnapshot] = None
    audit_event_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data['operator'] = self.operator.to_dict()
        data['metrics_before'] = self.metrics_before.to_dict()
        if self.metrics_after:
            data['metrics_after'] = self.metrics_after.to_dict()
        return data


@dataclass
class AuditEventRecord:
    """Immutable audit event with hash-chaining."""
    event_id: str
    event_type: str  # operator_approved_skill, operator_deferred_skill, etc.
    tenant_id: str
    timestamp: str
    operator_id: str
    approval_id: str
    skill_id: str
    skill_version: str
    decision: str
    reason: Optional[str] = None
    prev_hash: Optional[str] = None
    event_hash: str = field(default="")
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Compute hash on creation (immutable)."""
        if not self.event_hash:
            hash_payload = json.dumps({
                "event_id": self.event_id,
                "event_type": self.event_type,
                "tenant_id": self.tenant_id,
                "timestamp": self.timestamp,
                "operator_id": self.operator_id,
                "approval_id": self.approval_id,
                "skill_id": self.skill_id,
                "skill_version": self.skill_version,
                "decision": self.decision,
                "prev_hash": self.prev_hash,
            }, sort_keys=True)
            self.event_hash = hashlib.sha256(hash_payload.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================================
# Fixture Factories
# ============================================================================


class OperatorFactory:
    """Factory for creating mock operators."""

    @staticmethod
    def create_operator(operator_id: str, name: str, email: str, tenant_id: str = "_default") -> MockOperator:
        """Create a mock operator."""
        return MockOperator(
            operator_id=operator_id,
            name=name,
            email=email,
            tenant_id=tenant_id,
        )

    @staticmethod
    def operator_a() -> MockOperator:
        """Operator A fixture."""
        return OperatorFactory.create_operator("op:alice", "Alice", "alice@example.com")

    @staticmethod
    def operator_b() -> MockOperator:
        """Operator B fixture."""
        return OperatorFactory.create_operator("op:bob", "Bob", "bob@example.com")

    @staticmethod
    def operator_c() -> MockOperator:
        """Operator C fixture."""
        return OperatorFactory.create_operator("op:charlie", "Charlie", "charlie@example.com")


class ApprovalContextFactory:
    """Factory for creating approval contexts."""

    @staticmethod
    def create_approval_context(
        approval_id: str,
        skill_id: str,
        skill_version: str,
        operator: MockOperator,
        decision: OperatorDecision,
        reason: Optional[str] = None,
    ) -> ApprovalContext:
        """Create an approval context."""
        return ApprovalContext(
            approval_id=approval_id,
            skill_id=skill_id,
            skill_version=skill_version,
            operator=operator,
            decision=decision,
            reason=reason,
        )


class AuditEventFactory:
    """Factory for creating audit events."""

    @staticmethod
    def create_event(
        event_type: str,
        operator: MockOperator,
        approval_id: str,
        skill_id: str,
        skill_version: str,
        decision: str,
        reason: Optional[str] = None,
        prev_hash: Optional[str] = None,
    ) -> AuditEventRecord:
        """Create an audit event."""
        # Generate unique event ID
        event_id = f"evt:{int(time.time() * 1000)}:{approval_id}"

        return AuditEventRecord(
            event_id=event_id,
            event_type=event_type,
            tenant_id=operator.tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            operator_id=operator.operator_id,
            approval_id=approval_id,
            skill_id=skill_id,
            skill_version=skill_version,
            decision=decision,
            reason=reason,
            prev_hash=prev_hash,
        )


# ============================================================================
# Audit Trail Lineage
# ============================================================================


class AuditTrailLineage:
    """Simulates immutable audit trail with hash-chaining."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.events: List[AuditEventRecord] = []

    def add_event(self, event: AuditEventRecord) -> AuditEventRecord:
        """Add event to chain and compute hash."""
        prev_hash = self.events[-1].event_hash if self.events else None
        event.prev_hash = prev_hash
        # Recompute hash with prev_hash included
        hash_payload = json.dumps({
            "event_id": event.event_id,
            "event_type": event.event_type,
            "tenant_id": event.tenant_id,
            "timestamp": event.timestamp,
            "operator_id": event.operator_id,
            "approval_id": event.approval_id,
            "prev_hash": prev_hash,
        }, sort_keys=True)
        event.event_hash = hashlib.sha256(hash_payload.encode()).hexdigest()[:16]
        self.events.append(event)
        return event

    def verify_chain_integrity(self) -> bool:
        """Verify hash-chain is intact (no tampering)."""
        if not self.events:
            return True

        for i, event in enumerate(self.events):
            if i == 0:
                if event.prev_hash is not None:
                    return False
            else:
                expected_prev_hash = self.events[i - 1].event_hash
                if event.prev_hash != expected_prev_hash:
                    return False

        return True

    def get_lineage_for_approval(self, approval_id: str) -> List[AuditEventRecord]:
        """Get all events related to an approval."""
        return [e for e in self.events if e.approval_id == approval_id]

    def to_dict(self) -> List[Dict[str, Any]]:
        """Export entire chain as dicts."""
        return [e.to_dict() for e in self.events]


# ============================================================================
# Live Metrics Simulator
# ============================================================================


class LiveMetricsSimulator:
    """Simulates live metrics stream for canary monitoring."""

    def __init__(
        self,
        skill_id: str,
        initial_metrics: Optional[CanaryMetricsSnapshot] = None,
        failure_scenario: bool = False,
    ):
        self.skill_id = skill_id
        self.current_metrics = initial_metrics or CanaryMetricsSnapshot()
        self.failure_scenario = failure_scenario
        self.request_count = 0
        self.failure_triggers_at = 10  # Fail after N requests in failure scenario

    async def stream_metrics(self, count: int = 60) -> List[CanaryMetricsSnapshot]:
        """Stream N metric snapshots (async generator simulation)."""
        metrics_list = []

        for i in range(count):
            await asyncio.sleep(0.01)  # Simulate ~1 request every 100ms in real time

            # Update metrics
            if self.failure_scenario and i >= self.failure_triggers_at:
                # Simulate degradation
                self.current_metrics.error_rate = 0.40  # 40% error rate
                self.current_metrics.latency_p99_ms = 5000.0  # 5s latency
                self.current_metrics.audit_integrity = 0.85
                self.current_metrics.throughput_rps = 1200.0
            else:
                # Healthy metrics
                self.current_metrics.error_rate = 0.001
                self.current_metrics.latency_p99_ms = 200.0
                self.current_metrics.audit_integrity = 0.9995
                self.current_metrics.throughput_rps = 4300.0

            self.current_metrics.timestamp = int(time.time())
            metrics_list.append(
                CanaryMetricsSnapshot(
                    timestamp=self.current_metrics.timestamp,
                    latency_p99_ms=self.current_metrics.latency_p99_ms,
                    error_rate=self.current_metrics.error_rate,
                    throughput_rps=self.current_metrics.throughput_rps,
                    audit_integrity=self.current_metrics.audit_integrity,
                    skill_version=self.current_metrics.skill_version,
                )
            )

        return metrics_list

    def metrics_healthy(self) -> bool:
        """Check if current metrics pass SLOs."""
        return (
            self.current_metrics.error_rate < 0.05
            and self.current_metrics.latency_p99_ms < 1500.0
            and self.current_metrics.audit_integrity > 0.99
        )

    def metrics_failed(self) -> bool:
        """Check if current metrics exceed failure threshold."""
        return (
            self.current_metrics.error_rate > 0.30
            or self.current_metrics.latency_p99_ms > 3000.0
        )


# ============================================================================
# Mock WebSocket Client
# ============================================================================


class MockWebSocketClient:
    """Mock WebSocket client for live metrics streaming."""

    def __init__(self, base_url: str = "ws://localhost:8765"):
        self.base_url = base_url
        self.connected = False
        self.messages: List[Dict[str, Any]] = []
        self.metrics_stream: List[CanaryMetricsSnapshot] = []

    async def connect(self, task_id: str, approval_id: str):
        """Simulate WebSocket connection."""
        self.connected = True
        logger.info(f"[WebSocket] Connected: task={task_id}, approval={approval_id}")

    async def disconnect(self):
        """Simulate WebSocket disconnection."""
        self.connected = False
        logger.info("[WebSocket] Disconnected")

    async def send_message(self, message: Dict[str, Any]):
        """Send a message."""
        if self.connected:
            self.messages.append(message)

    async def receive_metrics(self) -> Optional[Dict[str, Any]]:
        """Receive next metrics update."""
        if not self.metrics_stream:
            return None

        metrics = self.metrics_stream.pop(0)
        return {
            "type": "metrics_update",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "metrics": metrics.to_dict(),
        }

    async def listen_for_metrics(self, simulator: LiveMetricsSimulator, count: int = 60):
        """Listen for metrics from simulator."""
        self.metrics_stream = await simulator.stream_metrics(count)


# ============================================================================
# Test Data Generators
# ============================================================================


def generate_test_approval_workflow(
    skill_id: str = "os.delegation_router",
    skill_version: str = "v1.2.4",
    operator: Optional[MockOperator] = None,
) -> Dict[str, Any]:
    """Generate complete test approval workflow data."""
    if operator is None:
        operator = OperatorFactory.operator_a()

    approval_id = f"appr:{int(time.time() * 1000)}:{skill_id}"

    return {
        "approval_id": approval_id,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "operator": operator,
        "canary_metrics": CanaryMetricsSnapshot(),
        "status": SkillStatus.CANARY_10.value,
        "audit_trail": AuditTrailLineage(operator.tenant_id),
    }


def generate_multi_operator_approval_sequence(
    skill_id: str = "os.delegation_router",
    operators: Optional[List[MockOperator]] = None,
) -> List[ApprovalContext]:
    """Generate sequence of approvals from multiple operators."""
    if operators is None:
        operators = [
            OperatorFactory.operator_a(),
            OperatorFactory.operator_b(),
            OperatorFactory.operator_c(),
        ]

    sequence = []

    # Op A: Triggers pause
    sequence.append(
        ApprovalContextFactory.create_approval_context(
            approval_id=f"appr:{int(time.time() * 1000)}:1",
            skill_id=skill_id,
            skill_version="v1.2.4",
            operator=operators[0],
            decision=OperatorDecision.PAUSE,
            reason="Need team review",
        )
    )

    # Op B: Resumes
    sequence.append(
        ApprovalContextFactory.create_approval_context(
            approval_id=f"appr:{int(time.time() * 1000)}:2",
            skill_id=skill_id,
            skill_version="v1.2.4",
            operator=operators[1],
            decision=OperatorDecision.RESUME,
        )
    )

    # Op C: Approves
    sequence.append(
        ApprovalContextFactory.create_approval_context(
            approval_id=f"appr:{int(time.time() * 1000)}:3",
            skill_id=skill_id,
            skill_version="v1.2.4",
            operator=operators[2],
            decision=OperatorDecision.APPROVE,
            reason="All metrics healthy",
        )
    )

    return sequence


# ============================================================================
# Pytest Fixtures
# ============================================================================


@pytest.fixture
def operator_a() -> MockOperator:
    """Operator A fixture."""
    return OperatorFactory.operator_a()


@pytest.fixture
def operator_b() -> MockOperator:
    """Operator B fixture."""
    return OperatorFactory.operator_b()


@pytest.fixture
def operator_c() -> MockOperator:
    """Operator C fixture."""
    return OperatorFactory.operator_c()


@pytest.fixture
def operators(operator_a, operator_b, operator_c) -> List[MockOperator]:
    """All operators fixture."""
    return [operator_a, operator_b, operator_c]


@pytest.fixture
def approval_context(operator_a) -> ApprovalContext:
    """Single approval context fixture."""
    return ApprovalContextFactory.create_approval_context(
        approval_id=f"appr:{int(time.time() * 1000)}",
        skill_id="os.delegation_router",
        skill_version="v1.2.4",
        operator=operator_a,
        decision=OperatorDecision.APPROVE,
    )


@pytest.fixture
def audit_trail() -> AuditTrailLineage:
    """Empty audit trail fixture."""
    return AuditTrailLineage(tenant_id="_default")


@pytest.fixture
def live_metrics_simulator() -> LiveMetricsSimulator:
    """Live metrics simulator fixture."""
    return LiveMetricsSimulator("os.delegation_router", failure_scenario=False)


@pytest.fixture
def live_metrics_failure_simulator() -> LiveMetricsSimulator:
    """Live metrics simulator with failure scenario."""
    return LiveMetricsSimulator("os.delegation_router", failure_scenario=True)


@pytest.fixture
def websocket_client() -> MockWebSocketClient:
    """Mock WebSocket client fixture."""
    return MockWebSocketClient()


@pytest.fixture
def test_approval_workflow() -> Dict[str, Any]:
    """Complete test approval workflow fixture."""
    return generate_test_approval_workflow()


@pytest.fixture
def multi_operator_sequence(operators) -> List[ApprovalContext]:
    """Multi-operator approval sequence fixture."""
    return generate_multi_operator_approval_sequence(operators=operators)
