"""Shared pytest fixtures for the core/license test suite.

The RS256 keypair / JWT / pinned-pubkey fixtures and the nine test modules that
used them exercised ``corvin_license`` — the ADR-0111 legacy stack deleted in
853ee7c54 (ADR-0703 §3.1). They could no longer be collected (ModuleNotFoundError)
and were removed with their subject (adversarial review 2026-09-27).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_THIS = Path(__file__).resolve().parent
_PLUGIN = _THIS.parent
_REPO = _PLUGIN.parent.parent
_FORGE = _REPO / "corvin_operator" / "forge"

# Make the plugin + forge importable without bootstrap.
for p in (_PLUGIN, _FORGE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


@pytest.fixture
def sandbox_home(tmp_path, monkeypatch):
    """Set CORVIN_HOME to a fresh tmpdir + provision _default tenant tree."""
    home = tmp_path / "corvin"
    home.mkdir(parents=True)
    (home / "tenants" / "_default" / "global" / "forge").mkdir(parents=True)
    # Strangler-fig symlink (Phase 1) — global/ points at _default/global/
    (home / "global").symlink_to(home / "tenants" / "_default" / "global")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    return home
