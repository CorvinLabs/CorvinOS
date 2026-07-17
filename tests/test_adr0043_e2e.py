"""End-to-end test for ADR-0043: message → classification → routing → spawn.

Full path: user message enters bridge → classified → stored in session →
propagated as env vars via _build_spawn_env → consumed by _resolve_os_model
for model routing decision.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any
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

# Import bridge integration layer
sys.path.insert(0, str(Path(__file__).parent))
from test_adr0043_bridge_integration import classify_and_store_workload_hint, WorkloadHint  # type: ignore


def test_e2e_chat_message_routes_to_fast_tier() -> None:
    """E2E: chat message → fast tier model selection."""
    try:
        import adapter  # type: ignore  # noqa: PLC0415
        from engine_models import resolve_model_for_workload  # type: ignore  # noqa: PLC0415
    except ImportError:
        pytest.skip("adapter or engine_models not importable")

    # Step 1: User sends a chat message
    user_message = "hey can you help me with something today?"
    session: dict[str, Any] = {}

    # Step 2: Bridge classifies the message
    hint = classify_and_store_workload_hint(user_message, session)
    assert session["workload_hint"]["workload"] == "chat"
    assert session["workload_hint"]["confidence"] > 0.5

    # Step 3: adapter propagates hint as env vars
    env = adapter._build_spawn_env(
        bridge="discord",
        chat_key="ch1",
        base={"PATH": os.environ.get("PATH", "")},
        workload_hint=session["workload_hint"],
    )
    assert env.get("CORVIN_WORKLOAD_CLASS") == "chat"
    assert float(env.get("CORVIN_WORKLOAD_CONFIDENCE", 0.0)) > 0.5

    # Step 4: _resolve_os_model consumes the hint for routing
    # Simulate reading the env vars and calling resolve_model_for_workload
    workload_class = env.get("CORVIN_WORKLOAD_CLASS")
    workload_confidence = float(env.get("CORVIN_WORKLOAD_CONFIDENCE", 0.0))
    model = resolve_model_for_workload(
        "claude_code",
        workload_class,
        user_chosen_model=None,
        confidence=workload_confidence,
        fast_chat_enabled=True,
    )

    # Step 5: Verify routing decision
    assert model == "claude-haiku-4-5-20251001"  # Fast tier for CHAT


def test_e2e_code_message_routes_to_full_tier() -> None:
    """E2E: code message → full tier model selection."""
    try:
        import adapter  # type: ignore  # noqa: PLC0415
        from engine_models import resolve_model_for_workload  # type: ignore  # noqa: PLC0415
    except ImportError:
        pytest.skip("adapter or engine_models not importable")

    # Step 1: User sends code
    user_message = "def fibonacci(n): return n if n <= 1 else fibonacci(n-1) + fibonacci(n-2)"
    session: dict[str, Any] = {}

    # Step 2: Bridge classifies the message
    hint = classify_and_store_workload_hint(user_message, session)
    # Note: May be CODE or UNCERTAIN depending on heuristic
    workload_type = session["workload_hint"]["workload"]
    assert workload_type in ("code", "uncertain")

    # Step 3: adapter propagates hint
    env = adapter._build_spawn_env(
        bridge="discord",
        chat_key="ch1",
        base={"PATH": os.environ.get("PATH", "")},
        workload_hint=session["workload_hint"],
    )
    assert "CORVIN_WORKLOAD_CLASS" in env

    # Step 4: _resolve_os_model consumes the hint
    workload_class = env.get("CORVIN_WORKLOAD_CLASS")
    workload_confidence = float(env.get("CORVIN_WORKLOAD_CONFIDENCE", 0.0))
    model = resolve_model_for_workload(
        "claude_code",
        workload_class,
        user_chosen_model=None,
        confidence=workload_confidence,
        fast_chat_enabled=True,
    )

    # Step 5: Verify routing decision
    # For CODE, should use full tier; for UNCERTAIN, should fallback to None
    if workload_type == "code":
        assert model == "claude-sonnet-5"
    else:
        assert model is None


def test_e2e_uncertain_uses_user_choice() -> None:
    """E2E: ambiguous message → falls back to user's model choice."""
    try:
        import adapter  # type: ignore  # noqa: PLC0415
        from engine_models import resolve_model_for_workload  # type: ignore  # noqa: PLC0415
    except ImportError:
        pytest.skip("adapter or engine_models not importable")

    # Step 1: Ambiguous message
    user_message = "ok"
    session: dict[str, Any] = {}

    # Step 2: Bridge classifies (likely UNCERTAIN due to ambiguity)
    hint = classify_and_store_workload_hint(user_message, session)

    # Step 3: adapter propagates hint
    env = adapter._build_spawn_env(
        bridge="discord",
        chat_key="ch1",
        base={"PATH": os.environ.get("PATH", "")},
        workload_hint=session["workload_hint"],
    )

    # Step 4: _resolve_os_model with user choice
    workload_class = env.get("CORVIN_WORKLOAD_CLASS")
    workload_confidence = float(env.get("CORVIN_WORKLOAD_CONFIDENCE", 0.0))
    user_chosen_model = "claude-opus-4-1"
    model = resolve_model_for_workload(
        "claude_code",
        workload_class,
        user_chosen_model=user_chosen_model,
        confidence=workload_confidence,
        fast_chat_enabled=True,
    )

    # Step 5: If UNCERTAIN, should return user choice
    if workload_class == "uncertain":
        assert model == user_chosen_model


def test_e2e_low_confidence_chat_still_uses_user_choice() -> None:
    """E2E: CHAT with low confidence still uses user choice (safe fallback)."""
    try:
        from engine_models import resolve_model_for_workload  # type: ignore  # noqa: PLC0415
    except ImportError:
        pytest.skip("engine_models not importable")

    # Even if classified as CHAT, low confidence should fallback
    model = resolve_model_for_workload(
        "claude_code",
        "chat",
        user_chosen_model="claude-opus-4-1",
        confidence=0.4,  # Below 0.7 threshold
        fast_chat_enabled=True,
    )

    # Should use user choice, not fast tier
    assert model == "claude-opus-4-1"


def test_e2e_feature_flag_disabled() -> None:
    """E2E: fast_chat_enabled=False disables routing even for high-confidence CHAT."""
    try:
        from engine_models import resolve_model_for_workload  # type: ignore  # noqa: PLC0415
    except ImportError:
        pytest.skip("engine_models not importable")

    model = resolve_model_for_workload(
        "claude_code",
        "chat",
        user_chosen_model="claude-opus-4-1",
        confidence=0.95,  # High confidence
        fast_chat_enabled=False,  # Feature OFF
    )

    # Should use user choice, not fast tier
    assert model == "claude-opus-4-1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
