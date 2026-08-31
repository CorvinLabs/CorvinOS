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
    "data.": ("operator/forge", "core/compute"),
    # ADR-0190 M6 — compute.delegation_loop's tool (acs_delegate) is
    # registered in core/orchestration, NOT operator/forge or core/compute.
    # Matched correctly regardless of dict order below: _subsystem_source
    # picks the LONGEST matching prefix, not the first-in-iteration-order
    # one, so a more-specific entry can never be silently shadowed by a
    # shorter, more general one added later (or reordered by a future edit).
    "compute.delegation_loop": ("core/orchestration",),
    "compute.": ("operator/forge", "core/compute"),
    "skill_forge.": ("operator/skill-forge",),
    "delegate.": ("core/delegate",),
    "workflows.": ("core/orchestration", "core/workflows"),
    "a2a.": ("core/orchestration",),
}


# Directory-name segments never worth scanning for a hand-written CorvinOS
# tool name — vendored dependencies (core/compute/.venv alone is ~270 MB /
# 3000+ .py files) that can never contain a match but cost a full read+
# decode every time a capability under that root is checked.
_SKIP_DIR_PARTS = frozenset({"__pycache__", ".venv", "venv", "node_modules", ".git"})

# Cache concatenated source per root-tuple so capabilities that share roots
# (e.g. every "compute.*" entry) pay the rglob+read cost once, not once per
# capability that happens to map to the same subsystem tree.
_source_cache: dict[tuple[str, ...], str] = {}


def _subsystem_source(capability_id: str) -> str | None:
    """Concatenate every .py file under the subsystem root(s) for this
    capability id. Returns None if no root mapping exists (e.g. externally
    -wired capabilities like Playwright/ImageGen, which have no CorvinOS
    source tree to check tool names against)."""
    matches = [prefix for prefix in _SUBSYSTEM_ROOTS if capability_id.startswith(prefix)]
    if not matches:
        return None
    # Longest-prefix-wins — correctness must not depend on dict insertion
    # order (a "compute.foo" root_bar mapping added after the general
    # "compute." entry must still win over it).
    roots = _SUBSYSTEM_ROOTS[max(matches, key=len)]
    if roots in _source_cache:
        return _source_cache[roots]
    chunks: list[str] = []
    for rel in roots:
        root = REPO_ROOT / rel
        if not root.is_dir():
            continue
        for f in root.rglob("*.py"):
            if _SKIP_DIR_PARTS.intersection(f.parts):
                continue
            try:
                chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    source = "\n".join(chunks)
    _source_cache[roots] = source
    return source


# ── Reverse direction (ADR-0190 CI promise): every tool any MCP server in
# the repo actually advertises must appear in SOME registry entry —
# the orphaned-core/pipe class of bug (a fully built server nobody can
# discover because no registry entry says it exists).
#
# repo-relative mcp-server file → the mcp__<server>__ prefix its tools are
# published under. A NEW mcp_server.py that isn't in this map fails the
# check with an explicit "add it here + add a registry entry" message.
_MCP_SERVER_FILES: dict[str, str] = {
    "operator/forge/forge/mcp_server.py": "forge",
    "operator/skill-forge/skill_forge/mcp_server.py": "skill_forge",
    "core/delegate/corvin_delegate/mcp_server.py": "corvin_delegate",
    "core/orchestration/corvin_orchestration/mcp_server.py": "corvin_orchestration",
    "core/pipe/mcp_server.py": "corvin_pipe",
    "operator/mcp_manager/servers/imagegen-zero-config/main.py": "imagegen-zero-config",
}

# Infra-level tools deliberately NOT user-facing capabilities (never shown
# in the capability map, not meaningful chat verbs) — documented here
# instead of silently skipped.
_INTERNAL_TOOLS = frozenset({
    "audit.write_event",  # forge audit-chain writer for engines without native audit
})


def _advertised_tool_names(path: Path) -> set[str]:
    """AST-extract the tool names an MCP server file advertises: dict
    literals shaped like {"name": ..., "inputSchema"/"input_schema": ...}
    (the stdio-server tool tables) plus @*.tool()-decorated functions
    (FastMCP servers)."""
    import ast
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)]
            if "name" in keys and ("inputSchema" in keys or "input_schema" in keys):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "name"
                            and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                        names.add(v.value)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                fn = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(fn, ast.Attribute) and fn.attr == "tool":
                    names.add(node.name)
    return names


def _check_reverse_direction(reg) -> None:
    registry_names: set[str] = set()
    wildcards: set[str] = set()
    for cap in reg.CAPABILITIES:
        for tn in cap.tool_names:
            if tn.endswith("__*"):
                wildcards.add(tn[:-1])  # "mcp__forge__"
            else:
                registry_names.add(tn)

    # 1. Every known server file's advertised tools are registry-tracked.
    for rel, server_key in _MCP_SERVER_FILES.items():
        f = REPO_ROOT / rel
        if not f.is_file():
            continue  # partial checkout — the forward checks still run
        for bare in sorted(_advertised_tool_names(f)):
            if bare in _INTERNAL_TOOLS:
                continue
            full = f"mcp__{server_key}__{bare}"
            covered = (full in registry_names
                       or any(full.startswith(w) for w in wildcards))
            expect(covered,
                   f"reverse: {rel} tool {bare!r} is tracked in the registry",
                   f"expected {full!r} in some capability's tool_names")

    # 2. No unmapped mcp_server.py anywhere in the tree.
    skip_parts = _SKIP_DIR_PARTS | {"tests", "test"}
    for base in ("operator", "core"):
        for f in (REPO_ROOT / base).rglob("mcp_server.py"):
            if skip_parts.intersection(f.parts):
                continue
            rel = str(f.relative_to(REPO_ROOT))
            expect(rel in _MCP_SERVER_FILES,
                   f"reverse: MCP server file {rel} is known to this check",
                   "add it to _MCP_SERVER_FILES and give it a registry entry")


def main() -> int:
    import capability_registry as reg  # type: ignore

    resolver_src = (HERE.parent / "lib" / "resolver.py").read_text(encoding="utf-8")

    _check_reverse_direction(reg)

    ids_seen: set[str] = set()
    for cap in reg.CAPABILITIES:
        expect(cap.id not in ids_seen, f"capability id {cap.id!r} is unique")
        ids_seen.add(cap.id)

        if cap.status != "wired":
            continue

        # ── Tool names must appear in the subsystem's real source ────────
        _matches = [p for p in _SUBSYSTEM_ROOTS if cap.id.startswith(p)]
        matched_prefix = max(_matches, key=len) if _matches else None
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
