"""Layer 9 — namespace gate: every persona owns a registration prefix.

These tests verify the cross-persona safety boundary the ``forge`` MCP
server enforces when a ``CORVIN_CALLER_PERSONA`` env var is present:

  1. ``coder`` may register ``code.foo`` — registry write succeeds.
  2. ``coder`` may NOT register ``inbox.foo`` — error envelope of type
     ``PermissionDenied``, audit event ``tool.namespace_denied`` fires.
  3. Missing env → wildcard (legacy), any name works.
  4. Persona not listed in ``persona_namespaces`` → wildcard, any name works.
  5. Successful create writes ``details.caller_persona`` into the audit event.

Style mirrors test_voice_persona_acl.py (plain-python PASS / FAIL counters).

Run as: python3 operator/forge/tests/test_namespace_gate.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forge.policy import Policy  # noqa: E402
from test_mcp import MCPClient, ROOT as TEST_ROOT  # noqa: E402

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


NOOP_IMPL = '''#!/usr/bin/env python3
import json, sys
print(json.dumps({"data": {"hi": "ok"}}))
'''
NOOP_SCHEMA = {"type": "object", "properties": {}}


class _PersonaMCPClient(MCPClient):
    """MCP client that injects CORVIN_CALLER_PERSONA into the spawn env.

    Reuses the parent's stdio transport but re-spawns the subprocess so the
    env var is visible to the child (env-only MCP servers can't observe a
    parent's later mutation)."""

    def __init__(self, root: Path, *, caller_persona: str | None = None,
                 permission_mode: str = "yes"):
        env = dict(os.environ)
        # Strip both spellings before injecting the test value (Phase 7:
        # CORVIN_CALLER_PERSONA is no longer read by runtime code).
        env.pop("CORVIN_CALLER_PERSONA", None)
        env.pop("CORVIN_CALLER_PERSONA", None)
        if caller_persona:
            env["CORVIN_CALLER_PERSONA"] = caller_persona
        # Don't let a stale FORGE_PERSONA from outside the test confuse
        # the audit event — the new gate is the SUT.
        env.pop("FORGE_PERSONA", None)
        self.root = root
        self.proc = subprocess.Popen(
            [sys.executable, str(TEST_ROOT / "forge.py"),
             "--root", str(root), "mcp",
             "--permission-mode", permission_mode],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            cwd=str(TEST_ROOT), env=env,
        )
        self._next_id = 0
        self._buffered = []
        self._buffer_lock = threading.Lock()
        self._reader_alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()


def _audit_events(root: Path) -> list[dict]:
    p = root / "audit.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _forge_args(client: MCPClient, name: str) -> dict:
    return {"name": name, "description": "ns-gate test",
            "input_schema": NOOP_SCHEMA, "impl": NOOP_IMPL,
            "runtime": "python"}


# -- Case 1: coder may register code.* ----------------------------------------

def test_coder_can_register_in_namespace():
    print("\n[case 1: CORVIN_CALLER_PERSONA=coder may register code.foo]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = _PersonaMCPClient(td, caller_persona="coder")
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "forge_tool",
                                "arguments": _forge_args(client, "code.foo")})
            ok = r["result"].get("isError") is False
            t("forge_tool returned non-error", ok,
              detail=("" if ok else r["result"]["content"][0]["text"]))
            tools = client.request("tools/list")["result"]["tools"]
            names = [tt["name"] for tt in tools]
            t("code.foo visible in tools/list", "code.foo" in names)
        finally:
            client.close()
        events = _audit_events(td)
        creates = [e for e in events
                   if e["event_type"] == "tool.created"
                   and e.get("tool") == "code.foo"]
        t("tool.created event for code.foo", len(creates) == 1)
        if creates:
            t("audit details.caller_persona == 'coder'",
              creates[0].get("details", {}).get("caller_persona") == "coder")


# -- Case 2: coder may NOT register inbox.* (gate fires) ----------------------

