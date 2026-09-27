"""The gateway registers its deployment identity from CORVIN_* variables only.

Until 2026-09-27 the lifespan read bare ``INSTANCE_ID`` / ``TENANT_ID`` — not
Corvin variables (project identity hard cut) — so a gateway started with
``CORVIN_TENANT_ID=acme`` registered under ``_default`` for drift detection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "forge"))

from corvin_gateway.app import _deployment_identity  # noqa: E402


def test_corvin_vars_are_read(monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    monkeypatch.setenv("CORVIN_INSTANCE_ID", "gw-7")
    monkeypatch.setenv("TENANT_ID", "wrong")
    monkeypatch.setenv("INSTANCE_ID", "wrong")
    assert _deployment_identity() == ("gw-7", "acme")


def test_bare_vars_are_ignored(monkeypatch):
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    monkeypatch.delenv("CORVIN_INSTANCE_ID", raising=False)
    monkeypatch.setenv("TENANT_ID", "legacy")
    monkeypatch.setenv("INSTANCE_ID", "legacy")
    assert _deployment_identity() == ("gateway-local", "_default")


def test_invalid_tenant_raises(monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "../etc")
    with pytest.raises(Exception):
        _deployment_identity()
