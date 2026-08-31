"""bridge_channel_plugin.py — Template for a bridge_channel CorvinPlugin (ADR-0030).

Copy this file, rename the class, and fill in the sections marked
"── IMPLEMENT ME ──".

A bridge_channel plugin implements:
  CorvinPlugin  (ADR-0030): on_load, on_unload, health_check
  BridgeChannel  (Bridge):   send, start, stop

on_load() registers the channel with ctx.channel_registry.  The bridge then
delivers inbound messages from this channel to the adapter's dispatch loop,
and the adapter calls send() for outbound replies.

Quick-start checklist
---------------------
1.  Pick a unique ``plugin_id`` and ``channel_name``.
2.  Implement ``_connect``: open your HTTP/WebSocket/etc. connection.
3.  Implement ``_poll`` (or set up a push/webhook instead): receive inbound messages.
4.  Implement ``_send``: deliver outbound messages.
5.  Register in tenant config under ``spec.plugins.installed``.

Run inline tests:
    python3 core/plugins/templates/bridge_channel_plugin.py
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# ── Adjust path for standalone runs ──────────────────────────────────────────
_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[3]
if str(_REPO / "plugins" / "core" / "plugins") not in sys.path:
    sys.path.insert(0, str(_REPO / "plugins" / "core" / "plugins"))

from corvin_plugins.protocol import CorvinPlugin, HealthStatus, PluginContext  # noqa: E402

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1.  Message types
# ──────────────────────────────────────────────────────────────────────────────

class BridgeChannelConfig(dict):
    """TypedDict-like config keys for this channel (read from ctx.config)."""
    # webhook_url:       str  — inbound webhook endpoint (if push-based)
    # token:             str  — API token / bearer auth (from vault, not ctx.config)
    # poll_interval_s:   int  — polling interval in seconds (if poll-based)


@dataclass
class InboundMessage:
    chat_id: str
    sender_id: str
    text: str
    attachments: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # original payload for debugging


@dataclass
class OutboundMessage:
    chat_id: str
    text: str
    attachments: list[dict] = field(default_factory=list)
    reply_to_id: str | None = None


# ──────────────────────────────────────────────────────────────────────────────
# 2.  Plugin class
# ──────────────────────────────────────────────────────────────────────────────

class BridgeChannelPluginTemplate:
    """Template: implements CorvinPlugin lifecycle + BridgeChannel capability.

    on_load() starts the polling/webhook loop and registers self with
    ctx.channel_registry.  The bridge adapter calls send() for outbound replies.
    Inbound messages are routed to the adapter via _dispatch_inbound().
    """

    # ── CorvinPlugin identity ────────────────────────────────────────────────
    plugin_id    = "my-bridge-channel"        # ── IMPLEMENT ME ── globally unique
    plugin_type  = "bridge_channel"
    version      = "0.1.0"                   # ── IMPLEMENT ME ── semver
    display_name = "My Bridge Channel"        # ── IMPLEMENT ME ──

    # ── BridgeChannel identity ────────────────────────────────────────────────
    channel_name = "mychannel"               # ── IMPLEMENT ME ── unique channel id

    def __init__(self) -> None:
        self._ctx: PluginContext | None = None
        self._connected = False
        self._running = False
        self._lock = threading.Lock()
        self._poll_thread: threading.Thread | None = None
        self._inbound_callback: Callable[[InboundMessage], None] | None = None

    # ── CorvinPlugin: lifecycle ──────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Store context, connect, start polling, and register with channel_registry."""
        self._ctx = ctx
        cfg: dict = ctx.config

        # Read config — API tokens come from vault, not ctx.config
        # ── IMPLEMENT ME ── read non-secret config here, e.g.:
        # self._poll_interval = int(cfg.get("poll_interval_s", 5))
        self._poll_interval: float = float(cfg.get("poll_interval_s", 5))

        # ── IMPLEMENT ME ── read inbound_callback from ctx.extra if provided
        self._inbound_callback = ctx.extra.get("inbound_callback")

        self._connected = self._connect()
        if not self._connected:
            log.error("plugin %r: _connect() returned False — channel %r unavailable",
                      self.plugin_id, self.channel_name)

        self._running = True
        self._poll_thread = threading.Thread(
            target=self._polling_loop,
            daemon=True,
            name=f"bridge-{self.channel_name}",
        )
        self._poll_thread.start()

        # ── NOTE 2026-07-27: there is no channel_registry to register with ────
        # `ctx.channel_registry` is a real PluginContext field, but no class
        # implementing it exists anywhere in CorvinOS — the only references in
        # the tree are in this file. So the handle is not "not passed yet"; the
        # thing it would point at was never built (see surface_map.py's
        # bridge_channel row, and ADR-0245's deferred design question).
        #
        # This branch therefore states the fact instead of logging a warning
        # that reads like a configuration mistake. A quiet `if ... is not None:`
        # here is what turned a missing API into no signal at all: the plugin
        # loaded, reported healthy, and never received a message.
        if ctx.channel_registry is not None:
            ctx.channel_registry.register(self)
            log.info("plugin %r registered channel %r", self.plugin_id, self.channel_name)
        else:
            log.error(
                "plugin %r: channel %r is NOT reachable and cannot be made "
                "reachable by configuration — CorvinOS has no channel_registry "
                "implementation. This template targets an API that does not "
                "exist yet; see ADR-0245. Inbound and outbound messages will "
                "not flow.",
                self.plugin_id, self.channel_name,
            )

    def on_unload(self) -> None:
        """Stop polling loop, disconnect, and deregister."""
        with self._lock:
            self._running = False

        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)

        self._disconnect()

        if self._ctx is not None and self._ctx.channel_registry is not None:
            try:
                self._ctx.channel_registry.unregister(self.channel_name)
            except Exception:
                log.exception("plugin %r: error during channel_registry.unregister", self.plugin_id)

    def health_check(self) -> HealthStatus:
        with self._lock:
            connected = self._connected
        return HealthStatus(
            ok=connected,
            message="connected" if connected else "disconnected",
            details={"channel": self.channel_name, "connected": connected},
        )

    # ── BridgeChannel: send ───────────────────────────────────────────────────

    def send(self, msg: OutboundMessage) -> None:
        """Send an outbound message to the channel.

        Called by the adapter for every reply destined for this channel.
        Must be thread-safe (may be called from multiple threads).
        """
        if not self._connected:
            log.warning("plugin %r: send() called while disconnected", self.plugin_id)
            return
        self._send(msg)

    # ── Abstract: implement these ─────────────────────────────────────────────

    def _connect(self) -> bool:
        """Open the channel connection.  Return True on success.

        ── IMPLEMENT ME ── open your HTTP session / WebSocket / etc.
        Read API tokens from the vault (not from ctx.config):

            token = self._read_vault_secret("MY_CHANNEL_TOKEN")

        Return False to signal unavailability (health_check will report ok=False).
        """
        # ── IMPLEMENT ME ──────────────────────────────────────────────────────
        log.info("plugin %r: _connect() stub — returning True", self.plugin_id)
        return True

    def _disconnect(self) -> None:
        """Close the channel connection gracefully.

        ── IMPLEMENT ME ── close HTTP session / WebSocket / etc.
        """
        # ── IMPLEMENT ME ──────────────────────────────────────────────────────
        log.info("plugin %r: _disconnect() stub", self.plugin_id)
        with self._lock:
            self._connected = False

    def _poll(self) -> list[InboundMessage]:
        """Fetch pending inbound messages.  Return an empty list if none.

        ── IMPLEMENT ME ── call your API / dequeue from WebSocket buffer / etc.
        Must be non-blocking or have a short timeout (< poll_interval_s).

        Example skeleton::

            resp = self._session.get(f"{self._api_base}/messages/pending", timeout=3)
            resp.raise_for_status()
            return [
                InboundMessage(
                    chat_id=m["chat_id"],
                    sender_id=m["from"],
                    text=m["text"],
                    raw=m,
                )
                for m in resp.json().get("messages", [])
            ]
        """
        # ── IMPLEMENT ME ──────────────────────────────────────────────────────
        return []

    def _send(self, msg: OutboundMessage) -> None:
        """Deliver an outbound message to the channel API.

        ── IMPLEMENT ME ── POST / WebSocket send / etc.

        Example skeleton::

            self._session.post(
                f"{self._api_base}/messages/send",
                json={"chat_id": msg.chat_id, "text": msg.text},
                timeout=10,
            ).raise_for_status()
        """
        # ── IMPLEMENT ME ──────────────────────────────────────────────────────
        log.debug("plugin %r: _send() stub — chat=%r text=%r", self.plugin_id, msg.chat_id, msg.text[:60])

    # ── Internal: polling loop ────────────────────────────────────────────────

    def _polling_loop(self) -> None:
        """Background thread: poll for inbound messages and dispatch them."""
        log.info("plugin %r: polling loop started (interval=%.1fs)", self.plugin_id, self._poll_interval)
        while True:
            with self._lock:
                if not self._running:
                    break

            try:
                messages = self._poll()
                for msg in messages:
                    self._dispatch_inbound(msg)
            except Exception:  # noqa: BLE001
                log.exception("plugin %r: error in _poll()", self.plugin_id)
                with self._lock:
                    self._connected = False

            time.sleep(self._poll_interval)

        log.info("plugin %r: polling loop stopped", self.plugin_id)

    def _dispatch_inbound(self, msg: InboundMessage) -> None:
        """Route an inbound message to the adapter's dispatch callback.

        The callback is supplied via ctx.extra["inbound_callback"] during on_load.
        If no callback is registered, the message is logged and dropped.

        ── IMPLEMENT ME ── If your bridge uses a different routing mechanism
        (e.g. an asyncio queue, a shared inbox file, or a direct adapter method),
        replace this with the appropriate call.
        """
        if self._inbound_callback is not None:
            try:
                self._inbound_callback(msg)
            except Exception:
                log.exception("plugin %r: inbound_callback raised for chat=%r", self.plugin_id, msg.chat_id)
        else:
            log.warning(
                "plugin %r: no inbound_callback — dropping message from chat=%r sender=%r",
                self.plugin_id, msg.chat_id, msg.sender_id,
            )

    # ── Vault helper (reference only) ─────────────────────────────────────────

    def _read_vault_secret(self, env_var_name: str) -> str | None:
        """Read a secret by env-var name from the Corvin vault.

        The vault injects secrets as environment variables into the bwrap
        sandbox (Layer 16 v3).  Read them from os.environ — never from
        ctx.config or any log-visible location.

        ── IMPLEMENT ME ── if you need secrets, declare them in your plugin's
        meta.secrets list and read here::

            import os
            return os.environ.get(env_var_name)
        """
        import os
        return os.environ.get(env_var_name)


