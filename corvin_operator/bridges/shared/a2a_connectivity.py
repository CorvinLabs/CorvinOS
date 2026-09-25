"""Layer 38 — A2A connectivity manager: the owner of "is this friendship
reachable in both directions, and if not, which transport repairs it".

Concept: Corvin-ADR/concepts/a2a-robust-connectivity-concept.md. Before this
module A2A had three mechanisms — direct HTTP, relay, reciprocal handshake —
each assuming the other two worked, and none owning the outcome. A single
lost ack left a pairing half-open forever; a relay listener built once at
boot never learned about pairings made later; an address that changed with
DHCP was never re-announced. Measured 2026-09-24: two machines in one home
LAN needed four hours and a hand-written proxy to pair.

What runs here (one asyncio task per host process, ticking every few
seconds, all real work off the event loop):

1. **Ingress** — keeps the dedicated A2A listener (:mod:`a2a_ingress`) in
   line with its config; restartable, never fatal.
2. **Advertised URL** — keeps ``my_a2a_url`` pointing at a reachable
   address (mesh-VPN or LAN IP + ingress port) while it is auto-managed;
   an operator-chosen URL (hostname, public address, other port) is never
   touched.
3. **Relay listener** — started, stopped and re-pointed on the fly when the
   relay URL or the ``a2a_relay_fallback`` flag changes (previously boot-
   only); new pairings are registered without a reconnect.
4. **Friendship upkeep** — for every connection: send the signed
   hello/ack (idempotent since 2026-09-24, see
   ``a2a_friendship._process_friendship_hello``) and ping, with exponential
   backoff while unhealthy and a slow keepalive while healthy. This is what
   completes a half-open handshake, carries a changed address to the peer,
   and keeps ``state``/``peer_knows_us`` true to reality on BOTH sides.

Every state transition is written to the audit chain (content-free:
connection id, state token, transport). Nothing here can weaken a
guarantee: every message it sends is the same HMAC-signed message the
operator-triggered paths send, and every inbound message is verified by the
unchanged receiver code.

CI lint: MUST NOT import the anthropic SDK.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import a2a_friendship as ft
import a2a_ingress

_log = logging.getLogger("corvin.a2a.connectivity")

TICK_S = 5.0
HEALTHY_INTERVAL_S = 600.0     # keepalive / address re-announce when all is well
RETRY_MIN_S = 10.0             # first retry after a failure
RETRY_MAX_S = 300.0            # backoff ceiling while unhealthy
MESH_DETECT_INTERVAL_S = 120.0
_CONSOLE_PORT = 8765


# ── audit (content-free, fail-safe) ─────────────────────────────────────

def _audit(event_type: str, severity: str, **details: Any) -> None:
    try:
        import remote_trigger_sender as _rts  # noqa: PLC0415
        se = getattr(_rts, "_forge_se", None)
        if se is None:
            return
        path = _rts.audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        se.write_event(
            path, event_type, severity=severity, tool="", run_id="",
            details=_rts._assert_audit_details_safe(details), hash_chain=True,
        )
    except Exception:  # noqa: BLE001 — audit is best-effort on this path
        pass


# ── advertised URL ──────────────────────────────────────────────────────

def _is_auto_managed_url(url: str, ports: set[int]) -> bool:
    """An address this module (or the pre-2026-09-24 auto-suggest) wrote:
    plain http to a private / shared-space IP literal on one of our ports.
    Anything else (a hostname, https, a public IP, a foreign port) is an
    operator decision and is left alone."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").strip("[]")
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "http" or port not in ports or not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or (
        isinstance(ip, ipaddress.IPv4Address) and ip in a2a_ingress._SHARED_ADDRESS_SPACE)


# ── per-connection upkeep ───────────────────────────────────────────────

@dataclass
class _Schedule:
    next_due: float = 0.0
    backoff: float = RETRY_MIN_S
    last_result: dict[str, Any] = field(default_factory=dict)


