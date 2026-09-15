#!/usr/bin/env python3
"""capability_map.py — ADR-0190 self-aware capability map.

Renders the compact, always-injected system-prompt brief for a
``capability_aware: true`` persona, generated entirely from
``capability_registry.CAPABILITIES`` — never hand-written prose, so it
cannot silently drift from what is actually wired (ADR-0190 "What NOT to
Do": a capability the registry doesn't mark ``status="wired"`` must never
be implied as available).
"""
from __future__ import annotations

try:
    import capability_registry as _reg  # type: ignore[import-not-found]
except ImportError:
    import sys as _sys
    from pathlib import Path as _Path
    _here = _Path(__file__).resolve().parent
    if str(_here) not in _sys.path:
        _sys.path.insert(0, str(_here))
    import capability_registry as _reg  # type: ignore[import-not-found]


_MARKER = "**What CorvinOS can do (generated — see ADR-0190, do not hand-edit)**"

_HEADER = (
    f"{_MARKER}\n"
    "You are Corvin OS. Below is what you can actually DO from this chat "
    "right now, generated from the live capability registry — not a guess. "
    "If a request matches something marked 'not yet available', say so "
    "honestly instead of attempting a workaround that bypasses the real "
    "gate.\n"
)


def _server_key_from_tool_names(tool_names: tuple[str, ...]) -> str | None:
    """Derive the mcp_servers dict key from a capability's tool names —
    ``mcp__<server>__<tool>`` → ``<server>``. None if no tool follows the
    MCP naming convention."""
    for name in tool_names:
        if name.startswith("mcp__"):
            rest = name[len("mcp__"):]
            if "__" in rest:
                return rest.split("__", 1)[0]
    return None


def render_capability_map(
    *,
    forge_enabled: bool = False,
    skill_forge_enabled: bool = False,
    delegate_enabled: bool = False,
    orchestration_enabled: bool = False,
    available_servers: set[str] | None = None,
) -> str:
    """Build the capability-map brief for a resolved persona profile.

    A ``wired`` capability whose ``persona_flag`` is set but not satisfied
    by this profile is omitted — it genuinely is not available to THIS
    persona, and showing it would be misleading.

    A ``wired`` capability with no ``persona_flag`` (persona-hardcoded or
    catalog-attached servers like Playwright / imagegen-zero-config) is
    gated against ``available_servers`` — the set of MCP server keys the
    caller's resolved profile actually attaches (persona mcp_servers plus
    tenant-activated mcp_manager catalog ids). Without that gate the map
    claimed Browser Automation for personas that never attach Playwright —
    a direct violation of ADR-0190's "never claim a capability that isn't
    actually reachable" rule (adversarial-review finding, 2026-07-12).
    ``available_servers=None`` (legacy callers) preserves the old
    always-show behavior.

    ``planned`` capabilities are always shown — they are equally
    unavailable to every persona today, and disclosure matters more than
    persona-scoping for something that doesn't exist yet.

    Returns "" if there is nothing to say (should not normally happen —
    the "planned" list alone guarantees non-empty output as long as the
    registry has any planned entries).
    """
    flags = {
        "forge_enabled": bool(forge_enabled),
        "skill_forge_enabled": bool(skill_forge_enabled),
        "delegate_enabled": bool(delegate_enabled),
        "orchestration_enabled": bool(orchestration_enabled),
    }

    wired_lines: list[str] = []
    for cap in _reg.capabilities_by_status("wired"):
        if cap.persona_flag is not None and not flags.get(cap.persona_flag, False):
            continue
        if cap.persona_flag is None and available_servers is not None:
            key = _server_key_from_tool_names(cap.tool_names)
            if key is not None and key not in available_servers:
                continue
        wired_lines.append(f"- **{cap.domain}** — {cap.one_liner}")

    planned_lines: list[str] = []
    for cap in _reg.capabilities_by_status("planned"):
        planned_lines.append(f"- **{cap.domain}** — {cap.not_yet_note}")

    if not wired_lines and not planned_lines:
        return ""

    parts = [_HEADER]
    if wired_lines:
        parts.append("You can do:\n" + "\n".join(wired_lines))
    if planned_lines:
        parts.append(
            "Not yet available via chat (tell the user to use the console, "
            "or that it's on the roadmap — never pretend to do it another "
            "way):\n" + "\n".join(planned_lines)
        )
    return "\n\n".join(parts)
