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
this install answers to (:func:`host_allowed`); the port of a Host never matters:

* a loopback name or address: ``localhost``, ``*.localhost``, ``127.0.0.0/8``, ``::1``;
* any IP literal. Rebinding needs a NAME the attacker's DNS answers for; a Host
  that is an address is the address the browser connected to, so its origin is the
  server itself. This keeps a bind address (``0.0.0.0``, a LAN IP) working;
* the configured bind host when it is a name: ``CORVIN_BIND_HOST``, the ``--host``
  argument of the uvicorn process serving this app, or ``spec.console.bind``;
* ``CORVIN_DOMAIN`` (the public name the Caddy deployment serves) and ``www.<it>``;
* the Tailscale names when the Tailscale deployment is configured: with
  ``CORVIN_TS_HOSTNAME=<name>`` (set by ``ops/tailscale/docker-compose.yml``) the
  MagicDNS short name ``<name>`` and its FQDN ``<name>.<tailnet>.ts.net`` (exactly
  one tailnet label; no other ``.ts.net`` name);
* on a LAN bind (bind host ``0.0.0.0``/``::``/a non-loopback address or name) the
  machine's own hostname, its short name and its mDNS name ``<short>.local``;
* the operator's allowlist: env ``CORVIN_ALLOWED_HOSTS`` (comma separated) plus
  ``spec.console.allowed_hosts`` in the ``_default`` tenant's ``tenant.corvin.yaml``.
  An entry is a host name (a port written on it is ignored) or a domain suffix with
  a leading ``.`` (``.corvin.lan``, never with a port). Empty entries, ``.`` and a
  suffix carrying a port are ignored and logged once (:func:`parse_entry`).

A WebSocket that carries an ``Origin`` must also come from an allowed origin
(:func:`origin_allowed`): the same host as its Host header and the same explicit
port (a port written on one side only is a mismatch), the vite dev server
(``localhost:5173`` / ``127.0.0.1:5173``), or an entry in ``CORVIN_ALLOWED_ORIGINS``.
The scheme is never compared: behind a TLS-terminating proxy the browser sends
``Origin: https://x`` on a socket the app sees as plain ``ws``. Without this check
any page on another localhost port could ride the Strict cookie into the chat socket.

``/auth/local-login`` additionally requires a LOOPBACK Host (``require_loopback_host``):
even an allowlisted public name never gets a credential-less session.

Not guarded: ``POST`` on the signed A2A wire routes (:data:`A2A_SIGNED_POST_PATHS`).
A paired peer reaches them by DNS name and every request is authenticated by the
pairing HMAC, never by a cookie, so a rebound page gains nothing there.

A refusal is answered 400 (HTTP; the body names the two allowlist settings) or close
1008 (WebSocket) and recorded as ``http.host_rejected`` in the ``_default`` tenant's
audit chain: at most once a minute per (reason, host) AND at most
:data:`AUDIT_MAX_PER_WINDOW` records a minute across all hosts, so a page rotating
names cannot turn refusals into one chain write each. Every record carries
``suppressed_count``: the refusals not recorded since the previous record. The
per-host map is bounded (LRU eviction, which never resets the global throttle).
Only a fingerprint of the offending host/origin is stored, never the value itself.

One module, used three ways: ``HostGuardMiddleware`` (ASGI, installed by
``corvin_gateway.app``, ``corvin_console.app.create_app`` and
``corvin_console.standalone.create_app``), the console router's
``require_allowed_host`` dependency, and ``require_loopback_host`` on local-login.
The bridge daemons use the JS twin ``corvin_operator/bridges/shared/js/host_guard.js``;
both parse allowlist entries by one table (``shared/js/host_guard_cases.json``).
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from starlette.requests import HTTPConnection

log = logging.getLogger(__name__)

