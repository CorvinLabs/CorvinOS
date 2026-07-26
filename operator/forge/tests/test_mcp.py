"""End-to-end tests for the forge MCP server.

Each test spawns ``forge.py mcp`` as a subprocess, drives it over stdio
JSON-RPC just like Claude Code would, and validates responses + behaviour.

Run as: python3 tests/test_mcp.py
"""
from __future__ import annotations

import concurrent.futures
import functools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0
CURRENT_SECTION = ""


def section(name: str) -> None:
    global CURRENT_SECTION
    CURRENT_SECTION = name
    print(f"\n[{name}]")


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    mark = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  {mark}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- MCP client harness ---------------------------------------------

# Env vars the bridge adapter sets per Discord/Telegram/etc. turn. They
# leak into the test subprocess if the developer (or autograder) runs the
# suite from inside a bridge persona, which makes the persona-namespace
# gate fire on tool names like ``synth_sales`` (the gate then expects
# ``code.synth_sales`` because the leaked persona is ``coder``). Stripping
# them in the harness is the single-point fix — a test that legitimately
# wants to assert namespace behaviour passes ``caller_persona`` via the
# MCP tool argument or sets ``FORGE_PERSONA`` explicitly, both of which
# stay untouched here.
_BRIDGE_ENV_LEAKS = (
    "CORVIN_CALLER_PERSONA", "CORVIN_CHANNEL_ID",
    "CORVIN_CALLER_PERSONA", "CORVIN_CHANNEL_ID",
)


def _clean_subprocess_env() -> dict[str, str]:
    """Inherit os.environ but drop the bridge-only env vars that would
    otherwise turn unit tests into integration tests against the active
    persona namespace."""
    env = os.environ.copy()
    for k in _BRIDGE_ENV_LEAKS:
        env.pop(k, None)
    return env


class MCPClient:
    """Drives a forge.py mcp subprocess over stdio JSON-RPC."""

    def __init__(self, root: Path, *, permission_mode: str = "yes") -> None:
        self.root = root
        self.proc = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "forge.py"),
                "--root",
                str(root),
                "mcp",
                "--permission-mode",
                permission_mode,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(ROOT),
            env=_clean_subprocess_env(),
        )
        self._next_id = 0
        self._buffered: list[dict[str, Any]] = []
        self._buffer_lock = threading.Lock()
        # background reader: pushes every parsed message into _buffered
        self._reader_alive = True
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._buffer_lock:
                self._buffered.append(msg)
        self._reader_alive = False

    def _take(self, predicate: Callable[[dict], bool], timeout: float) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._buffer_lock:
                for i, msg in enumerate(self._buffered):
                    if predicate(msg):
                        return self._buffered.pop(i)
            if self.proc.poll() is not None and not self._reader_alive:
                return None
            time.sleep(0.01)
        return None

    # request/response helpers -----------------------------------------

    def request(self, method: str, params: Any = None, *, timeout: float = 5.0) -> dict:
        self._next_id += 1
        msgid = self._next_id
        msg: dict[str, Any] = {"jsonrpc": "2.0", "id": msgid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        resp = self._take(lambda m: m.get("id") == msgid, timeout)
        if resp is None:
            raise TimeoutError(f"no response to {method} within {timeout}s")
        return resp

    def notify(self, method: str, params: Any = None) -> None:
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def expect_notification(
        self, method: str, *, timeout: float = 2.0
    ) -> Optional[dict]:
        return self._take(
            lambda m: "id" not in m and m.get("method") == method, timeout
        )

    def initialize(self) -> dict:
        resp = self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "forge-test", "version": "0.0"},
            },
        )
        self.notify("notifications/initialized")
        return resp

    def close(self) -> str:
        if self.proc.poll() is None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        stderr = self.proc.stderr.read() if self.proc.stderr else ""
        return stderr


# ---------- harness sanity --------------------------------------------------

