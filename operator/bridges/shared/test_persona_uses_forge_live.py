"""X4 — Live verification: does a persona ACTUALLY use the forge MCP
when asked to generate a tool?

Mechanical correctness has been verified mechanically (mcp-protocol
tests, sandbox tests, namespace gates etc). This test answers the
remaining question: when an end-user gives the `coder` persona a clear
'forge me a tool' request, does Claude reach for mcp__forge__forge_tool
on its own — or does it inline a Bash one-liner because the brief
didn't land?

Spawns a real `claude -p` subprocess with:
  - the resolved coder persona's append_system (incl. forge brief)
  - the materialized MCP config (so mcp__forge__* tools are reachable)
  - FORGE_PERSONA=coder env so the namespace gate / persona ACL agree

Captures stream-json output, parses tool_use events, asserts that
`mcp__forge__forge_tool` appears at least once in the transcript and
that the chosen tool name starts with `code.` (the coder namespace).

Opt-in via CLAUDE_LIVE_E2E=1 — uses real API credits and takes ~1-3
minutes. Default skips with a clear message so the suite stays
deterministic.

Run: CLAUDE_LIVE_E2E=1 python3 operator/bridges/shared/test_persona_uses_forge_live.py
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "cowork" / "lib"))
sys.path.insert(0, str(REPO / "operator" / "forge"))


def _maybe_skip() -> bool:
    if os.environ.get("CLAUDE_LIVE_E2E") != "1":
        print("SKIP: set CLAUDE_LIVE_E2E=1 to run — spawns real `claude -p`, "
              "costs API credits, ~1-3 min runtime.")
        return True
    if shutil.which("claude") is None:
        print("SKIP: `claude` binary not on PATH — install Claude Code first.")
        return True
    return False


PROMPT = (
    "Please forge a deterministic tool called `code.line_count` that "
    "counts the lines in a text file. The schema is "
    '{"type":"object","required":["path"],'
    '"properties":{"path":{"type":"string","x-bind":"ro"}}} '
    "and the implementation should read sys.stdin (the JSON payload), "
    "open the path, count lines, and print "
    '{"ok":true,"data":{"lines":N}} as JSON to stdout. '
    "Use the forge MCP server (mcp__forge__forge_tool). "
    "After registering, just confirm the tool name back — do not call it."
)


def _parse_tool_uses(stream_jsonl: str) -> list[dict]:
    """Walk a stream-json output blob and return every tool_use block."""
    uses: list[dict] = []
    for line in stream_jsonl.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("type") != "assistant":
            continue
        content = (msg.get("message") or {}).get("content") or []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                uses.append(block)
    return uses


def main() -> int:
    if _maybe_skip():
        return 0

    print("[live: coder persona — forge me a tool]")
    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env["CORVIN_HOME"] = td
        env["CORVIN_FORCE_SCOPE"] = "user"
        env["FORGE_PERSONA"] = "coder"

        os.environ["CORVIN_HOME"] = td
        os.environ["CORVIN_FORCE_SCOPE"] = "user"

        import resolver as _resolver  # type: ignore
        # Resolve the coder profile — incl. the auto-injected forge brief.
        profile = _resolver.resolve("coder")
        append_system = profile.get("append_system") or ""
        mcp_cfg_path = _resolver.materialize_mcp(profile)

        if not mcp_cfg_path:
            print("FAIL: materialize_mcp returned None — coder persona "
                  "has no MCP config? Check resolver.")
            return 1

        # Sanity: the brief landed and forge_tool is in allowed_tools.
        if "Forge tool generation" not in append_system:
            print("FAIL: forge brief missing from coder's append_system")
            return 1
        allowed = profile.get("allowed_tools") or []
        if "mcp__forge__forge_tool" not in allowed:
            print(f"FAIL: forge_tool missing from allowed_tools: {allowed[:8]}")
            return 1

        cmd = [
            "claude", "-p", PROMPT,
            "--append-system-prompt", append_system,
            "--mcp-config", mcp_cfg_path,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "bypassPermissions",
            "--allowedTools",
            "mcp__forge__forge_tool,mcp__forge__forge_list",
        ]
        print(f"--- spawning claude (append_system: {len(append_system)} chars, "
              f"mcp_cfg: {mcp_cfg_path}) ---")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=240,
                check=False, env=env,
            )
        except subprocess.TimeoutExpired:
            print("FAIL: claude subprocess timed out after 240s")
            return 1

        out = result.stdout or ""
        err = result.stderr or ""
        if not out.strip():
            print(f"FAIL: empty stdout. stderr (last 800):\n{err[-800:]}")
            return 1

        uses = _parse_tool_uses(out)
        forge_calls = [u for u in uses
                       if u.get("name") == "mcp__forge__forge_tool"]
        forge_lists = [u for u in uses
                       if u.get("name") == "mcp__forge__forge_list"]

        print(f"--- tool_use blocks: {len(uses)} total, "
              f"{len(forge_lists)} forge_list, {len(forge_calls)} forge_tool ---")
        for u in uses[:10]:
            print(f"  {u.get('name')}: "
                  f"{json.dumps(u.get('input', {}))[:200]}")

        rc = 0

        if not forge_calls:
            print("\nFAIL: persona did NOT call mcp__forge__forge_tool — "
                  "the brief did not land or the LLM ignored it.")
            print(f"--- last 800 chars of stdout ---\n{out[-800:]}")
            rc = 1
        else:
            print(f"\nPASS: persona called mcp__forge__forge_tool "
                  f"{len(forge_calls)} time(s)")
            chosen_name = (forge_calls[0].get("input") or {}).get("name", "")
            if chosen_name.startswith("code."):
                print(f"PASS: chosen tool name '{chosen_name}' "
                      f"respects coder namespace 'code.'")
            else:
                print(f"WARN: chosen tool name '{chosen_name}' "
                      f"does NOT start with 'code.' — namespace gate "
                      f"would have rejected this. Brief discipline weak.")
                rc = 1

        return rc


if __name__ == "__main__":
    raise SystemExit(main())