AUDIT_EVENT = "http.host_rejected"
ENV_HOSTS = "CORVIN_ALLOWED_HOSTS"
ENV_ORIGINS = "CORVIN_ALLOWED_ORIGINS"
ENV_BIND = "CORVIN_BIND_HOST"
ENV_DOMAIN = "CORVIN_DOMAIN"
ENV_TS_HOSTNAME = "CORVIN_TS_HOSTNAME"
YAML_KEY = "spec.console.allowed_hosts"
_DEV_ORIGINS = frozenset({("localhost", "5173"), ("127.0.0.1", "5173"), ("::1", "5173")})
_AUDIT_THROTTLE_S = 60.0
#: Records per throttle window across ALL hosts (caps a rotating-name flood).
AUDIT_MAX_PER_WINDOW = 20
#: Hosts remembered for the per-host throttle (LRU).
AUDIT_KEYS_MAX = 4096
HINT = ("this Host is not one this install answers to; add it to CORVIN_ALLOWED_HOSTS "
        "or spec.console.allowed_hosts in tenant.corvin.yaml")
REFUSAL_BODY = ('{"detail":"host not allowed","hint":"' + HINT + '"}').encode()
#: Signed A2A wire routes on the console/gateway app (same set as
#: ``core/a2a/a2a_ingress.py``). A paired peer reaches them by DNS name and every
#: request is authenticated by the pairing HMAC (no cookie, no session), so a
#: rebound page gains nothing there: they carry no Host check.
A2A_SIGNED_POST_PATHS = frozenset({"/v1/a2a/receive", "/v1/a2a/ping", "/v1/a2a/friendship-ack"})
_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


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


def _explicit_port(host_header: str) -> str:
    """The port written in a Host value, ``""`` when none is written."""
    v = host_header.strip().lower()
    if v.startswith("["):
        rest = v[v.find("]") + 1:]
        return rest[1:] if rest.startswith(":") else ""
    if v.count(":") == 1:
        return v.rsplit(":", 1)[1]
    return ""


def _origin_parts(origin: str) -> tuple[str, str] | None:
    """``(host, explicit port or "")`` of an Origin value; ``None`` when it is not
    an http(s) origin ("null", file:, chrome-extension:, garbage). The scheme is
    checked for shape only and never compared."""
    from urllib.parse import urlsplit  # noqa: PLC0415
    try:
        u = urlsplit(origin.strip())
        port = u.port
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.hostname:
        return None
    return u.hostname.lower(), (str(port) if port is not None else "")


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


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    return True


def parse_entry(raw: Any) -> tuple[str, str] | None:
    """One allowlist entry -> ``("exact", host)`` / ``("suffix", ".domain")``, or
    ``None`` when it is ignored: empty, ``.``/dots only, a suffix with a port, a
    path, an empty label. A port on an exact entry is dropped, as is one trailing
    dot. The JS twin follows the same table (``shared/js/host_guard_cases.json``)."""
    s = str(raw if raw is not None else "").strip().lower()
    if not s or "/" in s or any(c.isspace() for c in s):
        return None
    if s.startswith("."):
        if ":" in s:
            return None
        body = s[1:-1] if s.endswith(".") else s[1:]
        if not body or "" in body.split("."):
            return None
        return ("suffix", "." + body)
    h = split_host(s)
    if h.endswith("."):
        h = h[:-1]
    if not h or "" in h.split("."):
        return None
    return ("exact", h)


_ignored_logged: set[str] = set()
_ignored_lock = threading.Lock()


def _parse_entries(raws: Iterable[Any], source: str) -> tuple[frozenset[str], frozenset[str]]:
    exact: set[str] = set()
    suffix: set[str] = set()
    for raw in raws:
        parsed = parse_entry(raw)
        if parsed is None:
            text = str(raw if raw is not None else "")
            if not text.strip():
                continue  # a trailing comma is not a misconfiguration worth a log line
            with _ignored_lock:
                first = text not in _ignored_logged
                _ignored_logged.add(text)
            if first:
                log.warning("host_guard: ignoring %s entry %r (empty, '.', a suffix with a "
                            "port, or malformed)", source, text[:80])
            continue
        (exact if parsed[0] == "exact" else suffix).add(parsed[1])
    return frozenset(exact), frozenset(suffix)


