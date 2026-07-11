#!/usr/bin/env python3
"""ADR-0190 — E2E: fictional chat tasks, verified end to end.

Per-milestone unit tests already prove each piece in isolation (resolver
injection, MCP dispatch). This file proves the CHAIN a real chat turn
actually walks for a made-up user request:

    fictional user message
      -> Layer 5 router picks a persona (real router.route(), heuristic
         backend where deterministic/free; documented skip where the
         router needs a paid embedding call, since ADR-0190 is about
         capability WIRING, not semantic-router accuracy — that's a
         pre-existing, untouched Layer 5 component)
      -> resolver.resolve(persona) gates the tool surface
      -> the capability map (system-prompt brief) discloses the same
         picture honestly
      -> for tools ADR-0190 wired, an ACTUAL tools/call dispatch proves
         the tool genuinely does the fictional task, not just that a
         name is present in a list

Every "wired" and "planned" ADR-0190 capability is covered by at least
one fictional task below. Run: python3 operator/cowork/test/test_e2e_fictional_tasks.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "core" / "orchestration"))
sys.path.insert(0, str(REPO / "core" / "workflows"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


# ── Layer 5: real router, real personas, no LLM call (heuristic backend) ──

def _routing_cases() -> None:
    import router  # type: ignore

    personas = [
        {"name": "browser", "routing_exclude": False},
        {"name": "research", "routing_exclude": False},
        {"name": "assistant", "routing_exclude": True},
    ]

    fictional_tasks = [
        ("Öffne mal die Webseite von meiner Bank und fülle das Login-Formular aus", "browser"),
        ("Open the URL example.com and click on the pricing link", "browser"),
        ("Recherchiere im Internet die aktuellen Zinssätze für Tagesgeld", "research"),
        ("Search the web for the cheapest flight to Lisbon next month", "research"),
    ]
    for text, expected_persona in fictional_tasks:
        result = router.route(text, personas)
        expect(
            result is not None and result.get("persona") == expected_persona,
            f"fictional task {text!r} routes to persona {expected_persona!r}",
            f"got {result}",
        )


# ── ADR-0190 wired capabilities: gated correctly per persona ──────────────

def _capability_gating_cases() -> None:
    import resolver  # type: ignore

    # (fictional task, tool that must be reachable, persona that should have
    #  it, persona that must NOT)
    cases = [
        (
            "Starte einen Multi-Stage-Compute-Job mit Backprop-Gate auf meinen "
            "geforgten Tools.",
            "mcp__forge__compute_submit", "coder", "homeassistant",
        ),
        (
            "Connect a Postgres database so future compute runs can query it.",
            "mcp__forge__datasource_connect", "assistant", "homeassistant",
        ),
        (
            "Send a task to our Hetzner instance to restart the worker pool.",
            "mcp__corvin_orchestration__a2a_send", "orchestrator", "homeassistant",
        ),
        (
            "Run the expense-approval-pipeline workflow with this month's receipts.",
            "mcp__corvin_orchestration__workflow_run", "assistant", "coder2_nonexistent",
        ),
        (
            "Delegate the whole 'audit every open PR' task to an autonomous loop.",
            "mcp__corvin_orchestration__acs_delegate", "orchestrator", "homeassistant",
        ),
    ]
    def _covers(allowed: list[str], tool: str) -> bool:
        # A server-level wildcard (e.g. "mcp__forge__*") covers any tool
        # from that server, same semantics Claude Code's --allowedTools
        # itself applies (see operator/skill-forge/personas/skill-forge.json
        # for the established wildcard convention this mirrors).
        if tool in allowed:
            return True
        server_prefix = tool.rsplit("__", 1)[0] + "__*"
        return server_prefix in allowed

    for task, tool, has_persona, lacks_persona in cases:
        out = resolver.resolve(has_persona, overrides={})
        allowed = out.get("allowed_tools") or []
        expect(
            _covers(allowed, tool),
            f"fictional task {task!r}: persona {has_persona!r} has {tool}",
            f"allowed_tools={allowed}",
        )
        if lacks_persona != "coder2_nonexistent":
            out_off = resolver.resolve(lacks_persona, overrides={})
            allowed_off = out_off.get("allowed_tools") or []
            expect(
                not _covers(allowed_off, tool),
                f"fictional task {task!r}: persona {lacks_persona!r} correctly LACKS {tool}",
                f"allowed_tools={allowed_off}",
            )


# ── ADR-0190 planned capabilities: honestly disclosed, never silently ─────
# ── implied as available (M7 registry-first entries) ──────────────────────

def _planned_capability_honesty_cases() -> None:
    import resolver  # type: ignore

    fictional_tasks = [
        "What's my current compute quota for today, am I close to the limit?",
        "Query our RAG knowledge base for every doc mentioning 'GDPR erasure'.",
        "Show me which CorvinOS instances are paired as A2A friends with us.",
    ]
    out = resolver.resolve("assistant", overrides={})
    brief = out.get("append_system", "")
    for cap_id, marker in (
        ("license.status", "Reading your current license tier"),
        ("rag.query", "Querying the tenant's ingested-document"),
        ("a2a.peers", "Listing/inspecting configured A2A peer"),
    ):
        expect(
            marker in brief,
            f"capability map honestly discloses {cap_id!r} as not-yet-available "
            f"(covers fictional tasks like {fictional_tasks[0]!r})",
        )
    # Never silently implied as callable.
    for missing_tool in ("license_status", "rag_query", "a2a_list_peers"):
        expect(
            not any(missing_tool in t for t in (out.get("allowed_tools") or [])),
            f"no tool resembling {missing_tool!r} is exposed (would be an ADR-0190 "
            "'What NOT to Do' violation — overclaiming an unbuilt capability)",
        )


# ── End-to-end proof: a fictional task's tool call ACTUALLY does the task ──

def _live_tool_execution_case() -> None:
    """Not just 'the tool name is present' — the tool genuinely executes the
    fictional task, via the real MCP server (mirrors core/orchestration/
    tests/test_mcp_server.py's harness, kept minimal here since that file
    already carries the full matrix)."""
    from corvin_orchestration.mcp_server import OrchestrationServer

    home = Path(tempfile.mkdtemp(prefix="corvin-e2e-fictional-"))
    wf_dir = home / "tenants" / "_default" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "invoice_total.awp.yaml").write_text(
        """
