"""ADR-0701 G1 over the real transport: the forge MCP stdio server.

A ``forge_tool`` / ``forge_promote`` call is sent as JSON-RPC to a
``forge.py mcp`` subprocess (the harness from
``corvin_operator/forge/tests/test_mcp.py``). The child's tier RESOLVER is
pinned per case; the gate, the limits matrix and the audit write run unmodified.

Pinned defect (adversarial review 2026-09-27): the MCP gate tested
``decision.allowed`` — the tier LIMIT, ``None`` (unlimited) for the member tier
— as a boolean, so EVERY paying member got ``license_required``; and the free
tier got ``license_enforcement_unavailable`` instead of ``license_required``.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_FORGE_TESTS = Path(__file__).resolve().parents[2] / "corvin_operator" / "forge" / "tests"
sys.path.insert(0, str(_FORGE_TESTS))
from test_mcp import MCPClient  # noqa: E402

IMPL = ("#!/usr/bin/env python3\nimport json, sys\njson.loads(sys.stdin.read())\n"
        "print(json.dumps({'ok': True, 'status': 200, 'data': {}, 'error': None}))\n")


def _call(client, tool, args):
    resp = client.request("tools/call", {"name": tool, "arguments": args}, timeout=30)
    result = resp.get("result", {})
    text = "".join(c.get("text", "") for c in result.get("content", []))
    return bool(result.get("isError")), text


def _forge_args(name):
    return {"name": name, "description": name, "input_schema": {"type": "object"},
            "impl": IMPL}


def _decisions():
    chain = (Path(os.environ["CORVIN_HOME"]) / "tenants" / "_default" / "global"
             / "forge" / "audit.jsonl")
    if not chain.exists():
        return []
    return [r["details"] for r in map(json.loads, chain.read_text().splitlines())
            if r.get("event_type") == "license.capability_decision"]


@pytest.mark.parametrize("tier", ["free", "member"])
def test_forge_tool_and_promote_over_stdio(tmp_path, tier):
    before = len(_decisions())
    client = MCPClient(tmp_path / "ws", licence_tier=tier)
    try:
        client.initialize()
        err, text = _call(client, "forge_tool", _forge_args("g1_stdio"))
        if tier == "free":
            assert err and text.startswith("license_required:"), text
            assert "not_available_in_tier" in text
            assert not (tmp_path / "ws" / "tools").exists() or not any(
                (tmp_path / "ws" / "tools").iterdir())
        else:
            assert not err, text
            assert any((tmp_path / "ws" / "tools").glob("g1_stdio.*"))
            err, text = _call(client, "forge_promote", {"name": "g1_stdio"})
            assert not err, text
    finally:
        client.close()
    got = [(d["tier"], d["decision"], d["entry_point"]) for d in _decisions()[before:]]
    if tier == "free":
        assert got == [("free", "deny", "mcp:forge_tool")]
    else:
        assert ("member", "allow", "mcp:forge_tool") in got
        assert ("member", "allow", "forge:registry.create") in got
        assert ("member", "allow", "mcp:forge_promote") in got