def with_client(*, permission_mode: str = "yes"):
    def deco(fn):
        @functools.wraps(fn)
        def wrapped():
            with tempfile.TemporaryDirectory() as td:
                client = MCPClient(Path(td), permission_mode=permission_mode)
                try:
                    fn(client, Path(td))
                finally:
                    err = client.close()
                    if "Traceback" in err:
                        t("server stderr clean", False, detail=err.splitlines()[-1])
                    else:
                        t("server stderr clean", True)
        return wrapped
    return deco


# ---------- iteration 1 tests: skeleton ------------------------------------

@with_client()
def test_initialize_handshake(client: MCPClient, root: Path) -> None:
    section("initialize handshake")
    resp = client.initialize()
    t("response has result", "result" in resp)
    result = resp.get("result", {})
    t("protocolVersion advertised",
      result.get("protocolVersion") == "2024-11-05")
    caps = result.get("capabilities", {})
    t("tools.listChanged capability set",
      caps.get("tools", {}).get("listChanged") is True)
    info = result.get("serverInfo", {})
    t("serverInfo.name = claude-tool-forge",
      info.get("name") == "claude-tool-forge")


@with_client()
def test_tools_list_initial(client: MCPClient, root: Path) -> None:
    section("tools/list initial")
    client.initialize()
    resp = client.request("tools/list")
    tools = resp.get("result", {}).get("tools", [])
    names = [tool["name"] for tool in tools]
    t("forge_tool present", "forge_tool" in names)
    t("forge_promote present", "forge_promote" in names)
    forge_tool = next((x for x in tools if x["name"] == "forge_tool"), None)
    t("forge_tool has inputSchema",
      forge_tool is not None and "inputSchema" in forge_tool)
    schema = forge_tool["inputSchema"] if forge_tool else {}
    required = set(schema.get("required") or [])
    t("forge_tool requires (name, description, input_schema, impl)",
      required == {"name", "description", "input_schema", "impl"})


@with_client()
def test_unknown_method(client: MCPClient, root: Path) -> None:
    section("unknown method")
    client.initialize()
    resp = client.request("nope/nope")
    err = resp.get("error", {})
    t("error code = -32601", err.get("code") == -32601)


@with_client()
def test_garbage_input_does_not_crash(client: MCPClient, root: Path) -> None:
    section("garbage input")
    client.initialize()
    # send garbage line directly
    client.proc.stdin.write("this is not json\n")
    client.proc.stdin.flush()
    # server should still answer subsequent valid request
    resp = client.request("tools/list", timeout=3.0)
    t("server still alive after garbage",
      "result" in resp and "tools" in resp["result"])


# ---------- iteration 2 tests: forge_tool registers ------------------------

