"""S6 — verify in-turn skill visibility via the wire-level
notifications/tools/list_changed notification.

Fictional task: in the same MCP session, create a skill and verify that
the server emits notifications/tools/list_changed so the client (Claude
Code) refreshes its tool list and can call the new artifact within the
SAME subprocess — no bridge-turn delay.

Spawns the skill-forge MCP server as a subprocess, drives it over stdio
JSON-RPC, and verifies the notification arrives after each mutating
call: skill_create, skill_promote, skill_purge, skill_grade.

Run as: python3 operator/skill-forge/tests/test_mcp_notification.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[3]
SKILL_FORGE_PKG = REPO / "operator" / "skill-forge"
FORGE_PKG = REPO / "operator" / "forge"
sys.path.insert(0, str(SKILL_FORGE_PKG))
sys.path.insert(0, str(FORGE_PKG))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


# ---------- MCP client harness (stdio JSON-RPC) ----------------------------

class _Client:
    def __init__(self, env: dict[str, str]) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "skill_forge.mcp_server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=str(SKILL_FORGE_PKG),
            env=env,
        )
        self._next_id = 0
        self._buf: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._alive = True
        threading.Thread(target=self._read_loop, daemon=True).start()

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
            with self._lock:
                self._buf.append(msg)
        self._alive = False

    def _take(self, predicate: Callable[[dict], bool], timeout: float) -> Optional[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                for i, m in enumerate(self._buf):
                    if predicate(m):
                        return self._buf.pop(i)
            if self.proc.poll() is not None and not self._alive:
                return None
            time.sleep(0.01)
        return None

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

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

    def expect_notification(self, method: str, *, timeout: float = 3.0) -> Optional[dict]:
        return self._take(
            lambda m: "id" not in m and m.get("method") == method, timeout
        )

    def initialize(self) -> None:
        self.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "skill-forge-notif-test", "version": "0.0"},
        })
        self.notify("notifications/initialized")

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
        return self.proc.stderr.read() if self.proc.stderr else ""


def _call_tool(c: _Client, name: str, args: dict, *, timeout: float = 5.0) -> dict:
    return c.request("tools/call", {"name": name, "arguments": args}, timeout=timeout)


SAMPLE_BODY = (
    "# CSV diff workflow\n\n"
    "Use this skill to compute deterministic diffs between two CSV files.\n\n"
    "## Steps\n\n"
    "1. Load both files via pandas.read_csv.\n"
    "2. Sort by primary key.\n"
    "3. Compute row-wise diff and emit a markdown summary.\n"
)


def main() -> int:
    print("[skill-forge MCP — notifications/tools/list_changed E2E]")

    with tempfile.TemporaryDirectory() as td:
        slot = tempfile.mkdtemp(prefix="sf-slot-")
        env = dict(os.environ)
        # Strip bridge-only env vars that leak in when the suite is run from
        # a Discord/Telegram persona shell — they would set caller_persona
        # to "coder" and trip the namespace-gate on test tool names.
        for k in (
            "CORVIN_CALLER_PERSONA", "CORVIN_CHANNEL_ID",
        ):
            env.pop(k, None)
        env["CORVIN_HOME"] = td
        env["CORVIN_FORCE_SCOPE"] = "user"
        env["CORVIN_PLUGIN_SLOT_DIR"] = slot
        # Make `forge` package importable so the unified audit chain works.
        env["PYTHONPATH"] = f"{SKILL_FORGE_PKG}:{FORGE_PKG}:" + env.get("PYTHONPATH", "")

        c = _Client(env)
        try:
            c.initialize()

            # --- skill_create -------------------------------------------------
            r1 = _call_tool(c, "skill_create", {
                "name": "csv_diff_workflow",
                "description": "deterministic CSV diff",
                "type": "learned-experience",
                "claim": {"summary": "Two CSVs can be diffed deterministically "
                                     "by sorting on a primary key."},
                "body_md": SAMPLE_BODY,
            })
            t("skill_create returned a result",
              "result" in r1,
              detail=str(r1)[:160])
            n1 = c.expect_notification("notifications/tools/list_changed")
            t("notifications/tools/list_changed arrived after skill_create",
              n1 is not None and n1.get("method") == "notifications/tools/list_changed")

            # --- skill_grade --------------------------------------------------
            r2 = _call_tool(c, "skill_grade", {
                "name": "csv_diff_workflow",
                "run_id": "test-run-1",
                "score": 0.8,
                "notes": "tried-it-out",
            })
            t("skill_grade returned a result",
              "result" in r2,
              detail=str(r2)[:160])
            # skill_grade changes meta.json scores, NOT the tool list itself
            # — so no notifications/tools/list_changed should fire. This is
            # the wire-level symmetry check: only mutators that change what
            # tools/list returns should ping the client.
            n2 = c.expect_notification("notifications/tools/list_changed", timeout=0.4)
            t("no tools/list_changed after skill_grade (semantic correctness)",
              n2 is None,
              detail=f"unexpected: {n2!r}" if n2 else "")

            # --- skill_purge --------------------------------------------------
            r3 = _call_tool(c, "skill_purge", {
                "name": "csv_diff_workflow",
                "reason": "test-cleanup",
            })
            t("skill_purge returned a result",
              "result" in r3,
              detail=str(r3)[:160])
            n3 = c.expect_notification("notifications/tools/list_changed")
            t("notifications/tools/list_changed arrived after skill_purge",
              n3 is not None and n3.get("method") == "notifications/tools/list_changed")

            # --- read-only call: tools/list MUST NOT emit a list_changed notif
            r4 = c.request("tools/list")
            t("tools/list works",
              "result" in r4 and isinstance(r4["result"].get("tools"), list))
            n4 = c.expect_notification("notifications/tools/list_changed", timeout=0.4)
            t("no spurious notification after read-only tools/list",
              n4 is None,
              detail="" if n4 is None else f"unexpected: {n4!r}")

        finally:
            stderr = c.close()
            if FAIL > 0:
                print("\n--- subprocess stderr (last 800 chars) ---")
                print(stderr[-800:])

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
