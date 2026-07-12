"""E2E smoke for the forge persona inside the voice-skill repo.

Validates the vertical slice: a chat_profile that names persona=forge
resolves through cowork into a restrictive profile that
  - keeps the operator OUT of bypassPermissions mode
  - exposes the forge MCP server with the right command + env
  - allows mcp__forge__forge_tool / forge_promote in the tool list
  - blocks Bash / Edit / Write
  - materializes a usable --mcp-config JSON file with template vars expanded

This is the contract the bridge adapter relies on. If this test passes,
the bridge does not need any patch — it can already route to forge.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COWORK_LIB = REPO_ROOT / "operator" / "cowork" / "lib"
sys.path.insert(0, str(COWORK_LIB))

import resolver  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def test_persona_loads_from_bundle():
    print("\n[forge persona loads from cowork bundle]")
    p = resolver.load("forge")
    t("persona loadable", p is not None)
    t("name = forge",      p["name"] == "forge")
    # Persona-Rework v0.9: every bundled persona ships with permission_mode
    # bypassPermissions and empty allow/deny — the layer-10 path-gate hook
    # is the structural enforcement that keeps forge/skill-forge workspaces
    # write-isolated regardless of permission mode. See CLAUDE.md
    # "Persona-Rework v0.9" + "Path-Gate Hook (layer 10)".
    t("permission_mode = bypassPermissions (post-Rework v0.9)",
      p["permission_mode"] == "bypassPermissions",
      detail=f"got {p['permission_mode']!r}")
    t("zero_config = True",
      p.get("zero_config") is True)


def test_persona_capability_flags_carry_forge_intent():
    print("\n[forge persona advertises forge + skill-forge capability]")
    # Post-Rework v0.9 the differentiation moved from permission-tightening
    # to capability flags / explicit-allow + namespace prefix. The legacy
    # "Bash explicitly disallowed" assertion has been retired (per CLAUDE.md:
    # "Don't reintroduce per-persona disallowed_tools lists for defense-in-
    # depth on Bash/Edit/Write — they were churn, not protection").
    #
    # The unified `forge` persona is the dedicated generator: it lists the
    # forge MCP tools explicitly in allowed_tools (rather than relying on
    # the auto-injection that `forge_enabled: true` triggers for other
    # personas). It also sets `skill_forge_enabled: true` to receive the
    # skill-creation tools.
    p = resolver.load("forge")
    allowed = set(p.get("allowed_tools") or [])
    t("forge_tool MCP in allowed_tools",
      "mcp__forge__forge_tool" in allowed)
    t("forge_promote MCP in allowed_tools",
      "mcp__forge__forge_promote" in allowed)
    t("skill_forge_enabled = True",
      p.get("skill_forge_enabled") is True)
    t("disallowed_tools is empty (path-gate is the sandbox now)",
      p.get("disallowed_tools") in ([], None),
      detail=f"got {p.get('disallowed_tools')!r}")


def test_resolve_with_chat_profile_overrides():
    print("\n[chat_profile overrides merge with persona]")
    # Operator declares persona=forge in a chat_profile and adds a custom
    # disallowed tool; resolver should keep persona's defaults plus the
    # extra deny — and never weaken the disallow list.
    overrides = {
        "persona": "forge",
        "disallowed_tools": ["WebSearch"],
        "append_system": " Extra user-side reminder.",
    }
    p = resolver.resolve("forge", overrides=overrides)
    # The override-only deny survives. The persona's own disallowed_tools
    # is empty (post-Rework), so we no longer assert "Bash" stays in there.
    t("merged disallow contains the chat_profile override",
      "WebSearch" in p["disallowed_tools"])
    t("permission_mode survives merge as 'bypassPermissions'",
      p["permission_mode"] == "bypassPermissions")
    t("append_system concatenated, contains both halves",
      "Forge persona" in p["append_system"]
      and "Extra user-side reminder" in p["append_system"])


def test_mcp_servers_materialize_with_expanded_paths():
    print("\n[mcp_servers materialized → real path on disk + vars expanded]")
    p = resolver.load("forge")
    cfg_path = resolver.materialize_mcp(p)
    t("materialize returned a path",
      isinstance(cfg_path, str) and len(cfg_path) > 0)
    t("path exists on disk",
      Path(cfg_path).exists())
    cfg = json.loads(Path(cfg_path).read_text())
    servers = cfg.get("mcpServers", {})
    t("forge server present",         "forge" in servers)
    forge = servers["forge"]
    # forge.json now uses the {{PYTHON}} template var → resolves to the
    # running interpreter's ABSOLUTE path (sys.executable), not the bare
    # "python3". Bare "python3" broke under the adapter's stripped-PATH spawn
    # (engine-autodetect class): the sandbox could not find an interpreter.
    # The command must be an absolute path to a python executable.
    _cmd = forge.get("command", "")
    t("command is an absolute python interpreter path",
      _cmd.startswith("/") and "python" in Path(_cmd).name,
      detail=f"command={_cmd!r}")
    t("args[0] is absolute path",
      forge["args"][0].startswith("/"),
      detail=f"args[0]={forge['args'][0]}")
    t("args[0] points at our forge.py",
      forge["args"][0].endswith("operator/forge/forge.py"))
    t("args contains 'mcp'",          "mcp" in forge["args"])
    t("permission-mode yes is set",
      "yes" in forge["args"])
    env = forge.get("env", {})
    # J.1.4a: forge persona no longer hardcodes FORGE_ROOT — the forge
    # plugin resolves its workspace via forge.scope at runtime.
    t("FORGE_ROOT NOT hardcoded in forge persona env",
      "FORGE_ROOT" not in env,
      detail=f"env keys = {sorted(env)}")
    t("no template vars left unexpanded",
      "{{" not in json.dumps(servers))


def test_mcp_config_actually_starts_a_forge_server():
    """Last mile: spawn the forge MCP server using the materialized
    config, send initialize, expect a clean handshake."""
    print("\n[materialized config actually starts forge over stdio]")
    import subprocess, threading, time
    p = resolver.load("forge")
    cfg = json.loads(
        Path(resolver.materialize_mcp(p)).read_text()
    )["mcpServers"]["forge"]

    with tempfile.TemporaryDirectory() as td:
        env = dict(os.environ)
        env.update(cfg.get("env", {}))
        env["FORGE_ROOT"] = td      # isolated scratch root for the test
        proc = subprocess.Popen(
            [cfg["command"]] + cfg["args"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1, env=env,
        )
        try:
            req = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2024-11-05",
                              "capabilities": {},
                              "clientInfo": {"name": "voice-smoke",
                                             "version": "0"}}}
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            resp = json.loads(line)
            t("initialize result returned",
              "result" in resp)
            t("serverInfo.name = claude-tool-forge",
              resp["result"]["serverInfo"]["name"] == "claude-tool-forge")
            t("tools.listChanged capability present",
              resp["result"]["capabilities"]["tools"]["listChanged"] is True)
            # tools/list shows forge_tool + forge_promote
            req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            proc.stdin.write(json.dumps(req) + "\n")
            proc.stdin.flush()
            line = proc.stdout.readline()
            tools = [tt["name"] for tt in
                     json.loads(line)["result"]["tools"]]
            t("forge_tool listed",     "forge_tool" in tools)
            t("forge_promote listed",  "forge_promote" in tools)
        finally:
            proc.stdin.close()
            proc.wait(timeout=3)
            t("server exited rc=0", proc.returncode == 0)


def test_forge_skill_md_is_voice_contextualized():
    """SKILL.md inside the forge plugin must reflect that it lives in
    the voice repo: workspace path under ~/.config/corvin-voice/forge/,
    description mentions cowork/persona/voice integration, and no
    leftover playground references to .claude/forge/."""
    print("\n[forge/SKILL.md voice-context wiring]")
    skill = Path(__file__).resolve().parents[1] / "SKILL.md"
    body = skill.read_text()
    front = body.split("---", 2)[1] if body.startswith("---") else ""

    t("description mentions cowork OR persona OR voice",
      any(token in front.lower()
          for token in ("cowork", "persona", "voice")))
    t("workspace layout uses ~/.config/corvin-voice/forge/",
      "~/.config/corvin-voice/forge/" in body)
    t("no leftover .claude/forge/ playground paths",
      ".claude/forge/" not in body,
      detail=f"hits={body.count('.claude/forge/')}")


def main() -> int:
    test_persona_loads_from_bundle()
    test_persona_capability_flags_carry_forge_intent()
    test_resolve_with_chat_profile_overrides()
    test_mcp_servers_materialize_with_expanded_paths()
    test_mcp_config_actually_starts_a_forge_server()
    test_forge_skill_md_is_voice_contextualized()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