def _ping_peer(kid: str, endpoints_dir: Path) -> tuple[bool, str | None]:
    """Signed ADR-0199 ping (direct, else relay). (reachable, via)."""
    try:
        from remote_trigger_sender import (  # type: ignore[import-not-found]  # noqa: PLC0415
            RemoteEndpointRegistry as _RER, RemoteTriggerSender as _RTS,
        )
        result = _RTS(endpoints_dir, _RER(endpoints_dir)).ping(kid, timeout_s=5)
        reachable = bool(result.reachable)
        return reachable, (getattr(result, "via", None) if reachable else None)
    except Exception:  # noqa: BLE001 — reachability check is best-effort
        return False, None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def refresh_friendship(
    kid: str, *, origins_dir: Path, endpoints_dir: Path, hello: bool = True,
) -> dict[str, Any]:
    """Re-verify one connection in both directions and persist the result.

    1. hello/ack (signed, idempotent) — proves the peer still holds our
       record, hands it our current address, and makes the peer ping us
       back (its verdict comes back as ``peer_reports_reachable``);
    2. our own signed ping — proves WE reach THEM (direct, else relay).

    Unlike the pre-2026-09-24 recheck, the hello does NOT wait for the ping:
    a half-open handshake is exactly the case where the peer cannot answer
    a ping yet (bug #4). Returns the persisted summary.
    """
    endpoint_path = endpoints_dir / f"{kid}.json"
    origin_path = origins_dir / f"{kid}.json"
    ecfg = _read_json(endpoint_path)
    if ecfg is None or not ecfg.get("_friendship"):
        return {"ok": False, "kid": kid, "error": "not_found"}
    ocfg = _read_json(origin_path) or {}
    if ocfg.get("_operator_disabled") or ecfg.get("_operator_disabled"):
        # The operator switched it off: no hellos, no pings, no state writes.
        return {"ok": False, "kid": kid, "error": "disabled"}
    if ocfg and not ocfg.get("enabled") and ocfg.get("state") != "PENDING":
        return {"ok": False, "kid": kid, "error": "disabled"}
    # Imported from a token without an address: disabled + PENDING until the
    # bound issuer's verified hello activates it (a2a_friendship
    # _awaiting_activation). This pass may say hello and ping, but must NOT
    # rewrite its state — round 3: writing ACTIVE/UNREACHABLE here (the origin
    # file has no "url" key, so the PENDING branch never matched) made the
    # connection un-activatable before the issuer's hello could arrive.
    awaiting = ft._awaiting_activation(ocfg) if ocfg else False

    peer_knows_us = bool(ecfg.get("_peer_knows_us", False))
    peer_reports_reachable = bool(ecfg.get("_peer_reports_reachable", False))
    hello_error: str | None = None
    hello_via: str | None = None
    if hello:
        result = ft.retry_friendship_ack(kid, endpoints_dir=endpoints_dir)
        if result.get("ok"):
            peer_knows_us = True
            peer_reports_reachable = bool(result.get("reachable"))
            hello_via = str(result.get("via") or "direct")
        else:
            hello_error = str(result.get("error") or "failed")
            if hello_error == "http_403":
                # The peer answered and does not recognise the pairing
                # (revoked on their side): say so instead of keeping a stale
                # "they know us".
                peer_knows_us = False
                peer_reports_reachable = False

    # The hello may have completed a PENDING connection or rewritten the
    # peer URL on the other side; our own endpoint file only changes when
    # the PEER's hello reaches us, so re-read before pinging.
    reachable, via = _ping_peer(kid, endpoints_dir)
    if not reachable and hello_via is not None:
        # A verified hello response is itself a reachability proof (the ping
        # can still fail on the peer's rate limit or a transient relay hop).
        reachable, via = True, hello_via

    now = time.time()
    with ft.config_file_lock(origins_dir, endpoints_dir):
        for p in (origin_path, endpoint_path):
            cfg = _read_json(p)
            if cfg is None:
                continue
            if cfg.get("_operator_disabled"):
                continue
            if awaiting and not cfg.get("enabled") and cfg.get("state") == "PENDING":
                pass  # keep PENDING: activation is the issuer hello's job
            elif cfg.get("url", None) == "" and not reachable:
                cfg["state"] = "PENDING"
            else:
                cfg["state"] = "ACTIVE" if reachable else "UNREACHABLE"
            cfg["_peer_knows_us"] = peer_knows_us
            cfg["_peer_reports_reachable"] = peer_reports_reachable
            if via is not None:
                cfg["_last_via"] = via
            if reachable:
                cfg["_last_ok_at"] = now
            cfg["_last_check_at"] = now
            ft._atomic_write(p, cfg)

    return {
        "ok": True, "kid": kid,
        "state": "ACTIVE" if reachable else "UNREACHABLE",
        "reachable": reachable, "via": via,
        "peer_knows_us": peer_knows_us,
        "peer_reports_reachable": peer_reports_reachable,
        "hello_error": hello_error,
    }


