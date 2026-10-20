"""E2E: every bridge spawn carries the status-marker rule (ADR-2269).

The adapter runs as its real daemon (``adapter.py``), consumes inbox items through
``process_one`` and spawns the Claude CLI. ``ADAPTER_FAKE_CLAUDE`` replaces only the
binary: the adapter still composes the full system prompt, writes the
``--append-system-prompt-file`` the CLI would read, and dumps the argv
(``ADAPTER_FAKE_ARGS_DUMP``). This test reads that file back for

  1. a normal Discord turn, and
  2. a ``/task`` turn, whose worker runs detached in its own process
     (``_spawn_detached_bg_worker``, the same core the self-delegation hook uses).

Every dumped spawn must carry ``status_marker_rule()``. The test is a plain script
(it boots the daemon), so it runs under pytest and under ``python3`` alike.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ADAPTER = ROOT / "adapter.py"
CHANNEL = "discord"
CHAT = "status-marker-e2e"
sys.path.insert(0, str(ROOT))
import status_markers  # noqa: E402
import unit_sweep  # noqa: E402

RULE = status_markers.status_marker_rule()


def _adapter_argv() -> list[str]:
    """argv that starts the adapter daemon in a cgroup of its OWN.

    On shutdown the adapter terminates every process in its cgroup except itself
    (``unit_sweep.sweep_unit_leftovers``). Started inside the runner's cgroup that sweep
    kills pytest, the shell and every other CLI session sharing the cgroup (measured: two
    ``exit 137`` runs that took the operator's shell down). So on cgroup v2 the daemon runs
    in its own transient scope; where that is not possible the test is skipped rather than
    run unsafely. Without cgroup v2 the sweep is a no-op and the plain argv is safe.
    """
    argv = [sys.executable, str(ADAPTER)]
    if unit_sweep.own_cgroup_procs_file() is None:
        return argv
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise unittest.SkipTest("cgroup v2 without systemd-run: the adapter's shutdown sweep "
                                "would kill this test runner's cgroup")
    probe = subprocess.run([systemd_run, "--user", "--scope", "--quiet", "true"],
                           capture_output=True, timeout=30)
    if probe.returncode != 0:
        raise unittest.SkipTest("systemd --user scopes unavailable: cannot isolate the adapter's cgroup")
    return [systemd_run, "--user", "--scope", "--quiet", *argv]


def _env(sb: Path) -> dict:
    env = os.environ.copy()
    env.pop("VOICE_AUDIT_PATH", None)
    env.update({
        "ADAPTER_INBOX": str(sb / "inbox"), "ADAPTER_OUTBOX": str(sb / "outbox"),
        "ADAPTER_PROCESSED": str(sb / "processed"),
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0.05",
        "ADAPTER_FAKE_ARGS_DUMP": str(sb / "args.jsonl"),
        "ADAPTER_POLL_INTERVAL": "0.1", "ADAPTER_BRIDGES_DIR": str(sb / "bridges"),
        "ADAPTER_DISABLE_VOICE": "1", "BRIDGE_PROGRESS_UPDATES": "0",
        "ADAPTER_ROUTING_MODE": "off",
        "CORVIN_HOME": str(sb / "home"), "XDG_CONFIG_HOME": str(sb / "xdg"),
        "FORGE_ROOT": str(sb / "forge-root"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(sb / "anchor.key"),
        "CORVIN_PLUGIN_SLOT_DIR": str(sb / "slot"), "CORVIN_PROJECT_ROOT": "",
        "COWORK_USER_DIR": str(sb / "cowork-user"), "COWORK_MCP_CACHE": str(sb / "mcp-cache"),
        "CLAUDE_CONFIG_DIR": str(sb / "claude-cfg"),
        "CORVIN_TENANT_ID": "_default",
        # The detached /task worker is a separate process; it must dump into the same file.
        "CORVIN_BG_TASK_MAX": "3",
    })
    return env


def _dump(sb: Path) -> list[dict]:
    path = sb / "args.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _system_text(sb: Path, entry: dict) -> str:
    args = entry["args"]
    if "--append-system-prompt" in args:
        return args[args.index("--append-system-prompt") + 1]
    if "--append-system-prompt-file" in args:
        return Path(args[args.index("--append-system-prompt-file") + 1]).read_text(encoding="utf-8")
    raise AssertionError(f"no system prompt in argv: {args}")


class StatusMarkerProcessOneE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sb = Path(tempfile.mkdtemp(prefix="status-marker-e2e-"))
        for d in ("inbox", "outbox", "processed", "home", "xdg", "bridges"):
            (cls.sb / d).mkdir(parents=True, exist_ok=True)
        (cls.sb / "bridges" / CHANNEL).mkdir(parents=True, exist_ok=True)
        (cls.sb / "bridges" / CHANNEL / "settings.json").write_text(
            json.dumps({"chat_profiles": {CHAT: {}}}))
        cls.proc = subprocess.Popen(
            _adapter_argv(), env=_env(cls.sb),
            stdout=open(cls.sb / "adapter.log", "a"), stderr=subprocess.STDOUT)
        cls._n = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        log = (cls.sb / "adapter.log").read_text(encoding="utf-8", errors="replace")
        if os.environ.get("KEEP_E2E_SANDBOX") != "1":
            shutil.rmtree(cls.sb, ignore_errors=True)
        else:
            print("sandbox kept:", cls.sb, "\n", log[-2000:])

    def _send(self, text: str) -> str:
        type(self)._n += 1
        item_id = f"sm-{type(self)._n}"
        (self.sb / "inbox" / f"{item_id}.json").write_text(json.dumps({
            "id": item_id, "channel": CHANNEL, "chat_id": CHAT, "from": CHAT, "text": text,
        }))
        return item_id

    def _wait_processed(self, item_id: str, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (self.sb / "processed" / f"{item_id}.json").exists():
                return
            time.sleep(0.05)
        self.fail(f"{item_id} not processed; see {self.sb / 'adapter.log'}")

    def _wait_dump(self, count: int, timeout: float = 60.0) -> list[dict]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            entries = _dump(self.sb)
            if len(entries) >= count:
                return entries
            time.sleep(0.1)
        self.fail(f"expected {count} spawn(s), saw {len(_dump(self.sb))}")

    def test_a_normal_discord_turn_carries_the_rule(self) -> None:
        item = self._send("which river runs through the old town?")
        self._wait_processed(item)
        entries = self._wait_dump(1)
        turn = [e for e in entries if e.get("chat_key") == CHAT]
        self.assertTrue(turn, entries)
        self.assertIn(RULE, _system_text(self.sb, turn[-1]))
        self.assertIn("Discord", _system_text(self.sb, turn[-1]))

    def test_b_task_worker_spawn_carries_the_rule(self) -> None:
        # Positive control: the normal turn above already dumped one spawn.
        before = len(_dump(self.sb))
        item = self._send("/task summarise the sandbox notes")
        self._wait_processed(item)
        entries = self._wait_dump(before + 1)
        workers = entries[before:]
        self.assertTrue(workers, "the /task worker did not dump a spawn")
        for entry in workers:
            self.assertIn(RULE, _system_text(self.sb, entry), entry["args"][:6])


if __name__ == "__main__":
    unittest.main()
