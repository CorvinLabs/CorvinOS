"""
Integration layer for ADR-0043: Bridge → Workload Classifier → Session Hint.

This module provides the bridge integration function that:
1. Takes a user message (from Discord/Web/CLI)
2. Classifies it
3. Stores the workload_hint in a session dict
4. Logs an audit event

Later, _resolve_os_model will consult this hint.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (
    _REPO,
    _REPO / "operator",
    _REPO / "operator" / "bridges",
    _REPO / "operator" / "bridges" / "shared",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from workload_classifier import classify_workload, WorkloadType  # type: ignore


class WorkloadHint(TypedDict):
    """Workload classification result stored in session."""
    workload: str  # "chat" | "code" | "uncertain"
    confidence: float
    timestamp: int


def classify_and_store_workload_hint(
    user_message: str,
    session: dict[str, Any],
    audit_callback: callable | None = None,
) -> WorkloadHint:
    """
    ADR-0043 Phase 1: Classify user message and store hint in session.

    Called early in the bridge request handler (after message is received,
    before _build_spawn_env is called).

    Args:
        user_message: The raw user input from Discord/Web/CLI
        session: Session dict to store the workload_hint in
        audit_callback: Optional callback to log audit event

    Returns:
        WorkloadHint with workload type, confidence, and timestamp

    Side-effects:
        - Updates session["workload_hint"] with the classification result
        - Calls audit_callback if provided (for compliance)
    """
    if not user_message or not isinstance(user_message, str):
        # Classify empty as CHAT (safe default)
        hint: WorkloadHint = {
            "workload": "chat",
            "confidence": 1.0,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
        session["workload_hint"] = hint
        return hint

    # Classify the message (pure function, non-blocking)
    classification_result = classify_workload(user_message)

    # Build the hint
    hint = {
        "workload": str(classification_result.workload.value),
        "confidence": classification_result.confidence,
        "timestamp": int(datetime.now().timestamp() * 1000),
    }
    session["workload_hint"] = hint

    # Audit trail: log the classification decision
    if audit_callback:
        try:
            audit_callback({
                "event_type": "workload_classification",
                "workload": hint["workload"],
                "confidence": hint["confidence"],
                "message_hash": hash(user_message) % (10 ** 8),  # truncate to int64
                "timestamp": hint["timestamp"],
            })
        except Exception as e:
            # Audit failure is non-fatal; never break the request
            print(f"[WARN] audit callback failed: {e}")

    return hint


class TestBridgeIntegrationLayer:
    """Tests for the bridge integration layer."""

    def test_classify_and_store_chat_message(self) -> None:
        """Classifying a chat message stores hint in session."""
        session: dict[str, Any] = {}
        msg = "hey how are you doing today"

        hint = classify_and_store_workload_hint(msg, session)

        assert "workload_hint" in session
        assert session["workload_hint"]["workload"] == "chat"
        assert session["workload_hint"]["confidence"] > 0.5
        assert session["workload_hint"]["timestamp"] > 0
        assert hint == session["workload_hint"]

    def test_classify_and_store_code_message(self) -> None:
        """Classifying code stores CODE hint."""
        session: dict[str, Any] = {}
        msg = "def foo(): pass"

        hint = classify_and_store_workload_hint(msg, session)

        assert session["workload_hint"]["workload"] in ["code", "uncertain"]
        assert session["workload_hint"]["confidence"] >= 0.0

    def test_classify_empty_message(self) -> None:
        """Empty message classifies as CHAT (safe default)."""
        session: dict[str, Any] = {}
        hint = classify_and_store_workload_hint("", session)

        assert hint["workload"] == "chat"
        assert hint["confidence"] == 1.0

    def test_audit_callback_called(self) -> None:
        """Audit callback is invoked with classification event."""
        session: dict[str, Any] = {}
        audit_events: list[dict] = []

        def mock_audit(event: dict) -> None:
            audit_events.append(event)

        msg = "hello world"
        classify_and_store_workload_hint(msg, session, audit_callback=mock_audit)

        assert len(audit_events) == 1
        event = audit_events[0]
        assert event["event_type"] == "workload_classification"
        assert event["workload"] in ["chat", "code", "uncertain"]
        assert "confidence" in event
        assert "timestamp" in event

    def test_audit_callback_failure_not_fatal(self) -> None:
        """If audit fails, classification still completes."""
        session: dict[str, Any] = {}

        def failing_audit(event: dict) -> None:
            raise RuntimeError("audit system down")

        msg = "test message"
        hint = classify_and_store_workload_hint(msg, session, audit_callback=failing_audit)

        # Classification succeeds despite audit failure
        assert "workload_hint" in session
        assert hint["workload"] is not None

    def test_confidence_stored_for_all_types(self) -> None:
        """Confidence is stored for all classification types."""
        test_msgs = [
            "hello",  # CHAT
            "def foo(): pass",  # CODE or UNCERTAIN
            "please explain this algorithm",  # CHAT
        ]

        for msg in test_msgs:
            session: dict[str, Any] = {}
            hint = classify_and_store_workload_hint(msg, session)

            assert 0.0 <= hint["confidence"] <= 1.0
            assert hint["timestamp"] > 0

    def test_multiple_classifications_in_session(self) -> None:
        """Session can store multiple classification hints (e.g., multi-turn)."""
        session: dict[str, Any] = {}

        # Turn 1: chat
        hint1 = classify_and_store_workload_hint("hi there", session)
        assert session["workload_hint"]["workload"] == "chat"

        # Turn 2: code (overwrites hint1)
        hint2 = classify_and_store_workload_hint("def foo(): pass", session)
        assert session["workload_hint"]["workload"] in ["code", "uncertain"]

        # Both hints have timestamps
        assert hint1["timestamp"] <= hint2["timestamp"]

    def test_hint_is_typed_dict(self) -> None:
        """Returned hint matches WorkloadHint type."""
        session: dict[str, Any] = {}
        hint = classify_and_store_workload_hint("test", session)

        # Type checking (static, but verify fields exist)
        assert "workload" in hint
        assert "confidence" in hint
        assert "timestamp" in hint
        assert isinstance(hint["workload"], str)
        assert isinstance(hint["confidence"], float)
        assert isinstance(hint["timestamp"], int)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