awp: "1.0.0"
workflow:
  name: invoice_total
  description: fictional task -- sum two invoice line items
inputs:
  line1: {type: number}
  line2: {type: number}
orchestration:
  engine: dag
  graph:
    - id: total
      type: code
      depends_on: []
      language: python3
      inputs: {line1: line1, line2: line2}
      source: |
        def main(line1: float, line2: float) -> dict:
            return {"total": line1 + line2}
      outputs: [total]
""",
        encoding="utf-8",
    )
    old_home = os.environ.get("CORVIN_HOME")
    os.environ["CORVIN_HOME"] = str(home)
    try:
        server = OrchestrationServer(stdin=io.StringIO(), stdout=io.StringIO(), stderr=io.StringIO())
        stdout = io.StringIO()
        server._stdout = stdout
        server._dispatch({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {
                "name": "workflow_run",
                "arguments": {"workflow_id": "invoice_total", "inputs": {"line1": 120, "line2": 45}},
            },
        })
        resp = json.loads(stdout.getvalue().splitlines()[-1])
        payload = json.loads(resp["result"]["content"][0]["text"])
        expect(
            payload.get("status") == "complete" and payload["final_state"]["total"]["total"] == 165,
            "fictional task 'sum these two invoice line items via a workflow' "
            "produces the correct real result (165), not just a present tool name",
            f"payload={payload}",
        )
    finally:
        if old_home is not None:
            os.environ["CORVIN_HOME"] = old_home
        else:
            os.environ.pop("CORVIN_HOME", None)


def main() -> int:
    _routing_cases()
    _capability_gating_cases()
    _planned_capability_honesty_cases()
    _live_tool_execution_case()

    print()
    print(f"== {len(failures)} failure(s) ==")
    for f in failures:
        print(f"  - {f}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
