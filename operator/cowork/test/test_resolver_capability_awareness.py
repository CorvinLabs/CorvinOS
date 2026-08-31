#!/usr/bin/env python3
"""Per-subtask E2E for ADR-0190's capability-awareness injection.

Verifies that ``_inject_capability_awareness`` is reached from
``resolver.resolve(...)`` and:

  * appends the generated capability-map brief into ``append_system``
    (idempotent) when the persona has ``capability_aware: true``
  * is a no-op when ``capability_aware`` is missing / false
  * only lists capabilities the resolved persona's own flags actually
    satisfy (forge_enabled / skill_forge_enabled / delegate_enabled) —
    never over-claims for a persona that doesn't have a given flag
  * the ``assistant``/``coder``/``orchestrator`` bundle personas opt in
    (regression gate against a future persona edit that drops the flag)
  * "planned" capabilities are always disclosed, even for a persona with
    every wired flag off

Run: python3 operator/cowork/test/test_resolver_capability_awareness.py
"""
from __future__ import annotations

import os
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
    sandbox = Path(tempfile.mkdtemp(prefix="cowork-capaware-test-"))
    user_dir = sandbox / "user"
    mcp_dir = sandbox / "mcp"
    (user_dir / "personas").mkdir(parents=True)
    os.environ["COWORK_USER_DIR"] = str(user_dir)
    os.environ["COWORK_MCP_CACHE"] = str(mcp_dir)

    for mod in [m for m in list(sys.modules) if m in ("resolver", "capability_map", "capability_registry")]:
        del sys.modules[mod]
    import resolver  # type: ignore

    # ── 1. Bundle personas carry capability_aware=True ────────────────────
    for name in ("assistant", "coder", "orchestrator"):
        p = resolver.load(name)
        expect(p is not None and p.get("capability_aware") is True,
               f"{name} persona carries capability_aware=True",
               f"got {p.get('capability_aware') if p else None}")

    # ── 2. resolve(assistant) injects the capability map ──────────────────
    out = resolver.resolve("assistant", overrides={})
    brief = out.get("append_system", "")
    expect("What CorvinOS can do" in brief,
           "resolve(assistant) appends the capability-map brief")
    expect("Forge (Tool Generation)" in brief,
           "capability map lists Forge (assistant has forge_enabled=True)")
    expect("not yet available via chat" in brief.lower() or "Not yet available via chat" in brief,
           "capability map discloses planned/not-yet-available capabilities")

    # ── 3. Idempotency: re-resolve doesn't double the brief ──────────────
    out2 = resolver.resolve("assistant", overrides={})
    brief2 = out2.get("append_system", "")
    marker = "What CorvinOS can do"
    expect(brief2.count(marker) == 1,
           "capability-map brief appears exactly once on re-resolve",
           f"count={brief2.count(marker)}")

    # ── 4. Persona WITHOUT capability_aware: no injection ─────────────────
    (user_dir / "personas" / "plain.json").write_text(
        '{"name": "plain", "permission_mode": "bypassPermissions"}'
    )
    for mod in [m for m in list(sys.modules) if m in ("resolver",)]:
        del sys.modules[mod]
    import resolver as resolver2  # type: ignore
    out_plain = resolver2.resolve("plain", overrides={})
    expect("What CorvinOS can do" not in out_plain.get("append_system", ""),
           "persona without capability_aware does NOT receive the capability map")

    # ── 5. Flag-scoping: a capability_aware persona WITHOUT forge_enabled
    #    must not list Forge as available. ────────────────────────────────
    (user_dir / "personas" / "narrow.json").write_text(
        '{"name": "narrow", "permission_mode": "bypassPermissions", '
        '"capability_aware": true}'
    )
    for mod in [m for m in list(sys.modules) if m in ("resolver",)]:
        del sys.modules[mod]
    import resolver as resolver3  # type: ignore
    out_narrow = resolver3.resolve("narrow", overrides={})
    brief_narrow = out_narrow.get("append_system", "")
    expect("What CorvinOS can do" in brief_narrow,
           "narrow (capability_aware, no other flags) still gets the map")
    expect("Forge (Tool Generation)" not in brief_narrow,
           "narrow (no forge_enabled) does NOT list Forge as available",
           f"brief={brief_narrow!r}")
    expect("Not yet available via chat" in brief_narrow,
           "narrow persona still sees the planned/disclosure section")

    shutil_rmtree_ok = True
    try:
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)
    except Exception:  # noqa: BLE001
        shutil_rmtree_ok = False
    expect(shutil_rmtree_ok, "sandbox cleanup did not raise")

    os.environ.pop("COWORK_USER_DIR", None)
    os.environ.pop("COWORK_MCP_CACHE", None)

    print()
    print(f"== {len(failures)} failure(s) ==")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
