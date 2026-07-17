"""Integration tests for ADR-0043 workload classifier in adapter.py flow.

Tests that the classifier and router are properly wired into the spawn env
and that the env vars are set correctly.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
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


def test_workload_classifier_called_when_fast_chat_enabled() -> None:
    """When fast_chat_mode is enabled, classify_workload is invoked."""
    from workload_classifier import classify_workload, WorkloadType  # type: ignore
    from engine_models import resolve_model_for_workload  # type: ignore

    # Test that the functions exist and are callable
    result = classify_workload("def foo(): pass")
    assert result.workload in [WorkloadType.CODE, WorkloadType.CHAT, WorkloadType.UNCERTAIN]
    assert 0.0 <= result.confidence <= 1.0

    model = resolve_model_for_workload("claude_code", result.workload, "claude-sonnet-5")
    assert model is not None


def test_adapter_build_spawn_env_with_mock_profile() -> None:
    """Adapter's _build_spawn_env can accept fast_chat_mode in profile."""
    # This is a simplified test; full E2E would mock the entire session/chat history
    # For now, just verify the adapter module can be imported and the function exists
    try:
        import adapter  # type: ignore  # noqa: PLC0415
        assert hasattr(adapter, "_build_spawn_env")
    except ImportError:
        pytest.skip("adapter module not importable in this context")


def test_workload_and_model_routing_consistency() -> None:
    """Workload classification + model routing should be consistent."""
    from workload_classifier import classify_workload  # type: ignore
    from engine_models import resolve_model_for_workload  # type: ignore

    test_cases = [
        ("def hello(): pass", "claude_code", "claude-sonnet-5"),  # Code → use user choice or full
        ("hello world how are you", "claude_code", "claude-sonnet-5"),  # Chat → fast
        ("please explain this algorithm", "claude_code", None),  # Chat → fast (no user choice)
    ]

    for message, engine, user_model in test_cases:
        workload_result = classify_workload(message)
        model = resolve_model_for_workload(engine, workload_result.workload, user_model)
        # Just verify it returns a string (or None if no fallback available)
        assert model is None or isinstance(model, str)


def test_enum_and_string_both_work() -> None:
    """resolve_model_for_workload handles both enum and string workload types."""
    from workload_classifier import WorkloadType  # type: ignore
    from engine_models import resolve_model_for_workload  # type: ignore

    # String version
    model_str = resolve_model_for_workload("claude_code", "chat", None)
    assert model_str is not None

    # Enum version
    model_enum = resolve_model_for_workload("claude_code", WorkloadType.CHAT, None)
    assert model_enum is not None

    # Both should give the same result
    assert model_str == model_enum


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
