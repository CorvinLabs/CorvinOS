"""Events emitted by the Forge MCP server / license API are registered (2026-09-27).

Adversarial review round 4: ``compute.license.*``, ``compute.permit.*``,
``datasource.connected`` / ``datasource.license_denied``,
``license.enforcement_unavailable``, ``license.capability_decision`` and
``teb.path_gate.denied`` were emitted with no ``EVENT_SEVERITY`` entry (the
writer defaulted them to INFO) and no allowlist (details fell to the vocabulary
floor, which drops e.g. ``fabric_allowed`` / ``trial_remaining``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_FORGE = _REPO / "corvin_operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.append(str(_FORGE))

from forge import security_events as se  # noqa: E402

_EVENTS = {
    "compute.license.checked": "INFO",
    "compute.license.denied": "WARNING",
    "compute.license.fabric_checked": "INFO",
    "compute.license.fabric_denied": "WARNING",
    "compute.license.gate_error": "WARNING",
    "compute.permit.server_error": "WARNING",
    "compute.permit.fail_closed": "WARNING",
    "datasource.connected": "INFO",
    "datasource.license_denied": "WARNING",
    "license.enforcement_unavailable": "WARNING",
    "license.capability_decision": "INFO",
    "teb.path_gate.denied": "WARNING",
    "tool.tamper_detected": "WARNING",
    "path_gate.self_test_failed": "CRITICAL",
}


@pytest.mark.parametrize("event,severity", sorted(_EVENTS.items()))
def test_event_is_in_both_registries(event, severity):
    assert se.EVENT_SEVERITY.get(event) == severity
    assert event in se._EVENT_ALLOWLIST


def test_features_url_override_has_severity_and_stays_on_the_floor():
    # Its only field is an operator-supplied URL: a positive allowlist would
    # switch off the floor's PII value scan for it (see FLOOR_BY_DESIGN).
    assert se.EVENT_SEVERITY.get("license.features_url_override") == "WARNING"
    assert "license.features_url_override" not in se._EVENT_ALLOWLIST


def test_compute_gate_fields_survive_the_writer(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "k" / "anchor.key"))
    chain = tmp_path / "audit.jsonl"
    se.write_event(chain, "compute.license.checked", tool="compute_run", details={
        "allowed": True, "mode": "trial", "tier": "free",
        "fabric_allowed": False, "trial_remaining": 3,
    })
    rec = json.loads(chain.read_text().splitlines()[-1])
    assert rec["severity"] == "INFO"
    assert rec["details"]["fabric_allowed"] is False
    assert rec["details"]["trial_remaining"] == 3
    assert "_dropped_fields" not in rec["details"]
