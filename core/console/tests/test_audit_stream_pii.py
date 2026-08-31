"""Regression (security review 2026-06-28): the /audit/stream SSE endpoint must
NOT leak raw audit `details` (user / uid / command / error …) — L16 metadata-only
/ GDPR Art. 5. streams._project must sanitize via the same allowlist+PII-denylist
as the REST audit-tail, and strip chain-internal fields.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge",
           _REPO / "operator" / "bridges" / "shared"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_console.routes import streams  # type: ignore


def test_project_strips_chain_internal_fields():
    ev = {"event": "x", "ts": 1, "hash": "h", "prev_hash": "p",
          "mac": "m", "instance_sig": "s", "instance_id": "i"}
    out = streams._project(ev)
    for k in ("hash", "prev_hash", "mac", "instance_sig", "instance_id"):
        assert k not in out, f"{k} must be stripped from the stream"
    assert out["event"] == "x" and out["ts"] == 1


def test_project_sanitizes_details_pii():
    ev = {"event": "house_rules.escalated", "ts": 1,
          "details": {"user": "raw-uid-123", "uid": "raw-uid-123",
                      "command": "rm -rf /", "error": "secret trace",
                      "reason": "classifier_error", "action": "escalate"}}
    out = streams._project(ev)
    det = out["details"]
    # PII / free-text must be gone
    for k in ("user", "uid", "command", "error"):
        assert k not in det, f"PII key {k!r} leaked into the audit stream"
    # allowlisted metadata is kept
    assert det.get("reason") == "classifier_error"


def test_project_fails_closed_on_non_dict_details():
    # A non-dict details is left as-is by the dict-guard (no crash).
    out = streams._project({"event": "x", "details": None})
    assert out["details"] is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