# ──────────────────────────────────────────────────────────────────────────────
# Tenant configuration reference
#
#   spec:
#     plugins:
#       installed:
#         - id: my-bridge-channel
#           config:
#             poll_interval_s: 3
# ──────────────────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────────────────
# Inline tests
#   python3 core/plugins/templates/bridge_channel_plugin.py
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import unittest

    class BridgeChannelPluginTests(unittest.TestCase):

        def setUp(self) -> None:
            self.plugin = BridgeChannelPluginTemplate()

        def _make_ctx(self, channel_registry: Any = None, **extra: Any) -> PluginContext:
            return PluginContext(
                plugin_id=self.plugin.plugin_id,
                tenant_id="test",
                corvin_home=Path("/tmp"),
                config={"poll_interval_s": 60},  # long interval so thread doesn't actually poll
                audit_emit=lambda *_: None,
                channel_registry=channel_registry,
                extra=extra,
            )

        def test_implements_corvin_plugin(self) -> None:
            self.assertIsInstance(self.plugin, CorvinPlugin)

        def test_on_load_without_registry(self) -> None:
            ctx = self._make_ctx(channel_registry=None)
            self.plugin.on_load(ctx)
            self.plugin.on_unload()

        def test_on_load_registers_with_registry(self) -> None:
            registered: list[Any] = []

            class FakeRegistry:
                def register(self, ch: Any) -> None:
                    registered.append(ch)
                def unregister(self, name: str) -> None:
                    pass

            ctx = self._make_ctx(channel_registry=FakeRegistry())
            self.plugin.on_load(ctx)
            self.assertEqual(len(registered), 1)
            self.plugin.on_unload()

        def test_health_check_ok_when_connected(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            hs = self.plugin.health_check()
            self.assertTrue(hs.ok)
            self.plugin.on_unload()

        def test_send_drops_when_disconnected(self) -> None:
            # Should not raise even when disconnected
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            self.plugin._connected = False
            self.plugin.send(OutboundMessage(chat_id="c1", text="hi"))
            self.plugin.on_unload()

        def test_inbound_callback_called(self) -> None:
            received: list[InboundMessage] = []

            def cb(msg: InboundMessage) -> None:
                received.append(msg)

            ctx = self._make_ctx(inbound_callback=cb)
            self.plugin.on_load(ctx)
            msg = InboundMessage(chat_id="c1", sender_id="u1", text="hello")
            self.plugin._dispatch_inbound(msg)
            self.assertEqual(len(received), 1)
            self.assertEqual(received[0].text, "hello")
            self.plugin.on_unload()

        def test_on_unload_stops_loop(self) -> None:
            ctx = self._make_ctx()
            self.plugin.on_load(ctx)
            self.plugin.on_unload()
            with self.plugin._lock:
                self.assertFalse(self.plugin._running)

    suite = unittest.TestLoader().loadTestsFromTestCase(BridgeChannelPluginTests)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
