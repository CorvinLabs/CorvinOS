"""ADR-0154 M4 (compliant tamper response) E2E.

Asserts the deterrent without the GDPR/audit-integrity violations the original
M4 chaos-injection design would have introduced: tamper → loud CRITICAL +
fail-closed, NO silent audit dropping.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_LIC_DIR = Path(__file__).resolve().parents[1]
if str(_LIC_DIR) not in sys.path:
    sys.path.insert(0, str(_LIC_DIR))

import tamper_response as tr  # type: ignore  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    tr._reset_for_tests()
    yield
    tr._reset_for_tests()


def test_starts_unengaged():
    assert tr.is_engaged() is False
    assert tr.status() is None


def test_engage_sets_state(monkeypatch):
    emitted = []
    monkeypatch.setattr(tr, "_emit_audit", lambda reason: emitted.append(reason))
    tr.engage("canary_mismatch")
    assert tr.is_engaged() is True
    st = tr.status()
    assert st is not None and st["reason"] == "canary_mismatch"
    assert emitted == ["canary_mismatch"]  # audit ADDED, never dropped


def test_engage_is_idempotent_first_wins(monkeypatch):
    emitted = []
    monkeypatch.setattr(tr, "_emit_audit", lambda reason: emitted.append(reason))
    tr.engage("first")
    tr.engage("second")
    st = tr.status()
    assert st["reason"] == "first"
    assert st["count"] == 2
    assert emitted == ["first"]  # no log/audit spam on repeat


def test_engage_never_raises_on_audit_failure(monkeypatch):
    def _boom(reason):
        raise RuntimeError("audit backend down")

    monkeypatch.setattr(tr, "_emit_audit", _boom)
    # First engage swallows the audit failure; fail-closed state still set.
    # (engage() calls _emit_audit only on first detection.)
    try:
        tr.engage("x")
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"engage must not raise: {exc}")
    # State recorded regardless of audit outcome.
    assert tr.is_engaged() is True


def test_real_emit_audit_writes_critical_event(tmp_path, monkeypatch):
    """REGRESSION (review CRITICAL): exercise the REAL _emit_audit → audit_event
    path (NOT monkeypatched). The earlier code called audit_event(reason=...),
    which raised TypeError that the broad except SILENTLY SWALLOWED — so the
    tamper event was never written. The monkeypatched tests above masked it.
    """
    import json
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(audit_path))
    # ensure operator/bridges/shared is importable for the real audit_event
    shared = Path(__file__).resolve().parents[2] / "bridges" / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))

    tr.engage("canary_mismatch")   # real path — no monkeypatch

    assert audit_path.exists(), "tamper engage must WRITE an audit event, not drop it"
    events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
    hits = [e for e in events if e.get("event_type") == "license.tamper_response"]
    assert len(hits) == 1, f"expected exactly 1 license.tamper_response, got {len(hits)}"
    ev = hits[0]
    assert ev.get("severity") == "CRITICAL"
    assert ev.get("details", {}).get("reason") == "canary_mismatch"
    # metadata-only: no token / license key material leaked
    assert "token" not in json.dumps(ev).lower()


def test_compliance_no_chaos_api_surface():
    """The compliant module must NOT expose chaos-injection knobs."""
    for forbidden in ("inject_p", "audit_drop_p", "drop_audit", "random_delay"):
        assert not hasattr(tr, forbidden), f"{forbidden} would violate audit integrity"
