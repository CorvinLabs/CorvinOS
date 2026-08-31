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
