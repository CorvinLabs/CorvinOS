"""peer_agent audit events (ADR-2276): registered in both registries, scalar-only, chain verifies.

The records are written by the node's real emitter (`peer_gate.audit_peer_event`) through the real
`forge.security_events.write_event` into a temp chain.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "corvin_operator" / "forge", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "core" / "workflows", _REPO):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from forge import security_events as se  # noqa: E402

EVENTS = {
    "workflow.peer_delegated": "INFO",
    "workflow.peer_completed": "INFO",
    "workflow.peer_refused": "WARNING",
    "workflow.peer_outcome_unknown": "WARNING",
}
_CONTENT_FIELDS = {"instruction", "text", "result", "output", "prompt", "answer", "reason", "error"}


@pytest.mark.parametrize("event,severity", sorted(EVENTS.items()))
def test_registered_in_both_registries_without_content_fields(event, severity):
    assert se.EVENT_SEVERITY[event] == severity
    allowed = se._EVENT_ALLOWLIST[event]
    assert {"tenant_id", "node_id", "rid", "wid"} <= allowed
    assert not (allowed & _CONTENT_FIELDS), "an audit record never carries instruction or answer text"


def test_all_four_events_chain_and_verify(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    from corvin_workflows import peer_gate
    from core.paths import tenant_audit_chain

    for ev in EVENTS:
        assert peer_gate.audit_peer_event(ev, "_default", strict=True, wid="w", rid="r", node_id="n",
                                          endpoint_id="ep", agent_id="a", status="ok", reason_code="x")
    chain = tenant_audit_chain("_default")
    ok, problems = se.verify_chain(chain)
    assert ok, problems
    recs = [json.loads(x) for x in chain.read_text().splitlines() if x.strip()]
    assert [r["event_type"] for r in recs[-4:]] == list(EVENTS)
    assert all(r["details"]["tenant_id"] == "_default" and r["details"]["node_id"] == "n" for r in recs[-4:])