ECHO_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"echo": p}))
'''
ECHO_SCHEMA = {"type": "object", "required": ["msg"],
               "properties": {"msg": {"type": "string"}}}


def _forge(client: MCPClient, name: str, *, desc: str = "echo",
           schema: dict = None, impl: str = ECHO_IMPL,
           runtime: str = "python", overwrite: bool = False) -> dict:
    return client.request(
        "tools/call",
        {
            "name": "forge_tool",
            "arguments": {
                "name": name,
                "description": desc,
                "input_schema": schema or ECHO_SCHEMA,
                "impl": impl,
                "runtime": runtime,
                "overwrite": overwrite,
            },
        },
    )


@with_client()
def test_forge_then_list(client: MCPClient, root: Path) -> None:
    section("forge_tool registers, appears in tools/list")
    client.initialize()
    resp = _forge(client, "echo")
    result = resp.get("result", {})
    t("forge isError=False", result.get("isError") is False)
    text = result.get("content", [{}])[0].get("text", "")
    t("forge response mentions echo", "echo" in text)
    t("structuredContent contains sha256",
      "sha256" in (result.get("structuredContent") or {}))

    # next tools/list should include the forged tool
    listing = client.request("tools/list")
    names = [tool["name"] for tool in listing["result"]["tools"]]
    t("forged tool appears in tools/list", "echo" in names)


@with_client()
def test_forge_invalid_name(client: MCPClient, root: Path) -> None:
    section("forge with invalid name")
    client.initialize()
    resp = _forge(client, "bad name with spaces")
    result = resp.get("result", {})
    t("forge invalid name returns isError=True",
      result.get("isError") is True)


@with_client()
def test_forge_missing_arg(client: MCPClient, root: Path) -> None:
    section("forge missing required arg")
    client.initialize()
    resp = client.request("tools/call", {
        "name": "forge_tool",
        "arguments": {"name": "x"},  # missing description, schema, impl
    })
    result = resp.get("result", {})
    t("missing-arg returns isError=True", result.get("isError") is True)


@with_client()
def test_forge_overwrite(client: MCPClient, root: Path) -> None:
    section("forge overwrite semantics")
    client.initialize()
    r1 = _forge(client, "echo")
    t("first forge ok", r1["result"]["isError"] is False)
    r2 = _forge(client, "echo")
    t("second forge without overwrite errors",
      r2["result"]["isError"] is True)
    r3 = _forge(client, "echo", overwrite=True)
    t("forge with overwrite succeeds",
      r3["result"]["isError"] is False)


# ---------- iteration 3 tests: list_changed notification -------------------

@with_client()
def test_notification_after_forge(client: MCPClient, root: Path) -> None:
    section("notifications/tools/list_changed after forge")
    client.initialize()
    _forge(client, "echo")
    notif = client.expect_notification("notifications/tools/list_changed")
    t("notification received", notif is not None)


@with_client()
def test_notification_after_promote(client: MCPClient, root: Path) -> None:
    section("notifications/tools/list_changed after promote")
    client.initialize()
    _forge(client, "echo")
    # drain the post-forge notification
    client.expect_notification("notifications/tools/list_changed")
    client.request("tools/call", {
        "name": "forge_promote",
        "arguments": {"name": "echo"},
    })
    notif = client.expect_notification("notifications/tools/list_changed")
    t("notification after promote", notif is not None)
    # skill folder must exist
    skill_md = root / "skills" / "echo" / "SKILL.md"
    t("SKILL.md materialized", skill_md.exists())


# ---------- iteration 4 tests: forged tool is callable ---------------------

@with_client()
def test_call_forged_tool(client: MCPClient, root: Path) -> None:
    section("call a forged tool over MCP")
    client.initialize()
    _forge(client, "echo")
    resp = client.request("tools/call", {
        "name": "echo",
        "arguments": {"msg": "hello"},
    })
    result = resp["result"]
    t("isError=False", result.get("isError") is False)
    structured = result.get("structuredContent", {})
    t("structuredContent.ok=True", structured.get("ok") is True)
    # _artifacts_dir is auto-injected; only assert the user payload echoes
    echoed = (structured.get("data") or {}).get("echo") or {}
    t("data echoes payload",
      echoed.get("msg") == "hello")
    t("sandbox label present",
      structured.get("sandbox") in ("bwrap", "rlimits"))


@with_client()
def test_call_forged_schema_error(client: MCPClient, root: Path) -> None:
    section("forged-tool schema violation surfaces as error")
    client.initialize()
    _forge(client, "echo")
    resp = client.request("tools/call", {
        "name": "echo",
        "arguments": {"msg": 42},  # type-mismatch
    })
    result = resp["result"]
    t("isError=True for schema violation", result.get("isError") is True)
    text = result["content"][0]["text"]
    t("error text mentions schema", "schema" in text.lower())


@with_client()
def test_call_unknown_tool(client: MCPClient, root: Path) -> None:
    section("call unknown tool")
    client.initialize()
    resp = client.request("tools/call", {
        "name": "ghost",
        "arguments": {},
    })
    t("unknown tool isError=True",
      resp["result"].get("isError") is True)


@with_client()
def test_call_crashing_tool(client: MCPClient, root: Path) -> None:
    section("forged tool that crashes")
    client.initialize()
    _forge(client, "boom",
           desc="raises", schema={"type": "object", "properties": {}},
           impl="#!/usr/bin/env python3\nimport sys; sys.exit(7)\n")
    resp = client.request("tools/call",
                          {"name": "boom", "arguments": {}})
    result = resp["result"]
    t("crashing tool isError=True", result.get("isError") is True)
    text = result["content"][0]["text"]
    t("error text mentions exit code", "exited 7" in text or "tool error" in text)


@with_client()
def test_call_bash_runtime(client: MCPClient, root: Path) -> None:
    section("bash-runtime forged tool works")
    client.initialize()
    bash_impl = (
        '#!/bin/bash\nread -r line\n'
        'echo "{\\"raw\\": \\"got input\\"}"\n'
    )
    _forge(
        client,
        "bashy",
        desc="bash demo",
        schema={"type": "object", "properties": {}},
        impl=bash_impl,
        runtime="bash",
    )
    resp = client.request("tools/call",
                          {"name": "bashy", "arguments": {}})
    t("bash tool isError=False",
      resp["result"].get("isError") is False)


# ---------- iteration 5 tests: edge cases ----------------------------------

@with_client()
def test_concurrent_forge_and_call(client: MCPClient, root: Path) -> None:
    section("concurrent forge + call (single client, parallel ids)")
    client.initialize()
    _forge(client, "echo")
    # drain notification
    client.expect_notification("notifications/tools/list_changed")

    def call_once(_):
        # 30 s, not the 5 s default: this fires EIGHT concurrent tool calls, each
        # executing in its own bwrap sandbox. On an idle machine that is well under a
        # second; inside run-all-tests.sh, with other suites competing for CPU, it
        # regularly exceeded 5 s and the whole "forge plugin" suite reported FAIL with
        # no traceback (the runner discards stdout). The subject here is CONCURRENCY
        # CORRECTNESS — that eight parallel ids all get their own correct response —
        # not sandbox startup latency.
        return client.request("tools/call", {
            "name": "echo",
            "arguments": {"msg": "c"},
        }, timeout=30.0)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(call_once, range(8)))
    ok = all(r["result"].get("isError") is False for r in results)
    t("8 concurrent calls all ok", ok)


@with_client()
def test_tamper_over_mcp(client: MCPClient, root: Path) -> None:
    section("tamper detection survives the MCP path")
    client.initialize()
    forge_resp = _forge(client, "echo")
    impl_path = Path(forge_resp["result"]["structuredContent"]["impl_path"])
    # mutate impl behind the registry's back
    impl_path.write_text(ECHO_IMPL + "\n# evil\n")
    resp = client.request("tools/call", {
        "name": "echo",
        "arguments": {"msg": "x"},
    })
    text = resp["result"]["content"][0]["text"]
    t("tamper -> isError=True",
      resp["result"].get("isError") is True)
    t("error text says tamper", "tamper" in text.lower() or "sha256" in text.lower())


@with_client()
def test_large_payload_roundtrip(client: MCPClient, root: Path) -> None:
    section("large payload roundtrip (256 KiB)")
    client.initialize()
    _forge(
        client,
        "len_tool",
        desc="returns input length",
        schema={"type": "object", "required": ["s"],
                "properties": {"s": {"type": "string"}}},
        impl=(
            '#!/usr/bin/env python3\nimport json,sys\n'
            'p=json.loads(sys.stdin.read())\n'
            'print(json.dumps({"len": len(p["s"])}))\n'
        ),
    )
    big = "x" * (256 * 1024)
    resp = client.request("tools/call", {
        "name": "len_tool",
        "arguments": {"s": big},
    }, timeout=10.0)
    structured = resp["result"].get("structuredContent", {})
    t("len roundtrip exact",
      structured.get("data", {}).get("len") == len(big))


@with_client()
def test_invalid_jsonrpc_envelope(client: MCPClient, root: Path) -> None:
    section("invalid JSON-RPC envelope")
    client.initialize()
    # send a message without jsonrpc=2.0 — must get an error, server alive
    client.proc.stdin.write(json.dumps({"id": 99, "method": "tools/list"}) + "\n")
    client.proc.stdin.flush()
    err = client._take(lambda m: m.get("id") == 99, timeout=3.0)
    t("invalid envelope yields error",
      err is not None and "error" in err)
    # subsequent valid request still works
    resp = client.request("tools/list")
    t("server still answers after bad envelope",
      "result" in resp)


@with_client(permission_mode="deny")
def test_permission_mode_deny(client: MCPClient, root: Path) -> None:
    section("permission_mode=deny blocks forged calls")
    client.initialize()
    _forge(client, "echo")
    resp = client.request("tools/call", {
        "name": "echo",
        "arguments": {"msg": "x"},
    })
    result = resp["result"]
    text = result["content"][0]["text"]
    t("isError=True", result.get("isError") is True)
    t("text mentions permission",
      "permission" in text.lower() or "denied" in text.lower())


@with_client()
def test_call_hanging_tool_times_out(client: MCPClient, root: Path) -> None:
    section("forged tool that hangs is killed and surfaces a tool error")
    client.initialize()
    # The runner has a default timeout of 30s, but we don't want to wait
    # that long. We forge a hang impl and override timeout via the runner...
    # ...except MCP doesn't expose timeout in tools/call. Instead we shorten
    # the impl to sleep just longer than the runner's default test budget,
    # while still proving the kill path works. We use a short busy-wait CPU
    # impl to also trip RLIMIT_CPU as a fast secondary signal.
    _forge(
        client,
        "spinhog",
        desc="burns CPU forever",
        schema={"type": "object", "properties": {}},
        impl=(
            '#!/usr/bin/env python3\n'
            'import sys, json\n'
            'json.loads(sys.stdin.read())\n'
            'while True: pass\n'  # RLIMIT_CPU=10s will fire
        ),
    )
    resp = client.request(
        "tools/call",
        {"name": "spinhog", "arguments": {}},
        timeout=30.0,
    )
    result = resp["result"]
    t("hung tool isError=True", result.get("isError") is True)


@with_client()
def test_eof_shuts_server_down_cleanly(client: MCPClient, root: Path) -> None:
    section("EOF on stdin terminates the server cleanly")
    client.initialize()
    _forge(client, "echo")
    # close stdin → server's `for line in stdin` loop ends gracefully
    client.proc.stdin.close()
    try:
        rc = client.proc.wait(timeout=3.0)
        clean = rc == 0
    except subprocess.TimeoutExpired:
        clean = False
        client.proc.kill()
        client.proc.wait()
    t("server exits cleanly on EOF (rc=0)", clean)


@with_client()
def test_promote_from_session_makes_skill(client: MCPClient, root: Path) -> None:
    section("forge -> call -> promote -> skill on disk")
    client.initialize()
    _forge(client, "echo")
    client.request("tools/call",
                   {"name": "echo", "arguments": {"msg": "x"}})
    client.request("tools/call",
                   {"name": "forge_promote", "arguments": {"name": "echo"}})
    skill_md = root / "skills" / "echo" / "SKILL.md"
    impl_copy = root / "skills" / "echo" / "echo.py"
    t("SKILL.md exists", skill_md.exists())
    t("impl copied alongside SKILL.md", impl_copy.exists())
    body = skill_md.read_text() if skill_md.exists() else ""
    t("SKILL.md frontmatter starts with name:",
      body.startswith("---\nname: echo"))


# ---------- driver ---------------------------------------------------------

ALL_TESTS = [
    # Iter 1
    test_initialize_handshake,
    test_tools_list_initial,
    test_unknown_method,
    test_garbage_input_does_not_crash,
    # Iter 2
    test_forge_then_list,
    test_forge_invalid_name,
    test_forge_missing_arg,
    test_forge_overwrite,
    # Iter 3
    test_notification_after_forge,
    test_notification_after_promote,
    # Iter 4
    test_call_forged_tool,
    test_call_forged_schema_error,
    test_call_unknown_tool,
    test_call_crashing_tool,
    test_call_bash_runtime,
    # Iter 5
    test_concurrent_forge_and_call,
    test_tamper_over_mcp,
    test_large_payload_roundtrip,
    test_invalid_jsonrpc_envelope,
    test_permission_mode_deny,
    test_call_hanging_tool_times_out,
    test_eof_shuts_server_down_cleanly,
    test_promote_from_session_makes_skill,
]


def main(argv: list[str]) -> int:
    only = set(argv[1:])
    for fn in ALL_TESTS:
        if only and fn.__name__ not in only:
            continue
        try:
            fn()
        except Exception as e:
            t(f"{fn.__name__} crashed", False, detail=repr(e))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
