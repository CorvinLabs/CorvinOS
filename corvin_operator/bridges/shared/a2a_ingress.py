"""Layer 38 — dedicated A2A ingress listener (inbound peer traffic only).

The console binds loopback — it serves ``local-login``, which authorises by
TCP peer address, so it must never be reachable from the LAN. Until
2026-09-24 the A2A wire routes lived only on that same socket, so an install
could either accept peers (``a2a_lan_bind`` — the whole console on the LAN)
or stay safe (no inbound A2A at all). Every correctly configured install
chose safe, and pairing two machines in the same home network needed a
hand-written proxy.

This listener is the separation: its own port (default 8775), its own
socket, and exactly three routes —

    POST /v1/a2a/receive          signed TaskEnvelope  (RemoteTriggerReceiver)
    POST /v1/a2a/ping             signed liveness probe (ADR-0199)
    POST /v1/a2a/friendship-ack   signed handshake / hello (ADR-0257)

— each already authenticated by the pairing HMAC or answered with an opaque
anti-enumeration 403. Everything else is 404, including every GET. It reuses
the stdlib handler of :mod:`a2a_http_server` (same route implementations the
standalone A2A server has always shipped) with three restrictions on top:

* peer-address gate — only loopback, RFC 1918 / RFC 4193 private, and the
  RFC 6598 shared range (Tailscale / CGNAT mesh) peers are served unless the
  operator sets ``allow_public`` (port-forward / reverse proxy setups);
* per-address rate limit (token bucket) in front of the handlers;
* body cap and socket timeout inherited from the base handler.

Configuration: ``<CORVIN_HOME>/global/remote_trigger/ingress.json``
(``{"enabled": true, "port": 8775, "allow_public": false}``), overridable by
``CORVIN_A2A_INGRESS`` (``off`` / ``on``) and ``CORVIN_A2A_INGRESS_PORT``.

CI lint: MUST NOT import the anthropic SDK.
"""
from __future__ import annotations

import http.server
import ipaddress
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_PORT = 8775
_ALLOWED_POST_PATHS = frozenset({
    "/v1/a2a/receive", "/v1/a2a/ping", "/v1/a2a/friendship-ack",
})
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")  # RFC 6598 (Tailscale)

# Token bucket per source address: generous for a real peer (a handful of
# requests per exchange), tight enough that one LAN host cannot keep the
# receiver's threads busy.
_RATE_BURST = 30
_RATE_PER_S = 2.0


@dataclass(frozen=True)
class IngressConfig:
    enabled: bool = True
    port: int = DEFAULT_PORT
    allow_public: bool = False
    host: str = "0.0.0.0"


def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    return Path(env) if env else Path.home() / ".corvin"


def config_path() -> Path:
    return _corvin_home() / "global" / "remote_trigger" / "ingress.json"


def load_config() -> IngressConfig:
    enabled, port, allow_public = True, DEFAULT_PORT, False
    try:
        raw = json.loads(config_path().read_text("utf-8"))
        if isinstance(raw, dict):
            enabled = bool(raw.get("enabled", True))
            port = int(raw.get("port", DEFAULT_PORT))
            allow_public = bool(raw.get("allow_public", False))
    except (OSError, ValueError, TypeError):
        pass
    env = os.environ.get("CORVIN_A2A_INGRESS", "").strip().lower()
    if env in ("off", "0", "false", "no"):
        enabled = False
    elif env in ("on", "1", "true", "yes"):
        enabled = True
    env_port = os.environ.get("CORVIN_A2A_INGRESS_PORT", "").strip()
    if env_port:
        try:
            port = int(env_port)
        except ValueError:
            pass
    if not (1 <= port <= 65535):
        port = DEFAULT_PORT
    return IngressConfig(enabled=enabled, port=port, allow_public=allow_public)


def peer_allowed(addr: str, *, allow_public: bool) -> bool:
    """True when ``addr`` may talk to the ingress."""
    if allow_public:
        return True
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_private:
        # is_private also covers link-local and a few reserved blocks; the
        # link-local range is harmless here (same L2 segment by definition).
        return not ip.is_unspecified
    return isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED_ADDRESS_SPACE


class _RateLimiter:
    def __init__(self, burst: int = _RATE_BURST, per_s: float = _RATE_PER_S) -> None:
        self._burst = burst
        self._per_s = per_s
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self._burst), now))
            tokens = min(float(self._burst), tokens + (now - last) * self._per_s)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            if len(self._buckets) > 4096:  # bound memory under address churn
                for k in list(self._buckets)[:2048]:
                    self._buckets.pop(k, None)
            return True


