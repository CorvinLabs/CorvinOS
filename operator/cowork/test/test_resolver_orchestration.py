#!/usr/bin/env python3
"""Per-subtask E2E for ADR-0190 M4/M5/M6 orchestration-capability injection.

Verifies that ``_inject_orchestration_capability`` is reached from
``resolver.resolve(...)`` and:

  * adds the six ``mcp__corvin_orchestration__*`` tools to ``allowed_tools``
  * wires the ``corvin_orchestration`` MCP server into ``mcp_servers``
  * appends the orchestration brief into ``append_system`` (idempotent)
  * is a no-op when ``orchestration_enabled`` is missing / false
  * assistant/coder/orchestrator bundle personas opt in, homeassistant does
    NOT (regression gate against a future persona edit widening blast
    radius silently)

Run: python3 operator/cowork/test/test_resolver_orchestration.py
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
    sandbox = Path(tempfile.mkdtemp(prefix="cowork-orchestration-test-"))
    user_dir = sandbox / "user"
    mcp_dir = sandbox / "mcp"
    (user_dir / "personas").mkdir(parents=True)
    os.environ["COWORK_USER_DIR"] = str(user_dir)
    os.environ["COWORK_MCP_CACHE"] = str(mcp_dir)

    for mod in [m for m in list(sys.modules) if m == "resolver"]:
        del sys.modules[mod]
    import resolver  # type: ignore

    # ── 1. assistant/coder/orchestrator opt in; homeassistant does NOT ────
    for name in ("assistant", "coder", "orchestrator"):
        p = resolver.load(name)
        expect(p is not None and p.get("orchestration_enabled") is True,
               f"{name} persona carries orchestration_enabled=True",
               f"got {p}")
    ha = resolver.load("homeassistant")
    expect(ha is not None and not ha.get("orchestration_enabled"),
           "homeassistant persona does NOT carry orchestration_enabled "
           "(narrower blast radius kept for constrained personas)",
           f"got {ha}")

    # ── 2. resolve(assistant) injects all six orchestration tools ─────────
    out = resolver.resolve("assistant", overrides={})
    allowed = out.get("allowed_tools") or []
    for t in (
        "mcp__corvin_orchestration__workflow_run",
        "mcp__corvin_orchestration__workflow_resume",
        "mcp__corvin_orchestration__workflow_list_paused",
        "mcp__corvin_orchestration__a2a_send",
        "mcp__corvin_orchestration__a2a_list_endpoints",
        "mcp__corvin_orchestration__acs_delegate",
    ):
        expect(t in allowed,
               f"resolve(assistant) → {t} in allowed_tools",
               f"allowed_tools={allowed}")

    mcp = out.get("mcp_servers") or {}
    expect("corvin_orchestration" in mcp,
           "resolve(assistant) wires corvin_orchestration MCP server",
           f"mcp_servers={list(mcp.keys())}")

    if "corvin_orchestration" in mcp:
        cfg = mcp["corvin_orchestration"]
        expect(cfg.get("command") == sys.executable,
               "corvin_orchestration command is the running interpreter (sys.executable)",
               f"command={cfg.get('command')}")
        args = cfg.get("args") or []
        expect("corvin_orchestration.mcp_server" in args,
               "corvin_orchestration args run -m corvin_orchestration.mcp_server",
               f"args={args}")
        env = cfg.get("env") or {}
        py = env.get("PYTHONPATH", "")
        for needle in ("core/orchestration", "core/workflows", "operator/bridges/shared", "operator/forge"):
            expect(needle in py, f"PYTHONPATH carries {needle}", f"PYTHONPATH={py}")
        expect(env.get("CORVIN_CALLER_PERSONA") == "assistant",
               "CORVIN_CALLER_PERSONA tag set to persona name")

    brief = out.get("append_system", "")
    expect("Orchestration (ADR-0190 M4/M5/M6)" in brief,
           "resolve(assistant) appends the orchestration brief")

    # ── 3. Idempotency: re-resolve doesn't double the brief ──────────────
    out2 = resolver.resolve("assistant", overrides={})
    brief2 = out2.get("append_system", "")
    expect(brief2.count("Orchestration (ADR-0190 M4/M5/M6)") == 1,
           "orchestration brief appears exactly once on re-resolve",
           f"count={brief2.count('Orchestration (ADR-0190 M4/M5/M6)')}")

    # ── 4. Persona WITHOUT orchestration_enabled: no injection ───────────
    no_orch = resolver.resolve("homeassistant", overrides={})
    allowed_ha = no_orch.get("allowed_tools") or []
    expect("mcp__corvin_orchestration__acs_delegate" not in allowed_ha,
           "homeassistant (no orchestration_enabled) does NOT receive orchestration tools")
    mcp_ha = no_orch.get("mcp_servers") or {}
    expect("corvin_orchestration" not in mcp_ha,
           "homeassistant (no orchestration_enabled) does NOT get corvin_orchestration MCP")

    # ── 5. Explicit opt-out via user-persona override ────────────────────
    (user_dir / "personas" / "assistant.json").write_text(json.dumps({
        "name": "assistant",
        "orchestration_enabled": False,
        "permission_mode": "bypassPermissions",
    }))
    for mod in [m for m in list(sys.modules) if m == "resolver"]:
        del sys.modules[mod]
    import resolver as resolver2  # type: ignore
    out_off = resolver2.resolve("assistant", overrides={})
    allowed_off = out_off.get("allowed_tools") or []
    expect("mcp__corvin_orchestration__acs_delegate" not in allowed_off,
           "user-override orchestration_enabled=False suppresses injection")

    shutil.rmtree(sandbox, ignore_errors=True)
    os.environ.pop("COWORK_USER_DIR", None)
    os.environ.pop("COWORK_MCP_CACHE", None)

    print()
    print(f"== {len(failures)} failure(s) ==")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