# ── what this install knows about itself ────────────────────────────────────

_cfg_lock = threading.Lock()
_cfg_cache: dict[str, Any] = {"key": None, "console": {}}


def _tenant_yaml_console() -> dict[str, Any]:
    """``spec.console`` of the _default tenant, mtime-cached. An unreadable file
    contributes nothing (the loopback default still holds)."""
    try:
        from forge import paths as _forge_paths  # noqa: PLC0415
        p = Path(_forge_paths.tenant_global_dir("_default")) / "tenant.corvin.yaml"
        st = p.stat()
    except (ImportError, OSError):  # no file / no forge: nothing extra
        return {}
    key = (str(p), st.st_mtime_ns, st.st_size)
    with _cfg_lock:
        if _cfg_cache["key"] == key:
            return _cfg_cache["console"]
    console: dict[str, Any] = {}
    import yaml  # noqa: PLC0415
    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        log.warning("host_guard: tenant.corvin.yaml unreadable (%s); spec.console ignored",
                    type(exc).__name__)
        doc = {}
    spec = doc.get("spec") if isinstance(doc, dict) else None
    raw = spec.get("console") if isinstance(spec, dict) else None
    if isinstance(raw, dict):
        console = raw
    with _cfg_lock:
        _cfg_cache["key"] = key
        _cfg_cache["console"] = console
    return console


def _tenant_yaml_hosts() -> list[Any]:
    raw = _tenant_yaml_console().get("allowed_hosts") or []
    if not isinstance(raw, list):
        log.warning("host_guard: %s is not a list; ignoring it", YAML_KEY)
        return []
    return raw


def _env_list(name: str) -> list[str]:
    return os.environ.get(name, "").split(",")


def _argv_bind_host(argv: list[str] | None = None) -> str:
    """The ``--host`` of the uvicorn process serving this app (``uvicorn …`` or
    ``python -m uvicorn …``; a ``--reload``/``--workers`` child inherits the argv).
    ``""`` in any other process."""
    args = list(sys.argv if argv is None else argv)
    if not args or "uvicorn" not in str(args[0]).replace("\\", "/").lower():
        return ""
    for i, a in enumerate(args):
        if a == "--host" and i + 1 < len(args):
            return args[i + 1]
        if a.startswith("--host="):
            return a.split("=", 1)[1]
    return ""


def bind_hosts() -> list[str]:
    """The configured bind host(s): ``CORVIN_BIND_HOST``, the uvicorn ``--host``,
    ``spec.console.bind`` (lowercased, without port/brackets)."""
    out = [os.environ.get(ENV_BIND, ""), _argv_bind_host()]
    yb = _tenant_yaml_console().get("bind")
    if isinstance(yb, str):
        out.append(yb)
    hosts = []
    for b in out:
        b = (b or "").strip().lower()
        if b:
            hosts.append(b if b.count(":") > 1 and not b.startswith("[") else split_host(b))
    return [h for h in hosts if h]


def _lan_bind(binds: list[str]) -> bool:
    return any(not is_loopback_host(b) for b in binds)


_hostname_cache: dict[str, str] = {}


def _own_hostname() -> str:
    if "h" not in _hostname_cache:
        try:
            _hostname_cache["h"] = socket.gethostname().strip().lower().rstrip(".")
        except OSError:
            _hostname_cache["h"] = ""
    return _hostname_cache["h"]


def derived_hosts() -> frozenset[str]:
    """Exact names this install answers to without an allowlist entry."""
    names: set[str] = set()
    binds = bind_hosts()
    for b in binds:
        if not _is_ip_literal(b) and not is_loopback_host(b):
            names.add(b)
    domain = parse_entry(os.environ.get(ENV_DOMAIN, ""))
    if domain and domain[0] == "exact" and not is_loopback_host(domain[1]) \
            and not _is_ip_literal(domain[1]):
        names.add(domain[1])
        names.add("www." + domain[1])
    ts = os.environ.get(ENV_TS_HOSTNAME, "").strip().lower()
    if ts and _LABEL.match(ts):
        names.add(ts)
    if _lan_bind(binds):
        h = _own_hostname()
        if h and not is_loopback_host(h):
            short = h.split(".", 1)[0]
            names.update({h, short, short + ".local"})
    return frozenset(n for n in names if n)


