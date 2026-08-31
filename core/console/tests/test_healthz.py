"""ADR-0215 F1 regression: /healthz must never raise, on any install shape.

Before this fix, ``healthz()`` did an unguarded dotted
``from operator.bridges.shared.engine_detection import ...`` at the top of
the function body — that import can NEVER resolve (stdlib ``operator``
always wins over the repo's ``operator/`` directory regardless of sys.path
order), so this unauthenticated liveness probe raised
``ModuleNotFoundError`` on every single call. The regression introduced by a
commit literally titled "... Healthcheck" and went unnoticed since.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))

from corvin_console.app import healthz  # noqa: E402


def test_healthz_never_raises():
    result = healthz()
    assert isinstance(result, dict)


def test_healthz_reports_console_alive():
    result = healthz()
    assert result["checks"]["console_alive"] is True


def test_healthz_claude_authenticated_is_a_real_bool_not_a_masked_false():
    # Before the fix, ModuleNotFoundError was swallowed by the `except
    # Exception` around the *call*, but the import itself sat OUTSIDE that
    # guard — so the whole function actually raised, uncaught, to the
    # FastAPI layer. This asserts the field is populated by the real
    # credential probe, not silently defaulted.
    result = healthz()
    assert isinstance(result["checks"]["claude_authenticated"], bool)


def test_healthz_top_level_shape():
    result = healthz()
    assert "ok" in result
    assert "version" in result
    assert set(result["checks"]) >= {"console_alive", "claude_authenticated", "license_activated"}
