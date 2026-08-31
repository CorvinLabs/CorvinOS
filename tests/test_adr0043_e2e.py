"""End-to-end tests for ADR-0043: message → classification → routing.

These exercise the PRODUCTION path: classify_and_store_workload_hint from
the production module (not a test copy), and adapter._resolve_os_model
Tier 2.7 consuming the hint as a function parameter — exactly how
call_claude / call_claude_streaming thread it. An earlier revision of this
file hand-assembled step 4 ("simulate reading the env vars") and imported
a duplicate implementation from the bridge-integration test file, so it
stayed green while the feature was dead in production (adversarial review
2026-07-18).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SHARED = _REPO / "operator" / "bridges" / "shared"
for _p in (_REPO, _REPO / "operator", _REPO / "operator" / "bridges", _SHARED):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from workload_classifier import classify_and_store_workload_hint  # type: ignore


def _resolve(profile: dict | None, hint: dict | None) -> "str | None":
    """Call the real Tier-2.7 consumer hermetically: no operator override,
    and CORVIN_HOME pinned to an empty tmp dir so Tier 2.5 cannot read the
    LIVE machine's tenant YAML (refutation round 2026-07-18: an operator
    os_model pin there outranks Tier 2.7 and failed these tests)."""
    import tempfile  # noqa: PLC0415
    import adapter  # type: ignore  # noqa: PLC0415
    with tempfile.TemporaryDirectory() as tmp_home:
        with mock.patch.dict(os.environ, {"CORVIN_HOME": tmp_home}, clear=False):
            os.environ.pop("CORVIN_OS_MODEL_OVERRIDE", None)
            return adapter._resolve_os_model(
                profile, payload_chars=100, engine_id="claude_code",
                tenant_id="_default", workload_hint=hint, chat_key="e2e-test",
            )


def test_e2e_chat_message_routes_to_fast_tier() -> None:
    """Chat message + fast_chat_mode on → fast tier via _resolve_os_model."""
    pytest.importorskip("adapter")
    session: dict[str, Any] = {}
    hint = classify_and_store_workload_hint(
        "hey how was your day, tell me something interesting", session)
    assert hint["workload"] == "chat"
    assert hint["confidence"] >= 0.7

    model = _resolve({"fast_chat_mode": True}, hint)
    assert model == "claude-haiku-4-5-20251001"


def test_e2e_flag_off_is_fully_inert() -> None:
    """With fast_chat_mode off (the default), a high-confidence CHAT hint
    must change NOTHING: the result equals the no-hint result."""
    pytest.importorskip("adapter")
    hint = {"workload": "chat", "confidence": 0.95, "timestamp": 1}
    assert _resolve({}, hint) == _resolve({}, None)
    assert _resolve({"fast_chat_mode": False}, hint) == _resolve({}, None)


def test_e2e_code_and_uncertain_fall_through() -> None:
    """CODE/UNCERTAIN hints never touch the model decision — identical to
    the no-hint path (adaptive tiers keep full authority)."""
    pytest.importorskip("adapter")
    baseline = _resolve({"fast_chat_mode": True}, None)
    for workload in ("code", "uncertain"):
        hint = {"workload": workload, "confidence": 0.99, "timestamp": 1}
        assert _resolve({"fast_chat_mode": True}, hint) == baseline


def test_e2e_coding_request_in_natural_language_never_fast_tier() -> None:
    """The ADR's own risk section: a natural-language coding request must
    never reach the fast tier. The old density classifier said CHAT 1.0
    for all of these."""
    pytest.importorskip("adapter")
    for msg in (
        "Schreib mir eine Python-Funktion die eine Liste sortiert",
        "Write a bash script that backs up my home directory",
        "Fix this bug: TypeError: 'NoneType' object is not iterable",
        "Kannst du diese SQL-Query optimieren: SELECT * FROM users",
        "Refactor adapter.py so _build_spawn_env takes a tenant_id parameter",
        # Paraphrased/anaphoric work requests without any signal noun —
        # these leaked through as CHAT 0.9 in the refutation round.
        "mach das Ding aus Punkt 3 schneller",
        "kannst du das von gestern nochmal anders lösen",
        "can you redo yesterday's solution differently",
        "automate that for me like last time",
        "make the thing from step 3 faster please",
    ):
        session: dict[str, Any] = {}
        hint = classify_and_store_workload_hint(msg, session)
        assert hint["workload"] != "chat", f"coding request classified CHAT: {msg!r}"
        model = _resolve({"fast_chat_mode": True}, hint)
        assert model != "claude-haiku-4-5-20251001", f"fast tier for: {msg!r}"


def test_e2e_explicit_model_pin_beats_workload_routing() -> None:
    """Tier 2 (explicit pin) wins over Tier 2.7 — a pinned user is never
    downgraded, even with the flag on and a confident CHAT hint."""
    pytest.importorskip("adapter")
    hint = {"workload": "chat", "confidence": 0.95, "timestamp": 1}
    model = _resolve({"fast_chat_mode": True, "model": "claude-opus-4-8"}, hint)
    assert model == "claude-opus-4-8"


def test_e2e_routing_decision_is_audited() -> None:
    """ADR-0043 §6: every fast-tier routing decision emits an audit event
    (BUG#15 — the old code died on a NameError inside a blanket except and
    never audited anything)."""
    import adapter  # type: ignore  # noqa: PLC0415
    events: list[tuple] = []

    def _spy(event_type, *a, **kw):
        events.append((event_type, kw))

    hint = {"workload": "chat", "confidence": 0.95, "timestamp": 1}
    with mock.patch.object(adapter, "_audit_event", _spy):
        model = _resolve({"fast_chat_mode": True}, hint)
    assert model == "claude-haiku-4-5-20251001"
    routing = [e for e in events if e[0] == "bridge.workload_model_selection"]
    assert len(routing) == 1
    details = routing[0][1].get("details") or {}
    assert details.get("selected_model") == model
    assert routing[0][1].get("chat_key") == "e2e-test"
    # PII rule: no message content in the audit payload
    assert "message" not in details and "prompt" not in details


def test_e2e_classifier_importable_as_top_level_module() -> None:
    """Production runs adapter.py as a top-level module (__package__ '').
    The dual import pattern used at the call sites must succeed there —
    the old relative-only import raised and silently disabled the feature."""
    snippet = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "try:\n"
        "    from . import workload_classifier as _wc\n"
        "except ImportError:\n"
        "    import workload_classifier as _wc\n"
        "hint = _wc.classify_and_store_workload_hint('hello there', {})\n"
        "assert hint['workload'] == 'chat', hint\n"
        "print('OK')\n" % _SHARED
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet], capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
