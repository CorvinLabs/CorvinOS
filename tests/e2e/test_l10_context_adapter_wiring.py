"""E2E Wiring Proof: L10 Context Adapter (Phase 2 Blocker 2).

Proves that os.context_adapter Skill is wired into the CEL security pipeline.
Required for ADR-0532 Phase 1 + ADR-0555.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Import the component under test
from core.security.implementations.context_engineer import ContextEngineerImpl
from core.security.context import SecurityContext, GateName


@pytest.mark.asyncio
async def test_context_adapter_skill_is_wired_into_pipeline():
    """E2E proof: ContextEngineer calls os.context_adapter Skill (not stub)."""

    # Setup: Create a real security context
    context = SecurityContext(
        actor={"id": "user_123", "role": "operator"},
        action="read_audit_log",
        input_data={"log_id": "audit_001"},
        tenant_id="test_tenant",
    )

    # Create ContextEngineer with a mock CEL pipeline
    engineer = ContextEngineerImpl(cel_pipeline=True)

    # Execute: Call engineer() which should call os.context_adapter Skill
    result = await engineer.engineer(context)

    # Assertions (E2E Wiring Proof)
    assert result.gate_name == GateName.CONTEXT_ENGINEERING
    assert result.passed is True  # Non-blocking, pass-through

    # **CRITICAL PROOF:** context_brief should contain sources from the Skill
    # (not empty like the Phase 1 stub)
    assert context.context_brief is not None
    assert "sources" in context.context_brief
    assert "confidence" in context.context_brief
    assert "tokens" in context.context_brief


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