def _ts_fqdn_allowed(host: str) -> bool:
    """``<CORVIN_TS_HOSTNAME>.<tailnet>.ts.net`` - one tailnet label, closed suffix."""
    ts = os.environ.get(ENV_TS_HOSTNAME, "").strip().lower()
    if not ts or not _LABEL.match(ts):
        return False
    parts = host.split(".")
    return (len(parts) == 4 and parts[0] == ts and parts[2:] == ["ts", "net"]
            and bool(_LABEL.match(parts[1])))


def operator_hosts() -> tuple[frozenset[str], frozenset[str]]:
    """``(exact, suffixes)`` of the operator allowlist (env + tenant YAML)."""
    e1, s1 = _parse_entries(_env_list(ENV_HOSTS), ENV_HOSTS)
    e2, s2 = _parse_entries(_tenant_yaml_hosts(), YAML_KEY)
    return e1 | e2, s1 | s2


def host_allowed(host_header: str | None) -> bool:
    """True when *host_header* names this install (see module docstring).
    A request without a Host header (HTTP/1.0, non-browser) is allowed: a browser
    always sends one, and only a browser carries the ambient cookie."""
    if host_header is None or host_header == "":
        return True
    h = split_host(host_header)
    if not h:
        return False
    if is_loopback_host(h) or _is_ip_literal(h):
        return True
    exact, suffixes = operator_hosts()
    if h in exact or h in derived_hosts():
        return True
    for suf in suffixes:
        if h.endswith(suf) or h == suf[1:]:
            return True
    return _ts_fqdn_allowed(h)


def origin_allowed(origin: str | None, host_header: str | None, scheme: str = "") -> bool:
    """WebSocket Origin rule: absent (non-browser), or the same host as the Host
    header with the same explicit port, the vite dev server, or
    ``CORVIN_ALLOWED_ORIGINS``. *scheme* is accepted for callers and deliberately
    unused: a TLS-terminating proxy turns ``https`` into plain ``ws``."""
    del scheme
    if origin is None:
        return True
    parts = _origin_parts(origin)
    if parts is None:
        return False
    if host_header and parts == (split_host(host_header), _explicit_port(host_header)):
        return True
    if parts in _DEV_ORIGINS:
        return True
    for o in _env_list(ENV_ORIGINS):
        o = o.strip()
        if not o:
            continue
        extra = _origin_parts(o) if "://" in o else (split_host(o), _explicit_port(o))
        if extra is not None and extra == parts:
            return True
    return False


# ── audit ───────────────────────────────────────────────────────────────────

_audit_lock = threading.Lock()
#: (reason, host_fp) -> time of that key's last record; insertion order = LRU order.
_audit_last: dict[tuple[str, str], float] = {}
_audit_window: dict[str, float] = {"start": float("-inf"), "count": 0, "suppressed": 0}


def reset_audit_throttle() -> None:
    """Forget the throttle state (tests)."""
    with _audit_lock:
        _audit_last.clear()
        _audit_window.update(start=float("-inf"), count=0, suppressed=0)


def _fp(value: str | None) -> str:
    return hashlib.sha256((value or "").encode("utf-8", "replace")).hexdigest()[:16]


def _path_class(path: str) -> str:
    """First two path segments only - never ids or query strings."""
    segs = [s for s in (path or "/").split("?", 1)[0].split("/") if s][:2]
    return ("/" + "/".join(segs))[:64]


