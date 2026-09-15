"""F2 E2E: CLAUDE.md must spell out the forge-plugin conventions.

Future Claude editing this repo needs to read the rules without
re-discovering them by tripping over them. This test asserts the
relevant invariants are spelled out in CLAUDE.md:

  - forge is OPTIONAL — voice/cowork must not hard-import it
  - generated tools land in ~/.config/corvin-voice/forge/ (FORGE_ROOT)
  - the forge persona never gets bypassPermissions
  - policy.json is the only envelope; chat_profiles can't widen it
  - tool name validation accepts alnum + . + _ ; rejects /, .., leading/trailing .
  - policy hot-reload happens in tools/list / tools/call

We don't grade prose, only the load-bearing strings.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def main() -> int:
    print("\n[CLAUDE.md forge section]")
    body = CLAUDE_MD.read_text()
    t("CLAUDE.md exists", CLAUDE_MD.exists())

    # Section heading
    t("section heading present",
      "## Forge plugin (layer 6)" in body)

    # Inserted in the right spot: after Auto-routing (layer 5), before
    # Notification relay (layer 3).
    p_auto = body.find("## Auto-routing (layer 5)")
    p_forge = body.find("## Forge plugin (layer 6)")
    p_relay = body.find("## Notification relay (layer 3)")
    t("forge section is between auto-routing and notification-relay",
      p_auto != -1 and p_forge != -1 and p_relay != -1
      and p_auto < p_forge < p_relay)

    # Editorial frame
    t("'What you, as Claude Code, need to know when editing:' present",
      "What you, as Claude Code, need to know when editing:" in body)
    t("'What you must NOT do:' present",
      "What you must NOT do:" in body)

    # Forge is OPTIONAL — must mirror cowork's optionality language
    t("forge OPTIONAL clause",
      "OPTIONAL" in body and "forge" in body.lower()
      and ("hard-import" in body or "hard import" in body
           or "_se is not None" in body))

    # Generated tools land in ~/.config/corvin-voice/forge/ via FORGE_ROOT
    t("FORGE_ROOT mentioned",
      "FORGE_ROOT" in body)
    t("workspace path under user-config home",
      "~/.config/corvin-voice/forge" in body)
    t("rule against tool impls in the repo",
      "Never write tool implementations into the repo" in body
      or "do not commit tool" in body.lower()
      or "never commit tool implementations" in body.lower())

    # Forge persona never gets bypassPermissions
    t("rule: forge persona never bypassPermissions",
      "bypassPermissions" in body and "forge persona" in body.lower())
    t("operator can tighten but not loosen",
      "tighten" in body.lower() and "loosen" in body.lower())

    # policy.json is the only envelope
    t("policy.json envelope-only rule",
      "policy.json is the only" in body
      or "only place where the workflow safety envelope" in body)
    t("chat_profiles can't widen the envelope",
      ("chat_profiles" in body and "personas" in body and "widen" in body))

    # Tool name validation rules
    t("tool name validation: alnum + . + _",
      "alnum + `.` + `_`" in body
      or ("alnum" in body and "." in body and "_" in body
          and "tool name" in body.lower()))
    t("tool name validation: rejects /",
      "/" in body and ("reject" in body.lower() or "must not" in body.lower()))
    t("tool name validation: rejects ..",
      ".." in body)

    # Hot-reload of policy.json
    t("hot-reload of policy.json mentioned",
      "Hot-reload" in body or "hot-reload" in body.lower())
    t("_handle_tools_call / _handle_tools_list named",
      "_handle_tools_call" in body and "_handle_tools_list" in body)
    t("don't cache policy elsewhere rule",
      "Don't cache policy across calls" in body
      or "do not cache" in body.lower())

    # Anti-patterns
    t("anti-pattern: don't bypass MCP server",
      "Don't bypass the MCP server" in body
      or "no other supported path" in body)
    t("anti-pattern: forge_tool / forge_promote not on bypass-perm personas",
      "forge_tool" in body and "bypassPermissions" in body
      and "neutralize" in body.lower())
    t("anti-pattern: don't write policy.json from bridge",
      "Don't write to" in body and "policy.json" in body
      and ("bridge" in body.lower() or "adapter" in body.lower()))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
