"""``a2a_license_gate`` — the one module behind the member-only network (gate 28ad07b54).

Enforcement (``require_a2a_network``): an ALLOW is remembered for a window, a DENY never.
Status (``network_status``, round 2 of the fresh-system review, 2026-10-21): a read-only display
remembers BOTH verdicts for ``STATUS_CACHE_TTL_S`` — the console polls it every 4 s per open peer
chat and each DENY evaluation writes a deny pair to the chain, which on a Free install buried the
real events. Both caches fall with ``reset_allow_cache`` (licence reload).

The licence API is the only thing stubbed; the tier marker (conftest) decides the verdict.
"""
from __future__ import annotations

import a2a_license_gate as gate
import pytest

from corvin_operator.license import capability_api


@pytest.fixture(autouse=True)
def _fresh_caches():
    gate.reset_allow_cache()
    yield
    gate.reset_allow_cache()


@pytest.fixture()
def evaluations(monkeypatch):
    """Count the real evaluations (each one is an audit record on a real install)."""
    calls: list[str] = []
    real = capability_api.require_capability

    def counting(capability, requested=1, *, tenant_id, entry_point):
        calls.append(entry_point)
        return real(capability, requested, tenant_id=tenant_id, entry_point=entry_point)
    monkeypatch.setattr(capability_api, "require_capability", counting)
    return calls


def test_member_allow_is_evaluated_once_per_window(evaluations):
    for _ in range(5):
        gate.require_a2a_network(entry_point="t", tenant_id="_default")
    assert len(evaluations) == 1


@pytest.mark.licence_tier("free")
def test_free_deny_is_remembered_briefly_and_a_reload_forgets_it(evaluations, monkeypatch):
    for _ in range(3):
        with pytest.raises(gate.A2ANetworkDenied):
            gate.require_a2a_network(entry_point="t", tenant_id="_default")
    assert len(evaluations) == 1, "every probe of a Free install wrote a deny record"
    gate.reset_allow_cache()   # what validator.reload_from_disk does when the token on disk changed
    with pytest.raises(gate.A2ANetworkDenied):
        gate.require_a2a_network(entry_point="t", tenant_id="_default")
    assert len(evaluations) == 2
    import time
    expiry = gate._DENY_CACHE["_default"]
    monkeypatch.setattr(time, "monotonic", lambda: expiry + 1)
    with pytest.raises(gate.A2ANetworkDenied):
        gate.require_a2a_network(entry_point="t", tenant_id="_default")
    assert len(evaluations) == 3


def test_a_seat_that_arrives_is_honoured_after_the_reload(evaluations, monkeypatch):
    from corvin_operator.license import capability_api as _cap
    monkeypatch.setattr(_cap, "active_tier", lambda **_k: "free")
    with pytest.raises(gate.A2ANetworkDenied):
        gate.require_a2a_network(entry_point="t", tenant_id="_default")
    monkeypatch.setattr(_cap, "active_tier", lambda **_k: "member")
    with pytest.raises(gate.A2ANetworkDenied):   # within the window the deny still stands…
        gate.require_a2a_network(entry_point="t", tenant_id="_default")
    gate.reset_allow_cache()                      # …until the reload that loaded the new token
    gate.require_a2a_network(entry_point="t", tenant_id="_default")
    assert len(evaluations) == 2


@pytest.mark.licence_tier("free")
def test_status_read_remembers_a_deny_for_the_window(evaluations):
    first = gate.network_status(entry_point="console.a2a_pair", tenant_id="_default")
    assert first == {"allowed": False, "reason": "member_seat_required"}
    for _ in range(20):
        assert gate.network_status(entry_point="console.a2a_pair", tenant_id="_default") == first
    assert len(evaluations) == 1, "a status poll re-evaluated (and audited) the deny"
    # enforcement after the status read: the same deny, remembered for its own window
    with pytest.raises(gate.A2ANetworkDenied):
        gate.require_a2a_network(entry_point="remote_trigger_sender.send", tenant_id="_default")
    assert len(evaluations) == 1


def test_status_read_remembers_an_allow_and_a_reload_forgets_it(evaluations):
    assert gate.network_status(entry_point="console.a2a_pair", tenant_id="_default") == {"allowed": True, "reason": None}
    gate.network_status(entry_point="console.a2a_pair", tenant_id="_default")
    assert len(evaluations) == 1
    gate.reset_allow_cache()
    gate.network_status(entry_point="console.a2a_pair", tenant_id="_default")
    assert len(evaluations) == 2


@pytest.mark.licence_tier("free")
def test_status_read_expires(monkeypatch, evaluations):
    gate.network_status(entry_point="console.a2a_pair", tenant_id="_default")
    tid_expiry, _ = gate._STATUS_CACHE["_default"]
    import time
    # past BOTH windows: the status display's and the enforcement DENY's (the longer one)
    monkeypatch.setattr(time, "monotonic", lambda: max(tid_expiry, gate._DENY_CACHE["_default"]) + 1)
    gate.network_status(entry_point="console.a2a_pair", tenant_id="_default")
    assert len(evaluations) == 2


def test_status_is_fail_closed_when_the_checker_breaks(monkeypatch):
    def boom(capability, requested=1, *, tenant_id, entry_point):
        raise RuntimeError("licensing broke")
    monkeypatch.setattr(capability_api, "require_capability", boom)
    assert gate.network_status(entry_point="t", tenant_id="_default") == {
        "allowed": False, "reason": "licence_check_unavailable"}
