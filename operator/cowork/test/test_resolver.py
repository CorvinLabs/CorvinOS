#!/usr/bin/env python3
"""Unit-Tests for cowork.resolver — load, resolve-Merge, materialize_mcp,
expand_dirs, list_available, User-Override-Reihenfolge.

Run: python3 operator/cowork/test/test_resolver.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
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
    sandbox = Path(tempfile.mkdtemp(prefix="cowork-test-"))
    user_dir = sandbox / "user"
    mcp_dir = sandbox / "mcp"
    (user_dir / "personas").mkdir(parents=True)
    os.environ["COWORK_USER_DIR"] = str(user_dir)
    os.environ["COWORK_MCP_CACHE"] = str(mcp_dir)

    # Clear any prior cached resolver module
    for mod in [m for m in list(sys.modules) if m == "resolver"]:
        del sys.modules[mod]
    import resolver  # type: ignore

    # ── 1. load: Bundle-Persona findbar ────────────────────────────────────
    p = resolver.load("coder")
    expect(p is not None and p.get("name") == "coder",
           "load(coder) returns Bundle-Persona",
           f"got {p}")
    expect(p is not None and p.get("permission_mode") == "bypassPermissions",
           "coder hat bypassPermissions als Default")

    # ── 2. load: unknown persona → None ────────────────────────────────────
    expect(resolver.load("does-not-exist") is None,
           "load(unknown) returns None")

    # ── 3. load: User-Persona shadows Bundle ─────────────────────────
    (user_dir / "personas" / "coder.json").write_text(json.dumps({
        "name": "coder",
        "description": "user-override",
        "permission_mode": "plan",
    }))
    p2 = resolver.load("coder")
    expect(p2.get("description") == "user-override",
           "User-Persona shadows Bundle",
           f"got {p2.get('description')}")
    expect(p2.get("permission_mode") == "plan",
           "User-Persona-Felder gewinnen")

    # ── 4. resolve: chat_profile-overrides mergen sauber ───────────────────
    # research-Persona + chat-spezifischer override (browser persona removed f1e3246).
    merged = resolver.resolve("research", overrides={
        "permission_mode": "acceptEdits",      # override scalar
        "allowed_tools": ["mcp__custom__foo"],  # union mit persona-list
        "append_system": "Plus diese Chat-Regel.",  # konkat
    })
    expect(merged.get("permission_mode") == "acceptEdits",
           "scalar override gewinnt",
           f"got {merged.get('permission_mode')}")
    # After the persona-rework, all bundled personas use the unified
    # bypassPermissions + empty-allowed pattern. Differentiation is by role,
    # not by tool-list. The override is still merged as a union; capability
    # injections (forge/skill-forge tools when forge_enabled is true) appear
    # alongside it. The override entry must be present.
    expect("mcp__custom__foo" in (merged.get("allowed_tools") or []),
           "allowed_tools merge: override entry survives",
           f"got {merged.get('allowed_tools')}")
    expect("Plus diese Chat-Regel." in (merged.get("append_system") or "")
           and ("research" in (merged.get("append_system") or "").lower()
                or "web" in (merged.get("append_system") or "").lower()),
           "append_system konkateniert (persona + override)")

    # ── 5. resolve: Persona unbekannt → overrides werden durchgereicht ────
    out = resolver.resolve("does-not-exist", overrides={"permission_mode": "plan"})
    expect(out.get("permission_mode") == "plan",
           "unknown persona → overrides werden durchgereicht (graceful)")

    # ── 5b. tts_voice + tts_voice_<lang> propagate from persona ─────────
    # Regression: resolver MUST forward tts_voice scalars unchanged so that
    # synthesize_voice_note() in adapter.py picks up the persona's TTS voice.
    # Test via assistant (always present); jarvis was removed in f1e3246.
    j = resolver.resolve("assistant", overrides={})
    bundle_voice = j.get("tts_voice")  # may be None if assistant has no tts_voice set
    # chat_profile override beats persona default for tts_voice_<lang>
    j2 = resolver.resolve("assistant", overrides={"tts_voice_de": "fable"})
    expect(j2.get("tts_voice_de") == "fable",
           "tts_voice_de override schlägt persona default",
           f"got {j2.get('tts_voice_de')}")
    expect(j2.get("tts_voice") == bundle_voice,
           "tts_voice (lang-agnostic) bleibt erhalten neben tts_voice_de",
           f"got {j2.get('tts_voice')} expected {bundle_voice}")

    # ── 5c. Phase-4: default_engine + awp_enabled propagate ─────────────
    j3 = resolver.resolve("assistant", overrides={
        "default_engine": "codex_cli",
        "awp_enabled": True,
    })
    expect(j3.get("default_engine") == "codex_cli",
           "Phase 4: chat_profile.default_engine propagiert (codex_cli)",
           f"got {j3.get('default_engine')}")
    expect(j3.get("awp_enabled") is True,
           "Phase 4: chat_profile.awp_enabled propagiert (True)",
           f"got {j3.get('awp_enabled')}")

    # ── 6. materialize_mcp: writes file + idempotent ───────────────────
    mcp_path1 = resolver.materialize_mcp({"mcp_servers": {
        "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}
    }})
    expect(mcp_path1 is not None and Path(mcp_path1).is_file(),
           "materialize_mcp writes file", f"path={mcp_path1}")
    if mcp_path1:
        content = json.loads(Path(mcp_path1).read_text())
        expect("mcpServers" in content and "playwright" in content["mcpServers"],
               "MCP-File-Format hat 'mcpServers' top-level key")
    mcp_path2 = resolver.materialize_mcp({"mcp_servers": {
        "playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}
    }})
    expect(mcp_path1 == mcp_path2,
           "materialize_mcp idempotent (gleicher content → gleicher path)")
    expect(resolver.materialize_mcp({"mcp_servers": {}}) is None,
           "materialize_mcp({}) → None")
    expect(resolver.materialize_mcp({}) is None,
           "materialize_mcp(no key) → None")

    # ── 7. expand_dirs: ~ wed expandiert + mkdir ─────────────────────────
    test_dir = sandbox / "expand-target"
    expanded = resolver.expand_dirs({"add_dirs": [str(test_dir)]})
    expect(len(expanded) == 1 and expanded[0] == str(test_dir),
           "expand_dirs gibt path back")
    expect(test_dir.is_dir(),
           "expand_dirs mkdir'd das directory")
    expect(resolver.expand_dirs({"add_dirs": []}) == [],
           "expand_dirs([]) → []")

    # ── 8. list_available: Bundle + User dedup'd ──────────────────────────
    avail = {p["name"]: p for p in resolver.list_available()}
    # browser + jarvis removed from bundle in f1e3246; test with remaining personas
    for need in ("coder", "research", "inbox", "assistant"):
        expect(need in avail, f"list_available kennt '{need}'")
    # User-coder muss overschattend sein
    expect(avail.get("coder", {}).get("description") == "user-override",
           "list_available — User-Persona shadows Bundle")

    # cleanup
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
