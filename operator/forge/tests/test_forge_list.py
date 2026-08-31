"""X2 — wire-level E2E for the new forge_list MCP tool.

Fictional task: a coder persona arrives in a workspace that already has
two forged tools — `code.csv_diff` and `code.txt_count`. Before forging
a third, it calls forge_list to see what's there. Verifies the wire
shape: tools/list contains forge_list, tools/call returns the two
existing tools as structured content, scope filter narrows the result.

Run: python3 operator/forge/tests/test_forge_list.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import os

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "forge"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Isolate the forge MCP subprocess from pre-existing repo-scope tools by
# pinning everything to a tempdir-backed user scope. MCPClient inherits
# our env, so this redirects all four scopes (task/session/project/user)
# to a fresh location for the duration of the test.
_SCOPE_TD = tempfile.mkdtemp(prefix="forge-list-test-")
os.environ["CORVIN_HOME"] = _SCOPE_TD
os.environ["CORVIN_FORCE_SCOPE"] = "user"

# Reuse the existing MCP harness from test_mcp.py — same client, same patterns.
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


CSV_DIFF_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"ok": True, "diff": "stub"}))
'''
CSV_DIFF_SCHEMA = {
    "type": "object",
    "properties": {"a": {"type": "string"}, "b": {"type": "string"}},
}

TXT_COUNT_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"ok": True, "n": 0}))
'''
TXT_COUNT_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
}


def main() -> int:
    print("[forge_list — discovery before forging duplicates]")

    with tempfile.TemporaryDirectory() as td:
        client = MCPClient(Path(td), permission_mode="yes")
        try:
            client.initialize()

            # 1) tools/list MUST contain forge_list now
            r_list = client.request("tools/list")
            tool_names = {t["name"] for t in r_list["result"]["tools"]}
            t("tools/list exposes forge_tool",   "forge_tool" in tool_names)
            t("tools/list exposes forge_promote","forge_promote" in tool_names)
            t("tools/list exposes forge_list (new)",
              "forge_list" in tool_names)

            # 2) Baseline: capture the pre-test set of tool names. The
            # forge MCP server walks ALL scopes (incl. project = git
            # repo), so an honest "empty" check would require purging
            # the whole repo's .corvin/forge — too invasive for one
            # test. Instead we record the baseline and assert the delta.
            r_base = client.request("tools/call", {
                "name": "forge_list", "arguments": {},
            })
            res_base = r_base["result"]
            t("forge_list (baseline): isError=False",
              res_base.get("isError") is False,
              detail=str(res_base)[:160])
            baseline_tools = (res_base.get("structuredContent") or {}).get("tools") or []
            baseline_names = {tt.get("name") for tt in baseline_tools}
            t("baseline does not contain our test names",
              not ({"code.csv_diff", "code.txt_count"} & baseline_names),
              detail=f"baseline={baseline_names!r}")

            # 3) Forge two tools
            for name, schema, impl in [
                ("code.csv_diff", CSV_DIFF_SCHEMA, CSV_DIFF_IMPL),
                ("code.txt_count", TXT_COUNT_SCHEMA, TXT_COUNT_IMPL),
            ]:
                r = client.request("tools/call", {
                    "name": "forge_tool",
                    "arguments": {
                        "name": name,
                        "description": f"stub {name}",
                        "input_schema": schema,
                        "impl": impl,
                    },
                })
                t(f"forge {name}",
                  r.get("result", {}).get("isError") is not True,
                  detail=str(r)[:160])
                # consume the tools/list_changed notification
                client.expect_notification("notifications/tools/list_changed")

            # 4) Now forge_list returns both
            r_two = client.request("tools/call", {
                "name": "forge_list", "arguments": {},
            })
            res_two = r_two["result"]
            t("forge_list (after 2 forges) isError=False",
              res_two.get("isError") is False)
            tools = (res_two.get("structuredContent") or {}).get("tools") or []
            names_seen = {tt.get("name") for tt in tools}
            t("forge_list returns both tools",
              {"code.csv_diff", "code.txt_count"}.issubset(names_seen),
              detail=f"got {names_seen!r}")
            t("forge_list does NOT include forge_tool / forge_promote / forge_list",
              not ({"forge_tool", "forge_promote", "forge_list"} & names_seen))
            t("each entry has name, description, scope, call_count",
              all({"name", "description", "scope", "call_count"} <= set(tt.keys())
                  for tt in tools),
              detail=f"sample={tools[0] if tools else None}")

            # 5) scope filter — task scope wasn't created in this test
            r_scope = client.request("tools/call", {
                "name": "forge_list", "arguments": {"scope": "task"},
            })
            res_scope = r_scope["result"]
            tools_scope = (res_scope.get("structuredContent") or {}).get("tools") or []
            scope_names = {tt.get("name") for tt in tools_scope}
            t("scope=task filter excludes our test tools (we wrote into legacy)",
              not ({"code.csv_diff", "code.txt_count"} & scope_names),
              detail=f"got {scope_names!r}")

            # 6) invalid scope value yields an isError=True response
            r_bad = client.request("tools/call", {
                "name": "forge_list", "arguments": {"scope": "garbage"},
            })
            res_bad = r_bad["result"]
            err_text = (res_bad.get("content") or [{}])[0].get("text", "")
            t("invalid scope: isError=True",
              res_bad.get("isError") is True,
              detail=str(res_bad)[:200])
            t("invalid scope: error text mentions 'garbage'",
              "garbage" in err_text,
              detail=f"err={err_text!r}")

        finally:
            stderr = client.close()
            if FAIL > 0:
                print("\n--- subprocess stderr (last 800 chars) ---")
                print(stderr[-800:])

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
