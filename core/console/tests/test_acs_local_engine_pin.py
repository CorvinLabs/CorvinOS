"""Regression: the worker-engine (ACS/WDAT) graph must populate when delegation
runs on a fresh LOCAL (Hermes/Ollama) install.

Root cause it guards: the delegation branch pinned the ACS model to local ONLY
inside `if _os_model:`. When the model selector was unavailable (_os_model=None)
on a hermes OS engine, ACS fell back to its claude-sonnet default → routed to
claude_code → manager raised "claude CLI not found" → workers_spawned=0 → empty
worker graph. _acs_local_pin_model now always yields a concrete local model for
hermes, which _resolve_worker_engine routes to the Hermes engine.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge",
           _REPO / "operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_console import chat_runtime as CR  # type: ignore


def test_pin_none_for_cloud_engine():
    # Cloud OS engines keep their existing worker cost-tier fallback (no pin).
    assert CR._acs_local_pin_model("claude_code", "claude-opus-4-8", "_default") is None
    assert CR._acs_local_pin_model("codex_cli", None, "_default") is None


def test_pin_local_model_for_hermes_even_when_os_model_none():
    # The load-bearing case: hermes + no selected model must STILL pin a concrete
    # local model (not fall through to ACS's claude default).
    pinned = CR._acs_local_pin_model("hermes", None, "_default")
    assert pinned and isinstance(pinned, str)
    assert "claude" not in pinned.lower()


def test_pin_respects_explicit_os_model():
    assert CR._acs_local_pin_model("hermes", "qwen3:8b", "_default") == "qwen3:8b"


# A tenant with NO tenant.corvin.yaml → no spec.default_worker_engine override,
# so _resolve_worker_engine falls to the model-name heuristic — i.e. a FRESH
# install (the template ships default_worker_engine commented out).
_FRESH = "fresh-install-no-config-tenant"


def test_pinned_model_routes_to_hermes_engine():
    # End-to-end invariant: on a fresh install (no default_worker_engine), the
    # pinned local model routes to the Hermes engine — the property that makes
    # delegation produce workers locally instead of dying on a missing claude CLI.
    import acs_runtime  # type: ignore
    pinned = CR._acs_local_pin_model("hermes", "qwen3:8b", _FRESH)
    engine_id, _resolved = acs_runtime._resolve_worker_engine(pinned, _FRESH)
    assert engine_id == "hermes", f"pinned local model {pinned!r} must route to hermes, got {engine_id}"


def test_bug_control_claude_default_routes_to_claude():
    # Proves WHY the unpinned path broke: ACS's claude-sonnet default routes to
    # claude_code, which needs the claude CLI (absent on a fresh hermes install).
    import acs_runtime  # type: ignore
    engine_id, _ = acs_runtime._resolve_worker_engine("claude-sonnet-4-6", _FRESH)
    assert engine_id == "claude_code"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
