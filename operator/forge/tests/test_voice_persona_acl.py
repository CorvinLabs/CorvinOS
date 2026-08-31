"""Phase C E2E: per-persona allowlist for forged tools.

Three concrete fictional scenarios:

  1. Persona without `allowed_forged_tools` → sees every forged tool, can
     call any of them (legacy behaviour, never regressed).

  2. Persona with `allowed_forged_tools = ["csv.*"]` → tools/list shows
     forge_tool, forge_promote, plus only csv.* tools; tools/call to a
     non-matching tool is rejected with `acl.persona_denied`. The audit
     log records the violation.

  3. Resolver wires the persona's allowlist into the MCP server through
     the FORGE_ALLOWED_TOOLS env var (template-var expansion).

We forge two real tools (csv.count, stats.median), then drive a stdio
MCP server with explicit FORGE_ALLOWED_TOOLS to validate (1) and (2),
and use materialize_mcp + a custom persona dict to validate (3).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))
sys.path.insert(0, str(REPO_ROOT / "operator" / "cowork" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import resolver  # noqa: E402
from forge.registry import Registry  # noqa: E402
from test_mcp import MCPClient  # noqa: E402  reuse harness


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# Two impls covering distinct namespaces
CSV_IMPL = '''#!/usr/bin/env python3
import json, sys
print(json.dumps({"data": {"hi": "csv"}}))
'''
STATS_IMPL = '''#!/usr/bin/env python3
import json, sys
print(json.dumps({"data": {"hi": "stats"}}))
'''
NOOP_SCHEMA = {"type": "object", "properties": {}}


def _seed_workspace(root: Path):
    """Populate the workspace with two forged tools out-of-band so we can
    run the ACL test without re-forging via MCP each time."""
    reg = Registry(root)
    reg.create("csv.count",    "csv counter", NOOP_SCHEMA, CSV_IMPL)
    reg.create("stats.median", "stats median", NOOP_SCHEMA, STATS_IMPL)


# ---------- (1) — no allowlist, no restriction ---------------------------

def test_no_allowlist_no_restriction():
    print("\n[no FORGE_ALLOWED_TOOLS → all forged tools visible + callable]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _seed_workspace(td)
        # MCPClient inherits parent env. We just don't set FORGE_ALLOWED_TOOLS.
        env = dict(os.environ); env.pop("FORGE_ALLOWED_TOOLS", None)
        client = MCPClient(td, permission_mode="yes")
        try:
            client.initialize()
            tools = client.request("tools/list")["result"]["tools"]
            names = [tt["name"] for tt in tools]
            t("forge_tool present",     "forge_tool" in names)
            t("forge_promote present",  "forge_promote" in names)
            t("csv.count visible",      "csv.count" in names)
            t("stats.median visible",   "stats.median" in names)
            r1 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            r2 = client.request("tools/call",
                                 {"name": "stats.median", "arguments": {}})
            t("csv.count callable",     r1["result"].get("isError") is False)
            t("stats.median callable",  r2["result"].get("isError") is False)
        finally:
            client.close()


# ---------- (2) — explicit FORGE_ALLOWED_TOOLS=csv.* ---------------------

class _AllowlistMCPClient(MCPClient):
    """Subprocess variant that injects FORGE_ALLOWED_TOOLS into the spawn."""

    def __init__(self, root: Path, *, allowed: str):
        # We can't go through the parent constructor cleanly because it
        # spawns inherited-env. Re-implement minimally.
        from test_mcp import ROOT as TEST_ROOT
        env = dict(os.environ)
        env["FORGE_ALLOWED_TOOLS"] = allowed
        self.root = root
        self.proc = subprocess.Popen(
            [sys.executable, str(TEST_ROOT / "forge.py"),
             "--root", str(root), "mcp",
             "--permission-mode", "yes"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            cwd=str(TEST_ROOT), env=env,
        )
        self._next_id = 0
        self._buffered: list[dict] = []
        self._buffer_lock = threading.Lock()
        self._reader_alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()


def test_allowlist_filters_list_and_gates_call():
    print("\n[FORGE_ALLOWED_TOOLS=csv.* → only csv.* visible/callable]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _seed_workspace(td)
        client = _AllowlistMCPClient(td, allowed="csv.*")
        try:
            client.initialize()
            tools = client.request("tools/list")["result"]["tools"]
            names = [tt["name"] for tt in tools]
            t("forge_tool still visible (meta-tool, never gated)",
              "forge_tool" in names)
            t("forge_promote still visible",
              "forge_promote" in names)
            t("csv.count visible (matches csv.*)",
              "csv.count" in names)
            t("stats.median FILTERED OUT",
              "stats.median" not in names)

            # call csv tool — works
            r1 = client.request("tools/call",
                                 {"name": "csv.count", "arguments": {}})
            t("csv.count callable",
              r1["result"].get("isError") is False)

            # call stats tool — denied with acl.persona_denied
            r2 = client.request("tools/call",
                                 {"name": "stats.median", "arguments": {}})
            t("stats.median rejected",
              r2["result"].get("isError") is True)
            text = r2["result"]["content"][0]["text"]
            t("error names acl.persona_denied",
              "acl.persona_denied" in text)
        finally:
            client.close()

        # Audit on disk records the denial
        audit = (td / "audit.jsonl").read_text().splitlines()
        events = [json.loads(l) for l in audit]
        types = [e["event_type"] for e in events]
        t("acl.persona_denied event recorded",
          "acl.persona_denied" in types)
        denied = [e for e in events if e["event_type"] == "acl.persona_denied"]
        t("denied event names stats.median",
          any(e.get("tool") == "stats.median" for e in denied))


def test_allowlist_glob_with_exact_name():
    print("\n[FORGE_ALLOWED_TOOLS=stats.median → exact match works]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        _seed_workspace(td)
        client = _AllowlistMCPClient(td, allowed="stats.median")
        try:
            client.initialize()
            tools = [tt["name"] for tt in
                      client.request("tools/list")["result"]["tools"]]
            t("stats.median visible (exact match)",
              "stats.median" in tools)
            t("csv.count NOT visible (no match)",
              "csv.count" not in tools)
        finally:
            client.close()


# ---------- (3) — resolver wires persona.allowed_forged_tools into env ----

def test_resolver_injects_allowed_forged_tools_via_template_var():
    print("\n[resolver expands {{ALLOWED_FORGED_TOOLS}} from persona into env]")
    # A custom persona dict (we don't write a new bundle file) with the
    # allowlist set. We invoke materialize_mcp directly.
    persona = {
        "name": "csv_only_consumer",
        "allowed_forged_tools": ["csv.*", "stats.median"],
        "mcp_servers": {
            "forge": {
                "command": "python3",
                "args": ["{{REPO_ROOT}}/operator/forge/forge.py", "mcp"],
                "env": {
                    "FORGE_ROOT": "{{HOME}}/.config/corvin-voice/forge",
                    "FORGE_ALLOWED_TOOLS": "{{ALLOWED_FORGED_TOOLS}}",
                },
            },
        },
    }
    cfg_path = resolver.materialize_mcp(persona)
    t("materialize returned a path", isinstance(cfg_path, str))
    cfg = json.loads(Path(cfg_path).read_text())
    server = cfg["mcpServers"]["forge"]
    env = server.get("env", {})
    t("FORGE_ROOT expanded (HOME)",
      env.get("FORGE_ROOT", "").endswith("/.config/corvin-voice/forge"))
    t("FORGE_ALLOWED_TOOLS expanded to csv.*,stats.median",
      env.get("FORGE_ALLOWED_TOOLS") == "csv.*,stats.median")
    t("REPO_ROOT in args[0] expanded to absolute path",
      server["args"][0].startswith("/")
      and server["args"][0].endswith("operator/forge/forge.py"))


def test_resolver_empty_allowlist_yields_empty_env_value():
    print("\n[no allowed_forged_tools → empty FORGE_ALLOWED_TOOLS string = no restriction]")
    persona = {
        "name": "open_persona",
        "mcp_servers": {
            "forge": {
                "command": "python3",
                "args": ["x"],
                "env": {"FORGE_ALLOWED_TOOLS": "{{ALLOWED_FORGED_TOOLS}}"},
            },
        },
        # no allowed_forged_tools field
    }
    cfg_path = resolver.materialize_mcp(persona)
    cfg = json.loads(Path(cfg_path).read_text())
    env = cfg["mcpServers"]["forge"]["env"]
    t("FORGE_ALLOWED_TOOLS = '' (empty string)",
      env.get("FORGE_ALLOWED_TOOLS") == "")
    # And on the server side that empty string parses back to None (no restriction)
    from forge.mcp_server import MCPServer
    t("server parses '' to None (= no restriction)",
      MCPServer._parse_allowed_env("") is None)
    t("server parses 'csv.*, stats_*' correctly",
      MCPServer._parse_allowed_env("csv.*, stats_*") == ["csv.*", "stats_*"])


def main() -> int:
    test_no_allowlist_no_restriction()
    test_allowlist_filters_list_and_gates_call()
    test_allowlist_glob_with_exact_name()
    test_resolver_injects_allowed_forged_tools_via_template_var()
    test_resolver_empty_allowlist_yields_empty_env_value()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
