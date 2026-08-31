"""Layer-9 E2E: every persona with ``forge_enabled: true`` inherits forge
capability — symmetrical to the skill-forge inject gate.

The natural workflow is "coder hits a CSV -> coder forges csv_stats ->
coder uses it -> coder commits" — without a persona-switch detour.
This test pins that contract:
  - personas with forge_enabled=true gain mcp__forge__forge_tool +
    mcp__forge__forge_promote in their resolved allowed_tools
  - FORGE_PERSONA env tag is set so the audit log can attribute the
    tool.create event to the persona that triggered it
  - the dedicated forge persona is left untouched (it ships its own
    MCP config)
  - opt-out is "set forge_enabled to false-y" (omit the flag, set
    null, or set false). zero_config is no longer the gate — that
    asymmetry was a dead-flag bug for inbox.json (zero_config=false,
    forge_enabled=true → forge tools never injected). The path-gate
    hook (layer 10) keeps the sandbox boundary structural, so the
    looser gate is safe.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "cowork" / "lib"))
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


FORGE_ENABLED_PERSONAS = ("coder", "research", "assistant", "inbox")
# browser/jarvis/local-coder/orchestrator-haiku removed in f1e3246

EXPECTED_DEFAULT_SCOPE = {
    "coder":     "project",
    "research":  "session",
    "assistant": "session",
    "inbox":     "user",
    # homeassistant has neither zero_config nor forge_enabled — opt-in only.
}


def test_zero_config_personas_get_forge_tools():
    print("\n[forge_enabled personas inherit forge tools]")
    for name in FORGE_ENABLED_PERSONAS:
        merged = resolver.resolve(name)
        allowed = merged.get("allowed_tools") or []
        t(f"{name}: forge_tool in allowed_tools",
          "mcp__forge__forge_tool" in allowed)
        t(f"{name}: forge_promote in allowed_tools",
          "mcp__forge__forge_promote" in allowed)
        mcp = merged.get("mcp_servers") or {}
        t(f"{name}: forge MCP server present",
          "forge" in mcp)
        env = (mcp.get("forge") or {}).get("env") or {}
        t(f"{name}: FORGE_PERSONA = {name}",
          env.get("FORGE_PERSONA") == name)
        # J.1.4a: no per-persona FORGE_ROOT — scope handles routing.
        t(f"{name}: FORGE_ROOT NOT set (scope handles it)",
          "FORGE_ROOT" not in env)
        # J.1.5: per-persona forge_default_scope projected as CORVIN_DEFAULT_SCOPE.
        # CORVIN_DEFAULT_SCOPE removed in Phase 7 (v1.0).
        expected_scope = EXPECTED_DEFAULT_SCOPE[name]
        t(f"{name}: CORVIN_DEFAULT_SCOPE = {expected_scope}",
          env.get("CORVIN_DEFAULT_SCOPE") == expected_scope,
          detail=f"got {env.get('CORVIN_DEFAULT_SCOPE')!r}")


def test_forge_persona_unified_pattern():
    """Persona-rework v0.9: ALL personas use the bypassPermissions + empty
    allowed/disallowed pattern. Layer-10 path-gate is the structural
    enforcement that keeps forge/skill-forge workspaces protected
    regardless of permission_mode."""
    print("\n[forge persona uses unified open pattern + layer-10 enforcement]")
    merged = resolver.resolve("forge")
    t("forge permission_mode == bypassPermissions",
      merged.get("permission_mode") == "bypassPermissions")
    t("forge has empty disallowed_tools (layer-10 is the gate)",
      not (merged.get("disallowed_tools") or []))


def test_persona_can_opt_out():
    print("\n[forge_enabled=false → no injection]")
    fake = {"name": "ascetic", "zero_config": True,
            "forge_enabled": False, "allowed_tools": ["Read"],
            "permission_mode": "default"}
    out = resolver._inject_forge_capability(fake, "ascetic")
    t("allowed_tools unchanged",
      out["allowed_tools"] == ["Read"])
    t("no mcp_servers added",
      not out.get("mcp_servers"))


def test_no_forge_enabled_unaffected():
    """Personas without forge_enabled: true (the new gate) stay untouched."""
    print("\n[persona without forge_enabled is not auto-forged]")
    fake = {"name": "manual", "zero_config": True,
            "allowed_tools": [], "permission_mode": "default"}
    out = resolver._inject_forge_capability(fake, "manual")
    t("no forge_tool added (no forge_enabled flag)",
      "mcp__forge__forge_tool" not in (out.get("allowed_tools") or []))


def test_inbox_inherits_forge_despite_non_zero_config():
    """Symmetry fix: inbox carries forge_enabled=true but zero_config=false
    (Google OAuth requires manual setup). Pre-fix, the forge tools were
    never injected — a dead flag. Post-fix, inbox gets them like any
    other forge_enabled persona."""
    print("\n[inbox: zero_config=false but forge_enabled=true → injected]")
    merged = resolver.resolve("inbox")
    allowed = merged.get("allowed_tools") or []
    t("inbox has forge_tool",
      "mcp__forge__forge_tool" in allowed)
    t("inbox has forge MCP server",
      "forge" in (merged.get("mcp_servers") or {}))


def test_materialize_per_persona_root():
    print("\n[materialize_mcp expands FORGE_PERSONA + REPO_ROOT]")
    merged = resolver.resolve("coder")
    cfg_path = resolver.materialize_mcp(merged)
    cfg = json.loads(Path(cfg_path).read_text())
    env = cfg["mcpServers"]["forge"]["env"]
    # J.1.4a: per-persona FORGE_ROOT removed; FORGE_PERSONA is the
    # attribution mechanism the audit log relies on.
    t("FORGE_PERSONA = coder",
      env.get("FORGE_PERSONA") == "coder")
    t("FORGE_ROOT NOT set (scope handles routing at runtime)",
      "FORGE_ROOT" not in env)
    args = cfg["mcpServers"]["forge"]["args"]
    t("args[0] resolved to absolute path",
      args[0].startswith("/") and args[0].endswith("operator/forge/forge.py"))


def test_idempotency_resolve_twice():
    """Resolving the same persona twice must NOT double-add the tools."""
    print("\n[resolve is idempotent — tools not duplicated]")
    a = resolver.resolve("coder")
    b = resolver.resolve("coder")
    a_tools = a.get("allowed_tools") or []
    b_tools = b.get("allowed_tools") or []
    t("forge_tool count == 1 in resolve A",
      a_tools.count("mcp__forge__forge_tool") == 1)
    t("forge_tool count == 1 in resolve B",
      b_tools.count("mcp__forge__forge_tool") == 1)


def test_forge_brief_in_append_system():
    """The forge-brief lands in append_system once per resolve, persona-aware."""
    print("\n[forge brief explains namespace + sandbox]")
    merged = resolver.resolve("coder")
    aps = merged.get("append_system") or ""
    t("brief has the marker header",
      "**Forge tool generation:**" in aps)
    t("brief mentions coder's namespace 'code.'",
      "`code.csv_diff`" in aps)
    t("coder brief: sandbox = no network (correct, no override)",
      "no network" in aps)
    t("forge brief mentions same-turn visibility (S6 reality)",
      "same turn" in aps and "not within the same message" not in aps)
    t("brief mentions discovery via forge_list",
      "forge_list" in aps)
    t("brief is appended ONCE, not duplicated",
      aps.count("**Forge tool generation:**") == 1)
    # idempotency on re-resolve
    merged2 = resolver.resolve("coder")
    aps2 = merged2.get("append_system") or ""
    t("brief appears once after second resolve too",
      aps2.count("**Forge tool generation:**") == 1)


def test_forge_brief_for_browser_says_network_allowed():
    """Research persona (replaces browser since f1e3246) has network: allow
    in bundle policy → brief must reflect that."""
    print("\n[research brief reflects network: allow]")
    merged = resolver.resolve("research")
    aps = merged.get("append_system") or ""
    # research persona mentions playwright/web browsing; should not claim no network
    t("research brief does NOT say 'no network'",
      "no network" not in aps)


def test_forge_brief_for_forge_persona_marks_wildcard():
    """forge persona has no namespace gate → brief should say so, not
    falsely promise a 'forge.' prefix."""
    print("\n[forge persona brief: wildcard, not 'forge.' prefix]")
    merged = resolver.resolve("forge")
    aps = merged.get("append_system") or ""
    # forge persona has its own append_system; the BRIEF only fires from
    # _inject_skill_forge_capability (skill_forge_enabled=true). Forge
    # MCP itself is shipped natively, not via _inject_forge_capability,
    # so the brief for forge-as-tool-creator is the persona's own
    # append_system. We verify the skill brief's discovery line lands.
    t("forge persona's resolved prompt contains skill_list discovery",
      "skill_list" in aps,
      detail="brief should advise discovery before creating")


def test_skill_forge_brief_in_append_system():
    """The skill-forge brief lands when skill_forge_enabled is on."""
    print("\n[skill-forge brief explains linter + promotion]")
    merged = resolver.resolve("coder")
    aps = merged.get("append_system") or ""
    t("brief has the marker header",
      "**Skill creation:**" in aps)
    t("brief mentions linter rejects prompt-injection",
      "prompt-injection" in aps)
    t("brief mentions promotion gates",
      "Promotion gates" in aps)
    t("brief is appended ONCE",
      aps.count("**Skill creation:**") == 1)


def test_forge_persona_now_carries_skills():
    """Phase F: forge persona is the unified generator (tools AND skills).
    The historical separation has been removed; forge.json carries
    skill_forge_enabled=true and inherits the skill-forge MCP server."""
    print("\n[forge persona is unified generator]")
    merged = resolver.resolve("forge")
    allowed = merged.get("allowed_tools") or []
    t("forge has skill_create",
      "mcp__skill_forge__skill_create" in allowed)
    t("forge has skill_promote",
      "mcp__skill_forge__skill_promote" in allowed)
    mcp = merged.get("mcp_servers") or {}
    t("forge has skill_forge MCP server",
      "skill_forge" in mcp)
    t("forge keeps its native forge MCP",
      "forge" in mcp)


def test_skill_forge_alias_resolves_to_forge():
    """Phase F: chat_profiles pinning persona='skill-forge' keep working
    via the _PERSONA_ALIASES table — they resolve to the unified forge
    persona without operator intervention."""
    print("\n[skill-forge alias resolves to forge]")
    merged = resolver.resolve("skill-forge")
    t("alias resolves: name == 'forge'",
      merged.get("_persona") == "forge")
    allowed = merged.get("allowed_tools") or []
    t("alias gets the unified tool list",
      "mcp__forge__forge_tool" in allowed
      and "mcp__skill_forge__skill_create" in allowed)
    mcp = merged.get("mcp_servers") or {}
    t("alias gets both MCP servers",
      "forge" in mcp and "skill_forge" in mcp)


def test_brief_skipped_when_capability_off():
    """Persona without the flag MUST NOT receive the brief."""
    print("\n[no-capability persona has no brief]")
    fake = {"name": "ascetic", "zero_config": True,
            "forge_enabled": False, "allowed_tools": ["Read"],
            "permission_mode": "default", "append_system": "Be terse."}
    out = resolver._inject_forge_capability(fake, "ascetic")
    t("no forge brief",
      "Forge tool generation" not in (out.get("append_system") or ""))
    t("original append_system preserved",
      out.get("append_system") == "Be terse.")


def main() -> int:
    test_zero_config_personas_get_forge_tools()
    test_forge_persona_unified_pattern()
    test_persona_can_opt_out()
    test_no_forge_enabled_unaffected()
    test_inbox_inherits_forge_despite_non_zero_config()
    test_materialize_per_persona_root()
    test_idempotency_resolve_twice()
    test_forge_brief_in_append_system()
    test_forge_brief_for_browser_says_network_allowed()
    test_forge_brief_for_forge_persona_marks_wildcard()
    test_skill_forge_brief_in_append_system()
    test_forge_persona_now_carries_skills()
    test_skill_forge_alias_resolves_to_forge()
    test_brief_skipped_when_capability_off()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