def _healthy(summary: dict[str, Any]) -> bool:
    return bool(summary.get("reachable") and summary.get("peer_knows_us")
                and summary.get("peer_reports_reachable"))


# ── the manager ─────────────────────────────────────────────────────────

class ConnectivityManager:
    def __init__(
        self, *, receiver: Any, origins_dir: Path, endpoints_dir: Path,
        pending_dir: Path, tenant_id: str = "_default",
    ) -> None:
        self._receiver = receiver
        self._origins_dir = Path(origins_dir)
        self._endpoints_dir = Path(endpoints_dir)
        self._pending_dir = Path(pending_dir)
        self._tenant_id = tenant_id
        self.ingress = a2a_ingress.IngressRunner(
            receiver=receiver, endpoints_dir=self._endpoints_dir,
            pending_dir=self._pending_dir,
        )
        self._relay_listener: Any = None
        self._relay_task: asyncio.Task | None = None
        self._task: asyncio.Task | None = None
        self._stop = False
        self._schedules: dict[str, _Schedule] = {}
        self._sched_lock = threading.Lock()
        self._known_states: dict[str, str] = {}
        self._mesh_cache: tuple[float, str] = (0.0, "")
        self._my_url_note: str = ""
        self._wake = threading.Event()

    # ── lifecycle ──
    def start(self) -> asyncio.Task:
        self._task = asyncio.get_running_loop().create_task(self._run(), name="a2a-connectivity")
        return self._task

    async def stop(self) -> None:
        self._stop = True
        self._wake.set()
        if self._relay_listener is not None:
            self._relay_listener.stop()
        if self._relay_task is not None:
            self._relay_task.cancel()
        if self._task is not None:
            self._task.cancel()
        await asyncio.to_thread(self.ingress.stop)

    def wake(self, kid: str | None = None) -> None:
        """Run a maintenance pass soon (thread-safe). ``kid`` → make that
        connection due immediately (called right after import/create)."""
        if kid:
            with self._sched_lock:
                self._schedules.setdefault(kid, _Schedule()).next_due = 0.0
        else:
            with self._sched_lock:
                for sch in self._schedules.values():
                    sch.next_due = 0.0
        try:
            import a2a_relay  # noqa: PLC0415
            a2a_relay.nudge_listeners()
        except Exception:  # noqa: BLE001
            pass
        self._wake.set()

    async def _run(self) -> None:
        while not self._stop:
            try:
                await asyncio.to_thread(self._ensure_ingress)
                await asyncio.to_thread(self._ensure_my_url)
                await self._ensure_relay_listener()
                await asyncio.to_thread(self._maintain_friendships)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad pass must not end the loop
                _log.exception("a2a connectivity pass failed")
            await self._sleep(TICK_S)

    async def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._stop and time.monotonic() < deadline:
            if self._wake.is_set():
                self._wake.clear()
                return
            await asyncio.sleep(0.25)

    # ── 1. ingress ──
    def _ensure_ingress(self) -> None:
        cfg = a2a_ingress.load_config()
        outcome = self.ingress.ensure(cfg)
        if outcome == "started":
            _log.warning("A2A ingress listening on %s:%d (A2A routes only)", cfg.host, cfg.port)
            _audit("A2A.ingress_state", "INFO", reason="started", source=str(cfg.port))
            self.wake()  # advertised URL may change → re-announce
        elif outcome == "failed":
            if self._my_url_note != f"ingress_failed:{self.ingress.last_error}":
                _log.warning("A2A ingress could not bind port %d (%s) — peers "
                             "will use the relay", cfg.port, self.ingress.last_error)
                _audit("A2A.ingress_state", "WARNING", reason="failed",
                       source=str(cfg.port))
                self._my_url_note = f"ingress_failed:{self.ingress.last_error}"
        elif outcome == "stopped":
            _audit("A2A.ingress_state", "INFO", reason="stopped", source=str(cfg.port))

    # ── 2. advertised URL ──
    def _detect_host(self) -> tuple[str, str]:
        now = time.monotonic()
        stamp, mesh = self._mesh_cache
        if now - stamp > MESH_DETECT_INTERVAL_S:
            mesh = ft.detect_mesh_vpn_address()
            self._mesh_cache = (now, mesh)
        if mesh:
            return mesh, "auto_mesh"
        return ft.detect_local_ip(), "auto_lan"

    def advertised_port(self) -> int:
        cfg = self.ingress.config
        if self.ingress.running and cfg is not None:
            return cfg.port
        return a2a_ingress.load_config().port

    def _ensure_my_url(self) -> None:
        import os  # noqa: PLC0415
        if os.environ.get("CORVIN_A2A_URL"):
            return  # pinned by the operator's environment
        stored = ft.get_my_url()
        port = self.advertised_port()
        managed_ports = {_CONSOLE_PORT, a2a_ingress.DEFAULT_PORT, port}
        if stored and not _is_auto_managed_url(stored, managed_ports):
            return
        if not self.ingress.running:
            # The ingress is NOT listening (disabled, or its port is held by
            # another process — e.g. a reverse proxy on :8775). Never announce
            # the ingress port then: peers would re-point to a dead or foreign
            # socket (2026-09-25, round 2). Only follow an address change of
            # an existing auto-managed URL, keeping ITS port.
            if not stored:
                return
            try:
                port = int(urlsplit(stored).port or 0)
            except ValueError:
                return
            if not port:
                return
            ingress_port = (self.ingress.config.port if self.ingress.config is not None
                            else a2a_ingress.load_config().port)
            if port == ingress_port:
                # That port belonged to OUR ingress, which is not listening
                # now: re-announcing it on a new IP points peers at a dead
                # socket (round 3). Leave it; peers keep the relay.
                return
        host, source = self._detect_host()
        if not host or host.startswith("127."):
            return
        desired = f"http://{host}:{port}"
        if stored == desired:
            return
        ft.set_my_url(desired)
        _log.warning("A2A advertised URL updated (%s, port %d)", source, port)
        _audit("A2A.my_url_updated", "INFO", reason=source, source=str(port))
        self.wake()  # every peer must hear the new address

    # ── 3. relay listener ──
    def _relay_desired(self) -> str | None:
        try:
            from corvin_core import feature_flags as _ff  # type: ignore[import-not-found]  # noqa: PLC0415
            if not _ff.is_enabled("a2a_relay_fallback"):
                return None
        except Exception:  # noqa: BLE001
            return None
        return ft.get_my_relay_url()

    async def _ensure_relay_listener(self) -> None:
        desired = await asyncio.to_thread(self._relay_desired)
        current = self._relay_listener.relay_url if self._relay_listener is not None else None
        task_dead = self._relay_task is not None and self._relay_task.done()
        if desired == current and not task_dead:
            return
        if self._relay_listener is not None:
            self._relay_listener.stop()
            if self._relay_task is not None:
                self._relay_task.cancel()
            self._relay_listener, self._relay_task = None, None
            _audit("A2A.relay_listener_state", "INFO", reason="stopped")
        if not desired or self._receiver is None:
            return
        import a2a_relay  # noqa: PLC0415
        listener = a2a_relay.RelayListener(
            relay_url=desired, receiver=self._receiver,
            origins_dir=self._origins_dir, pending_dir=self._pending_dir,
            endpoints_dir=self._endpoints_dir,
        )
        self._relay_listener = listener
        self._relay_task = asyncio.get_running_loop().create_task(
            listener.run_forever(), name="a2a-relay-listener")
        source = "configured" if ft.my_relay_url_is_explicit() else "default"
        _log.warning("A2A relay listener started (%s relay)", source)
        _audit("A2A.relay_listener_state", "INFO", reason="started", source=source)

    # ── 4. friendships ──
    def _connection_ids(self) -> list[str]:
        if not self._endpoints_dir.exists():
            return []
        return sorted(p.stem for p in self._endpoints_dir.glob("*.json"))

    def _maintain_friendships(self) -> None:
        now = time.time()
        for kid in self._connection_ids():
            with self._sched_lock:
                sch = self._schedules.setdefault(kid, _Schedule())
                if sch.next_due > now:
                    continue
            summary = refresh_friendship(
                kid, origins_dir=self._origins_dir, endpoints_dir=self._endpoints_dir)
            if summary.get("error") in ("not_found", "disabled"):
                with self._sched_lock:
                    sch.next_due = now + HEALTHY_INTERVAL_S
                continue
            with self._sched_lock:
                sch.last_result = summary
                if _healthy(summary):
                    sch.backoff = RETRY_MIN_S
                    sch.next_due = time.time() + HEALTHY_INTERVAL_S
                else:
                    sch.next_due = time.time() + sch.backoff
                    sch.backoff = min(RETRY_MAX_S, sch.backoff * 2)
            self._audit_transition(kid, summary)

    def _audit_transition(self, kid: str, summary: dict[str, Any]) -> None:
        state = ("active" if _healthy(summary)
                 else "one_way" if summary.get("reachable") or summary.get("peer_knows_us")
                 else "unreachable")
        token = f"{state}:{summary.get('via') or 'none'}"
        if self._known_states.get(kid) == token:
            return
        self._known_states[kid] = token
        _log.warning("A2A connection %s… is %s (via %s)", kid[:8], state,
                     summary.get("via") or "-")
        _audit("A2A.connection_state", "INFO" if state == "active" else "WARNING",
               endpoint_id=kid, reason=state, source=str(summary.get("via") or "none"),
               reachable=bool(summary.get("reachable")))

    # ── diagnostics ──
    def diagnostics(self) -> dict[str, Any]:
        cfg = self.ingress.config
        listener = self._relay_listener
        with self._sched_lock:
            schedules = {k: {"next_due": v.next_due, "backoff_s": v.backoff}
                         for k, v in self._schedules.items()}
        return {
            "ingress": {
                "running": self.ingress.running,
                "port": cfg.port if cfg else a2a_ingress.load_config().port,
                "allow_public": bool(cfg.allow_public) if cfg else False,
                "error": self.ingress.last_error,
                **self.ingress.stats.snapshot(),
            },
            "relay": {
                "enabled": self._relay_desired() is not None,
                "source": "configured" if ft.my_relay_url_is_explicit() else "default",
                **(dict(listener.status) if listener is not None else {"connected": False}),
            },
            "my_url": ft.get_my_url(),
            "schedules": schedules,
        }


