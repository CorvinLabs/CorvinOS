"""Shared fixtures for the SkillForge test suite.

Skill authoring is licence-gated (ADR-0701 G2: ``forge.create`` is a
member-tier capability, fail-closed). The registry-mechanics tests in this
directory exercise create/grade/promote/delete, which presuppose a licensed
install — so by default they run as the ``member`` tier. The gate itself is
NOT mocked: ``require_capability`` runs for real (limits matrix, audit on the
sandboxed tenant chain); only the tier resolver is pinned.

Tests that exercise the gate's own verdicts (``test_licence_gate.py``) opt out
with ``@pytest.mark.licence_tier("free")`` or patch it themselves.
"""
from __future__ import annotations

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "licence_tier(name): tier the licensing API resolves for this test "
        "(default 'member')",
    )


@pytest.fixture(autouse=True)
def _skill_forge_licence_tier(request, monkeypatch):
    marker = request.node.get_closest_marker("licence_tier")
    tier = marker.args[0] if marker else "member"
    try:
        from corvin_operator.license import capability_api
    except ImportError:  # licensing absent → the gate refuses (fail-closed)
        yield
        return
    monkeypatch.setattr(capability_api, "active_tier", lambda **_k: tier)
    yield
