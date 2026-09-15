"""Phase D E2E: policy.json hot-reload — operator edits the envelope
mid-session, the next tools/call honours it without an MCP restart.

Three concrete scenarios:

  1. Forbidden-tool-name flip: forge a tool named "csv.count" — call
     succeeds. Operator writes a policy.json that adds `csv.*` to
     forbidden_tool_names. Next call to csv.count is rejected with
     `policy.namespace_denied` and a `policy.reloaded` event is logged.

  2. Rate-limit tightening: a tool runs fine at default 60/min. Operator
     drops policy.rate_limit.default_calls_per_minute to 1. Next call
     to a tool that already consumed its single token is rejected with
     `rate_limit.exceeded`.

  3. Malformed policy.json: an edit that breaks JSON should NOT crash
     the server, and the previous policy stays in effect; a
     `policy.reload_failed` event is logged.

This matches the voice-repo's hot-reload convention (every settings
file is mtime-cached; reads pick up changes on the next request).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forge.registry import Registry  # noqa: E402
from test_mcp import MCPClient  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


CSV_IMPL = '''#!/usr/bin/env python3
import json, sys
print(json.dumps({"data": {"hi": "csv"}}))
'''
NOOP_SCHEMA = {"type": "object", "properties": {}}


def _seed(root: Path):
    Registry(root).create("csv.count", "csv counter", NOOP_SCHEMA, CSV_IMPL)


def _bump_mtime(path: Path) -> None:
    """Some filesystems have 1-second mtime resolution; force a future
    mtime so the server reliably sees the change."""
    now = time.time() + 1.5
    os.utime(path, (now, now))


def _audit_events(root: Path) -> list[dict]:
    p = root / "audit.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ---------- (1) — forbidden_tool_names flip ------------------------------

def test_forbidden_tool_names_flip_takes_effect_live():
    print("\n[forbidden_tool_names flip → next call rejected, policy.reloaded logged]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _seed(td)
        # Start without policy.json — defaults are wide
        client = MCPClient(td, permission_mode="yes")
        try:
            client.initialize()
            r1 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            t("call 1 ok (no policy yet)",
              r1["result"].get("isError") is False)

            # Operator writes a tightening policy
            (td / "policy.json").write_text(json.dumps({
                "forbidden_tool_names": ["shell.*", "csv.*"],
            }))
            _bump_mtime(td / "policy.json")

            # csv.count name is now forbidden — call should be denied
            r2 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            t("call 2 rejected after policy write",
              r2["result"].get("isError") is True)
            text = r2["result"]["content"][0]["text"]
            t("error names policy denial",
              "policy" in text.lower() and "csv.*" in text)
        finally:
            client.close()

        # Audit shows policy.reloaded + the namespace deny
        events = _audit_events(td)
        types = [e["event_type"] for e in events]
        t("policy.reloaded event recorded",
          "policy.reloaded" in types)
        t("policy.namespace_denied event recorded",
          "policy.namespace_denied" in types)


# ---------- (2) — rate_limit tightening on a live breaker ----------------

def test_rate_limit_tightening_propagates_to_existing_limiter():
    print("\n[rate-limit drop from 60→1 trips the existing limiter on next call]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _seed(td)
        # Start with default policy (60/min). One call → token bucket
        # initialised at capacity=60.
        client = MCPClient(td, permission_mode="yes")
        try:
            client.initialize()
            r1 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            t("baseline call ok",
              r1["result"].get("isError") is False)

            # Tighten to 1/min — already 1 token consumed (so 59 remaining
            # under old policy). With hot-reload setting capacity=1 AND
            # capping tokens at min(tokens, 1.0), tokens = min(59, 1) = 1
            # → next call consumes the token, second call hits zero.
            (td / "policy.json").write_text(json.dumps({
                "rate_limit": {"default_calls_per_minute": 1},
                # disable circuit breaker noise for this scenario
                "circuit_breaker": {"enabled": False},
            }))
            _bump_mtime(td / "policy.json")

            r2 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            r3 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
        finally:
            client.close()

        t("post-tightening: one more call passes",
          r2["result"].get("isError") is False,
          detail=str(r2["result"]["content"][0]["text"])[:120])
        t("post-tightening: subsequent call hits limit",
          r3["result"].get("isError") is True)
        t("error mentions rate_limit.exceeded",
          "rate_limit.exceeded" in r3["result"]["content"][0]["text"])

        events = _audit_events(td)
        types = [e["event_type"] for e in events]
        t("policy.reloaded recorded",
          "policy.reloaded" in types)
        t("rate_limit.exceeded recorded",
          "rate_limit.exceeded" in types)


# ---------- (3) — malformed policy.json keeps the old one ----------------

def test_malformed_policy_keeps_old_and_logs_failure():
    print("\n[malformed policy.json edit → old policy preserved, policy.reload_failed logged]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _seed(td)
        # Start with a known-good restrictive policy
        (td / "policy.json").write_text(json.dumps({
            "forbidden_tool_names": ["shell.*"],
        }))
        _bump_mtime(td / "policy.json")

        client = MCPClient(td, permission_mode="yes")
        try:
            client.initialize()
            r1 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            t("baseline call ok (csv.count not forbidden yet)",
              r1["result"].get("isError") is False)

            # Operator botches an edit (truncated JSON)
            (td / "policy.json").write_text("{ this is broken")
            _bump_mtime(td / "policy.json")

            # Server should NOT crash — old policy still in effect →
            # csv.count is still allowed
            r2 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            t("call after malformed edit still works (old policy held)",
              r2["result"].get("isError") is False)
        finally:
            client.close()

        events = _audit_events(td)
        types = [e["event_type"] for e in events]
        t("policy.reload_failed event recorded",
          "policy.reload_failed" in types)
        # And a policy.reloaded for the FIRST (good) write happened too,
        # but maybe before the client started — that's fine, we don't
        # assert on it here.


def main() -> int:
    test_forbidden_tool_names_flip_takes_effect_live()
    test_rate_limit_tightening_propagates_to_existing_limiter()
    test_malformed_policy_keeps_old_and_logs_failure()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
