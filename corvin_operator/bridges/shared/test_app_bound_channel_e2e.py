"""E2E: app-bound channels through the real adapter daemon (ADR-2333, T-0424 / T-0429 / T-0426).

The adapter runs as its real daemon (``adapter.py``) and consumes inbox items through ``process_one``.
``ADAPTER_FAKE_CLAUDE`` replaces only the Claude binary and dumps every engine spawn (``ADAPTER_FAKE_ARGS_DUMP``),
so "no engine ran" is a measured fact. The plugin side is a real handler program run by the adapter.

Covered: a bound chat reaches only its handler (no engine, no delegation spawn), its reply goes to the same chat even if
the handler names another, the ledger keeps neither text nor hash, the handler sees a cleared environment; an image,
an observer envelope, a non-owner and a ``_btw`` envelope never reach the engine or the handler; three handler failures
suspend the binding and later messages get one notice; a pending binding leaves the chat to the OS until ``/bind
confirm``; ``/bind release`` gives it back; an unbound chat is an ordinary OS turn; the operator CLI never prints a
full chat id.

    python3 -m pytest corvin_operator/bridges/shared/test_app_bound_channel_e2e.py -q
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
REPO = ROOT.parents[2]
ADAPTER = ROOT / "adapter.py"
CHANNEL = "discord"
OWNER = "100000000000000001"
STRANGER = "100000000000000002"
CHAT_A, CHAT_B, CHAT_C, CHAT_U = "200000000000000001", "200000000000000002", "200000000000000003", "200000000000000004"
for p in (str(ROOT), str(REPO), str(REPO / "corvin_operator" / "forge")):
    if p not in sys.path:
        sys.path.insert(0, p)
import unit_sweep  # noqa: E402

HANDLER = r'''
import json, os, sys
text = sys.stdin.read()
log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "received.jsonl")
with open(log, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"text": text, "env": sorted(os.environ)}) + "\n")
print(json.dumps({"text": "noted %d chars" % len(text), "changed": True, "chat_id": "999"}))
'''
FAILING = "import sys; sys.stdin.read(); sys.exit(1)\n"
DESCRIBE = 'import json, sys; print(json.dumps({"chat_id": sys.argv[1], "notices": {"media": "Text only, please."}}))\n'


def _adapter_argv() -> list[str]:
    """The daemon in a cgroup of its own (its shutdown sweep must never reach this runner)."""
    argv = [sys.executable, str(ADAPTER)]
    if unit_sweep.own_cgroup_procs_file() is None:
        return argv
    systemd_run = shutil.which("systemd-run")
    if systemd_run is None:
        raise unittest.SkipTest("cgroup v2 without systemd-run: cannot isolate the adapter's cgroup")
    probe = subprocess.run([systemd_run, "--user", "--scope", "--quiet", "true"], capture_output=True, timeout=30)
    if probe.returncode != 0:
        raise unittest.SkipTest("systemd --user scopes unavailable")
    return [systemd_run, "--user", "--scope", "--quiet", *argv]


def _env(sb: Path) -> dict:
    env = os.environ.copy()
    env.pop("VOICE_AUDIT_PATH", None)
    env.pop("FORGE_ROOT", None)
    env.update({
        "ADAPTER_INBOX": str(sb / "inbox"), "ADAPTER_OUTBOX": str(sb / "outbox"),
        "ADAPTER_PROCESSED": str(sb / "processed"),
        "ADAPTER_FAKE_CLAUDE": "1", "ADAPTER_FAKE_DELAY": "0.05",
        "ADAPTER_FAKE_ARGS_DUMP": str(sb / "args.jsonl"),
        "ADAPTER_POLL_INTERVAL": "0.1", "ADAPTER_BRIDGES_DIR": str(sb / "bridges"),
        "ADAPTER_DISABLE_VOICE": "1", "BRIDGE_PROGRESS_UPDATES": "0", "ADAPTER_ROUTING_MODE": "off",
        "CORVIN_HOME": str(sb / "home"), "XDG_CONFIG_HOME": str(sb / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str((sb / "anchor.key").resolve()),
        "CORVIN_PLUGIN_SLOT_DIR": str(sb / "slot"), "CORVIN_PROJECT_ROOT": "",
        "COWORK_USER_DIR": str(sb / "cowork-user"), "COWORK_MCP_CACHE": str(sb / "mcp-cache"),
        "CLAUDE_CONFIG_DIR": str(sb / "claude-cfg"), "CORVIN_TENANT_ID": "_default",
        "DISCORD_TOKEN": "must-not-reach-the-handler",
    })
    return env


class AppBoundChannelE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sb = Path(tempfile.mkdtemp(prefix="app-bound-e2e-"))
        for d in ("inbox", "outbox", "processed", "home", "xdg", "bridges/" + CHANNEL):
            (cls.sb / d).mkdir(parents=True, exist_ok=True)
        (cls.sb / "home" / "tenants" / "_default" / "global" / "forge").mkdir(parents=True, exist_ok=True)
        (cls.sb / "bridges" / CHANNEL / "settings.json").write_text(
            json.dumps({"whitelist": [OWNER], "chat_profiles": {}}), encoding="utf-8")
        cls.env = _env(cls.sb)
        # registry setup in THIS process, with the daemon's environment
        saved = {k: os.environ.get(k) for k in cls.env}
        os.environ.update(cls.env)
        try:
            import importlib
            import core.channel_bindings as cb
            cls.cb = importlib.reload(cb)
            cls.plugins = {}
            for name, handler in (("plug-a", HANDLER), ("plug-b", FAILING), ("plug-c", HANDLER)):
                d = cls.sb / name
                d.mkdir()
                (d / "handler.py").write_text(handler, encoding="utf-8")
                (d / "describe.py").write_text(DESCRIBE, encoding="utf-8")
                cls.plugins[name] = d
            led = cls.sb / "home" / "tenants" / "_default" / "session_ledger" / CHANNEL / CHAT_C / "ledger.jsonl"
            led.parent.mkdir(parents=True)
            led.write_text('{"kind": "turn"}\n', encoding="utf-8")          # CHAT_C has OS history -> pending
            cls.binding = {}
            for name, chat in (("plug-a", CHAT_A), ("plug-b", CHAT_B), ("plug-c", CHAT_C)):
                decl = cb.validate_declaration({"channel": CHANNEL, "describe": {"argv": ["python", "describe.py", chat]},
                                                "handler": {"argv": ["python", "handler.py"], "env_allow": ["HOME"]}},
                                               cls.plugins[name])
                info = cb.describe(decl, "_default")
                cls.binding[chat] = cb.claim("_default", plugin_id=name, decl=decl, chat_id=info["chat_id"],
                                             notices=info["notices"])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        cls.proc = subprocess.Popen(_adapter_argv(), env=cls.env,
                                    stdout=open(cls.sb / "adapter.log", "a", encoding="utf-8"), stderr=subprocess.STDOUT)
        cls._n = 0

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
        if os.environ.get("KEEP_E2E_SANDBOX") != "1":
            shutil.rmtree(cls.sb, ignore_errors=True)
        else:
            print("sandbox kept:", cls.sb)

    # ── helpers ───────────────────────────────────────────────────────────────────────────────────────────────────
    def _send(self, chat: str, sender: str = OWNER, **fields) -> str:
        type(self)._n += 1
        item_id = f"ab-{type(self)._n:03d}"
        payload = {"id": item_id, "channel": CHANNEL, "chat_id": chat, "from": sender, "ts": time.time() * 1000}
        payload.update(fields)
        (self.sb / "inbox" / f"{item_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        return item_id

    def _wait_gone(self, item_id: str, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not (self.sb / "inbox" / f"{item_id}.json").exists():
                time.sleep(0.3)                                  # let the reply / dump land
                return
            time.sleep(0.05)
        self.fail(f"{item_id} was not consumed; log tail: {(self.sb / 'adapter.log').read_text()[-1500:]}")

    def _wait_engine(self, chat: str, timeout: float = 60.0) -> list:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            hits = [e for e in self._dump() if str(e.get("chat_key")) == chat]
            if hits:
                return hits
            time.sleep(0.1)
        return []

    def _dump(self) -> list[dict]:
        p = self.sb / "args.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []

    def _replies(self, item_id: str) -> list[dict]:
        return [json.loads(p.read_text()) for p in sorted((self.sb / "outbox").glob(f"{item_id}_*.json"))]

    def _received(self, name: str) -> list[dict]:
        p = self.plugins[name] / "received.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []

    def _dropped_reasons(self) -> list[str]:
        # the adapter's chain is wherever its audit writer resolves in this environment (the tenant chain in
        # production; a test redirect may move it inside the sandbox) - read every audit file of the sandbox
        lines = [x for chain in self.sb.rglob("audit.jsonl") for x in chain.read_text().splitlines()]
        out = []
        for line in lines:
            if "channel_binding.dropped" in line:
                rec = json.loads(line)
                det = rec.get("details") or {}
                out.append(str(det.get("reason", "")))
        return out

    def _no_engine(self, chat: str) -> None:
        self.assertEqual([e for e in self._dump() if str(e.get("chat_key")) == chat], [],
                         "an engine was spawned for a bound chat")

    # ── cases (unittest runs them in name order) ──────────────────────────────────────────────────────────────────
    def test_01_owner_text_reaches_only_the_handler(self):
        item = self._send(CHAT_A, text="Please note the weekly summary.")
        self._wait_gone(item)
        replies = self._replies(item)
        self.assertEqual(len(replies), 1, replies)
        self.assertEqual(replies[0]["chat_id"], CHAT_A)                     # not the "999" the handler printed
        self.assertTrue(replies[0]["text"].startswith("noted "))
        got = self._received("plug-a")
        self.assertEqual(got[-1]["text"], "Please note the weekly summary.")
        self.assertNotIn("DISCORD_TOKEN", got[-1]["env"])
        self.assertTrue(set(got[-1]["env"]) <= {"HOME", "PATH", "PYTHONIOENCODING", "CORVIN_TENANT_ID",
                                                "CORVIN_BRIDGE_SENDER", "CORVIN_BRIDGE_OWNER", "CORVIN_BRIDGE_CHANNEL",
                                                "CORVIN_BRIDGE_MSG_ID", "LC_CTYPE"}, got[-1]["env"])
        self._no_engine(CHAT_A)

    def test_02_the_ledger_keeps_neither_text_nor_hash(self):
        led = list((self.sb / "home").rglob(f"session_ledger/**/{CHAT_A}/ledger.jsonl"))
        self.assertTrue(led, "no ledger row for the bound chat")
        rows = [json.loads(x) for x in led[0].read_text().splitlines() if x.strip()]
        app = [r for r in rows if r.get("refused") == "app_channel"]
        self.assertTrue(app)
        for r in app:
            self.assertFalse(r.get("user") or r.get("user_text") or r.get("user_sha256") or r.get("assistant")
                             or r.get("assistant_text"), r)

    def test_03_an_image_gets_a_notice_and_nothing_else(self):
        before = len(self._received("plug-a"))
        img = self.sb / "inbox" / "photo-1.jpg"
        img.write_bytes(b"\xff\xd8 a picture of a child")
        item = self._send(CHAT_A, image_path=str(img), caption="photo from the playground")
        self._wait_gone(item)
        self.assertFalse(img.exists(), "the picture of a message the app never takes in stayed on disk")
        self.assertEqual([r["text"] for r in self._replies(item)], ["Text only, please."])
        self.assertEqual(len(self._received("plug-a")), before)
        self._no_engine(CHAT_A)

    def test_04_observer_and_non_owner_and_btw_never_pass(self):
        before = len(self._received("plug-a"))
        obs = self._send(CHAT_A, sender=STRANGER, _observer=True, text="I am reading along")
        stranger = self._send(CHAT_A, sender=STRANGER, text="I am not the parent")
        btw = self._send(CHAT_A, _btw=True, text="by the way")
        for item in (obs, stranger, btw):
            self._wait_gone(item)
        self.assertEqual(self._replies(obs) + self._replies(stranger), [])
        self.assertEqual(len(self._replies(btw)), 1)
        self.assertEqual(len(self._received("plug-a")), before)
        self.assertEqual(list((self.sb / "home").rglob(f"{CHAT_A}/observers.jsonl")), [])
        reasons = self._dropped_reasons()
        self.assertIn("observer", reasons)                                  # dropped BY the binding, not by chance
        self.assertIn("not_owner", reasons)
        self._no_engine(CHAT_A)

    def test_05_three_handler_failures_suspend_and_later_messages_get_one_notice(self):
        items = [self._send(CHAT_B, text=f"attempt {i}") for i in range(3)]
        for item in items:
            self._wait_gone(item)
        saved = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self.env["CORVIN_HOME"]
        try:
            self.assertEqual(self.cb.resolve("_default", CHANNEL, CHAT_B)["status"], "suspended")
        finally:
            if saved is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = saved
        later = [self._send(CHAT_B, text="one more"), self._send(CHAT_B, text="and one more")]
        for item in later:
            self._wait_gone(item)
        notices = [r for item in later for r in self._replies(item)]
        self.assertEqual(len(notices), 1, notices)                          # rate-limited
        self._no_engine(CHAT_B)

    def test_06_a_pending_chat_stays_with_the_os_until_the_owner_confirms(self):
        first = self._send(CHAT_C, text="before the confirmation")
        self.assertTrue(self._wait_engine(CHAT_C), "pending chat did not take the OS turn")
        self._wait_gone(first)
        n_engine = len(self._wait_engine(CHAT_C))
        stranger = self._send(CHAT_C, sender=STRANGER, text="/bind confirm")
        self._wait_gone(stranger)
        confirm = self._send(CHAT_C, text="/bind confirm")
        self._wait_gone(confirm)
        self.assertIn("Confirmed", self._replies(confirm)[0]["text"])
        after = self._send(CHAT_C, text="after the confirmation")
        self._wait_gone(after)
        self.assertEqual(self._received("plug-c")[-1]["text"], "after the confirmation")
        self.assertEqual(len([e for e in self._dump() if str(e.get("chat_key")) == CHAT_C]), n_engine)
        release = self._send(CHAT_C, text="/bind release")
        self._wait_gone(release)
        self.assertIn("Released", self._replies(release)[0]["text"])
        back = self._send(CHAT_C, text="ordinary again")
        self._wait_gone(back)
        self.assertGreater(len(self._wait_engine(CHAT_C)), n_engine)

    def test_07_an_unbound_chat_is_an_ordinary_turn(self):
        item = self._send(CHAT_U, text="an ordinary question")
        self.assertTrue(self._wait_engine(CHAT_U), "the unbound chat did not take the OS turn")
        self._wait_gone(item)

    def test_09_system_made_envelopes_never_reach_the_app(self):
        before = len(self._received("plug-a"))
        sched = self._send(CHAT_A, _scheduled=True, text="reminder: dentist")
        wake = self._send(CHAT_A, _bg_wakeup=True, text="Background monitor check: please deliver")
        for item in (sched, wake):
            self._wait_gone(item)
        self.assertEqual(len(self._received("plug-a")), before)
        self.assertEqual(self._replies(sched) + self._replies(wake), [])
        self.assertGreaterEqual(self._dropped_reasons().count("system_envelope"), 2)
        self._no_engine(CHAT_A)

    def test_10_the_owner_resumes_a_suspended_binding(self):
        item = self._send(CHAT_B, text="/bind resume")
        self._wait_gone(item)
        self.assertIn("Resumed", self._replies(item)[0]["text"])
        saved = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = self.env["CORVIN_HOME"]
        try:
            self.assertEqual(self.cb.resolve("_default", CHANNEL, CHAT_B)["status"], "active")
        finally:
            if saved is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = saved
        self._no_engine(CHAT_B)

    def test_11_a_damaged_registry_drops_instead_of_falling_back_to_the_os(self):
        reg = self.sb / "home" / "tenants" / "_default" / "global" / "channel_bindings.json"
        good = reg.read_bytes()
        before = len(self._received("plug-a"))
        try:
            reg.write_bytes(good.replace(b'"status": "active"', b'"status": "Active"', 1))
            item = self._send(CHAT_A, text="after the damage")
            self._wait_gone(item)
            self.assertEqual(len(self._received("plug-a")), before)
            self.assertEqual(self._replies(item), [])
            self._no_engine(CHAT_A)
        finally:
            reg.write_bytes(good)

    def test_12_media_cleanup_never_touches_files_outside_the_inbox(self):
        outside = self.sb / "outside.jpg"
        outside.write_bytes(b"keep me")
        link = self.sb / "inbox" / "link.jpg"
        link.symlink_to(outside)
        item = self._send(CHAT_A, image_path=str(outside), caption="x")
        self._wait_gone(item)
        item2 = self._send(CHAT_A, image_path=str(link), caption="y")
        self._wait_gone(item2)
        self.assertTrue(outside.exists() and outside.read_bytes() == b"keep me")
        self._no_engine(CHAT_A)

    def test_13_a_disabled_plugin_never_gets_the_text(self):
        reg = self.sb / "home" / "tenants" / "_default" / "plugins" / "registry.yaml"
        reg.parent.mkdir(parents=True, exist_ok=True)
        reg.write_text("plugins:\n  plug-a: {plugin_id: plug-a, enabled: false}\n", encoding="utf-8")
        before = len(self._received("plug-a"))
        try:
            item = self._send(CHAT_A, text="while the app is disabled")
            self._wait_gone(item)
            self.assertEqual(len(self._received("plug-a")), before)
            self.assertIn("plugin_disabled", self._dropped_reasons())
            self._no_engine(CHAT_A)
        finally:
            reg.unlink()

    def test_08_the_operator_cli_never_prints_a_full_chat_id(self):
        r = subprocess.run([sys.executable, str(ROOT / "bindings_cli.py"), "list"], env=self.env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("plug-a", r.stdout)
        for chat in (CHAT_A, CHAT_B):
            self.assertNotIn(chat, r.stdout)


if __name__ == "__main__":
    unittest.main()
