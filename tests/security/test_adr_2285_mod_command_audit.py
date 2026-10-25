"""ADR-2285 — ``mod.command_executed`` is a registered, metadata-only audit event.

Asserted through the REAL ``forge.security_events.write_event`` into a temp chain
(no mocked writer, nothing touches the live chain):

* the event is in both ``EVENT_SEVERITY`` and ``_EVENT_ALLOWLIST``;
* its allowed fields survive the writer's detail floor and the chain verifies;
* a field outside the allowlist (the user's answer text) is dropped, not stored.

The live path (a Claude Code mod calling ``audit_write_event`` over MCP) needs an
interactive CLI and is shown in CONCEPT-0118 Evidence, not here.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "corvin_operator" / "forge",):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

from forge import security_events as se  # noqa: E402

EVENT = "mod.command_executed"


@pytest.fixture
def chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "keys" / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    return tmp_path / "audit.jsonl"


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_event_is_registered_in_both_tables():
    assert se.EVENT_SEVERITY[EVENT] == "INFO"
    assert se._EVENT_ALLOWLIST[EVENT] == frozenset(
        {"mod", "command", "skill", "score", "tenant_id"}
    )


def test_event_reaches_the_chain_with_its_fields(chain):
    se.write_event(
        chain,
        EVENT,
        details={
            "mod": "corvin-quiz-mod",
            "command": "quiz-antwort",
            "skill": "assistant.mod_quiz_phase0",
            "score": 1.0,
            "tenant_id": "_default",
        },
    )
    rec = [r for r in _records(chain) if r.get("event_type") == EVENT][-1]
    assert rec["details"]["mod"] == "corvin-quiz-mod"
    assert rec["details"]["score"] == 1.0
    assert "_dropped_fields" not in rec["details"]
    assert se.verify_chain(chain) == (True, [])


class _GatewayStub:
    """Duck-types the MCP server just enough to run the REAL _call_audit_write_event."""

    def __init__(self) -> None:
        self.responses: list[tuple] = []

    def _respond(self, msgid, result):
        self.responses.append(("ok", msgid, result))

    def _error(self, msgid, code, msg):
        self.responses.append(("err", msgid, code, msg))

    def _log_security_event(self, *a, **kw):
        pass


def _gateway_write(chain: Path, monkeypatch, details: dict) -> dict:
    from forge.mcp_server import MCPServer  # noqa: PLC0415

    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    stub = _GatewayStub()
    MCPServer._call_audit_write_event(stub, 1, {"event_type": EVENT, "details": details})
    assert stub.responses and stub.responses[0][0] == "ok", stub.responses
    return [r for r in _records(chain) if r.get("event_type") == EVENT][-1]


def test_gateway_sets_the_tenant_itself(chain, monkeypatch):
    from forge.tenants import current_tenant  # noqa: PLC0415

    rec = _gateway_write(
        chain, monkeypatch,
        {"mod": "corvin-quiz-mod", "command": "quiz-antwort",
         "skill": "assistant.mod_quiz_phase0", "score": 1},
    )
    assert rec["details"]["tenant_id"] == current_tenant()
    assert "_dropped_fields" not in rec["details"]
    assert se.verify_chain(chain) == (True, [])


def test_gateway_overwrites_a_tenant_claimed_by_the_caller(chain, monkeypatch):
    from forge.tenants import current_tenant  # noqa: PLC0415

    rec = _gateway_write(
        chain, monkeypatch,
        {"mod": "corvin-quiz-mod", "command": "quiz-antwort",
         "skill": "assistant.mod_quiz_phase0", "score": 1, "tenant_id": "another-tenant"},
    )
    assert rec["details"]["tenant_id"] == current_tenant()
    assert rec["details"]["tenant_id"] != "another-tenant"


def test_answer_text_is_dropped_not_stored(chain):
    se.write_event(
        chain,
        EVENT,
        details={
            "mod": "corvin-quiz-mod",
            "command": "quiz-antwort",
            "skill": "assistant.mod_quiz_phase0",
            "score": 0.0,
            "tenant_id": "_default",
            "answer": "GEHEIMER-ANTWORTTEXT",
        },
    )
    raw = chain.read_text()
    assert "GEHEIMER-ANTWORTTEXT" not in raw
    rec = [r for r in _records(chain) if r.get("event_type") == EVENT][-1]
    assert "answer" in rec["details"].get("_dropped_fields", [])
    assert se.verify_chain(chain) == (True, [])
