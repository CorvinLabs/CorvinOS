"""Delegate test isolation.

run_delegate enforces fail-closed license gates (engines_allowed +
compute_units_per_day, ADR-0149/0150). The delegate test suite mocks engines and
must not be metered by the live daily-quota gate, so activate the intended dual-env
test bypass (BOTH CORVIN_AGENTS_SKIP_LIVE=1 AND CORVIN_INTEGRATION_TEST=1) for every
test. Tests that deliberately verify a gate FIRES (test_license_engines_gate.py)
override this by monkeypatch.delenv in their own fixture.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _delegate_license_test_bypass():
    _saved = {
        k: os.environ.get(k)
        for k in ("CORVIN_AGENTS_SKIP_LIVE", "CORVIN_INTEGRATION_TEST")
    }
    os.environ["CORVIN_AGENTS_SKIP_LIVE"] = "1"
    os.environ["CORVIN_INTEGRATION_TEST"] = "1"
    try:
        yield
    finally:
        for k, v in _saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(autouse=True)
def _forge_root_sandbox(monkeypatch, tmp_path):
    """Keep spawned forge MCP servers out of the checkout's project-scope forge.

    ``forge.py`` resolves its registry root (and with it the audit chain its
    security events go to) from ``FORGE_ROOT``, else ``scope_root(detect_scope())``
    — for a cwd inside a git checkout that is ``<checkout>/.corvin/forge``,
    regardless of ``CORVIN_HOME``. ``test_live_e2e.py::ForgeMcpLiveTests`` spawns
    the real server from the repo cwd, so every run appended a
    ``compute.worker_unreachable`` record to ``<checkout>/.corvin/forge/audit.jsonl``
    — in the live checkout, the live install's file (caught by the root conftest's
    chain tripwire, 2026-09-27). A test that needs its own root still wins.
    """
    monkeypatch.setenv("FORGE_ROOT", str(tmp_path / "forge-root"))


@pytest.fixture(autouse=True)
def _restore_corvin_home():
    """Undo the suite's unconditional ``os.environ.pop("CORVIN_HOME")`` teardowns.

    Several unittest classes here (test_mcp_server, test_output_judge,
    test_delegation AuditChainTests, ...) set ``CORVIN_HOME`` in ``setUp`` and
    ``pop`` it in ``tearDown`` — leaving it UNSET, not restored. Unset means the
    repo-marker home, i.e. the live install when the suite runs in the live
    checkout (incident 2026-09-27). Restore the value the test started with.
    """
    saved = os.environ.get("CORVIN_HOME")
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("CORVIN_HOME", None)
        else:
            os.environ["CORVIN_HOME"] = saved