def admit_record(reason: str, host: str | None, now: float | None = None) -> int | None:
    """Throttle decision for one refusal. ``None`` = not recorded (counted as
    suppressed); otherwise the ``suppressed_count`` the record must carry."""
    key = (reason, _fp(split_host(host or "")))
    t = time.monotonic() if now is None else now
    with _audit_lock:
        last = _audit_last.get(key)
        if last is not None and t - last < _AUDIT_THROTTLE_S:
            del _audit_last[key]
            _audit_last[key] = last  # LRU touch
            _audit_window["suppressed"] += 1
            return None
        if t - _audit_window["start"] >= _AUDIT_THROTTLE_S:
            _audit_window["start"] = t
            _audit_window["count"] = 0
        if _audit_window["count"] >= AUDIT_MAX_PER_WINDOW:
            _audit_window["suppressed"] += 1
            return None
        _audit_window["count"] += 1
        _audit_last.pop(key, None)
        _audit_last[key] = t
        while len(_audit_last) > AUDIT_KEYS_MAX:
            del _audit_last[next(iter(_audit_last))]  # the least recently seen host
        carried = int(_audit_window["suppressed"])
        _audit_window["suppressed"] = 0
        return carried


def write_refusal(*, surface: str, reason: str, scope_type: str, path: str,
                  host: str | None, origin: str | None, suppressed_count: int) -> None:
    """Append one admitted ``http.host_rejected`` record to the _default chain."""
    log.warning("refused %s request: %s (path=%s, %d suppressed before)", scope_type, reason,
                _path_class(path), suppressed_count)
    details = {
        "surface": surface, "reason": reason, "scope_type": scope_type,
        "path_class": _path_class(path), "host_fp": _fp(split_host(host or "")),
        "origin_fp": _fp(origin) if origin is not None else "",
        "suppressed_count": int(suppressed_count),
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


def audit_refusal(*, surface: str, reason: str, scope_type: str, path: str,
                  host: str | None, origin: str | None = None) -> None:
    """Record a refused request, throttled (see module docstring)."""
    suppressed = admit_record(reason, host)
    if suppressed is None:
        return
    write_refusal(surface=surface, reason=reason, scope_type=scope_type, path=path,
                  host=host, origin=origin, suppressed_count=suppressed)


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
    if (scope["type"] == "http" and scope.get("method") == "POST"
            and scope.get("path") in A2A_SIGNED_POST_PATHS):
        return None  # authenticated by the pairing signature; peers use DNS names
    host = _header(scope, b"host")
    if not host_allowed(host):
        return "host_not_allowed"
    if scope["type"] == "websocket" and not origin_allowed(_header(scope, b"origin"), host):
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
        host = _header(scope, b"host")
        # The throttle decision is a dict lookup under a lock: taken on the loop, so
        # only an admitted record (at most AUDIT_MAX_PER_WINDOW a minute) costs a
        # threadpool hop for its fsync'd chain write.
        suppressed = admit_record(reason, host)
        if suppressed is not None:
            from starlette.concurrency import run_in_threadpool  # noqa: PLC0415
            await run_in_threadpool(
                write_refusal, surface=self.surface, reason=reason, scope_type=scope["type"],
                path=scope.get("path", ""), host=host, origin=_header(scope, b"origin"),
                suppressed_count=suppressed)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await send({"type": "http.response.start", "status": 400, "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(REFUSAL_BODY)).encode()),
        ]})
        await send({"type": "http.response.body", "body": REFUSAL_BODY})


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
    raise HTTPException(status_code=400, detail=f"host not allowed: {HINT}")


def require_loopback_host(conn: HTTPConnection) -> None:
    """``/auth/local-login``: the Host itself must be loopback - an allowlisted
    public name never earns a credential-less session. This is by design, and it
    is why a reverse proxy must pass the client's Host through
    (``proxy_set_header Host $host``): one that rewrites it to ``127.0.0.1`` makes
    every proxied visitor look local."""
    host = conn.headers.get("host")
    if host is not None and is_loopback_host(split_host(host)):
        return
    audit_refusal(surface="console", reason="local_login_host", scope_type="http",
                  path=conn.url.path, host=host, origin=conn.headers.get("origin"))
    from fastapi import HTTPException  # noqa: PLC0415
    raise HTTPException(status_code=403, detail="local-login is only available on a loopback host")
