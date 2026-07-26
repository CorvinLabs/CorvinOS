"""Tests for the bundled bridge supervisors (ADR-0238/0243).

The properties under test are the ones that cost real money when they break, not
the getters:

* the feature flag ships dark, and off is a QUIET path — no daemon, no exception,
  no red health tile,
* a daemon that is already running (systemd, bridge.sh, a hand start) is never
  duplicated, and a probe that CANNOT answer refuses to start rather than
  guessing,
* shutdown is bounded: SIGTERM first, SIGKILL only after the grace period, and a
  child that refuses to die is abandoned instead of blocking the shutdown path,
* a dead daemon is visible in health_check() rather than silently gone,
* registration lands on layer=bundled and stays disableable,
* no bot token from settings.json ever reaches a log line or an audit detail.

NOTHING here starts a real Node process: subprocess.Popen is mocked throughout.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
_PKG = _HERE.parents[1]
for _p in (str(_PKG), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from corvin_plugins.bridges import (  # noqa: E402
    BRIDGE_CHANNELS,
    BRIDGE_PLUGIN_CLASSES,
    BridgeSupervisorPlugin,
    DiscordBridgePlugin,
    declaration_entries,
    declaration_entry,
)
from corvin_plugins.bridges import supervisor as sup  # noqa: E402
from corvin_plugins.manifest import BootLayer  # noqa: E402
from corvin_plugins.protocol import PluginContext  # noqa: E402
from corvin_plugins.registry import PluginRegistry  # noqa: E402

#: A token shape that must never appear in a log line or an audit detail.
FAKE_TOKEN = "MTIzNDU2Nzg5MDEyMzQ1Njc4.GhIjKl.FAKE-DISCORD-TOKEN-do-not-leak"


# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakeBridgeManager:
    """Stand-in for operator/bridges/bridge_manager.py.

    Carries a settings dict holding FAKE_TOKEN so the leak test can assert the
    supervisor never reads a credential VALUE — only the boolean answer of
    channel_configured().
    """

    def __init__(self, tmp: Path, *, configured=True, running=None, node="/usr/bin/node",
                 provisioned=True, adapter_pid=4242):
        self.tmp = tmp
        self._configured = configured
        self._running = running or {"running": False, "via": "", "pid": 0,
                                    "confident": True}
        self._node = node
        self._provisioned = provisioned
        self._adapter_pid = adapter_pid
        self.settings = {"discord_token": FAKE_TOKEN}
        self.service_env = {"CORVIN_HOME": str(tmp)}
        self.calls: list[str] = []
        if provisioned:
            d = self._rt("discord")
            d.mkdir(parents=True, exist_ok=True)
            (d / "daemon.js").write_text("// fake daemon", encoding="utf-8")

    def _rt(self, channel: str) -> Path:
        return self.tmp / "runtime" / channel

    # — the surface the supervisor uses —
    def _runtime_channel_dir(self, channel: str) -> Path:
        return self._rt(channel)

    def _source_channel_dir(self, channel: str) -> Path:
        return self.tmp / "source" / channel

    def channel_configured(self, channel: str) -> bool:
        self.calls.append(f"channel_configured:{channel}")
        return self._configured

    def channel_daemon_running(self, channel: str) -> dict:
        self.calls.append(f"channel_daemon_running:{channel}")
        return dict(self._running)

    def adapter_running_pid(self) -> int:
        return self._adapter_pid

    def find_node(self):
        return self._node

    def _load_service_env(self, env: dict) -> None:
        env.update(self.service_env)


class FakePopen:
    """subprocess.Popen stand-in with scriptable death."""

    def __init__(self, *args, **kwargs):
        self.args = args[0] if args else kwargs.get("args")
        self.kwargs = kwargs
        self.cwd = kwargs.get("cwd")
        self.env = kwargs.get("env") or {}
        self.pid = 31337
        self._rc = None
        self.terminated = False
        self.killed = False
        #: number of wait() calls that time out before the process "dies"
        self.wait_timeouts = 0
        self.wait_calls: list[float | None] = []

    def poll(self):
        return self._rc

    def die(self, rc: int = 1) -> None:
        self._rc = rc

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        if self.wait_timeouts > 0:
            self.wait_timeouts -= 1
            raise subprocess.TimeoutExpired(cmd="node", timeout=timeout or 0)
        self._rc = 0
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _ctx(plugin_id="discord-bridge", *, sink=None, config=None) -> PluginContext:
    def emit(event_type: str, details: dict) -> None:
        if sink is not None:
            sink.append((event_type, details))

    return PluginContext(
        plugin_id=plugin_id,
        tenant_id="test",
        corvin_home=Path("/tmp"),
        config=config or {},
        audit_emit=emit,
    )


class _SupervisorTestCase(unittest.TestCase):
    """Base: a tmp tree, a fake manager, mocked Popen, and a flag switch."""

    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

        # Each fake manager gets its own root: a plugin built with
        # provisioned=False must not see the daemon.js an earlier fake wrote.
        self._roots = 0
        self.bm = FakeBridgeManager(self._root())
        self.spawned: list[FakePopen] = []

        def _popen(*a, **kw):
            p = FakePopen(*a, **kw)
            self.spawned.append(p)
            return p

        popen_patch = mock.patch.object(sup.subprocess, "Popen", side_effect=_popen)
        popen_patch.start()
        self.addCleanup(popen_patch.stop)

        self._flag = True
        #: the unpatched implementation, for the tests that exercise it directly
        self.real_flag_enabled = sup._flag_enabled
        flag_patch = mock.patch.object(
            sup, "_flag_enabled", side_effect=lambda tenant_id: self._flag
        )
        flag_patch.start()
        self.addCleanup(flag_patch.stop)

        # os.killpg / os.getpgid would touch a real process group in the test
        # runner: the FakePopen pid does not exist, so getpgid would raise and
        # the signal ladder would silently fall through to the single-process
        # path — hiding exactly the behaviour under test.
        killpg_patch = mock.patch.object(sup.os, "killpg")
        self.killpg = killpg_patch.start()
        self.addCleanup(killpg_patch.stop)
        pgid_patch = mock.patch.object(sup.os, "getpgid", side_effect=lambda pid: pid)
        pgid_patch.start()
        self.addCleanup(pgid_patch.stop)

    def _root(self) -> Path:
        self._roots += 1
        root = self.tmp / f"bm{self._roots}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _plugin(self, **bm_over) -> BridgeSupervisorPlugin:
        if bm_over:
            self.bm = FakeBridgeManager(self._root(), **bm_over)
        return DiscordBridgePlugin(bridge_manager=self.bm)


# ── 1. Identity + declaration shape ───────────────────────────────────────────


class TestIdentity(unittest.TestCase):
    def test_all_seven_bundled_bridges_have_a_class(self):
        self.assertEqual(set(BRIDGE_PLUGIN_CLASSES), set(BRIDGE_CHANNELS))
        self.assertEqual(len(BRIDGE_CHANNELS), 7)

    def test_each_class_binds_its_own_channel_and_plugin_id(self):
        for channel, cls in BRIDGE_PLUGIN_CLASSES.items():
            with self.subTest(channel=channel):
                p = cls()
                self.assertEqual(p.channel, channel)
                self.assertEqual(p.plugin_id, f"{channel}-bridge")
                self.assertEqual(p.plugin_type, "bridge_channel")
                self.assertEqual(p.layer, "bundled")
                self.assertTrue(p.display_name)
                self.assertTrue(p.version)

    def test_plugin_type_is_a_known_type(self):
        from corvin_plugins.protocol import KNOWN_PLUGIN_TYPES

        self.assertIn(DiscordBridgePlugin().plugin_type, KNOWN_PLUGIN_TYPES)

    def test_unknown_channel_is_refused(self):
        with self.assertRaises(ValueError):
            BridgeSupervisorPlugin("mastodon")

    def test_base_class_without_channel_is_refused(self):
        with self.assertRaises(ValueError):
            BridgeSupervisorPlugin()

    def test_declaration_entry_is_bundled_and_resolvable(self):
        from corvin_plugins.loader import load_from_class_path

        entry = declaration_entry("discord")
        self.assertEqual(entry["id"], "discord-bridge")
        self.assertEqual(entry["layer"], "bundled")
        cls = load_from_class_path(entry["class_path"])
        self.assertIs(cls, DiscordBridgePlugin)

    def test_declaration_entries_cover_every_bridge(self):
        entries = declaration_entries()
        self.assertEqual(len(entries), 7)
        self.assertEqual(
            {e["id"] for e in entries}, {f"{c}-bridge" for c in BRIDGE_CHANNELS}
        )
        self.assertTrue(all(e["layer"] == "bundled" for e in entries))

    def test_disabled_declaration_carries_the_off_switch(self):
        entry = declaration_entry("slack", enabled=False)
        self.assertIs(entry["config"]["enabled"], False)


# ── 2. Flag off — the quiet path ──────────────────────────────────────────────


class TestFlagOff(_SupervisorTestCase):
    def test_flag_off_starts_nothing_and_does_not_raise(self):
        self._flag = False
        p = self._plugin()
        p.on_load(_ctx())  # must not raise
        self.assertEqual(self.spawned, [])

    def test_flag_off_health_is_ok_and_says_off(self):
        self._flag = False
        p = self._plugin()
        p.on_load(_ctx())
        h = p.health_check()
        self.assertTrue(h.ok)
        self.assertIn("off", h.message.lower())
        self.assertEqual(h.details["state"], "flag_off")

    def test_flag_off_does_not_even_probe_the_bridge(self):
        # The point of a dark flag is that the old code path is untouched: no
        # settings read, no process scan, no systemd call.
        self._flag = False
        p = self._plugin()
        p.on_load(_ctx())
        self.assertEqual(self.bm.calls, [])

    def test_flag_off_unload_is_a_no_op(self):
        self._flag = False
        p = self._plugin()
        p.on_load(_ctx())
        p.on_unload()  # must not raise
        self.assertEqual(self.spawned, [])

    def test_flag_defaults_to_off_when_the_console_is_absent(self):
        # core/plugins must stay importable without corvin_console; a flag that
        # cannot be read is an OFF flag, never an on one.
        with mock.patch.object(sup, "_load_is_enabled", return_value=None):
            self.assertFalse(self.real_flag_enabled("test"))

    def test_flag_read_error_is_off_not_a_crash(self):
        def boom(*_a, **_k):
            raise RuntimeError("overlay unreadable")

        with mock.patch.object(sup, "_load_is_enabled", return_value=boom):
            self.assertFalse(self.real_flag_enabled("test"))

    def test_the_real_flag_is_registered_and_ships_off(self):
        # A flag id that the Console registry does not know would silently read
        # False forever — the feature would be undeliverable, not just dark.
        is_enabled = sup._load_is_enabled()
        if is_enabled is None:
            self.skipTest("Console package not importable in this layout")
        from corvin_console.feature_flags import flag

        self.assertIs(flag(sup.FLAG_ID).default, False)


# ── 3. Flag on — the start path ───────────────────────────────────────────────


class TestStart(_SupervisorTestCase):
    def test_flag_on_and_configured_starts_the_daemon(self):
        p = self._plugin()
        p.on_load(_ctx())
        self.assertEqual(len(self.spawned), 1)

    def test_spawn_uses_the_right_argv_and_cwd(self):
        p = self._plugin()
        p.on_load(_ctx())
        proc = self.spawned[0]
        rt = self.bm.tmp / "runtime" / "discord"
        self.assertEqual(proc.args, ["/usr/bin/node", str(rt / "daemon.js")])
        self.assertEqual(proc.cwd, str(rt))

    def test_spawn_gets_its_own_process_group_on_posix(self):
        # Without it, on_unload can only signal the parent and a forked helper
        # (WhatsApp/Baileys) survives as an orphan.
        p = self._plugin()
        p.on_load(_ctx())
        if sys.platform != "win32":
            self.assertTrue(self.spawned[0].kwargs.get("start_new_session"))

    def test_service_env_is_merged_into_the_daemon_env(self):
        p = self._plugin()
        p.on_load(_ctx())
        self.assertEqual(self.spawned[0].env.get("CORVIN_HOME"), str(self.bm.tmp))

    def test_start_emits_an_audit_event_with_the_channel_and_pid(self):
        sink: list = []
        p = self._plugin()
        p.on_load(_ctx(sink=sink))
        started = [d for e, d in sink if e == "bridge.supervisor.started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["channel"], "discord")
        self.assertEqual(started[0]["pid"], 31337)

    def test_explicit_disable_in_config_starts_nothing(self):
        p = self._plugin()
        p.on_load(_ctx(config={"enabled": False}))
        self.assertEqual(self.spawned, [])
        h = p.health_check()
        self.assertTrue(h.ok)
        self.assertEqual(h.details["state"], "disabled_in_config")

    def test_unconfigured_bridge_starts_nothing_and_stays_green(self):
        p = self._plugin(configured=False)
        p.on_load(_ctx())
        self.assertEqual(self.spawned, [])
        h = p.health_check()
        self.assertTrue(h.ok)
        self.assertEqual(h.details["state"], "not_configured")

    def test_unprovisioned_runtime_dir_starts_nothing(self):
        # on_load runs inside the platform boot; it must never trigger an
        # npm install, so an unprovisioned bridge is simply skipped.
        p = self._plugin(provisioned=False)
        p.on_load(_ctx())
        self.assertEqual(self.spawned, [])
        self.assertTrue(p.health_check().ok)

    def test_missing_node_starts_nothing_and_stays_green(self):
        p = self._plugin(node=None)
        p.on_load(_ctx())
        self.assertEqual(self.spawned, [])
        h = p.health_check()
        self.assertTrue(h.ok)
        self.assertEqual(h.details["state"], "node_missing")

    def test_spawn_failure_is_visible_not_raised(self):
        p = self._plugin()
        with mock.patch.object(sup.subprocess, "Popen", side_effect=OSError("ENOENT")):
            p.on_load(_ctx())  # must not raise
        h = p.health_check()
        self.assertFalse(h.ok)
        self.assertEqual(h.details["state"], "spawn_failed")

    def test_a_bridge_manager_missing_a_path_helper_does_not_break_the_boot(self):
        # A vendored/older bridge_manager may not expose every private helper.
        # An AttributeError here would propagate out of on_load and cost the
        # whole platform its boot, not just this bridge.
        p = self._plugin()
        self.bm._runtime_channel_dir = None
        self.bm._source_channel_dir = None
        p.on_load(_ctx())  # must not raise
        self.assertEqual(self.spawned, [])
        self.assertTrue(p.health_check().ok)

    def test_a_raising_path_helper_does_not_break_the_boot(self):
        p = self._plugin()
        self.bm._runtime_channel_dir = mock.Mock(side_effect=RuntimeError("boom"))
        self.bm._source_channel_dir = mock.Mock(side_effect=RuntimeError("boom"))
        p.on_load(_ctx())  # must not raise
        self.assertEqual(self.spawned, [])

    def test_the_parent_does_not_keep_the_daemon_logfile_open(self):
        # One leaked descriptor per supervised bridge per reload adds up.
        p = self._plugin()
        opened: list = []
        real_open = open

        def _tracking_open(*a, **kw):
            fh = real_open(*a, **kw)
            opened.append(fh)
            return fh

        with mock.patch("builtins.open", side_effect=_tracking_open):
            p.on_load(_ctx())
        self.assertTrue(opened)
        self.assertTrue(all(fh.closed for fh in opened))

    def test_missing_bridge_manager_is_visible_not_raised(self):
        p = DiscordBridgePlugin()
        with mock.patch.object(sup, "_import_bridge_manager", return_value=None):
            p.on_load(_ctx())
        h = p.health_check()
        self.assertFalse(h.ok)
        self.assertEqual(h.details["state"], "manager_missing")


# ── 4. Duplicate start — the load-bearing invariant ───────────────────────────


class TestNoDoubleStart(_SupervisorTestCase):
    def test_daemon_already_running_is_not_started_again(self):
        p = self._plugin(running={"running": True, "via": "systemd", "pid": 99,
                                  "confident": True})
        p.on_load(_ctx())
        self.assertEqual(self.spawned, [])
        h = p.health_check()
        self.assertTrue(h.ok)
        self.assertTrue(h.details["managed_externally"])

    def test_every_running_source_is_honoured(self):
        for via in ("systemd", "port", "process", "supervisor"):
            with self.subTest(via=via):
                p = self._plugin(running={"running": True, "via": via, "pid": 7,
                                          "confident": True})
                self.spawned.clear()
                p.on_load(_ctx())
                self.assertEqual(self.spawned, [])

    def test_a_second_on_load_does_not_spawn_a_second_daemon(self):
        p = self._plugin()
        p.on_load(_ctx())
        self.assertEqual(len(self.spawned), 1)
        p.on_load(_ctx())  # e.g. a hot-reload that re-registers the plugin
        self.assertEqual(len(self.spawned), 1)

    def test_a_second_on_load_keeps_the_daemon_ours(self):
        # If the re-load reclassified our own daemon as "externally managed",
        # health would consult the wrong probe and on_unload would stop killing
        # a process we are responsible for.
        p = self._plugin()
        p.on_load(_ctx())
        p.on_load(_ctx())
        self.assertEqual(p.health_check().details["state"], "starting")
        p.on_unload()
        self.assertEqual(self.killpg.call_count, 1)

    def test_unverifiable_probe_refuses_to_start(self):
        # No /proc, no pgrep, no wmic: "I found nothing" is NOT "nothing runs".
        p = self._plugin(running={"running": False, "via": "", "pid": 0,
                                  "confident": False})
        p.on_load(_ctx())
        self.assertEqual(self.spawned, [])
        h = p.health_check()
        self.assertFalse(h.ok)
        self.assertIn("verify", h.message.lower())

    def test_probe_exception_refuses_to_start(self):
        p = self._plugin()
        self.bm.channel_daemon_running = mock.Mock(side_effect=RuntimeError("boom"))
        p.on_load(_ctx())
        self.assertEqual(self.spawned, [])
        self.assertFalse(p.health_check().ok)

    def test_bridge_manager_without_the_probe_refuses_to_start(self):
        # An older vendored bridge_manager has no channel_daemon_running().
        p = self._plugin()
        self.bm.channel_daemon_running = None
        p.on_load(_ctx())
        self.assertEqual(self.spawned, [])
        self.assertFalse(p.health_check().ok)


# ── 5. Shutdown — bounded, never hanging ──────────────────────────────────────


class TestUnload(_SupervisorTestCase):
    def test_sigterm_first_then_no_kill_when_the_daemon_obeys(self):
        p = self._plugin()
        p.on_load(_ctx())
        proc = self.spawned[0]
        p.on_unload()
        self.assertEqual(self.killpg.call_count, 1)
        self.assertEqual(self.killpg.call_args[0][1], sup.signal.SIGTERM)
        self.assertFalse(proc.killed)

    def test_sigkill_only_after_the_grace_period_expires(self):
        p = self._plugin()
        p.on_load(_ctx())
        proc = self.spawned[0]
        proc.wait_timeouts = 1  # ignore the SIGTERM once
        p.on_unload()
        signals = [c[0][1] for c in self.killpg.call_args_list]
        self.assertEqual(signals, [sup.signal.SIGTERM, sup.signal.SIGKILL])
        # The first wait used the documented grace period, not an unbounded one.
        self.assertEqual(proc.wait_calls[0], sup.STOP_GRACE_S)

    def test_unkillable_child_is_abandoned_not_waited_on_forever(self):
        # A SIGTERM hang once took down every live session in this repo.
        p = self._plugin()
        p.on_load(_ctx())
        proc = self.spawned[0]
        proc.wait_timeouts = 99
        sink: list = []
        p._ctx = _ctx(sink=sink)
        p.on_unload()  # must return
        stopped = [d for e, d in sink if e == "bridge.supervisor.stopped"]
        self.assertEqual(stopped[0]["how"], "abandoned")
        self.assertLessEqual(len(proc.wait_calls), 2)

    def test_every_wait_is_bounded(self):
        p = self._plugin()
        p.on_load(_ctx())
        proc = self.spawned[0]
        proc.wait_timeouts = 99
        p.on_unload()
        self.assertTrue(all(t is not None and t > 0 for t in proc.wait_calls))

    def test_unload_without_a_start_is_a_no_op(self):
        p = self._plugin()
        p.on_unload()  # never loaded — must not raise
        self.assertEqual(self.killpg.call_count, 0)

    def test_externally_managed_daemon_is_not_killed_on_unload(self):
        # We did not start it, its owner is still running: stopping it here turns
        # a plugin hot-reload into an outage.
        p = self._plugin(running={"running": True, "via": "systemd", "pid": 99,
                                  "confident": True})
        p.on_load(_ctx())
        p.on_unload()
        self.assertEqual(self.killpg.call_count, 0)

    def test_already_exited_daemon_is_reported_not_signalled(self):
        p = self._plugin()
        p.on_load(_ctx())
        self.spawned[0].die(0)
        sink: list = []
        p._ctx = _ctx(sink=sink)
        p.on_unload()
        stopped = [d for e, d in sink if e == "bridge.supervisor.stopped"]
        self.assertEqual(stopped[0]["how"], "already_exited")
        self.assertEqual(self.killpg.call_count, 0)

    def test_unload_clears_the_handle_so_a_reload_can_start_again(self):
        p = self._plugin()
        p.on_load(_ctx())
        p.on_unload()
        p.on_load(_ctx())
        self.assertEqual(len(self.spawned), 2)


# ── 6. Health — a dead daemon must be loud ────────────────────────────────────


class TestHealth(_SupervisorTestCase):
    def test_dead_daemon_is_not_ok_and_carries_the_exit_code(self):
        p = self._plugin()
        p.on_load(_ctx())
        p._started_at -= 60  # past the "starting" window
        self.spawned[0].die(1)
        h = p.health_check()
        self.assertFalse(h.ok)
        self.assertIn("exited", h.message)
        self.assertEqual(h.details["exit_code"], 1)

    def test_a_daemon_that_dies_immediately_is_still_not_ok(self):
        p = self._plugin()
        p.on_load(_ctx())
        self.spawned[0].die(127)
        self.assertFalse(p.health_check().ok)

    def test_health_check_reaps_so_the_daemon_is_never_a_zombie(self):
        p = self._plugin()
        p.on_load(_ctx())
        proc = self.spawned[0]
        proc.die(0)
        with mock.patch.object(proc, "poll", wraps=proc.poll) as polled:
            p.health_check()
            self.assertTrue(polled.called)

    def test_fresh_daemon_reports_starting(self):
        p = self._plugin()
        p.on_load(_ctx())
        h = p.health_check()
        self.assertTrue(h.ok)
        self.assertEqual(h.details["state"], "starting")

    def test_settled_daemon_reports_running(self):
        p = self._plugin()
        p.on_load(_ctx())
        p._started_at -= sup.MIN_UPTIME_S + 1
        h = p.health_check()
        self.assertTrue(h.ok)
        self.assertEqual(h.message, "daemon running")

    def test_half_bridge_without_an_adapter_is_not_ok(self):
        # Daemon up, nothing polling the queue: the bot receives everything and
        # answers nothing, silently.
        p = self._plugin(adapter_pid=0)
        p.on_load(_ctx())
        p._started_at -= sup.MIN_UPTIME_S + 1
        h = p.health_check()
        self.assertFalse(h.ok)
        self.assertIn("adapter", h.message)

    def test_vanished_external_daemon_is_not_ok(self):
        p = self._plugin(running={"running": True, "via": "systemd", "pid": 9,
                                  "confident": True})
        p.on_load(_ctx())
        self.bm._running = {"running": False, "via": "", "pid": 0, "confident": True}
        h = p.health_check()
        self.assertFalse(h.ok)

    def test_health_check_before_on_load_is_ok(self):
        h = DiscordBridgePlugin().health_check()
        self.assertTrue(h.ok)

    def test_health_message_never_carries_a_filesystem_path(self):
        # A path carries the OS username; the message reaches the audit chain.
        for over in ({}, {"configured": False}, {"node": None}, {"provisioned": False}):
            with self.subTest(over=over):
                p = self._plugin(**over)
                p.on_load(_ctx())
                self.assertNotIn("/", p.health_check().message)


# ── 7. Registry integration ───────────────────────────────────────────────────


class TestRegistryIntegration(_SupervisorTestCase):
    def test_registration_lands_on_the_bundled_layer(self):
        reg = PluginRegistry()
        p = self._plugin()
        reg.register(p, _ctx(), layer=BootLayer.BUNDLED)
        self.assertIs(reg.boot_layer_of("discord-bridge"), BootLayer.BUNDLED)

    def test_self_declared_layer_is_bundled_without_an_explicit_argument(self):
        reg = PluginRegistry()
        reg.register(self._plugin(), _ctx())
        self.assertIs(reg.boot_layer_of("discord-bridge"), BootLayer.BUNDLED)

    def test_a_bridge_is_disableable(self):
        reg = PluginRegistry()
        p = self._plugin()
        reg.register(p, _ctx(), layer=BootLayer.BUNDLED)
        self.assertTrue(reg.can_disable("discord-bridge"))
        reg.disable("discord-bridge")
        self.assertEqual(self.killpg.call_count, 1)

    def test_registry_health_check_reports_the_bridge(self):
        reg = PluginRegistry()
        reg.register(self._plugin(), _ctx(), layer=BootLayer.BUNDLED)
        report = reg.health_check_all()
        self.assertIn("discord-bridge", report)

    def test_all_seven_can_be_registered_side_by_side(self):
        reg = PluginRegistry()
        for channel, cls in BRIDGE_PLUGIN_CLASSES.items():
            bm = FakeBridgeManager(self._root(), provisioned=False)
            reg.register(cls(bridge_manager=bm), _ctx(f"{channel}-bridge"),
                         layer=BootLayer.BUNDLED)
        self.assertEqual(
            len(reg.plugins_by_boot_layer(BootLayer.BUNDLED)), len(BRIDGE_CHANNELS)
        )


# ── 8. The declaration actually loads (call-site test) ────────────────────────


class TestDeclarativeBoot(_SupervisorTestCase):
    """Prove the tenant-config form REACHES the supervisor.

    Resolving the class path in isolation only proves the mechanism works when
    called. This drives the real ``bootstrap_declared`` path so a declaration
    that boot never picks up cannot pass as green.
    """

    def setUp(self):
        super().setUp()
        from corvin_plugins import bootstrap
        from corvin_plugins import registry as reg_mod

        # bootstrap builds its context with the REAL _default_audit_emit, which
        # resolves the hash-chained forge audit writer. There is no conftest
        # under core/plugins/tests that redirects VOICE_AUDIT_PATH, so an
        # unpatched run of this class writes permanent plugin.loaded records into
        # a live GDPR Art. 30 chain — and an append-only chain cannot be cleaned
        # up afterwards without breaking its hashes. Verified the hard way:
        # 28 such records from an early draft of this test are now permanently in
        # .corvin/global/forge/audit.jsonl. Redirect BEFORE the first boot.
        self.audit: list = []
        emit_patch = mock.patch.object(
            bootstrap, "_default_audit_emit",
            side_effect=lambda tenant_id: (
                lambda event, details: self.audit.append((event, details))
            ),
        )
        emit_patch.start()
        self.addCleanup(emit_patch.stop)

        self.reg = reg_mod.get_registry()
        self.addCleanup(self._drop_registered)

    def _drop_registered(self):
        for pid in list(self.reg.discover()):
            if pid.endswith("-bridge"):
                try:
                    self.reg.unregister(pid)
                except Exception:  # noqa: BLE001
                    pass

    def _boot(self, entries):
        from corvin_plugins import bootstrap

        return bootstrap.bootstrap_declared(
            tenant_id="test",
            corvin_home=self.tmp,
            tenant_config={"spec": {"plugins": {"installed": entries}}},
        )

    def test_a_declared_bridge_loads_on_the_bundled_layer(self):
        self._flag = False  # no daemon; we are testing the wiring, not the spawn
        loaded = self._boot([declaration_entry("discord")])
        self.assertIn("discord-bridge", loaded)
        self.assertIs(self.reg.boot_layer_of("discord-bridge"), BootLayer.BUNDLED)
        self.assertTrue(self.reg.can_disable("discord-bridge"))
        loaded_events = [d for e, d in self.audit if e == "plugin.loaded"]
        self.assertEqual(loaded_events[0]["layer"], "bundled")

    def test_the_boot_path_never_reaches_the_real_audit_writer(self):
        # Guard for the isolation itself: if a refactor reinstates the real
        # emit, this class silently starts writing into a live hash chain again.
        self._flag = False
        try:
            import audit  # type: ignore[import-not-found]
        except ImportError:
            self.skipTest("audit module not importable in this layout")
        with mock.patch.object(audit, "audit_event") as real_writer:
            self._boot([declaration_entry("discord")])
        real_writer.assert_not_called()
        self.assertTrue([e for e, _d in self.audit if e == "plugin.loaded"])

    def test_all_seven_declarations_load(self):
        self._flag = False
        loaded = self._boot(declaration_entries())
        self.assertEqual(set(loaded), {f"{c}-bridge" for c in BRIDGE_CHANNELS})

    def test_a_declaration_claiming_a_privileged_layer_is_downgraded(self):
        # A tenant config is operator-writable: it may say "bundled", never
        # "compliance" — that would make the bridge undisableable.
        self._flag = False
        entry = declaration_entry("discord")
        entry["layer"] = "compliance"
        self._boot([entry])
        self.assertIs(self.reg.boot_layer_of("discord-bridge"), BootLayer.INSTALLED)

    def test_declared_bridge_starts_nothing_while_the_flag_is_off(self):
        self._flag = False
        self._boot(declaration_entries())
        self.assertEqual(self.spawned, [])


# ── 9. Secrets must not leak ──────────────────────────────────────────────────


class TestNoSecretLeak(_SupervisorTestCase):
    def _all_text(self, sink, logs) -> str:
        return " ".join(
            [str(logs)]
            + [f"{e} {d}" for e, d in sink]
        )

    def test_no_token_in_audit_details_or_logs_on_the_happy_path(self):
        sink: list = []
        p = self._plugin()
        with self.assertLogs("corvin.plugins.bridges", level="DEBUG") as cm:
            p.on_load(_ctx(sink=sink))
            p.on_unload()
        blob = self._all_text(sink, cm.output)
        self.assertNotIn(FAKE_TOKEN, blob)
        self.assertNotIn("discord_token", blob)

    def test_no_token_in_audit_details_or_logs_on_every_skip_path(self):
        cases = [
            {"configured": False},
            {"node": None},
            {"provisioned": False},
            {"running": {"running": True, "via": "systemd", "pid": 1,
                         "confident": True}},
            {"running": {"running": False, "via": "", "pid": 0, "confident": False}},
        ]
        for over in cases:
            with self.subTest(over=over):
                sink: list = []
                p = self._plugin(**over)
                with self.assertLogs("corvin.plugins.bridges", level="DEBUG") as cm:
                    p.on_load(_ctx(sink=sink))
                blob = self._all_text(sink, cm.output)
                self.assertNotIn(FAKE_TOKEN, blob)

    def test_the_supervisor_never_reads_a_credential_value(self):
        # It only ever asks the boolean question. If this changes, a token could
        # reach a log line the next time someone adds a debug print.
        p = self._plugin()
        p.on_load(_ctx())
        self.assertIn("channel_configured:discord", self.bm.calls)

    def test_health_details_carry_no_token(self):
        p = self._plugin()
        p.on_load(_ctx())
        h = p.health_check()
        self.assertNotIn(FAKE_TOKEN, str(h.details) + h.message)

    def test_audit_details_are_a_closed_set_of_keys(self):
        sink: list = []
        p = self._plugin()
        p.on_load(_ctx(sink=sink))
        p.on_unload()
        allowed = {"channel", "plugin_id", "pid", "reason", "via", "how",
                   "error_type"}
        for _event, details in sink:
            self.assertLessEqual(set(details), allowed, msg=str(details))


# ── 10. bridge_manager probe (the additive dock) ──────────────────────────────


class TestBridgeManagerProbe(unittest.TestCase):
    """The generic 'is a daemon already running' probe added to bridge_manager."""

    @classmethod
    def setUpClass(cls):
        bm_dir = _REPO / "operator" / "bridges"
        if str(bm_dir) not in sys.path:
            sys.path.insert(0, str(bm_dir))
        import bridge_manager  # type: ignore[import-not-found]

        cls.bm = bridge_manager

    def test_cmdline_matcher_is_path_separator_agnostic(self):
        for line in (
            "/usr/bin/node /home/u/.corvin/bridges/discord/daemon.js",
            r"C:\node.exe C:\Users\u\.corvin\bridges\discord\daemon.js",
            "node /repo/operator/bridges/DISCORD/daemon.js",
        ):
            with self.subTest(line=line):
                self.assertTrue(self.bm._cmdline_names_daemon(line, "discord"))

    def test_cmdline_matcher_does_not_confuse_channels(self):
        line = "/usr/bin/node /home/u/.corvin/bridges/telegram/daemon.js"
        self.assertFalse(self.bm._cmdline_names_daemon(line, "discord"))
        self.assertTrue(self.bm._cmdline_names_daemon(line, "telegram"))

    def test_a_mere_channel_mention_is_not_a_running_daemon(self):
        self.assertFalse(
            self.bm._cmdline_names_daemon("tail -f /var/log/discord.log", "discord")
        )
        self.assertFalse(self.bm._cmdline_names_daemon("", "discord"))

    def test_systemd_active_short_circuits_the_scan(self):
        with mock.patch.object(self.bm, "_systemd_unit_active", return_value=True), \
             mock.patch.object(self.bm, "_scan_channel_daemon_pid") as scan:
            result = self.bm.channel_daemon_running("discord")
        self.assertTrue(result["running"])
        self.assertEqual(result["via"], "systemd")
        scan.assert_not_called()

    def test_bound_port_counts_as_running(self):
        with mock.patch.object(self.bm, "_systemd_unit_active", return_value=False), \
             mock.patch.object(self.bm, "_port_open", return_value=True):
            result = self.bm.channel_daemon_running("whatsapp")
        self.assertTrue(result["running"])
        self.assertEqual(result["via"], "port")

    def test_process_scan_result_is_reported_with_its_pid(self):
        with mock.patch.object(self.bm, "_systemd_unit_active", return_value=False), \
             mock.patch.object(self.bm, "_scan_channel_daemon_pid",
                               return_value=(4711, True)):
            result = self.bm.channel_daemon_running("discord")
        self.assertEqual(result, {"running": True, "via": "process", "pid": 4711,
                                  "confident": True})

    def test_nothing_running_is_confident_when_the_scan_worked(self):
        with mock.patch.object(self.bm, "_systemd_unit_active", return_value=False), \
             mock.patch.object(self.bm, "_scan_channel_daemon_pid",
                               return_value=(0, True)):
            result = self.bm.channel_daemon_running("discord")
        self.assertFalse(result["running"])
        self.assertTrue(result["confident"])

    def test_a_scan_that_could_not_run_is_not_confident(self):
        with mock.patch.object(self.bm, "_systemd_unit_active", return_value=False), \
             mock.patch.object(self.bm, "_scan_channel_daemon_pid",
                               return_value=(0, False)):
            result = self.bm.channel_daemon_running("discord")
        self.assertFalse(result["running"])
        self.assertFalse(result["confident"])

    def test_the_real_scan_finds_no_daemon_for_a_bogus_channel(self):
        # Exercises the real /proc (or pgrep) path once, without starting a node.
        pid, confident = self.bm._scan_channel_daemon_pid("nosuchchannel")
        self.assertEqual(pid, 0)
        self.assertTrue(confident)

    def test_adapter_running_pid_is_a_public_wrapper(self):
        with mock.patch.object(self.bm, "_adapter_running_pid", return_value=17) as inner:
            self.assertEqual(self.bm.adapter_running_pid(), 17)
        self.assertTrue(inner.called)


if __name__ == "__main__":
    unittest.main()