class IngressStats:
    """Counters for the diagnostics endpoint — no addresses, no content."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.served = 0
        self.rejected_peer = 0
        self.rate_limited = 0
        self.not_found = 0
        self.last_request_at: float | None = None

    def bump(self, field: str) -> None:
        with self._lock:
            setattr(self, field, getattr(self, field) + 1)
            if field == "served":
                self.last_request_at = time.time()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "served": self.served, "rejected_peer": self.rejected_peer,
                "rate_limited": self.rate_limited, "not_found": self.not_found,
                "last_request_at": self.last_request_at,
            }


def build_ingress_server(
    *, receiver: Any, endpoints_dir: Path, pending_dir: Path,
    config: IngressConfig, stats: IngressStats | None = None,
) -> http.server.ThreadingHTTPServer:
    """Build (not start) the ingress server around an EXISTING receiver.

    Sharing the host's receiver matters: one nonce store, one origin
    registry, one audit writer — a replayed envelope is rejected no matter
    which socket it arrives on.
    """
    from a2a_http_server import _A2AHandler  # type: ignore[import-not-found]

    limiter = _RateLimiter()
    counters = stats or IngressStats()
    allow_public = config.allow_public

    class _IngressHandler(_A2AHandler):
        google_adapter = None

        def _gate(self) -> bool:
            peer = self.client_address[0] if self.client_address else ""
            if not peer_allowed(str(peer), allow_public=allow_public):
                counters.bump("rejected_peer")
                self._respond(403, b'{"reason":"peer_not_allowed"}\n')
                return False
            if not limiter.allow(str(peer)):
                counters.bump("rate_limited")
                self._respond(429, b'{"reason":"rate_limited"}\n')
                return False
            return True

        def do_GET(self):  # noqa: N802
            counters.bump("not_found")
            self._respond(404, b'{"reason":"not_found"}\n')

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_GET  # noqa: N815

        def do_HEAD(self):  # noqa: N802
            counters.bump("not_found")
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):  # noqa: N802
            if self.path not in _ALLOWED_POST_PATHS:
                counters.bump("not_found")
                self._respond(404, b'{"reason":"not_found"}\n')
                return
            if not self._gate():
                return
            counters.bump("served")
            super().do_POST()

    _IngressHandler.receiver = receiver
    _IngressHandler.endpoints_dir = Path(endpoints_dir)
    _IngressHandler.pending_dir = Path(pending_dir)

    class _Server(http.server.ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return _Server((config.host, config.port), _IngressHandler)


class IngressRunner:
    """Owns the ingress socket + serving thread; restartable on config change."""

    def __init__(self, *, receiver: Any, endpoints_dir: Path, pending_dir: Path) -> None:
        self._receiver = receiver
        self._endpoints_dir = endpoints_dir
        self._pending_dir = pending_dir
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._config: IngressConfig | None = None
        self.stats = IngressStats()
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def config(self) -> IngressConfig | None:
        return self._config

    def ensure(self, config: IngressConfig) -> str:
        """Bring the listener in line with ``config``. Returns one of
        ``started``/``stopped``/``unchanged``/``failed``."""
        if self._server is not None and config == self._config:
            return "unchanged"
        if self._server is not None:
            self.stop()
            if not config.enabled:
                self._config = config
                return "stopped"
        if not config.enabled:
            self._config = config
            return "unchanged"
        try:
            server = build_ingress_server(
                receiver=self._receiver, endpoints_dir=self._endpoints_dir,
                pending_dir=self._pending_dir, config=config, stats=self.stats,
            )
        except OSError as exc:
            self.last_error = f"bind_failed:{exc.errno}"
            self._config = None
            return "failed"
        self._server = server
        self._config = config
        self.last_error = None
        self._thread = threading.Thread(
            target=server.serve_forever, name="a2a-ingress", daemon=True)
        self._thread.start()
        return "started"

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            try:
                server.shutdown()
                server.server_close()
            except Exception:  # noqa: BLE001 — best-effort on shutdown
                pass
        self._thread = None


__all__ = [
    "DEFAULT_PORT", "IngressConfig", "IngressRunner", "IngressStats",
    "build_ingress_server", "config_path", "load_config", "peer_allowed",
]