# ── process-wide handle (console routes reach the running manager) ─────

_MANAGER: ConnectivityManager | None = None


def get_manager() -> ConnectivityManager | None:
    return _MANAGER


async def start_manager(
    *, receiver: Any, origins_dir: Path, endpoints_dir: Path, pending_dir: Path,
) -> ConnectivityManager:
    """Start (once per process) and return the connectivity manager."""
    global _MANAGER
    if _MANAGER is not None:
        return _MANAGER
    mgr = ConnectivityManager(
        receiver=receiver, origins_dir=origins_dir,
        endpoints_dir=endpoints_dir, pending_dir=pending_dir,
    )
    mgr.start()
    _MANAGER = mgr
    return mgr


async def stop_manager() -> None:
    global _MANAGER
    mgr, _MANAGER = _MANAGER, None
    if mgr is not None:
        await mgr.stop()


def wake(kid: str | None = None) -> None:
    """Thread-safe nudge from request handlers; no-op without a manager."""
    mgr = _MANAGER
    if mgr is not None:
        mgr.wake(kid)
    else:
        try:
            import a2a_relay  # noqa: PLC0415
            a2a_relay.nudge_listeners()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "ConnectivityManager", "get_manager", "refresh_friendship",
    "start_manager", "stop_manager", "wake",
]
