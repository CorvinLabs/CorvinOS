#!/usr/bin/env python3
"""Unit tests for capability_registry.py + capability_map.py (ADR-0190).

Run: python3 operator/cowork/test/test_capability_registry.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "lib"))

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


def main() -> int:
    import capability_map as cmap
    import capability_registry as reg

    # ── __post_init__ validation ───────────────────────────────────────────
    try:
        reg.Capability(id="x", domain="X", status="wired")  # missing one_liner/tool_names
        expect(False, "wired capability without one_liner/tool_names raises ValueError")
    except ValueError:
        expect(True, "wired capability without one_liner/tool_names raises ValueError")

    try:
        reg.Capability(id="y", domain="Y", status="planned")  # missing not_yet_note
        expect(False, "planned capability without not_yet_note raises ValueError")
    except ValueError:
        expect(True, "planned capability without not_yet_note raises ValueError")

    ok_wired = reg.Capability(
        id="z", domain="Z", status="wired", one_liner="does a thing",
        tool_names=("mcp__z__do_thing",),
    )
    expect(ok_wired.id == "z", "well-formed wired Capability constructs cleanly")

    ok_planned = reg.Capability(
        id="w", domain="W", status="planned", not_yet_note="not built yet",
    )
    expect(ok_planned.status == "planned", "well-formed planned Capability constructs cleanly")

    # ── Registry-wide invariants ───────────────────────────────────────────
    ids = [c.id for c in reg.CAPABILITIES]
    expect(len(ids) == len(set(ids)), "no duplicate capability ids in CAPABILITIES")

    wired = reg.capabilities_by_status("wired")
    planned = reg.capabilities_by_status("planned")
    expect(len(wired) > 0, "at least one wired capability is registered")
    expect(len(planned) > 0, "at least one planned capability is registered (ADR-0190 M2-M8 tracked)")
    expect(len(wired) + len(planned) == len(reg.CAPABILITIES),
           "every capability is either wired or planned (none deprecated yet)")

    got = reg.get("forge.tools")
    expect(got is not None and got.status == "wired", "get('forge.tools') resolves the real entry")
    expect(reg.get("does.not.exist") is None, "get() on an unknown id returns None")

    all_tools = reg.all_tool_names()
    expect("mcp__forge__forge_tool" in all_tools, "all_tool_names() includes a known real tool")
    expect(not any(t.endswith("*") for t in all_tools),
           "all_tool_names() excludes wildcard entries")

    # ── capability_map rendering ───────────────────────────────────────────
    everything_off = cmap.render_capability_map(
        forge_enabled=False, skill_forge_enabled=False, delegate_enabled=False,
    )
    expect("Forge (Tool Generation)" not in everything_off,
           "map with all flags off omits forge_enabled-gated capabilities")
    expect("Browser Automation" in everything_off,
           "map with all flags off still shows capabilities with no persona_flag")
    expect("Not yet available via chat" in everything_off,
           "map with all flags off still discloses planned capabilities")

    everything_on = cmap.render_capability_map(
        forge_enabled=True, skill_forge_enabled=True, delegate_enabled=True,
    )
    for expected in ("Forge (Tool Generation)", "SkillForge (Skill Generation)", "Delegation"):
        expect(expected in everything_on, f"map with all flags on shows {expected!r}")

    # Rendering must never raise on an empty-but-valid input.
    try:
        cmap.render_capability_map()
        expect(True, "render_capability_map() with no args (all False) does not raise")
    except Exception as exc:  # noqa: BLE001
        expect(False, "render_capability_map() with no args (all False) does not raise", str(exc))

    print()
    print(f"== {len(failures)} failure(s) ==")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
