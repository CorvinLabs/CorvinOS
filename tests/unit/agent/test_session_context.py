"""Tests for SessionContext preservation (ADR-0403)."""

import pytest
from datetime import datetime
from core.agent.session_context import (
    SessionContext, MemoryContext, ContextConflictResolver
)


def test_session_context_frozen():
    """SessionContext is immutable."""
    ctx = SessionContext(
        user_request="mach Week 3-4",
        explicit_constraints={"rollout": "100%"},
        task_scope="production",
        session_timestamp=datetime.now()
    )

    # Attempting to modify should fail
    with pytest.raises((AttributeError, TypeError)):
        ctx.user_request = "mach Week 1"


def test_session_is_authoritative_keyword_match():
    """Session owns topics explicitly mentioned by user."""
    ctx = SessionContext(
        user_request="mach Week 3-4 production ready",
        explicit_constraints={},
        task_scope="autonomy",
        session_timestamp=datetime.now()
    )

    assert ctx.is_authoritative_on("Week")
    assert ctx.is_authoritative_on("production")
    assert ctx.is_authoritative_on("ready")
    assert not ctx.is_authoritative_on("canary")


def test_session_is_authoritative_constraint_match():
    """Session owns topics from explicit constraints."""
    ctx = SessionContext(
        user_request="do adversarial review",
        explicit_constraints={"rollout": "100%", "strategy": "direct"},
        task_scope="review",
        session_timestamp=datetime.now()
    )

    assert ctx.is_authoritative_on("rollout")
    assert ctx.is_authoritative_on("strategy")


def test_memory_can_augment_when_session_silent():
    """Memory can augment topics session doesn't own."""
    session = SessionContext(
        user_request="mach Week 3-4",
        explicit_constraints={},
        task_scope="orchestration",
        session_timestamp=datetime.now()
    )
    memory = MemoryContext(
        related_adrs=["ADR-0401"],
        prior_findings=["prior task used two-layer model"],
        architectural_patterns=[]
    )

    # Session doesn't mention "memory", so memory can augment
    assert memory.can_augment(session, "memory_patterns")


def test_memory_cannot_augment_when_session_owns():
    """Memory cannot override session-owned topics."""
    session = SessionContext(
        user_request="mach 100% rollout",
        explicit_constraints={},
        task_scope="production",
        session_timestamp=datetime.now()
    )
    memory = MemoryContext(
        related_adrs=["ADR-0363"],
        prior_findings=["canary strategy worked before"],
        architectural_patterns=[]
    )

    # Session owns "rollout", so memory CANNOT augment
    assert not memory.can_augment(session, "rollout")


def test_conflict_resolver_session_wins():
    """Conflict resolver favors session when it owns topic."""
    session = SessionContext(
        user_request="100% production ready",
        explicit_constraints={},
        task_scope="production",
        session_timestamp=datetime.now()
    )
    memory = MemoryContext(
        related_adrs=["ADR-0222"],
        prior_findings=["canary approach saved time before"],
        architectural_patterns=[]
    )

    resolved = ContextConflictResolver.resolve(session, memory, "production")

    assert resolved["source"] == "SESSION"
    assert "authoritative" in resolved["reason"].lower()


def test_conflict_resolver_memory_augments():
    """Conflict resolver allows memory augmentation when safe."""
    session = SessionContext(
        user_request="mach Week 3-4",
        explicit_constraints={},
        task_scope="orchestration",
        session_timestamp=datetime.now()
    )
    memory = MemoryContext(
        related_adrs=["ADR-0401", "ADR-0402"],
        prior_findings=["two-layer model proved effective"],
        architectural_patterns=[]
    )

    resolved = ContextConflictResolver.resolve(session, memory, "context_layers")

    assert resolved["source"] == "MEMORY"


def test_declare_conflict_output():
    """Conflict declaration is user-visible."""
    session = SessionContext(
        user_request="adversarial review production ready",
        explicit_constraints={"findings": "zero"},
        task_scope="review",
        session_timestamp=datetime.now()
    )
    memory = MemoryContext(
        related_adrs=["ADR-0403"],
        prior_findings=[],
        architectural_patterns=[]
    )

    # Session owns "production" and "ready"
    decl = ContextConflictResolver.declare_conflict(session, memory, "production")
    assert "SESSION" in decl
    assert "✓" in decl  # Visual indicator


def test_get_authority_reason():
    """Authority reason explains why session owns topic."""
    ctx = SessionContext(
        user_request="mach Week 3-4 production",
        explicit_constraints={"rollout": "100%"},
        task_scope="autonomy",
        session_timestamp=datetime.now()
    )

    reason = ctx.get_authority_reason("Week")
    assert reason is not None
    assert "explicitly mentioned" in reason.lower()

    reason = ctx.get_authority_reason("rollout")
    assert reason is not None
    assert "constraint" in reason.lower()
