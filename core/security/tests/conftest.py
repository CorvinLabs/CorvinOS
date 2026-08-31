"""Test fixtures."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from ..context import GateName, GateResult, SecurityContext
from ..implementations import (
    CapabilityCheckerImpl,
    InputValidatorImpl,
    PIIDetectorImpl,
    ContextEngineerImpl,
    AuditRecorderImpl,
)
from ..pipeline import IntegratedSecurityPipeline


@pytest.fixture
def event_loop():
    """Event loop fixture."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def capability_checker():
    """Capability checker fixture."""
    return CapabilityCheckerImpl()


@pytest.fixture
def input_validator():
    """Input validator fixture."""
    return InputValidatorImpl()


@pytest.fixture
def pii_detector():
    """PII detector fixture."""
    return PIIDetectorImpl()


@pytest.fixture
def context_engineer():
    """Context engineer fixture."""
    return ContextEngineerImpl()


@pytest.fixture
def audit_recorder():
    """Audit recorder fixture."""
    return AuditRecorderImpl()


@pytest.fixture
def pipeline(
    capability_checker,
    input_validator,
    pii_detector,
    context_engineer,
    audit_recorder,
):
    """Pipeline fixture."""
    return IntegratedSecurityPipeline(
        capability_checker=capability_checker,
        input_validator=input_validator,
        pii_detector=pii_detector,
        context_engineer=context_engineer,
        audit_recorder=audit_recorder,
    )


@pytest.fixture
def security_context():
    """SecurityContext fixture."""
    return SecurityContext(
        actor="user_123",
        action="list_sessions",
        resource="chat_session",
        capability_required="read_chat_sessions",
        tenant_id="test_tenant",
        transport="flask_route",
        input_data={},
    )