def test_coder_blocked_outside_namespace():
    print("\n[case 2: coder may NOT register inbox.foo]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = _PersonaMCPClient(td, caller_persona="coder")
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "forge_tool",
                                "arguments": _forge_args(client, "inbox.foo")})
            t("forge_tool returned isError",
              r["result"].get("isError") is True)
            text = r["result"]["content"][0]["text"]
            t("error mentions namespace-gate",
              "namespace-gate" in text or "namespace_gate" in text)
            t("error mentions persona name 'coder'",
              "coder" in text)
            tools = client.request("tools/list")["result"]["tools"]
            names = [tt["name"] for tt in tools]
            t("inbox.foo NOT registered", "inbox.foo" not in names)
        finally:
            client.close()
        events = _audit_events(td)
        denied = [e for e in events
                  if e["event_type"] == "tool.namespace_denied"]
        t("tool.namespace_denied audit event recorded",
          len(denied) >= 1)
        if denied:
            d = denied[-1]
            t("denied event names tool=inbox.foo",
              d.get("tool") == "inbox.foo")
            t("denied event records caller_persona=coder",
              d.get("details", {}).get("caller_persona") == "coder")


# -- Case 3: missing env → wildcard (any name works) --------------------------

def test_no_caller_persona_falls_back_to_wildcard():
    print("\n[case 3: no CORVIN_CALLER_PERSONA → any name works (legacy)]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = _PersonaMCPClient(td, caller_persona=None)
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "forge_tool",
                                "arguments": _forge_args(
                                    client, "anything.goes")})
            ok = r["result"].get("isError") is False
            t("any name registers without CALLER_PERSONA", ok,
              detail=("" if ok else r["result"]["content"][0]["text"]))
        finally:
            client.close()


# -- Case 4: persona not in policy.persona_namespaces → wildcard -------------

def test_unknown_persona_falls_back_to_wildcard():
    print("\n[case 4: persona NOT in persona_namespaces → wildcard]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Write a workspace policy that explicitly leaves out "stranger".
        (td / "policy.json").write_text(json.dumps({
            "persona_namespaces": {
                "coder": "code"
                # "stranger" missing on purpose
            }
        }))
        client = _PersonaMCPClient(td, caller_persona="stranger")
        try:
            client.initialize()
            r = client.request("tools/call",
                               {"name": "forge_tool",
                                "arguments": _forge_args(
                                    client, "freeform.thing")})
            t("unknown persona acts as wildcard",
              r["result"].get("isError") is False)
        finally:
            client.close()


# -- Case 5: caller_persona detail propagates on every lifecycle event -------

def test_caller_persona_in_audit_details():
    print("\n[case 5: caller_persona present in tool.created + tool.deleted]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        client = _PersonaMCPClient(td, caller_persona="browser")
        try:
            client.initialize()
            # Browser persona owns the "web." prefix per bundle policy.
            r = client.request("tools/call",
                               {"name": "forge_tool",
                                "arguments": _forge_args(
                                    client, "web.scrape")})
            t("forge_tool succeeded for web.scrape",
              r["result"].get("isError") is False,
              detail=("" if r["result"].get("isError") is False
                      else r["result"]["content"][0]["text"]))
        finally:
            client.close()
        events = _audit_events(td)
        creates = [e for e in events if e["event_type"] == "tool.created"
                   and e.get("tool") == "web.scrape"]
        t("tool.created event present", len(creates) >= 1)
        if creates:
            t("audit details.caller_persona == 'browser'",
              creates[0].get("details", {}).get("caller_persona") == "browser")


# -- Sanity check: Policy.namespace_for / namespace_check at unit level ------

def test_policy_unit_helpers():
    print("\n[case 0: Policy.namespace_for / namespace_check unit]")
    p = Policy.load(Path("/nonexistent-workspace-aaaaa"))
    # Bundle defaults must include all standard cowork personas.
    t("bundle default has coder→code",
      p.namespace_for("coder") == "code")
    t("bundle default has assistant→assistant",
      p.namespace_for("assistant") == "assistant")
    t("missing persona → None (wildcard)",
      p.namespace_for("totally-unknown") is None)
    t("namespace_check coder/code.foo allowed",
      p.namespace_check("coder", "code.foo")[0] is True)
    t("namespace_check coder/inbox.foo denied",
      p.namespace_check("coder", "inbox.foo")[0] is False)
    t("namespace_check no-persona allowed (wildcard)",
      p.namespace_check(None, "anything")[0] is True)


def main() -> int:
    test_policy_unit_helpers()
    test_coder_can_register_in_namespace()
    test_coder_blocked_outside_namespace()
    test_no_caller_persona_falls_back_to_wildcard()
    test_unknown_persona_falls_back_to_wildcard()
    test_caller_persona_in_audit_details()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
