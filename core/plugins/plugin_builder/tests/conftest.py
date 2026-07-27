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
