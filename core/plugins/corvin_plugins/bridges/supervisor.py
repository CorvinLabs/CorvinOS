"""Supervisor plugins for the bundled Node bridge daemons (ADR-0238/0243).

What this module is NOT
-----------------------
It is not a rewrite of the bridges. ADR-0238/0242 originally described the seven
bridges as Python modules under ``adapters/<name>_adapter`` that would be
refactored into ``CorvinPlugin`` classes. That description was wrong and the ADRs
have been corrected: every bridge is a **Node.js daemon** at
``operator/bridges/<channel>/daemon.js``, and ``adapters/discord_adapter`` never
existed. Rewriting seven working daemons in Python to satisfy a plugin protocol
would trade a shipped, battle-tested transport layer for a green field — so the
plugin here **supervises** the existing daemon as a subprocess instead.

What a supervisor plugin does
-----------------------------
One instance per channel (``discord``, ``slack``, ``telegram``, ``whatsapp``,
``signal``, ``teams``, ``email``). ``on_load()`` may start
``node <runtime>/<channel>/daemon.js``; ``on_unload()`` stops it with a bounded
SIGTERM→SIGKILL ladder; ``health_check()`` reports whether the daemon is alive.

All process knowledge (home resolution, runtime vs. source dir, service.env
merge, node discovery, systemd probe) is **borrowed from**
``operator/bridges/bridge_manager.py`` rather than reimplemented — a second copy
of "where does this daemon live" is exactly the reader≠writer split that has
already cost this repo two incidents.

Start gate (every condition must hold, each failure is a QUIET no-op)
--------------------------------------------------------------------
1. Feature flag ``bridge_supervisor_plugins`` is on. Off is the default and the
   shipped behaviour: bridges keep being managed exactly as they are today, by
   ``bridge.sh`` / systemd / the Console button. The flag is read defensively —
   if ``corvin_console`` is not importable at all (headless core, wheel without
   the Console) the flag reads *false* and this plugin does nothing.
2. The declaration is not explicitly switched off (``config.enabled: false``).
3. The channel has credentials (``bridge_manager.channel_configured``). A daemon
   started without a token dies on boot in a loop; WhatsApp additionally needs
   its pairing QR, which is a Console flow, so an unpaired WhatsApp stays a
   no-op here.
4. No daemon for this channel is already running — see "Duplicate start".
5. The runtime dir is already provisioned (``daemon.js`` present next to a
   ``node_modules``). This plugin deliberately does **not** run ``npm install``:
   ``on_load()`` executes inside the platform boot, and a one-minute npm install
   there would stall the whole start. Provisioning stays with the existing
   ``bridge.sh`` / Console path.
6. A usable Node (>=20) is already on the box. ``find_node()``, never
   ``ensure_node()`` — booting must not trigger a 25 MB download.

Duplicate start — the load-bearing invariant
--------------------------------------------
Two Discord daemons polling the same outbox answer every message twice, and the
second one is invisible to ``systemctl stop`` (ADR-0215 orphan class). So
"already running" is probed **before** every spawn, through
``bridge_manager.channel_daemon_running()``, which layers four independent
signals:

* our own handle from a previous ``on_load()`` in this process,
* ``systemctl --user is-active corvin-voice-bridge-<channel>.service`` — also
  true while the unit is *activating*, which closes the race window where a
  systemd-started daemon has not bound its port yet,
* a TCP probe of the channel's well-known local port (WhatsApp's pairing port
  today),
* a system-wide scan for a live process whose command line names
  ``<channel>/daemon.js`` — this is the generic one and it catches daemons
  started by ``bridge.sh``, by systemd, or by hand, none of which write a pidfile
  we own.

If **none** of those probes can run (no ``/proc``, no ``pgrep``, no ``wmic``) the
probe reports ``confident=False`` and this plugin **refuses to start** and says so
in ``health_check()``. Refusing to start costs an operator a bridge they can
still launch the old way; guessing wrong costs every user a duplicated
conversation.

Restart policy — deliberately none
-----------------------------------
A crashed daemon is **not** restarted automatically. The alternative was a
bounded restart ladder, and it was rejected:

* an auto-restart against a revoked token becomes a login loop against Discord's
  or Slack's API and gets the bot rate-limited or banned — the failure the
  operator sees is then worse than the one they had,
* systemd already supervises restarts on the path the maintainer actually uses;
  a second supervisor with its own opinion means two restart loops fighting over
  one daemon,
* every restart would emit an audit event, so a crash-looping bridge would spam
  the hash-chained trail,
* this repo's incident history is specifically about *automatic* lifecycle
  machinery failing silently (a wedged outbox poller delivered nothing for 38
  minutes without a single log line).

Instead a dead daemon is **loud**: ``health_check()`` returns ``ok=False`` with
the exit code, which reaches the Console health surface and the audit trail via
``plugin.health_alert``. Recovery is an explicit operator action — disable and
re-enable the plugin, or use ``bridge.sh`` / systemd.

Secrets
-------
``settings.json`` holds bot tokens, IMAP passwords and phone numbers. This module
never reads a credential *value*: ``channel_configured()`` answers a boolean, and
every log line and audit detail carries only the channel name, a closed-set
reason code and a pid. Daemon stdout goes to the daemon's own logfile in its
runtime dir and is never read back into an audit record — that output routinely
contains chat text and sender JIDs.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..protocol import HealthStatus, PluginContext

log = logging.getLogger("corvin.plugins.bridges")

#: Console feature flag gating the whole supervisor path. Default false.
FLAG_ID = "bridge_supervisor_plugins"

#: The seven bundled Node bridges, in the order bridge_manager lists them.
BRIDGE_CHANNELS: tuple[str, ...] = (
    "discord",
    "telegram",
    "whatsapp",
    "slack",
    "email",
    "signal",
    "teams",
)

#: Grace period between SIGTERM and SIGKILL, seconds. Bounded on purpose: a
#: shutdown that waits on an unresponsive child is how a SIGTERM hang once took
#: down every live session in this repo.
STOP_GRACE_S = 5.0
#: Extra wait to reap the process after SIGKILL. After this we give up and
#: return — on_unload never blocks the shutdown path indefinitely.
KILL_REAP_S = 2.0
#: Below this uptime a live daemon is reported as "starting" rather than
#: "running": it has not yet had the chance to crash on a bad credential.
MIN_UPTIME_S = 5.0


# ── bridge_manager resolution ─────────────────────────────────────────────────

def _import_bridge_manager() -> Any | None:
    """Import ``bridge_manager`` from the repo or the wheel's ``_vendor`` tree.

    Mirrors ``corvin_console.routes.setup._import_bridge_manager``. It is NOT
    imported from there: ``core/plugins`` must stay importable without the
    Console package (headless core, ADR-0234).

    ``operator/`` deliberately has no ``__init__.py`` — adding one shadows the
    stdlib ``operator`` module and has already killed the web service once — so
    the directory is put on ``sys.path`` and the module imported by bare name,
    exactly like every other caller does.
    """
    try:
        import bridge_manager  # type: ignore[import-not-found]
        return bridge_manager
    except ImportError:
        pass
    here = Path(__file__).resolve()
    # …/core/plugins/corvin_plugins/bridges/supervisor.py → repo root is parents[4]
    repo = here.parents[4]
    console_pkg = here.parents[3] / "console" / "corvin_console"
    for cand in (
        repo / "operator" / "bridges",
        console_pkg / "_vendor" / "operator" / "bridges",
    ):
        if not (cand / "bridge_manager.py").is_file():
            continue
        if str(cand) not in sys.path:
            # append, not insert(0): a bridges dir at the head of sys.path would
            # shadow same-named top-level modules for the whole process.
            sys.path.append(str(cand))
        try:
            import bridge_manager  # type: ignore[import-not-found]
            return bridge_manager
        except ImportError:
            continue
    return None


def _load_is_enabled() -> Any | None:
    """Return ``corvin_console.feature_flags.is_enabled``, or None if absent."""
    try:
        from corvin_console.feature_flags import is_enabled  # type: ignore[import-not-found]
        return is_enabled
    except ImportError:
        pass
    console = Path(__file__).resolve().parents[3] / "console"
    if console.is_dir() and str(console) not in sys.path:
        sys.path.append(str(console))
    try:
        from corvin_console.feature_flags import is_enabled  # type: ignore[import-not-found]
        return is_enabled
    except ImportError:
        return None


def _flag_enabled(tenant_id: str) -> bool:
    """Read the ``bridge_supervisor_plugins`` flag, defaulting to OFF.

    Every failure mode — Console package absent, flag unregistered, overlay
    unreadable — resolves to False. For a ship-dark flag that is the safe
    direction: an install that cannot answer the question keeps behaving exactly
    as it did before the feature existed.
    """
    is_enabled = _load_is_enabled()
    if is_enabled is None:
        return False
    try:
        return bool(is_enabled(FLAG_ID, tenant_id))
    except Exception:  # noqa: BLE001 — an unreadable flag is an off flag
        log.debug("feature flag %s could not be read — treating as off", FLAG_ID)
        return False


# ── The supervisor ────────────────────────────────────────────────────────────

class BridgeSupervisorPlugin:
    """Supervise ONE Node bridge daemon as a subprocess.

    Parameterised by channel name; the seven concrete classes below are thin
    subclasses so a tenant config can name a stable ``class_path`` per bridge.

    ``plugin_type`` is ``bridge_channel`` and ``boot_layer`` is ``bundled``.
    Bundled is disableable by design (``can_disable()`` is true for every boot
    layer except ``compliance``) — a messenger transport is not a compliance
    mechanism and an operator must be able to switch it off.

    Note what this plugin does *not* do: it never registers itself with
    ``ctx.channel_registry``. That registry expects an object that can send and
    receive messages; a process supervisor cannot, and handing it one would give
    callers a channel that silently drops everything.
    """

    plugin_type = "bridge_channel"
    version = "1.0.0"
    boot_layer = "bundled"
    #: Overridden by the concrete subclasses.
    channel = ""

    def __init__(
        self,
        channel: str | None = None,
        *,
        bridge_manager: Any | None = None,
    ) -> None:
        ch = channel or self.channel
        if not ch:
            raise ValueError("BridgeSupervisorPlugin needs a channel name")
        if ch not in BRIDGE_CHANNELS:
            raise ValueError(f"unknown bridge channel {ch!r}")
        self.channel = ch
        self.plugin_id = f"{ch}-bridge"
        self.display_name = f"{ch.capitalize()} bridge supervisor"
        self._bm_override = bridge_manager
        self._bm: Any | None = bridge_manager
        self._ctx: PluginContext | None = None
        self._proc: subprocess.Popen | None = None
        self._started_at: float = 0.0
        self._adopted_pid: int = 0
        self._state: str = "unloaded"
        self._detail: str = ""

    # ── helpers ───────────────────────────────────────────────────────────────

    def _manager(self) -> Any | None:
        if self._bm is None:
            self._bm = self._bm_override or _import_bridge_manager()
        return self._bm

    def _audit(self, event: str, details: dict) -> None:
        """Emit a lifecycle event. Channel name, reason code and pid only."""
        ctx = self._ctx
        if ctx is None:
            return
        try:
            ctx.audit_emit(event, {"channel": self.channel,
                                   "plugin_id": self.plugin_id, **details})
        except Exception:  # noqa: BLE001 — visibility must not break lifecycle
            log.debug("audit emit failed for %s", event)

    def _daemon_dir(self, bm: Any) -> Path | None:
        """Runtime dir if provisioned, else the source dir, else None.

        Runtime first because that is where a wheel install puts ``node_modules``
        (ADR-0130); the source dir is the fallback for source-tree installs where
        ``bridge.sh`` runs the daemon in place.

        Requires ``node_modules`` next to ``daemon.js`` (module docstring's
        start-gate #5), not ``daemon.js`` alone: a wheel install's vendored
        source dir (``corvin_console/_vendor/operator/bridges/<channel>``)
        always has ``daemon.js`` but can never have ``node_modules`` (it is
        deliberately never vendored, and site-packages is typically
        read-only besides). Accepting ``daemon.js`` alone made this "quiet
        no-op when unprovisioned" gate instead spawn a daemon that crashes
        on its first ``require()`` with no readable log (the vendored dir
        is where ``daemon-start.log`` would try to write, and often can't) —
        live-reported as "Discord bridge files completely missing" even
        though ``daemon.js``/``package.json`` were present in the vendored
        tree the whole time.
        """
        for name in ("_runtime_channel_dir", "_source_channel_dir"):
            getter = getattr(bm, name, None)
            if getter is None:
                continue
            try:
                d = Path(getter(self.channel))
                if (d / "daemon.js").is_file() and (d / "node_modules").is_dir():
                    return d
            except Exception:  # noqa: BLE001 — a probe must not break the boot
                continue
        return None

    def _quiet(self, state: str, detail: str = "") -> None:
        """Record an expected not-started state. Never an error."""
        self._state = state
        self._detail = detail
        log.debug("bridge supervisor %s: %s", self.channel, state)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Start the daemon iff every gate in the module docstring is satisfied.

        Never raises: a bridge that cannot start must cost the operator that
        bridge, not the platform boot. The reason is kept for ``health_check()``.
        """
        self._ctx = ctx
        self._state = "loaded"
        self._detail = ""

        if not _flag_enabled(ctx.tenant_id):
            self._quiet("flag_off")
            return

        cfg = ctx.config or {}
        if cfg.get("enabled") is False:
            self._quiet("disabled_in_config")
            self._audit("bridge.supervisor.skipped", {"reason": "disabled_in_config"})
            return

        if self._proc is not None and self._proc.poll() is None:
            # A repeated on_load() (hot-reload, double registration). Our own
            # daemon is still alive: keep managing it under the SAME state, or
            # health_check would start reporting it as an "external" daemon and
            # on_unload would stop treating it as ours to stop.
            self._state = "running"
            return

        bm = self._manager()
        if bm is None:
            self._state = "manager_missing"
            self._audit("bridge.supervisor.skipped", {"reason": "manager_missing"})
            log.error("bridge supervisor %s: bridge_manager not importable", self.channel)
            return

        try:
            configured = bool(bm.channel_configured(self.channel))
        except Exception as exc:  # noqa: BLE001 — class name only, paths leak
            self._state = "probe_failed"
            self._detail = type(exc).__name__
            self._audit("bridge.supervisor.skipped",
                        {"reason": "probe_failed", "error_type": type(exc).__name__})
            return
        if not configured:
            self._quiet("not_configured")
            return

        running = self._probe_running(bm)
        if not running.get("confident", False):
            self._state = "unverifiable"
            self._audit("bridge.supervisor.skipped", {"reason": "unverifiable"})
            log.error(
                "bridge supervisor %s: cannot verify whether a daemon is already "
                "running — refusing to start a possible duplicate", self.channel,
            )
            return
        if running.get("running"):
            self._adopted_pid = int(running.get("pid") or 0)
            self._quiet("already_running", str(running.get("via") or ""))
            self._audit("bridge.supervisor.skipped",
                        {"reason": "already_running", "via": running.get("via") or ""})
            return

        rt = self._daemon_dir(bm)
        if rt is None:
            self._quiet("not_provisioned")
            return

        try:
            node = bm.find_node()
        except Exception:  # noqa: BLE001
            node = None
        if not node:
            self._quiet("node_missing")
            return

        self._spawn(bm, node, rt)

    def _probe_running(self, bm: Any) -> dict:
        """Ask bridge_manager whether a daemon for this channel already runs.

        Our own live handle short-circuits the expensive scan; everything else
        goes to ``bridge_manager.channel_daemon_running()``.
        """
        if self._proc is not None and self._proc.poll() is None:
            return {"running": True, "via": "supervisor", "pid": self._proc.pid,
                    "confident": True}
        probe = getattr(bm, "channel_daemon_running", None)
        if probe is None:
            # An older vendored bridge_manager without the probe. Refusing is the
            # only safe answer — see the duplicate-start note in the module doc.
            return {"running": False, "confident": False}
        try:
            result = probe(self.channel)
        except Exception as exc:  # noqa: BLE001
            log.error("bridge supervisor %s: running-probe failed (%s)",
                      self.channel, type(exc).__name__)
            return {"running": False, "confident": False}
        return result if isinstance(result, dict) else {"running": False, "confident": False}

    def _spawn(self, bm: Any, node: str, rt: Path) -> None:
        """Launch the daemon, capturing its output to the daemon's own logfile."""
        env = os.environ.copy()
        try:
            bm._load_service_env(env)
        except Exception:  # noqa: BLE001
            pass
        # Prefer the validated node for anything the daemon itself spawns.
        env["PATH"] = str(Path(node).parent) + os.pathsep + env.get("PATH", "")
        # The daemon runs from the runtime dir (cwd=rt below) where only
        # shared/js is mirrored, so it cannot reach the Python CLIs its in-chat
        # commands shell out to by walking up from __dirname. bridge_manager's
        # own two spawn sites set the same variable; this is the third, and it
        # borrows the path from bridge_manager rather than deriving its own
        # (the module docstring's "never reimplement process knowledge" rule).
        try:
            env.setdefault(
                "CORVIN_BRIDGE_OPERATOR_ROOT", str(bm._BRIDGE_DIR.parent))
        except Exception:  # noqa: BLE001 — a missing hint only restores the old behaviour
            pass

        try:
            log_fh: Any = open(rt / "daemon-start.log", "ab")
        except OSError:
            log_fh = subprocess.DEVNULL
        kwargs: dict = {"stdout": log_fh, "stderr": subprocess.STDOUT}
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            kwargs["creationflags"] = 0x00000008 | 0x00000200
        else:
            # Own process group so on_unload can signal the whole tree — a
            # WhatsApp daemon forks helpers, and signalling only the parent
            # leaves them behind.
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [node, str(rt / "daemon.js")], cwd=str(rt), env=env, **kwargs
            )
        except OSError as exc:
            self._close(log_fh)
            self._state = "spawn_failed"
            self._detail = type(exc).__name__
            self._audit("bridge.supervisor.start_failed",
                        {"reason": "spawn_failed", "error_type": type(exc).__name__})
            log.error("bridge supervisor %s: spawn failed (%s)",
                      self.channel, type(exc).__name__)
            return
        # The child holds its own dup of the fd; keeping the parent's copy open
        # would leak one descriptor per supervised bridge per reload.
        self._close(log_fh)
        self._proc = proc
        self._started_at = time.monotonic()
        self._state = "running"
        self._detail = ""
        self._audit("bridge.supervisor.started", {"pid": proc.pid})
        log.info("bridge supervisor %s: started daemon (pid=%d)", self.channel, proc.pid)

    @staticmethod
    def _close(handle: Any) -> None:
        try:
            if hasattr(handle, "close"):
                handle.close()
        except Exception:  # noqa: BLE001
            pass

    def on_unload(self) -> None:
        """Stop a daemon WE started: SIGTERM, bounded wait, then SIGKILL.

        Bounded on purpose and never raising. A daemon adopted from systemd or
        ``bridge.sh`` is NOT stopped here: we did not start it, its owner is
        still running, and killing another supervisor's process on a plugin
        reload is how a hot-reload turns into an outage.
        """
        proc, self._proc = self._proc, None
        self._adopted_pid = 0
        self._state = "unloaded"
        if proc is None:
            return
        if proc.poll() is not None:
            self._audit("bridge.supervisor.stopped",
                        {"pid": proc.pid, "how": "already_exited"})
            return

        how = "sigterm"
        self._signal(proc, terminate=True)
        try:
            proc.wait(timeout=STOP_GRACE_S)
        except subprocess.TimeoutExpired:
            how = "sigkill"
            self._signal(proc, terminate=False)
            try:
                proc.wait(timeout=KILL_REAP_S)
            except subprocess.TimeoutExpired:
                # Unreapable child. Returning beats blocking the shutdown path.
                how = "abandoned"
            except Exception:  # noqa: BLE001
                how = "abandoned"
        except Exception:  # noqa: BLE001
            how = "abandoned"
        self._audit("bridge.supervisor.stopped", {"pid": proc.pid, "how": how})
        log.info("bridge supervisor %s: stopped daemon (pid=%d, %s)",
                 self.channel, proc.pid, how)

    @staticmethod
    def _signal(proc: subprocess.Popen, *, terminate: bool) -> None:
        """Signal the daemon's whole process group where the OS supports it."""
        if os.name != "nt":
            sig = signal.SIGTERM if terminate else signal.SIGKILL
            try:
                os.killpg(os.getpgid(proc.pid), sig)
                return
            except (ProcessLookupError, PermissionError, OSError):
                pass  # fall through to the single-process path
        try:
            if terminate:
                proc.terminate()
            else:
                proc.kill()
        except Exception:  # noqa: BLE001
            pass

    # ── health ────────────────────────────────────────────────────────────────

    def health_check(self) -> HealthStatus:
        """Report the daemon's liveness. Fast, non-blocking, PII-free.

        The message is scrubbed by the registry before it reaches the audit chain
        or the Console, but it is written PII-free at the source anyway: no
        filesystem paths (they carry the OS username), no tokens, no chat ids.

        Not-started states are ``ok=True``. A flag that is off, a bridge without
        credentials or a runtime dir nobody provisioned are all *expected*
        configurations, and painting them red would train the operator to ignore
        the health surface. ``ok=False`` is reserved for "this should be running
        and is not".
        """
        state = self._state
        details = {"channel": self.channel, "state": state}

        if state == "flag_off":
            return HealthStatus(ok=True, message="supervisor off (feature flag)",
                                details=details)
        if state in ("disabled_in_config", "not_configured", "not_provisioned",
                     "node_missing", "unloaded", "loaded"):
            return HealthStatus(ok=True, message=f"no daemon expected ({state})",
                                details=details)
        if state == "manager_missing":
            return HealthStatus(ok=False, message="bridge manager not available",
                                details=details)
        if state == "probe_failed":
            return HealthStatus(ok=False, message="bridge configuration unreadable",
                                details=details)
        if state == "unverifiable":
            return HealthStatus(
                ok=False,
                message="cannot verify whether a daemon is already running — "
                        "refused to start",
                details=details,
            )
        if state == "spawn_failed":
            return HealthStatus(ok=False, message="daemon could not be started",
                                details=details)

        if state == "already_running":
            alive = self._adopted_alive()
            details["managed_externally"] = True
            if self._detail:
                details["via"] = self._detail
            if alive:
                return HealthStatus(ok=True, message="daemon running (managed externally)",
                                    details=details)
            return HealthStatus(
                ok=False,
                message="externally managed daemon is no longer running",
                details=details,
            )

        proc = self._proc
        if proc is None:
            return HealthStatus(ok=True, message="no daemon expected", details=details)
        # poll() also reaps: without it an exited daemon lingers as a zombie and
        # would keep reporting healthy.
        rc = proc.poll()
        details["pid"] = proc.pid
        if rc is not None:
            details["exit_code"] = rc
            return HealthStatus(ok=False,
                                message=f"daemon exited (code {rc})", details=details)
        uptime = time.monotonic() - self._started_at
        details["uptime_s"] = round(uptime, 1)
        if uptime < MIN_UPTIME_S:
            details["state"] = "starting"
            return HealthStatus(ok=True, message="daemon starting", details=details)
        if not self._adapter_alive():
            # A daemon with no adapter polling the queue is a half bridge: it
            # receives messages and nothing ever answers them. Silent in every
            # log; surfaced here on purpose.
            details["adapter"] = "absent"
            return HealthStatus(
                ok=False,
                message="daemon running but no adapter is polling the queue",
                details=details,
            )
        return HealthStatus(ok=True, message="daemon running", details=details)

    def _adopted_alive(self) -> bool:
        bm = self._manager()
        if bm is None:
            return False
        try:
            return bool(bm.channel_daemon_running(self.channel).get("running"))
        except Exception:  # noqa: BLE001
            return False

    def _adapter_alive(self) -> bool:
        """True when an adapter process is polling the shared queue.

        Unknown counts as alive: an older vendored bridge_manager without the
        helper must not make every healthy bridge report red.
        """
        bm = self._manager()
        probe = getattr(bm, "adapter_running_pid", None) if bm else None
        if probe is None:
            return True
        try:
            return bool(probe())
        except Exception:  # noqa: BLE001
            return True


# ── Concrete per-channel plugins ──────────────────────────────────────────────
#
# One thin subclass per bundled bridge so a tenant config can name a stable
# class_path. They exist only to bind the channel name — all behaviour is in
# BridgeSupervisorPlugin, so a fix lands in one place for all seven.

class DiscordBridgePlugin(BridgeSupervisorPlugin):
    channel = "discord"


class TelegramBridgePlugin(BridgeSupervisorPlugin):
    channel = "telegram"


class WhatsAppBridgePlugin(BridgeSupervisorPlugin):
    channel = "whatsapp"


class SlackBridgePlugin(BridgeSupervisorPlugin):
    channel = "slack"


class EmailBridgePlugin(BridgeSupervisorPlugin):
    channel = "email"


class SignalBridgePlugin(BridgeSupervisorPlugin):
    channel = "signal"


class TeamsBridgePlugin(BridgeSupervisorPlugin):
    channel = "teams"


#: channel → concrete plugin class, for the declaration helper and the Console.
BRIDGE_PLUGIN_CLASSES: dict[str, type[BridgeSupervisorPlugin]] = {
    "discord": DiscordBridgePlugin,
    "telegram": TelegramBridgePlugin,
    "whatsapp": WhatsAppBridgePlugin,
    "slack": SlackBridgePlugin,
    "email": EmailBridgePlugin,
    "signal": SignalBridgePlugin,
    "teams": TeamsBridgePlugin,
}
