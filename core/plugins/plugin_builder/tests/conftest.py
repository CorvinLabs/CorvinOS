"""Audit-chain isolation for the Plugin-Builder suite (same reasoning as
``core/plugins/tests/conftest.py``: this suite lives next to the package it
tests, not under the repo-root ``tests/`` directory the root conftest covers,
so it needs its own redirect. None of these tests currently load a real
plugin registry (only ``ops.launcher.corvin.plugin_cmd``, which never touches
the audit chain), but the fixture is cheap and keeps the suite safe if a
future test does.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_audit_chain_for_plugin_builder_tests(monkeypatch, tmp_path):
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))


@pytest.fixture(autouse=True)
def _shipped_defaults_not_the_operators_flags(monkeypatch, tmp_path):
    """Same reasoning as ``core/plugins/tests/conftest.py``: ``CORVIN_HOME``
    decides both which feature flags this suite reads and where
    ``index_store`` writes its ``plugin_builder_index.json``. Left at the
    developer's real home, a test run reads their switched-on flags and writes
    scaffold records into their live tenant state.
    """
    home = tmp_path / "corvin_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))


@pytest.fixture(autouse=True)
def _member_tier_for_plugin_builder(monkeypatch):
    """``/plugin-builder`` is a member capability (ADR-0701 G4, fail-closed).

    An unlicensed test environment resolves to the free tier, where every
    ``command()`` now returns the member-seat refusal. Tests that drive the
    builder therefore run as ``member``; the refusal itself is proven by
    ``test_turn_licence_gate.py``, which re-pins ``free`` explicitly. The pin
    replaces the tier resolver the real ``require_capability`` reads — the
    gate, its audit record and its verdict stay real."""
    import corvin_operator.license.capability_api as _ca
    monkeypatch.setattr(_ca, "active_tier", lambda **_k: "member")
