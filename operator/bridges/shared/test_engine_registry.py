#!/usr/bin/env python3
"""Per-subtask E2E for the Phase-4 ``engine_registry`` module.

Asserts:
  * list_engine_ids returns at least the static catalogue entries
    (claude_code + codex_cli today)
  * list_engine_ids(available_only=True) only returns engines whose
    underlying module imported successfully
  * get_engine for a known id returns an engine instance with the
    expected ``capabilities`` dict shape
  * get_engine for an unknown id returns None gracefully
  * resolve_engine_id picks profile.default_engine first, env second,
    DEFAULT_ENGINE_ID third
  * resolve_engine_id rejects unknown ids and falls back to next tier
  * make_factory returns a callable that accepts engine_id=None for
    default and a specific id for override
  * The factory stamps _corvin_context on the engine instance so the
    audit-integrity rule (engine_id ↔ awp_task_id) can correlate
  * Factory carries diagnostics (corvin_default_engine,
    corvin_context) for /whoami introspection

Run: python3 operator/bridges/shared/test_engine_registry.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import engine_registry as er  # type: ignore  # noqa: E402

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


def main() -> int:
    # ── 1. Static catalogue ────────────────────────────────────────
    print("\n── Static catalogue ────────────────────────────────────")
    static_ids = er.list_engine_ids(available_only=False)
    expect("claude_code" in static_ids,
           "static catalogue contains claude_code",
           f"got {static_ids}")
    expect("codex_cli" in static_ids,
           "static catalogue contains codex_cli")
    expect(er.DEFAULT_ENGINE_ID == "claude_code",
           "DEFAULT_ENGINE_ID is claude_code")

    # ── 2. Available-only filter ───────────────────────────────────
    avail = er.list_engine_ids(available_only=True)
    expect(isinstance(avail, list),
           "available_only returns a list")
    expect(set(avail).issubset(set(static_ids)),
           "available_only is subset of static catalogue",
           f"avail={avail} static={static_ids}")
    # Every available id must be importable via get_engine.
    for eid in avail:
        engine = er.get_engine(eid)
        expect(engine is not None,
               f"available engine {eid} returns instance from get_engine")
        if engine is not None:
            expect(hasattr(engine, "capabilities"),
                   f"{eid} instance has capabilities attribute")

    # ── 3. Unknown id → None graceful ──────────────────────────────
    print("\n── Unknown id graceful ─────────────────────────────────")
    expect(er.get_engine("totally-fake-engine") is None,
           "get_engine(unknown) → None")
    expect(er.get_engine("") is None,
           "get_engine('') → None")

    # ── 4. resolve_engine_id precedence ───────────────────────────
    print("\n── resolve_engine_id precedence ────────────────────────")
    # Clean env first.
    for k in ("CORVIN_DEFAULT_ENGINE", "CORVIN_DEFAULT_ENGINE"):
        os.environ.pop(k, None)

    # 4a — profile.default_engine wins
    out = er.resolve_engine_id({"default_engine": "codex_cli"})
    expect(out == "codex_cli",
           "profile.default_engine (codex_cli) picked",
           f"got {out!r}")

    # 4b — env wins when profile silent
    os.environ["CORVIN_DEFAULT_ENGINE"] = "codex_cli"
    try:
        out = er.resolve_engine_id({})
        expect(out == "codex_cli",
               "env CORVIN_DEFAULT_ENGINE picked when profile silent")
    finally:
        os.environ.pop("CORVIN_DEFAULT_ENGINE", None)

    # 4c — legacy env CORVIN_DEFAULT_ENGINE picked when canonical absent
    os.environ["CORVIN_DEFAULT_ENGINE"] = "codex_cli"
    try:
        out = er.resolve_engine_id({})
        expect(out == "codex_cli",
               "legacy CORVIN_DEFAULT_ENGINE picked when canonical absent")
    finally:
        os.environ.pop("CORVIN_DEFAULT_ENGINE", None)

    # 4d — empty profile + empty env → DEFAULT
    out = er.resolve_engine_id({})
    expect(out == "claude_code",
           "empty profile + empty env → DEFAULT_ENGINE_ID")

    # 4e — None profile → DEFAULT
    out = er.resolve_engine_id(None)
    expect(out == "claude_code",
           "None profile → DEFAULT_ENGINE_ID")

    # 4f — unknown id falls back to next tier
    out = er.resolve_engine_id({"default_engine": "fake-engine-id"})
    expect(out == "claude_code",
           "unknown profile.default_engine falls back to DEFAULT",
           f"got {out!r}")

    # ── 5. make_factory: closure with persona context ─────────────
    print("\n── make_factory closures ───────────────────────────────")
    factory = er.make_factory("claude_code",
                              persona="jarvis",
                              channel="discord",
                              chat_key="1486034324108083345")
    expect(callable(factory), "make_factory returns a callable")
    expect(getattr(factory, "corvin_default_engine", None) == "claude_code",
           "factory stamps default_engine diagnostic")
    expect(isinstance(getattr(factory, "corvin_context", None), dict),
           "factory stamps corvin_context dict")

    # 5a — call with no arg → default engine
    engine = factory()
    expect(engine is not None,
           "factory() with no arg returns default engine instance")
    if engine is not None:
        ctx = getattr(engine, "_corvin_context", None)
        expect(isinstance(ctx, dict)
               and ctx.get("persona") == "jarvis"
               and ctx.get("channel") == "discord"
               and ctx.get("engine_id") == "claude_code",
               "factory stamps engine context for audit correlation",
               f"got {ctx}")

    # 5b — call with override id → that engine
    engine_codex = factory("codex_cli")
    expect(engine_codex is not None,
           "factory('codex_cli') returns codex instance")
    if engine_codex is not None:
        ctx = getattr(engine_codex, "_corvin_context", None)
        expect(isinstance(ctx, dict) and ctx.get("engine_id") == "codex_cli",
               "factory override stamps the correct engine_id")

    # 5c — call with unknown id → None graceful
    engine_unknown = factory("nonexistent-engine")
    expect(engine_unknown is None,
           "factory(unknown) → None graceful")

    # 5d — make_factory with None default → DEFAULT_ENGINE_ID
    factory2 = er.make_factory(None, persona=None, channel=None, chat_key=None)
    expect(callable(factory2),
           "make_factory(None default) still returns callable")
    expect(getattr(factory2, "corvin_default_engine", None) == "claude_code",
           "make_factory(None) uses DEFAULT_ENGINE_ID")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
