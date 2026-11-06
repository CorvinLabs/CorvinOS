"""data_register(connection=...) over the real forge MCP stdio server.

Adversarial review 2026-11-06: the connection name was never validated, so
"../../<other tenant>/datasource_connections/<name>" read ANOTHER tenant's DSI
manifest into the LLM-facing snapshot; config values were filtered by key name
only, so a dsn/url with embedded credentials was echoed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS))
from test_mcp import MCPClient  # noqa: E402


def _manifest(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"dsi_version": "1", "name": path.stem, "adapter": "postgresql",
                                "config": config, "data_classification": "INTERNAL",
                                "data_residency": "any"}), encoding="utf-8")


def _call(client, args):
    resp = client.request("tools/call", {"name": "data_register", "arguments": args}, timeout=30)
    result = resp.get("result", {})
    text = "".join(c.get("text", "") for c in result.get("content", []))
    # The whole response: the snapshot travels in structuredContent, not in the text.
    return bool(result.get("isError") or "error" in resp), text + json.dumps(resp)


def test_a_connection_name_cannot_leave_the_tenant(tmp_path):
    home = Path(os.environ["CORVIN_HOME"])
    _manifest(home / "tenants" / "victim" / "datasource_connections" / "prod.json",
              {"host": "victim-db.internal.example", "user": "victim_admin"})
    # The traversal needs the caller's own connections dir to exist (the OS
    # resolves ".." through it) — it always does on a real install.
    _manifest(home / "tenants" / "_default" / "datasource_connections" / "mine.json", {"host": "h"})
    client = MCPClient(tmp_path / "ws")
    try:
        client.initialize()
        err, text = _call(client, {"connection": "../../victim/datasource_connections/prod"})
    finally:
        client.close()
    assert err, text
    assert "victim-db.internal.example" not in text and "victim_admin" not in text


def test_credentials_inside_a_dsn_value_are_not_echoed(tmp_path):
    home = Path(os.environ["CORVIN_HOME"])
    _manifest(home / "tenants" / "_default" / "datasource_connections" / "own.json",
              {"dsn": "postgresql://reporter:S3cretPW@db.example:5432/sales"})
    client = MCPClient(tmp_path / "ws")
    try:
        client.initialize()
        err, text = _call(client, {"connection": "own"})
    finally:
        client.close()
    assert "S3cretPW" not in text and "reporter:" not in text, text
    assert "<redacted>@db.example" in text, text
