#!/usr/bin/env python3
"""Per-subtask E2E for the Layer-29 delegate-capability injection.

Verifies that ``_inject_delegate_capability`` is reached from
``resolver.resolve(...)`` and:

  * adds the three ``mcp__corvin_delegate__delegate_*`` tools to
    ``allowed_tools``
  * wires the ``corvin_delegate`` MCP server into ``mcp_servers``
  * appends the routing brief into ``append_system`` (idempotent)
  * is a no-op when ``delegate_enabled`` is missing / false
  * the ``orchestrator`` bundle persona opts in (regression gate
    against a future persona edit that drops the flag)

Run: python3 operator/cowork/test/test_resolver_delegate.py
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
    sandbox = Path(tempfile.mkdtemp(prefix="cowork-delegate-test-"))
    user_dir = sandbox / "user"
    mcp_dir = sandbox / "mcp"
    (user_dir / "personas").mkdir(parents=True)
    os.environ["COWORK_USER_DIR"] = str(user_dir)
    os.environ["COWORK_MCP_CACHE"] = str(mcp_dir)

    # Reset the resolver module so it picks up the env overrides.
    for mod in [m for m in list(sys.modules) if m == "resolver"]:
        del sys.modules[mod]
    import resolver  # type: ignore

    # ── 1. orchestrator bundle persona: delegate_enabled must be true ─────
    orch = resolver.load("orchestrator")
    expect(orch is not None and orch.get("delegate_enabled") is True,
           "orchestrator persona carries delegate_enabled=True",
           f"got {orch}")

    # ── 2. resolve(orchestrator) injects the three delegate tools ─────────
    out = resolver.resolve("orchestrator", overrides={})
    allowed = out.get("allowed_tools") or []
    for t in (
        "mcp__corvin_delegate__delegate_claude_code",
        "mcp__corvin_delegate__delegate_codex",
        "mcp__corvin_delegate__delegate_opencode",
    ):
        expect(t in allowed,
               f"resolve(orchestrator) → {t} in allowed_tools",
               f"allowed_tools={allowed}")

    mcp = out.get("mcp_servers") or {}
    expect("corvin_delegate" in mcp,
           "resolve(orchestrator) wires corvin_delegate MCP server",
           f"mcp_servers={list(mcp.keys())}")

    if "corvin_delegate" in mcp:
        cfg = mcp["corvin_delegate"]
        expect(cfg.get("command") == sys.executable,
               "corvin_delegate command is the running interpreter (sys.executable)",
               f"command={cfg.get('command')}")
        args = cfg.get("args") or []
        expect("corvin_delegate.mcp_server" in args,
               "corvin_delegate args run -m corvin_delegate.mcp_server",
               f"args={args}")
        env = cfg.get("env") or {}
        py = env.get("PYTHONPATH", "")
        expect("core/delegate" in py,
               "PYTHONPATH carries core/delegate")
        expect("operator/forge" in py,
               "PYTHONPATH carries operator/forge")
        expect(env.get("CORVIN_CALLER_PERSONA") == "orchestrator",
               "CORVIN_CALLER_PERSONA tag set to persona name")

    brief = out.get("append_system", "")
    expect("Delegation (Layer 29)" in brief,
           "resolve(orchestrator) appends Layer-29 delegation brief")

    # ── 3. Idempotency: re-resolve doesn't double the brief ──────────────
    out2 = resolver.resolve("orchestrator", overrides={})
    brief2 = out2.get("append_system", "")
    expect(brief2.count("Delegation (Layer 29)") == 1,
           "delegation brief appears exactly once on re-resolve",
           f"count={brief2.count('Delegation (Layer 29)')}")

    # ── 4. Persona WITHOUT delegate_enabled: no injection ────────────────
    no_delegate = resolver.resolve("coder", overrides={})
    allowed_coder = no_delegate.get("allowed_tools") or []
    expect("mcp__corvin_delegate__delegate_codex" not in allowed_coder,
           "coder (no delegate_enabled) does NOT receive delegate tools")
    mcp_coder = no_delegate.get("mcp_servers") or {}
    expect("corvin_delegate" not in mcp_coder,
           "coder (no delegate_enabled) does NOT get corvin_delegate MCP")

    # ── 5. Explicit opt-out via user-persona override ────────────────────
    (user_dir / "personas" / "orchestrator.json").write_text(json.dumps({
        "name": "orchestrator",
        "delegate_enabled": False,
        "permission_mode": "bypassPermissions",
    }))
    for mod in [m for m in list(sys.modules) if m == "resolver"]:
        del sys.modules[mod]
    import resolver as resolver2  # type: ignore
    out_off = resolver2.resolve("orchestrator", overrides={})
    allowed_off = out_off.get("allowed_tools") or []
    expect("mcp__corvin_delegate__delegate_codex" not in allowed_off,
           "user-override delegate_enabled=False suppresses injection")

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
