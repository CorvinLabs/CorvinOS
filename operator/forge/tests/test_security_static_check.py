"""ST9.2 E2E: forbidden-imports static check rejects bad tools.

Fictional scenario: Claude tries to forge a tool that smuggles
``import socket`` into a benign-looking CSV stats tool. The policy
forbids ``socket`` by default, so the static check rejects the forge
*before* the tool ever lands on disk.

We also confirm:
  - bash impls are not subject to Python AST checks
  - syntax errors don't crash the loader (they produce a clear violation)
  - per-policy customization works (operator narrows or widens the list)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from forge.policy import Policy
from forge.static_check import (
    StaticCheckError,
    assert_imports_ok,
    check_imports,
    scan_imports,
)
from test_mcp import MCPClient


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- unit-level checks first --------------------------------------

GOOD_IMPL = '''#!/usr/bin/env python3
import csv, json, sys, statistics
p = json.loads(sys.stdin.read())
print(json.dumps({"data": {"n": p["n"]}}))
'''

BAD_IMPL_DIRECT = '''#!/usr/bin/env python3
import socket, json, sys
print(json.dumps({"data": {}}))
'''

BAD_IMPL_FROM = '''#!/usr/bin/env python3
from subprocess import run
import json, sys
print(json.dumps({"data": {}}))
'''

BAD_IMPL_NESTED = '''#!/usr/bin/env python3
import json, sys
def helper():
    import socket
    return socket.gethostname()
print(json.dumps({"data": {}}))
'''

SYNTAX_ERROR = '''#!/usr/bin/env python3
def foo(:
    pass
'''

DOTTED = '''#!/usr/bin/env python3
import os.path, json, sys
print(json.dumps({"data": {}}))
'''

BASH_IMPL = '''#!/bin/bash
echo '{"data":{"hi":1}}'
'''


def test_scan_imports_finds_root_modules():
    print("\n[scan_imports finds top-level imports + nested ones]")
    roots, ok = scan_imports(GOOD_IMPL)
    t("good impl: parseable", ok)
    t("good impl roots = {csv, json, sys, statistics}",
      roots == {"csv", "json", "sys", "statistics"})

    roots, _ = scan_imports(BAD_IMPL_DIRECT)
    t("bad direct: socket detected", "socket" in roots)

    roots, _ = scan_imports(BAD_IMPL_FROM)
    t("bad from: subprocess detected", "subprocess" in roots)

    roots, _ = scan_imports(BAD_IMPL_NESTED)
    t("nested-import inside def: socket still detected",
      "socket" in roots)

    roots, _ = scan_imports(DOTTED)
    t("dotted import os.path: root is 'os', not 'os.path'",
      "os" in roots and "os.path" not in roots)

    roots, ok = scan_imports(SYNTAX_ERROR)
    t("syntax error: parseable=False", ok is False)
    t("syntax error: roots = {<unparseable>}",
      roots == {"<unparseable>"})


def test_check_imports_returns_violation_list():
    print("\n[check_imports returns violations against forbidden list]")
    forbidden = {"socket", "subprocess", "ctypes"}
    t("good impl: no violations",
      check_imports(GOOD_IMPL, forbidden=forbidden) == [])
    t("bad direct: socket in violations",
      check_imports(BAD_IMPL_DIRECT, forbidden=forbidden) == ["socket"])
    t("bad from: subprocess in violations",
      check_imports(BAD_IMPL_FROM, forbidden=forbidden) == ["subprocess"])
    t("syntax error: <unparseable> sole violation",
      check_imports(SYNTAX_ERROR, forbidden=forbidden) == ["<unparseable>"])
    t("bash runtime skips check entirely",
      check_imports(BASH_IMPL, forbidden=forbidden,
                     runtime="bash") == [])


def test_assert_imports_ok():
    print("\n[assert_imports_ok raises with violations attribute]")
    forbidden = {"socket"}
    try:
        assert_imports_ok(GOOD_IMPL, forbidden=forbidden)
        t("good impl passes", True)
    except StaticCheckError:
        t("good impl passes", False)

    try:
        assert_imports_ok(BAD_IMPL_DIRECT, forbidden=forbidden)
        t("bad impl raises", False)
    except StaticCheckError as e:
        t("bad impl raises StaticCheckError", True)
        t("violations attribute equals ['socket']",
          e.violations == ["socket"])
        t("message mentions 'forbidden imports'",
          "forbidden imports" in str(e))


# ---------- E2E through the MCP server ------------------------------------

def _forge(client, *, name, impl, schema=None, runtime="python", overwrite=False):
    args = {
        "name": name,
        "description": name,
        "input_schema": schema or {"type": "object", "properties": {}},
        "impl": impl,
        "runtime": runtime,
        "overwrite": overwrite,
    }
    return client.request("tools/call",
                          {"name": "forge_tool", "arguments": args})


def test_mcp_rejects_socket_import_with_default_policy():
    print("\n[MCP: default policy rejects 'import socket']")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            resp = _forge(client, name="benign_csv", impl=BAD_IMPL_DIRECT)
        finally:
            client.close()
        result = resp["result"]
        t("isError=True", result.get("isError") is True)
        text = result["content"][0]["text"]
        t("error mentions policy + socket",
          "policy" in text.lower() and "socket" in text)
        # Tool should NOT have been registered
        # (we can verify by checking the registry through the runtime)


def test_mcp_accepts_safe_tool():
    print("\n[MCP: stdlib-only csv impl forges fine]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            resp = _forge(client, name="csv_count", impl=GOOD_IMPL,
                          schema={"type": "object", "required": ["n"],
                                   "properties": {"n": {"type": "integer"}}})
        finally:
            client.close()
        t("isError=False",
          resp["result"].get("isError") is False)


def test_mcp_accepts_bash_impl_unchecked():
    print("\n[MCP: bash impl is not subject to Python static check]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            # this bash code says 'socket' textually, but it's bash, so check skips
            bash = '#!/bin/bash\n# socket socket socket — just text\necho \'{"data":{}}\'\n'
            resp = _forge(client, name="bashy", impl=bash, runtime="bash")
        finally:
            client.close()
        t("bash impl forges OK despite 'socket' literal",
          resp["result"].get("isError") is False)


def test_mcp_rejects_syntax_error():
    print("\n[MCP: unparseable Python is rejected]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            resp = _forge(client, name="broken", impl=SYNTAX_ERROR)
        finally:
            client.close()
        t("isError=True", resp["result"].get("isError") is True)
        t("error mentions <unparseable>",
          "<unparseable>" in resp["result"]["content"][0]["text"])


def test_mcp_respects_custom_policy_widens_forbidden():
    print("\n[MCP: operator policy can ADD forbidden imports]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Operator decides 'csv' is also forbidden (silly but tests the path).
        (td / "policy.json").write_text(json.dumps({
            "forbidden_imports": ["socket", "subprocess", "csv"],
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            resp = _forge(client, name="csv_count", impl=GOOD_IMPL)
        finally:
            client.close()
        t("widened policy rejects csv import too",
          resp["result"].get("isError") is True)
        t("error names csv as the violation",
          "csv" in resp["result"]["content"][0]["text"])


def test_mcp_respects_custom_policy_narrows_forbidden():
    print("\n[MCP: operator policy can shrink forbidden list]")
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        # Operator removes 'socket' from forbidden (e.g. on a sandboxed
        # workflow that needs network, knowing bwrap still unshares net
        # by default unless meta.network is also set).
        (td / "policy.json").write_text(json.dumps({
            "forbidden_imports": [],   # explicit empty list
        }))
        client = MCPClient(td)
        try:
            client.initialize()
            resp = _forge(client, name="net_tool", impl=BAD_IMPL_DIRECT)
        finally:
            client.close()
        t("empty forbidden list lets socket through",
          resp["result"].get("isError") is False)


def test_mcp_rejects_forbidden_tool_name():
    print("\n[MCP: forbidden_tool_names also gates registration]")
    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td))
        try:
            client.initialize()
            # Default policy has shell.* in forbidden_tool_names
            resp = _forge(client, name="shell_runner", impl=GOOD_IMPL)
            t("non-matching name 'shell_runner' allowed",
              resp["result"].get("isError") is False)
            resp = _forge(client, name="shell.execute", impl=GOOD_IMPL)
            t("matching glob 'shell.execute' denied",
              resp["result"].get("isError") is True)
            t("error names rule",
              "forbidden:shell.*" in resp["result"]["content"][0]["text"])
        finally:
            client.close()


def main() -> int:
    test_scan_imports_finds_root_modules()
    test_check_imports_returns_violation_list()
    test_assert_imports_ok()
    test_mcp_rejects_socket_import_with_default_policy()
    test_mcp_accepts_safe_tool()
    test_mcp_accepts_bash_impl_unchecked()
    test_mcp_rejects_syntax_error()
    test_mcp_respects_custom_policy_widens_forbidden()
    test_mcp_respects_custom_policy_narrows_forbidden()
    test_mcp_rejects_forbidden_tool_name()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
