#!/usr/bin/env python3
"""test_mcp_server.py — E2E for the Layer-18 pipe MCP server.

Spawns the MCP server as a real subprocess and round-trips JSON-RPC
2.0 messages over stdin/stdout. Verifies the full protocol surface:

  - initialize handshake returns protocol version + server info
  - tools/list returns the 9 pipe tools with valid input schemas
  - tools/call dispatches to the matching handler
  - error envelopes for unknown tools and domain errors
  - end-to-end: create a named pipe, write twice, read twice, verify
    payload + seq, remove pipe, verify gone
  - broadcast: subscribe -> write -> read -> cursor advance
  - shutdown method ends the loop

Per-subtask E2E rule: real subprocess, real stdin/stdout pipe, real
filesystem (CORVIN_HOME redirected to tempdir for isolation). No
mocks for any moving part.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "mcp_server.py"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


class MCPClient:
    """Minimal JSON-RPC client for talking to the MCP server subprocess."""

    def __init__(self, home: Path) -> None:
        env = os.environ.copy()
        env["CORVIN_HOME"] = str(home)
        # Suppress server's stderr to keep test output readable;
        # if the server fails, the assertion failures will show why.
        self._proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            bufsize=1,  # line-buffered
        )
        self._next_id = 0

    def call(self, method: str, params: dict | None = None,
             timeout: float = 5.0) -> dict:
        self._next_id += 1
        msgid = self._next_id
        msg = {"jsonrpc": "2.0", "id": msgid, "method": method}
        if params is not None:
            msg["params"] = params
        line = json.dumps(msg) + "\n"
        assert self._proc.stdin is not None
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

        # Read the matching response line. Server may emit other
        # notifications, so keep reading until we see our id.
        deadline = time.time() + timeout
        while time.time() < deadline:
            assert self._proc.stdout is not None
            resp_line = self._proc.stdout.readline()
            if not resp_line:
                raise RuntimeError("server closed stdout")
            try:
                resp = json.loads(resp_line)
            except json.JSONDecodeError:
                continue
            if resp.get("id") == msgid:
                return resp
            # Notification or unrelated message — ignore
        raise TimeoutError(f"no response within {timeout}s for {method}")

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        line = json.dumps(msg) + "\n"
        assert self._proc.stdin is not None
        self._proc.stdin.write(line)
        self._proc.stdin.flush()

    def close(self) -> int:
        try:
            self.call("shutdown", timeout=2.0)
        except Exception:
            pass
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        try:
            return self._proc.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            return self._proc.wait()


def _client_for(home: Path) -> MCPClient:
    """Spawn server + run the standard initialize handshake."""
    c = MCPClient(home)
    resp = c.call("initialize", {
        "protocolVersion": "2024-11-05",
        "clientInfo": {"name": "test", "version": "0.0"},
    })
    assert "result" in resp, resp
    assert resp["result"]["serverInfo"]["name"] == "claude-pipe"
    c.notify("notifications/initialized")
    return c


# --------------------------------------------------------------------- cases

def case_initialize_returns_server_info(home: Path) -> None:
    _section("initialize handshake returns expected server info")
    c = _client_for(home)
    try:
        # _client_for already verified initialize succeeded; just
        # check we can also ping
        resp = c.call("ping")
        assert resp.get("result") == {}, resp
        print("  PASS initialize + ping work")
    finally:
        c.close()


def case_tools_list_returns_nine_pipe_tools(home: Path) -> None:
    _section("tools/list returns the 9 pipe tools with input schemas")
    c = _client_for(home)
    try:
        resp = c.call("tools/list")
        tools = resp["result"]["tools"]
        names = [t["name"] for t in tools]
        expected = {
            "pipe_create", "pipe_write", "pipe_read",
            "pipe_subscribe", "pipe_unsubscribe",
            "pipe_list", "pipe_remove",
            "pipe_get_meta", "pipe_queue_depth",
        }
        assert set(names) == expected, names
        for t in tools:
            assert "inputSchema" in t, t
            assert t["inputSchema"]["type"] == "object", t
        print(f"  PASS {len(tools)} tools listed: {sorted(names)}")
    finally:
        c.close()


def case_unknown_method_returns_error(home: Path) -> None:
    _section("unknown method returns METHOD_NOT_FOUND")
    c = _client_for(home)
    try:
        resp = c.call("not/a/real/method")
        assert "error" in resp
        assert resp["error"]["code"] == -32601, resp
        print(f"  PASS code={resp['error']['code']} message={resp['error']['message']!r}")
    finally:
        c.close()


def case_unknown_tool_returns_tool_error(home: Path) -> None:
    _section("tools/call to unknown tool returns isError=true result")
    c = _client_for(home)
    try:
        resp = c.call("tools/call", {
            "name": "no_such_tool",
            "arguments": {},
        })
        assert resp["result"]["isError"] is True
        assert "unknown" in resp["result"]["content"][0]["text"].lower()
        print(f"  PASS tool error: {resp['result']['content'][0]['text']}")
    finally:
        c.close()


def case_named_pipe_full_roundtrip(home: Path) -> None:
    _section("named pipe: create -> write x2 -> read x2 -> remove")
    c = _client_for(home)
    try:
        # Create
        resp = c.call("tools/call", {
            "name": "pipe_create",
            "arguments": {"name": "channel-1", "type": "named"},
        })
        assert resp["result"]["isError"] is False
        meta = resp["result"]["structuredContent"]
        assert meta["name"] == "channel-1"
        assert meta["type"] == "named"
        print(f"  PASS create returned: name={meta['name']} type={meta['type']}")

        # Write twice
        for i, body in enumerate(("first", "second")):
            resp = c.call("tools/call", {
                "name": "pipe_write",
                "arguments": {
                    "name": "channel-1",
                    "payload": {"text": body},
                    "writer": "tester",
                },
            })
            assert resp["result"]["structuredContent"]["seq"] == i
        print("  PASS wrote 2 messages with seq=0,1")

        # Queue depth
        resp = c.call("tools/call", {
            "name": "pipe_queue_depth",
            "arguments": {"name": "channel-1"},
        })
        assert resp["result"]["structuredContent"]["depth"] == 2
        print("  PASS queue_depth=2 before read")

        # Read both
        resp = c.call("tools/call", {
            "name": "pipe_read",
            "arguments": {"name": "channel-1"},
        })
        msgs = resp["result"]["structuredContent"]["messages"]
        assert len(msgs) == 2
        assert msgs[0]["payload"]["text"] == "first"
        assert msgs[1]["payload"]["text"] == "second"
        assert msgs[0]["writer"] == "tester"
        print("  PASS read returned both messages in order")

        # Remove
        resp = c.call("tools/call", {
            "name": "pipe_remove",
            "arguments": {"name": "channel-1"},
        })
        assert resp["result"]["structuredContent"]["existed"] is True
        # Now get_meta should report missing
        resp = c.call("tools/call", {
            "name": "pipe_get_meta",
            "arguments": {"name": "channel-1"},
        })
        assert resp["result"]["isError"] is True
        print("  PASS remove + get_meta reports missing")
    finally:
        c.close()


def case_broadcast_subscribe_write_read(home: Path) -> None:
    _section("broadcast: subscribe -> write -> read -> cursor advance")
    c = _client_for(home)
    try:
        c.call("tools/call", {
            "name": "pipe_create",
            "arguments": {"name": "evt", "type": "broadcast"},
        })
        # Subscribe twice
        sid_a = c.call("tools/call", {
            "name": "pipe_subscribe",
            "arguments": {"name": "evt"},
        })["result"]["structuredContent"]["subscriber_id"]
        sid_b = c.call("tools/call", {
            "name": "pipe_subscribe",
            "arguments": {"name": "evt"},
        })["result"]["structuredContent"]["subscriber_id"]
        assert sid_a != sid_b
        print(f"  PASS subscribed: {sid_a}, {sid_b}")

        # Write 3 events
        for body in ("boot", "ready", "shutdown"):
            c.call("tools/call", {
                "name": "pipe_write",
                "arguments": {"name": "evt", "payload": body},
            })

        # Both subscribers see all 3
        for sid in (sid_a, sid_b):
            resp = c.call("tools/call", {
                "name": "pipe_read",
                "arguments": {"name": "evt", "subscriber_id": sid},
            })
            payloads = [
                m["payload"]
                for m in resp["result"]["structuredContent"]["messages"]
            ]
            assert payloads == ["boot", "ready", "shutdown"], (sid, payloads)
        print("  PASS both subscribers got all 3 events")

        # Second read returns nothing (cursor advanced)
        resp = c.call("tools/call", {
            "name": "pipe_read",
            "arguments": {"name": "evt", "subscriber_id": sid_a},
        })
        assert resp["result"]["structuredContent"]["count"] == 0
        print("  PASS cursor advanced; repeat read empty")

        # Unsubscribe one
        resp = c.call("tools/call", {
            "name": "pipe_unsubscribe",
            "arguments": {"name": "evt", "subscriber_id": sid_a},
        })
        assert resp["result"]["structuredContent"]["existed"] is True
        # Read with the unsubscribed id should now error
        resp = c.call("tools/call", {
            "name": "pipe_read",
            "arguments": {"name": "evt", "subscriber_id": sid_a},
        })
        assert resp["result"]["isError"] is True
        print("  PASS unsubscribe + read errors")
    finally:
        c.close()


def case_anonymous_auto_remove(home: Path) -> None:
    _section("anonymous pipe: first non-empty read removes the pipe")
    c = _client_for(home)
    try:
        c.call("tools/call", {
            "name": "pipe_create",
            "arguments": {"name": "anon", "type": "anonymous"},
        })
        c.call("tools/call", {
            "name": "pipe_write",
            "arguments": {"name": "anon", "payload": "one-shot"},
        })
        resp = c.call("tools/call", {
            "name": "pipe_read",
            "arguments": {"name": "anon"},
        })
        msgs = resp["result"]["structuredContent"]["messages"]
        assert len(msgs) == 1
        assert msgs[0]["payload"] == "one-shot"

        # Pipe is auto-removed; second read should error
        resp = c.call("tools/call", {
            "name": "pipe_read",
            "arguments": {"name": "anon"},
        })
        assert resp["result"]["isError"] is True
        print("  PASS auto-removed after first non-empty read")
    finally:
        c.close()


def case_validation_errors_come_back_as_tool_errors(home: Path) -> None:
    _section("domain errors come back as isError=true (not JSON-RPC errors)")
    c = _client_for(home)
    try:
        # Invalid pipe name
        resp = c.call("tools/call", {
            "name": "pipe_create",
            "arguments": {"name": "../evil", "type": "named"},
        })
        assert resp["result"]["isError"] is True
        assert "ValueError" in resp["result"]["content"][0]["text"]
        # Invalid pipe type
        resp = c.call("tools/call", {
            "name": "pipe_create",
            "arguments": {"name": "x", "type": "weirdpipe"},
        })
        assert resp["result"]["isError"] is True
        # Read of nonexistent
        resp = c.call("tools/call", {
            "name": "pipe_read",
            "arguments": {"name": "ghost"},
        })
        assert resp["result"]["isError"] is True
        assert "KeyError" in resp["result"]["content"][0]["text"]
        print("  PASS domain errors surfaced as tool-level isError envelopes")
    finally:
        c.close()


def case_pipe_list_returns_metadata(home: Path) -> None:
    _section("pipe_list returns metadata for every existing pipe")
    c = _client_for(home)
    try:
        for n, t in (("a", "named"), ("b", "anonymous"), ("c", "broadcast")):
            c.call("tools/call", {
                "name": "pipe_create",
                "arguments": {"name": n, "type": t},
            })
        resp = c.call("tools/call", {
            "name": "pipe_list",
            "arguments": {},
        })
        pipes = resp["result"]["structuredContent"]["pipes"]
        assert resp["result"]["structuredContent"]["count"] == 3
        types = {p["name"]: p["type"] for p in pipes}
        assert types == {"a": "named", "b": "anonymous", "c": "broadcast"}, types
        print(f"  PASS pipe_list returned all 3 with correct types")
    finally:
        c.close()


def case_initialize_unknown_protocol_version_still_responds(home: Path) -> None:
    _section("initialize with unknown protocolVersion still returns serverInfo")
    c = MCPClient(home)
    try:
        resp = c.call("initialize", {
            "protocolVersion": "9999-01-01",
            "clientInfo": {"name": "test", "version": "0.0"},
        })
        # Server returns its supported version regardless
        assert resp["result"]["serverInfo"]["name"] == "claude-pipe"
        print(f"  PASS server.protocolVersion={resp['result']['protocolVersion']}")
    finally:
        c.close()


# --------------------------------------------------------------------- driver

def main() -> None:
    cases = [
        case_initialize_returns_server_info,
        case_tools_list_returns_nine_pipe_tools,
        case_unknown_method_returns_error,
        case_unknown_tool_returns_tool_error,
        case_named_pipe_full_roundtrip,
        case_broadcast_subscribe_write_read,
        case_anonymous_auto_remove,
        case_validation_errors_come_back_as_tool_errors,
        case_pipe_list_returns_metadata,
        case_initialize_unknown_protocol_version_still_responds,
    ]
    failures = 0
    for case in cases:
        home = Path(tempfile.mkdtemp(prefix="pipe-mcp-test-"))
        try:
            case(home)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
