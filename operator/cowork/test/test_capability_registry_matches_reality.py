#!/usr/bin/env python3
"""ADR-0190 — CI enforcement: capability_registry entries must be real.

Two failure modes this test exists to catch BEFORE merge (both hit this
repo for real, the hard way, in the ADR-0190 and ADR-0145 production-
readiness audits):

  1. A registry entry claims ``status="wired"`` for a tool name that does
     not actually exist anywhere in the subsystem it's supposed to belong
     to (the "IBC domain that never resolved" class of bug — a status
     claim nobody checked against reality).
  2. A registry entry's ``persona_flag`` is not a real flag the resolver
     actually propagates (a typo'd flag name that silently never gates
     anything).

This is a lightweight, grep-based reality check — not a full AST scan of
every MCP server's tool-registration format (those differ per server and a
fully generic parser would be its own maintenance burden). It trades some
precision for being simple enough to trust and cheap enough to run on
every change.

Run: python3 operator/cowork/test/test_capability_registry_matches_reality.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "lib"))

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


# Maps a capability id prefix to the subsystem tree its tool names must
# appear somewhere under. Deliberately coarse (a subsystem tree, not one
# file) — tool names get re-exported/re-imported across a few files within
# a subsystem; the point is to catch a fabricated or renamed tool, not to
# pin an exact file:line.
_SUBSYSTEM_ROOTS: dict[str, tuple[str, ...]] = {
    "forge.": ("operator/forge",),
    "compute.": ("operator/forge", "core/compute"),
    "skill_forge.": ("operator/skill-forge",),
    "delegate.": ("core/delegate",),
}


def _subsystem_source(capability_id: str) -> str | None:
    """Concatenate every .py file under the subsystem root(s) for this
    capability id. Returns None if no root mapping exists (e.g. externally
    -wired capabilities like Playwright/ImageGen, which have no CorvinOS
    source tree to check tool names against)."""
    roots: tuple[str, ...] | None = None
    for prefix, paths in _SUBSYSTEM_ROOTS.items():
        if capability_id.startswith(prefix):
            roots = paths
            break
    if roots is None:
        return None
    chunks: list[str] = []
    for rel in roots:
        root = REPO_ROOT / rel
        if not root.is_dir():
            continue
        for f in root.rglob("*.py"):
            if "__pycache__" in f.parts:
                continue
            try:
                chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return "\n".join(chunks)


def main() -> int:
    import capability_registry as reg  # type: ignore

    resolver_src = (HERE.parent / "lib" / "resolver.py").read_text(encoding="utf-8")

    ids_seen: set[str] = set()
    for cap in reg.CAPABILITIES:
        expect(cap.id not in ids_seen, f"capability id {cap.id!r} is unique")
        ids_seen.add(cap.id)

        if cap.status != "wired":
            continue

        # ── Tool names must appear in the subsystem's real source ────────
        matched_prefix = next(
            (p for p in _SUBSYSTEM_ROOTS if cap.id.startswith(p)), None
        )
        source = _subsystem_source(cap.id)
        if source is None:
            # No CorvinOS source tree to check against (externally-wired
            # tools like Playwright/ImageGen) — nothing to verify here.
            continue
        for tool_name in cap.tool_names:
            if tool_name.endswith("*"):
                continue  # wildcard entries aren't literal tool names
            # Strip the mcp__<server>__ prefix — the source registers bare
            # tool names ("forge_tool"), the client-visible name adds the
            # server prefix.
            bare = tool_name
            if bare.startswith("mcp__"):
                parts = bare.split("__", 2)
                if len(parts) == 3:
                    bare = parts[2]
            expect(
                bare in source,
                f"capability {cap.id!r}: tool {tool_name!r} found in its subsystem source",
                f"searched under {_SUBSYSTEM_ROOTS.get(matched_prefix)}",
            )

        # ── persona_flag, if set, must be a real resolver-propagated flag ─
        if cap.persona_flag is not None:
            expect(
                f'"{cap.persona_flag}"' in resolver_src,
                f"capability {cap.id!r}: persona_flag {cap.persona_flag!r} is referenced in resolver.py",
            )

    # ── Every capability with status != "wired" must carry a tracking note ─
    for cap in reg.CAPABILITIES:
        if cap.status != "wired":
            expect(
                bool(cap.not_yet_note.strip()),
                f"capability {cap.id!r} (status={cap.status!r}) has a non-empty not_yet_note",
            )

    print()
    print(f"== {len(failures)} failure(s) ==")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
