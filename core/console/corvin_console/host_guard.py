"""Host / Origin allowlist for the console and the gateway (review SRF-1, DNS rebinding).

The console's local login is credential-less: "the TCP peer is 127.0.0.1" is the
authorisation. DNS rebinding defeats exactly that. A page on ``evil.example`` rebinds
its name to 127.0.0.1; the browser then sends ``Host: evil.example:8765`` from a
loopback peer, gets a session cookie for the ``evil.example`` origin, is same-origin
with the console, reads the CSRF token from ``/auth/whoami`` and can drive every
mutating route. ``SameSite=Strict`` does not help, because those requests are
same-site to the rebound origin.

The Host header is the only signal the attacker cannot choose. The browser always
sends the name it resolved. So every request (HTTP and WebSocket) must name a host
this install answers to:

* a loopback name or address: ``localhost``, ``*.localhost``, ``127.0.0.0/8``, ``::1``;
* any IP literal. Rebinding needs a NAME the attacker's DNS answers for; a Host
  that is an address is the address the browser connected to, so its origin is the
  server itself. This is what keeps the configured bind address working, including
  the machine's LAN IP on an ``a2a_lan_bind`` install;
* the operator's allowlist: env ``CORVIN_ALLOWED_HOSTS`` (comma separated; a leading
  ``.`` matches a domain suffix) plus ``spec.console.allowed_hosts`` in the
  ``_default`` tenant's ``tenant.corvin.yaml``. Use it for a reverse proxy name or a
  LAN hostname.

A WebSocket that carries an ``Origin`` must also come from an allowed origin: the
same host:port as its Host header, the vite dev server (``localhost:5173`` /
``127.0.0.1:5173``), or an entry in ``CORVIN_ALLOWED_ORIGINS``. Without this check,
any page on another localhost port could ride the Strict cookie into the chat socket.

``/auth/local-login`` additionally requires a LOOPBACK Host (``require_loopback_host``):
even an allowlisted public name never gets a credential-less session.

A refusal is answered 400 (HTTP) or close 1008 (WebSocket) and recorded once a
minute per (reason, host) as ``http.host_rejected`` in the ``_default`` tenant's
audit chain. Only a fingerprint of the offending host/origin is stored, never the
value itself.

One module, used three ways: ``HostGuardMiddleware`` (ASGI, installed by
``corvin_gateway.app`` and ``corvin_console.app.create_app``), the console router's
``require_allowed_host`` dependency (which also covers the standalone host, since
it mounts that router), and ``require_loopback_host`` on local-login.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from starlette.requests import HTTPConnection

log = logging.getLogger(__name__)

AUDIT_EVENT = "http.host_rejected"
ENV_HOSTS = "CORVIN_ALLOWED_HOSTS"
ENV_ORIGINS = "CORVIN_ALLOWED_ORIGINS"
_DEV_ORIGINS = frozenset({"localhost:5173", "127.0.0.1:5173", "[::1]:5173"})
_AUDIT_THROTTLE_S = 60.0


# ── parsing ─────────────────────────────────────────────────────────────────


def split_host(value: str | None) -> str:
    """Hostname part of a Host header value, lowercased, without port or brackets.
    ``""`` for an empty or malformed value."""
    if not value:
        return ""
    v = value.strip().lower()
    if v.startswith("["):
        end = v.find("]")
        return v[1:end] if end > 0 else ""
    if v.count(":") > 1:  # a bare IPv6 literal (not valid in Host, but be exact)
        return v
    return v.rsplit(":", 1)[0] if ":" in v else v


def _origin_hostport(origin: str) -> str:
    """``host:port`` of an Origin value (default port from the scheme), lowercased."""
    from urllib.parse import urlsplit  # noqa: PLC0415
    try:
        u = urlsplit(origin.strip())
    except ValueError:
        return ""
    if u.scheme not in ("http", "https") or not u.hostname:
        return ""
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        return ""
    host = u.hostname.lower()
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def _host_hostport(host_header: str, scheme: str) -> str:
    h = split_host(host_header)
    if not h:
        return ""
    v = host_header.strip().lower()
    port = ""
    if v.startswith("["):
        rest = v[v.find("]") + 1:]
        port = rest[1:] if rest.startswith(":") else ""
    elif ":" in v and v.count(":") == 1:
        port = v.rsplit(":", 1)[1]
    if not port:
        port = "443" if scheme in ("https", "wss") else "80"
    return f"[{h}]:{port}" if ":" in h else f"{h}:{port}"


def is_loopback_host(host: str) -> bool:
    """True for ``localhost``, ``*.localhost`` and loopback IP literals."""
    h = split_host(host)
    if not h:
        return False
    if h == "localhost" or h.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(h.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


# ── operator allowlist ──────────────────────────────────────────────────────

_cfg_lock = threading.Lock()
_cfg_cache: dict[str, Any] = {"key": None, "hosts": frozenset()}


def _tenant_yaml_hosts() -> frozenset[str]:
    """``spec.console.allowed_hosts`` of the _default tenant, mtime-cached.
    An unreadable file contributes nothing (the loopback default still holds)."""
    try:
        from forge import paths as _forge_paths  # noqa: PLC0415
        p = Path(_forge_paths.tenant_global_dir("_default")) / "tenant.corvin.yaml"
        st = p.stat()
    except Exception:  # noqa: BLE001 - no file / no forge: no extra hosts
        return frozenset()
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _cfg_lock:
        if _cfg_cache["key"] == key:
            return _cfg_cache["hosts"]
    hosts: frozenset[str] = frozenset()
    try:
        import yaml  # noqa: PLC0415
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        raw = ((doc.get("spec") or {}).get("console") or {}).get("allowed_hosts") or []
        if isinstance(raw, list):
            hosts = frozenset(str(h).strip().lower() for h in raw if str(h).strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("host_guard: spec.console.allowed_hosts unreadable (%s); ignoring it",
                    type(exc).__name__)
    with _cfg_lock:
        _cfg_cache["key"] = key
        _cfg_cache["hosts"] = hosts
    return hosts


def _env_list(name: str) -> list[str]:
    return [x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()]


def operator_hosts() -> frozenset[str]:
    return frozenset(_env_list(ENV_HOSTS)) | _tenant_yaml_hosts()


def _matches(host: str, patterns: Iterable[str]) -> bool:
    for pat in patterns:
        pat = split_host(pat) if not pat.startswith(".") else pat
        if not pat:
            continue
        if pat.startswith("."):
            if host.endswith(pat) or host == pat[1:]:
                return True
        elif host == pat:
            return True
    return False


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def host_allowed(host_header: str | None) -> bool:
    """True when *host_header* names this install (see module docstring).
    A request without a Host header (HTTP/1.0, non-browser) is allowed: a browser
    always sends one, and only a browser carries the ambient cookie."""
    if host_header is None or host_header == "":
        return True
    h = split_host(host_header)
    if not h:
        return False
    if is_loopback_host(h):
        return True
    if _is_ip_literal(h):
        return True
    return _matches(h, operator_hosts())


def origin_allowed(origin: str | None, host_header: str | None, scheme: str) -> bool:
    """WebSocket Origin rule: absent (non-browser) or same host:port as the Host
    header, the vite dev server, or ``CORVIN_ALLOWED_ORIGINS``."""
    if origin is None:
        return True
    hp = _origin_hostport(origin)
    if not hp:
        return False  # "null", file:, chrome-extension:, garbage
    if host_header and hp == _host_hostport(host_header, scheme):
        return True
    if hp in _DEV_ORIGINS:
        return True
    extra = {(_origin_hostport(o) or o) for o in _env_list(ENV_ORIGINS)}
    return hp in extra


# ── audit ───────────────────────────────────────────────────────────────────

_audit_lock = threading.Lock()
_audit_last: dict[tuple[str, str], float] = {}


def _fp(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()[:16]


def _path_class(path: str) -> str:
    """First two path segments only - never ids or query strings."""
    segs = [s for s in (path or "/").split("?", 1)[0].split("/") if s][:2]
    return ("/" + "/".join(segs))[:64]


def audit_refusal(*, surface: str, reason: str, scope_type: str, path: str,
                  host: str | None, origin: str | None = None) -> None:
    """Record a refused request (throttled: one record per reason+host per minute)."""
    host_fp = _fp(split_host(host or ""))
    key = (reason, host_fp)
    now = time.monotonic()
    with _audit_lock:
        last = _audit_last.get(key)
        if last is not None and now - last < _AUDIT_THROTTLE_S:
            return
        _audit_last[key] = now
        if len(_audit_last) > 4096:
            _audit_last.clear()
    log.warning("refused %s request: %s (path=%s)", scope_type, reason, _path_class(path))
    details = {
        "surface": surface, "reason": reason, "scope_type": scope_type,
        "path_class": _path_class(path), "host_fp": host_fp,
        "origin_fp": _fp(origin) if origin is not None else "",
        "tenant_id": "_default",
    }
    try:
        from forge import paths as _forge_paths  # noqa: PLC0415
        from forge import security_events as _se  # noqa: PLC0415
        _se.write_event(_forge_paths.tenant_audit_chain("_default"), AUDIT_EVENT,
                        details=details)
    except Exception as exc:  # noqa: BLE001 - the refusal stands even if the record cannot commit
        log.error("host_guard: %s audit record did not commit (%s)", AUDIT_EVENT,
                  type(exc).__name__)


# ── decision on a connection ────────────────────────────────────────────────


def _header(scope: dict, name: bytes) -> str | None:
    for k, v in scope.get("headers") or ():
        if k.lower() == name:
            return v.decode("latin-1")
    return None


def refusal_reason(scope: dict) -> str | None:
    """``None`` when the connection may proceed, else a short reason code."""
    if scope.get("type") not in ("http", "websocket"):
        return None
    host = _header(scope, b"host")
    if not host_allowed(host):
        return "host_not_allowed"
    if scope["type"] == "websocket":
        scheme = scope.get("scheme") or "ws"
        if not origin_allowed(_header(scope, b"origin"), host, scheme):
            return "origin_not_allowed"
    return None


class HostGuardMiddleware:
    """Pure ASGI middleware: refuse HTTP/WebSocket connections naming a foreign
    Host (or a foreign Origin on a WebSocket) before any route runs."""

    def __init__(self, app: Any, *, surface: str = "console") -> None:
        self.app = app
        self.surface = surface

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        reason = refusal_reason(scope)
        if reason is None:
            await self.app(scope, receive, send)
            return
        from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
        await run_in_threadpool(
            audit_refusal, surface=self.surface, reason=reason, scope_type=scope["type"],
            path=scope.get("path", ""), host=_header(scope, b"host"),
            origin=_header(scope, b"origin"))
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = b'{"detail":"host not allowed"}'
        await send({"type": "http.response.start", "status": 400, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ]})
        await send({"type": "http.response.body", "body": body})


def install(app: Any, *, surface: str) -> None:
    """Add :class:`HostGuardMiddleware` to a FastAPI/Starlette *app*. Added last,
    it runs first (Starlette middleware is LIFO)."""
    app.add_middleware(HostGuardMiddleware, surface=surface)


# ── FastAPI dependencies ────────────────────────────────────────────────────


def require_allowed_host(conn: HTTPConnection) -> None:
    """Router-level dependency for every console route (HTTP and WebSocket)."""
    reason = refusal_reason(conn.scope)
    if reason is None:
        return
    audit_refusal(surface="console", reason=reason, scope_type=conn.scope["type"],
                  path=conn.url.path, host=conn.headers.get("host"),
                  origin=conn.headers.get("origin"))
    if conn.scope["type"] == "websocket":
        from fastapi import WebSocketException  # noqa: PLC0415
        raise WebSocketException(code=1008)
    from fastapi import HTTPException  # noqa: PLC0415
    raise HTTPException(status_code=400, detail="host not allowed")


def require_loopback_host(conn: HTTPConnection) -> None:
    """``/auth/local-login``: the Host itself must be loopback - an allowlisted
    public name never earns a credential-less session."""
    host = conn.headers.get("host")
    if host is not None and is_loopback_host(split_host(host)):
        return
    audit_refusal(surface="console", reason="local_login_host", scope_type="http",
                  path=conn.url.path, host=host, origin=conn.headers.get("origin"))
    from fastapi import HTTPException  # noqa: PLC0415
    raise HTTPException(status_code=403, detail="local-login is only available on a loopback host")
