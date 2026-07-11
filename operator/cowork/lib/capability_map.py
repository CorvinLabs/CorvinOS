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


def render_capability_map(
    *,
    forge_enabled: bool = False,
    skill_forge_enabled: bool = False,
    delegate_enabled: bool = False,
    orchestration_enabled: bool = False,
) -> str:
    """Build the capability-map brief for a resolved persona profile.

    A ``wired`` capability whose ``persona_flag`` is set but not satisfied
    by this profile is omitted — it genuinely is not available to THIS
    persona, and showing it would be misleading. A ``wired`` capability
    with no ``persona_flag`` (externally-wired tools like Playwright/
    ImageGen, which are baked directly into specific persona JSON files
    rather than resolver-gated) is always shown, since this function has
    no way to know whether the caller's persona hardcodes those servers —
    callers whose persona doesn't should simply never see the corresponding
    tool calls succeed, which is a pre-existing property of those personas,
    not something this brief needs to gate.

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
