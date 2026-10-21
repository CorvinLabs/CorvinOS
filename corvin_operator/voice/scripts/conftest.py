"""Test-tier pin for the ``corvin-a2a`` CLI tests (mirrors ``corvin_operator/bridges/shared/conftest.py``).

``a2a.network`` is member-only (gate 28ad07b54) and ``corvin-a2a create-token`` / ``import-token`` run
the REAL gate since 2026-10-21 (round 2 of the fresh-system review). The CLI tests of the pairing
mechanics presuppose a licensed install, so for ``test_corvin_a2a_*`` files the tier RESOLVER reports
``member`` by default — the gate, the limits and the audit run unmodified. A test of the gate's own
verdict opts out with ``@pytest.mark.licence_tier("free")``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_A2A_TEST_FILE_MARKERS = ("a2a",)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "licence_tier(name): tier the licensing API resolves for an A2A CLI test (default 'member')",
    )


@pytest.fixture(autouse=True)
def _a2a_member_tier(request, monkeypatch):
    name = Path(str(request.node.fspath)).name
    if not any(m in name for m in _A2A_TEST_FILE_MARKERS):
        yield
        return
    marker = request.node.get_closest_marker("licence_tier")
    tier = marker.args[0] if marker else "member"
    try:
        from corvin_operator.license import capability_api
    except ImportError:  # licensing absent: the gate refuses on its own (fail-closed)
        yield
        return
    monkeypatch.setattr(capability_api, "active_tier", lambda **_k: tier)
    try:
        import a2a_license_gate  # type: ignore[import-not-found]
        a2a_license_gate.reset_allow_cache()
    except Exception:  # noqa: BLE001 — the ALLOW cache is a convenience
        pass
    yield
    try:
        a2a_license_gate.reset_allow_cache()
    except Exception:  # noqa: BLE001
        pass
