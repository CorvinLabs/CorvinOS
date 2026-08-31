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
