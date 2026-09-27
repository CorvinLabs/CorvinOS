"""drift_detector._audit_alert writes THE tenant chain, content-free.

Adversarial review 2026-09-27: it imported ``core.compliance.security_events``
(which does not exist), so every drift alert went unaudited behind a logged
ImportError, and it would have put the free-text alert message in the chain.
tests/conftest.py isolates CORVIN_HOME per test.
"""
import json
from unittest import mock

import pytest

drift_detector = pytest.importorskip("core.monitoring.drift_detector")

from forge import paths as forge_paths  # noqa: E402
from forge import security_events  # noqa: E402


def _svc():
    # _audit_alert uses no instance state; skip the heavy constructor
    return drift_detector.DriftDetectionService.__new__(drift_detector.DriftDetectionService)


def test_drift_alert_is_written_to_the_tenant_chain():
    _svc()._audit_alert(drift_detector.DriftSeverity.CRITICAL,
                        "manifest mismatch on host alice@example.com",
                        "inst-1", "MANIFEST_HASH_MISMATCH")
    chain = forge_paths.tenant_audit_chain("_default")
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    rec = [r for r in recs if r["event_type"] == "deployment.drift_alert"][-1]
    assert rec["details"]["instance_id"] == "inst-1"
    assert rec["details"]["drift_type"] == "MANIFEST_HASH_MISMATCH"
    assert rec["details"]["severity"] == "CRITICAL"
    assert rec["severity"] == "WARNING"
    assert "alice@example.com" not in chain.read_text()  # message never enters the chain
    assert "_dropped_fields" not in rec["details"]
    assert security_events.verify_chain(chain)[0]


def test_audit_failure_does_not_break_alert_routing():
    with mock.patch.object(security_events, "write_event", side_effect=OSError("disk")):
        _svc()._audit_alert(drift_detector.DriftSeverity.HIGH, "m", "inst-2", "CONFIG_DRIFT")
