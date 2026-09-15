#!/usr/bin/env python3
"""Unit-Tests for cowork.resolver — load, resolve-Merge, materialize_mcp,
expand_dirs, list_available.

The bundled persona JSONs were removed in e7e3560e (Skills replaced them).
The resolver still serves operator-shipped personas from
``$COWORK_USER_DIR/personas/`` (adapter.py resolves ``chat_profiles[].persona``
through it), so every case below runs against fixture personas in a temp
user dir — see ``_fixture_personas.py``.

Run: python3 operator/cowork/test/test_resolver.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _fixture_personas as fx  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


def main() -> int:
    sandbox, user_dir = fx.sandbox()
    resolver = fx.reload_resolver()

    # ── 0. Bundle is gone: nothing resolves without an operator persona ────
    expect(not resolver.BUNDLE_DIR.is_dir(),
           "bundle persona dir no longer exists (personas eliminated, e7e3560e)",
           str(resolver.BUNDLE_DIR))
    expect(resolver.load("coder") is None,
           "load(coder) → None with an empty user dir (no bundle fallback)")
    expect(resolver.list_available() == [],
           "list_available() → [] with an empty user dir")

    # ── 1. load: user-dir persona found ────────────────────────────────────
    fx.write_personas(user_dir, {"coder": fx.CODER, "research": fx.RESEARCH,
                                 "assistant": fx.ASSISTANT})
    p = resolver.load("coder")
    expect(p is not None and p.get("name") == "coder",
           "load(coder) returns the user-dir persona", f"got {p}")
    expect(p is not None and p.get("permission_mode") == "bypassPermissions",
           "persona scalar fields load verbatim")
    expect(p is not None and p.get("_source") == str(user_dir / "personas" / "coder.json"),
           "_source points at the user-dir file")

    # ── 2. load: unknown persona → None; path traversal refused ──────────
    expect(resolver.load("does-not-exist") is None, "load(unknown) returns None")
    expect(resolver.load("../coder") is None, "load('../x') refused (no traversal)")
    expect(resolver.load(".hidden") is None, "load('.hidden') refused")

    # ── 3. load: corrupt JSON → None, not an exception ───────────────────
    (user_dir / "personas" / "broken.json").write_text("{not json", encoding="utf-8")
    expect(resolver.load("broken") is None, "corrupt persona JSON → None")

    # ── 4. resolve: chat_profile overrides merge cleanly ─────────────────
    merged = resolver.resolve("research", overrides={
        "permission_mode": "acceptEdits",      # override scalar
        "allowed_tools": ["mcp__custom__foo"],  # union with persona list
        "append_system": "Plus diese Chat-Regel.",  # concatenated
    })
    expect(merged.get("permission_mode") == "acceptEdits",
           "scalar override wins", f"got {merged.get('permission_mode')}")
    expect("mcp__custom__foo" in (merged.get("allowed_tools") or []),
           "allowed_tools merge: override entry survives",
           f"got {merged.get('allowed_tools')}")
    aps = merged.get("append_system") or ""
    expect("Plus diese Chat-Regel." in aps and "web research agent" in aps.lower(),
           "append_system concatenated (persona + override)", aps[:120])
    expect("playwright" in (merged.get("mcp_servers") or {}),
           "persona mcp_servers survive the merge")
    expect(merged.get("_persona") == "research",
           "resolved profile carries _persona", f"got {merged.get('_persona')}")

    # ── 5. resolve: unknown persona → overrides pass through unchanged ──
    out = resolver.resolve("does-not-exist", overrides={"permission_mode": "plan"})
    expect(out.get("permission_mode") == "plan",
           "unknown persona → overrides pass through (graceful)")

    # ── 5b. tts_voice + tts_voice_<lang> propagate ───────────────────────
    j = resolver.resolve("assistant", overrides={})
    expect(j.get("tts_voice") == "alloy",
           "tts_voice propagates from persona", f"got {j.get('tts_voice')}")
    j2 = resolver.resolve("assistant", overrides={"tts_voice_de": "fable"})
    expect(j2.get("tts_voice_de") == "fable",
           "tts_voice_de override beats persona default", f"got {j2.get('tts_voice_de')}")
    expect(j2.get("tts_voice") == "alloy",
           "tts_voice (lang-agnostic) kept alongside tts_voice_de")

    # ── 5c. Phase-4: default_engine + awp_enabled propagate ─────────────
    j3 = resolver.resolve("assistant", overrides={"default_engine": "codex_cli",
                                                  "awp_enabled": True})
    expect(j3.get("default_engine") == "codex_cli",
           "chat_profile.default_engine propagates", f"got {j3.get('default_engine')}")
    expect(j3.get("awp_enabled") is True, "chat_profile.awp_enabled propagates")

    # ── 6. materialize_mcp: writes file, idempotent, expands template vars
    mcp_path1 = resolver.materialize_mcp({"mcp_servers": {
        "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}
    }})
    expect(mcp_path1 is not None and Path(mcp_path1).is_file(),
           "materialize_mcp writes file", f"path={mcp_path1}")
    if mcp_path1:
        content = json.loads(Path(mcp_path1).read_text())
        expect("mcpServers" in content and "playwright" in content["mcpServers"],
               "MCP file has the 'mcpServers' top-level key")
    mcp_path2 = resolver.materialize_mcp({"mcp_servers": {
        "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}
    }})
    expect(mcp_path1 == mcp_path2, "materialize_mcp idempotent (same content → same path)")
    expect(resolver.materialize_mcp({"mcp_servers": {}}) is None, "materialize_mcp({}) → None")
    expect(resolver.materialize_mcp({}) is None, "materialize_mcp(no key) → None")

    fx.write_personas(user_dir, {"forge": fx.FORGE})
    fp = resolver.materialize_mcp(resolver.load("forge"))
    cfg = json.loads(Path(fp).read_text())
    forge = cfg["mcpServers"]["forge"]
    expect(forge["command"] == sys.executable,
           "{{PYTHON}} expands to sys.executable", f"got {forge['command']}")
    expect(forge["args"][0].startswith("/") and forge["args"][0].endswith("operator/forge/forge.py"),
           "{{REPO_ROOT}} expands to an absolute repo path", f"got {forge['args'][0]}")
    expect("{{" not in json.dumps(cfg), "no unexpanded template var left in the MCP file")

    # ── 7. expand_dirs: ~ expanded + mkdir ───────────────────────────────
    test_dir = sandbox / "expand-target"
    expanded = resolver.expand_dirs({"add_dirs": [str(test_dir)]})
    expect(len(expanded) == 1 and expanded[0] == str(test_dir), "expand_dirs returns the path")
    expect(test_dir.is_dir(), "expand_dirs mkdir'd the directory")
    expect(resolver.expand_dirs({"add_dirs": []}) == [], "expand_dirs([]) → []")

    # ── 8. list_available: user-dir personas, corrupt skipped, disabled hidden
    avail = {p["name"]: p for p in resolver.list_available()}
    for need in ("coder", "research", "assistant", "forge"):
        expect(need in avail, f"list_available knows '{need}'")
    expect("broken" not in avail, "corrupt persona JSON is skipped by list_available")
    (user_dir / "personas" / ".disabled.json").write_text(
        json.dumps({"disabled": ["research"]}), encoding="utf-8")
    avail2 = {p["name"] for p in resolver.list_available()}
    expect("research" not in avail2 and "coder" in avail2,
           "console .disabled.json registry hides a persona from list_available")
    expect(resolver.load("research") is not None,
           "a disabled persona still resolves when pinned explicitly (load)")

    shutil.rmtree(sandbox, ignore_errors=True)

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
