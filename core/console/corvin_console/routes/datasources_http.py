"""DSI v2 HTTP Adapter Registry (ADR-0124 M4).

Any HTTP server implementing /ping, /schema, /query can be registered
as a data source adapter. The bridge protocol is simple and language-agnostic.

Routes:
  GET    /data-sources/adapters/http                  list all HTTP adapters
  PUT    /data-sources/adapters/http/{adapter_id}     register or update
  DELETE /data-sources/adapters/http/{adapter_id}     remove
  POST   /data-sources/adapters/http/{adapter_id}/ping  connectivity test
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

# ADR-0147 CON-DS-V2-01: license gate parity with the DSI-v1 register path.
# _bootstrap (imported above) already put operator/ and operator/license/ on
# sys.path, so the license import resolves without per-file path math.
# Fail-closed FREE_TIER fallback for the limits this route reads. A bare
# ``{}.get`` would return None for every feature, and None is the "unlimited"
# sentinel — so an unimportable license package would FAIL OPEN (every HTTP
# adapter allowed). Hard-code the FREE_TIER cap inline so the gate stays
# fail-closed: free tier allows only local-file connections (no "http"/"dsi_v2_http").
_DS_FREE_TIER_FALLBACK: dict = {"datasource_adapters_allowed": ["local_file"]}

try:
    from license.validator import get_limit as _lic_get_limit  # type: ignore[import]
except ImportError:
    try:
        from license.limits import FREE_TIER as _FREE_TIER  # type: ignore[import]
        _lic_get_limit = _FREE_TIER.get  # type: ignore[assignment]
    except ImportError:
        # Innermost fallback: license package entirely absent. Resolve via the
        # hard-coded FREE_TIER caps (fail-closed), never to None=unlimited.
        _lic_get_limit = _DS_FREE_TIER_FALLBACK.get  # type: ignore[assignment]

import logging
_log = logging.getLogger(__name__)

router = APIRouter()

_VALID_AUTH = frozenset({"none", "bearer", "api_key"})
_VALID_LOCALITIES = frozenset({"local", "eu_cloud", "us_cloud"})
_VALID_EGRESS = frozenset({"none", "restricted", "full"})
_ADAPTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ── Storage ───────────────────────────────────────────────────────────────────

def _adapters_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "datasources" / "http"


def _adapter_path(tid: str, adapter_id: str) -> Path:
    return _adapters_dir(tid) / f"{adapter_id}.json"


def _load_adapter(tid: str, adapter_id: str) -> dict[str, Any] | None:
    p = _adapter_path(tid, adapter_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_adapters(tid: str) -> list[dict[str, Any]]:
    d = _adapters_dir(tid)
    if not d.is_dir():
        return []
    results = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                masked = {k: v for k, v in data.items() if k != "_base_url"}
                results.append(masked)
        except (OSError, json.JSONDecodeError):
            pass
    return results


def _write_adapter(tid: str, adapter_id: str, data: dict[str, Any]) -> None:
    p = _adapter_path(tid, adapter_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# ── SSRF / egress hardening (CON-DS-V2-02 security fix) ─────────────────────────
#
# ``ping_http_adapter`` fetches a fully user-supplied ``base_url`` server-side and
# reflects the response, and can attach a bearer/api-key credential read from the
# process environment. Without the guards below an authenticated (paid-tier)
# console user could:
#   * SSRF the server into internal / cloud-metadata endpoints
#     (``http://169.254.169.254/…``, ``http://localhost:<port>/``) and read the
#     reflected ``name``/``version`` fields, or
#   * exfiltrate ANY process env var — provider API keys, cross-tenant secrets —
#     by naming it as ``auth_env`` and pointing ``base_url`` at an attacker host.
#
# All checks fail CLOSED: on any ambiguity (unparseable URL, no DNS resolution,
# resolver error) the target is refused rather than fetched.

_ALLOWED_SCHEMES = frozenset({"http", "https"})

# Adapter credentials MUST live in env vars matching this convention. Restricting
# ``auth_env`` to it stops a caller naming an arbitrary process env var (e.g.
# ``ANTHROPIC_API_KEY``) and having it sent to a remote host. Documented in
# ``docs/claude-ref/`` and the field description below.
_AUTH_ENV_RE = re.compile(r"^CORVIN_DS_[A-Z0-9_]{1,100}$")

# Hostnames that always resolve to loopback / a metadata service, regardless of
# what the local resolver returns.
_BLOCKED_HOSTNAMES = frozenset({
    "localhost", "ip6-localhost", "ip6-loopback",
    "metadata", "metadata.google.internal",
})

# Cloud-IMDS endpoints not caught by the structural ``is_private`` /
# ``is_link_local`` checks: Oracle (192.0.0.192), Alibaba (100.100.100.200),
# AWS IMDSv2 IPv6 (fd00:ec2::254 unique-local, fe80::a9fe:a9fe link-local-form).
_IMDS_EXTRA = frozenset({
    "192.0.0.192", "100.100.100.200", "fd00:ec2::254", "fe80::a9fe:a9fe",
})


class _UnsafeUrl(Exception):
    """base_url failed the SSRF / egress guard (fail-closed)."""


def _as_ip(text: str) -> "ipaddress._BaseAddress | None":
    """Parse a host string as an IP, normalising the kernel-equivalent encodings
    (decimal ``2130706433``, hex ``0x7f000001``, octal, short-dotted ``127.1``)
    that slip a naive ``127.``/``::1`` prefix check. Returns None for a real
    hostname."""
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        pass
    try:
        return ipaddress.ip_address(socket.inet_ntoa(socket.inet_aton(text)))
    except (OSError, ValueError):
        return None


def _ip_is_blocked(ip: "ipaddress._BaseAddress") -> bool:
    """True for any address that is not a public, routable destination."""
    if (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _ip_is_blocked(mapped):
        return True
    return str(ip) in _IMDS_EXTRA


def _assert_scheme_and_static_host(base_url: str) -> tuple[str, str]:
    """Cheap, liveness-independent checks usable at registration time: scheme
    allowlist + blocked hostname + literal private/metadata IP. Does NOT resolve
    DNS (so a temporarily-down service can still be registered). Returns
    (scheme, host). Raises :class:`_UnsafeUrl`."""
    try:
        parts = urlsplit(base_url)
    except ValueError as exc:  # malformed authority, bad IPv6 literal, …
        raise _UnsafeUrl("base_url is unparseable") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise _UnsafeUrl(f"scheme {scheme!r} not allowed (http/https only)")
    try:
        host = (parts.hostname or "").strip().lower()
    except ValueError as exc:
        raise _UnsafeUrl("base_url has an invalid host") from exc
    if not host:
        raise _UnsafeUrl("base_url has no host")
    if host in _BLOCKED_HOSTNAMES:
        raise _UnsafeUrl("host is a blocked loopback/metadata name")
    lit = _as_ip(host)
    if lit is not None and _ip_is_blocked(lit):
        raise _UnsafeUrl("target IP is private/loopback/link-local/reserved/metadata")
    return scheme, host


def _validated_pinned_ip(base_url: str) -> str:
    """Full pre-fetch guard that ALSO returns the single validated IP to pin.

    Static checks PLUS resolve the hostname to EVERY address and reject if any
    is non-public or if it resolves to nothing. Returns the first validated
    address so the caller can CONNECT to exactly that IP (defeating a
    DNS-rebind TOCTOU — PENTEST-7 — where a ~0-TTL record hands a public IP to
    this guard and 169.254.169.254 to urllib's own resolve at connect time).
    Fail-closed: any ambiguity raises :class:`_UnsafeUrl`."""
    scheme, host = _assert_scheme_and_static_host(base_url)
    lit = _as_ip(host)
    if lit is not None:
        return str(lit)  # literal IP already validated above; no DNS to resolve
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError, UnicodeError) as exc:
        raise _UnsafeUrl("host does not resolve") from exc
    addrs = [info[4][0] for info in infos if info and len(info) > 4 and info[4]]
    if not addrs:
        raise _UnsafeUrl("host resolves to no address")
    pinned: str | None = None
    for addr in addrs:
        ip = _as_ip(str(addr))
        if ip is None or _ip_is_blocked(ip):
            raise _UnsafeUrl("host resolves to a non-public address")
        if pinned is None:
            pinned = str(ip)
    if pinned is None:  # unreachable (addrs non-empty) — belt-and-suspenders
        raise _UnsafeUrl("host resolves to no usable address")
    return pinned


def _assert_base_url_safe(base_url: str) -> None:
    """Validate ``base_url`` (scheme + resolve-all-addresses). Fail-closed.
    Thin wrapper over :func:`_validated_pinned_ip` that discards the pin — kept
    for callers/tests that only need the pass/raise decision."""
    _validated_pinned_ip(base_url)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse ALL HTTP redirects. A public base_url that 302s to
    169.254.169.254 (or any host that would bypass the pre-fetch check) must not
    be followed — fail-closed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        raise _UnsafeUrl(f"redirect (HTTP {code}) blocked")


# ── IP-pinned connections (PENTEST-7: close the resolve→connect TOCTOU) ─────────
#
# urllib re-resolves the hostname when it opens the connection, so a validated
# public IP at guard time can flip to 169.254.169.254 at connect time (~0-TTL
# DNS rebinding). These connection subclasses connect to the ONE IP the guard
# validated while keeping the original hostname for the Host header and TLS
# SNI/cert validation — so the fetch can only ever land on the vetted address.

class _PinnedHTTPConnection(http.client.HTTPConnection):
    _pinned_ip: str | None = None

    def connect(self) -> None:  # noqa: D401
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address,
        )
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    _pinned_ip: str | None = None

    def connect(self) -> None:  # noqa: D401
        self.sock = socket.create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address,
        )
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        else:
            server_hostname = self.host  # SNI + cert validated vs the HOSTNAME
        self.sock = self._context.wrap_socket(
            self.sock, server_hostname=server_hostname,
        )


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, pinned_ip: str) -> None:
        super().__init__()
        self._pinned_ip = pinned_ip

    def http_open(self, req):  # noqa: D401
        pinned = self._pinned_ip

        def factory(host, **kw):
            conn = _PinnedHTTPConnection(host, **kw)
            conn._pinned_ip = pinned
            return conn

        return self.do_open(factory, req)


if hasattr(urllib.request, "HTTPSHandler"):
    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def __init__(self, pinned_ip: str) -> None:
            super().__init__()
            self._pinned_ip = pinned_ip

        def https_open(self, req):  # noqa: D401
            pinned = self._pinned_ip

            def factory(host, **kw):
                conn = _PinnedHTTPSConnection(host, **kw)
                conn._pinned_ip = pinned
                return conn

            return self.do_open(factory, req, context=self._context)
else:  # pragma: no cover — ssl-less build
    _PinnedHTTPSHandler = None  # type: ignore[assignment]


def _build_no_redirect_opener(pinned_ip: str) -> urllib.request.OpenerDirector:
    """No-redirect opener whose HTTP(S) connections are pinned to *pinned_ip*
    (the address the SSRF guard validated) — a connect-time re-resolution can
    no longer redirect the fetch to a private/metadata host."""
    handlers: list[urllib.request.BaseHandler] = [
        _NoRedirectHandler(), _PinnedHTTPHandler(pinned_ip),
    ]
    if _PinnedHTTPSHandler is not None:
        handlers.append(_PinnedHTTPSHandler(pinned_ip))
    return urllib.request.build_opener(*handlers)


# ── Models ────────────────────────────────────────────────────────────────────

class HttpAdapterRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    base_url: str = Field(..., min_length=1, max_length=500)
    auth_type: str = Field("none", description="none | bearer | api_key")
    auth_env: str | None = Field(
        None,
        description="Credential env-var name; MUST match CORVIN_DS_[A-Z0-9_]+ "
                    "(arbitrary env vars are refused to prevent secret exfiltration)",
    )
    auth_header: str | None = Field(None, description="Custom header name (for api_key)")
    locality: str = Field("local")
    network_egress: str = Field("none")
    description: str = Field("", max_length=500)
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/data-sources/adapters/http")
def list_http_adapters(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    adapters = _list_adapters(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(adapters), "adapters": adapters}


@router.put("/data-sources/adapters/http/{adapter_id}")
def register_http_adapter(
    adapter_id: str,
    body: HttpAdapterRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _ADAPTER_ID_RE.match(adapter_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "adapter_id must be lowercase alphanumeric with _ or -",
        )
    if body.auth_type not in _VALID_AUTH:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"auth_type must be one of {sorted(_VALID_AUTH)}",
        )
    # SSRF/exfil fix: a bearer/api_key adapter reads os.environ[auth_env] and
    # sends it to base_url on ping. Restrict auth_env to the adapter-credential
    # convention so a caller cannot name (and exfiltrate) an arbitrary env var.
    if body.auth_type in ("bearer", "api_key"):
        if not body.auth_env or not _AUTH_ENV_RE.match(body.auth_env):
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                "auth_env must name an adapter-credential env var matching "
                "CORVIN_DS_[A-Z0-9_]+ (arbitrary env vars are refused)",
            )
    # SSRF fix: reject non-http(s) schemes and literal private/loopback/metadata
    # targets at registration (cheap, liveness-independent). The full resolve-all
    # -addresses check runs again at ping time where the fetch actually happens.
    try:
        _assert_scheme_and_static_host(body.base_url)
    except _UnsafeUrl as exc:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"base_url rejected: {exc}",
        ) from exc
    if body.locality not in _VALID_LOCALITIES:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"locality must be one of {sorted(_VALID_LOCALITIES)}",
        )
    if body.network_egress not in _VALID_EGRESS:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"network_egress must be one of {sorted(_VALID_EGRESS)}",
        )

    # ADR-0147 CON-DS-V2-01: gate DSI-v2 HTTP registration with the same
    # datasource_adapters_allowed allowlist enforced on the DSI-v1 path
    # (data_sources.py). Every HTTP adapter is a remote (non-local_file) source;
    # FREE_TIER allows only ["local_file"]. Fail-closed: a missing license module
    # resolves via FREE_TIER, never to "all adapters". Skip the gate for an UPDATE
    # of an already-registered adapter (it was admitted under whatever tier applied
    # at create time; we only gate net-new remote adapters).
    _dl_allowed = _lic_get_limit("datasource_adapters_allowed")
    if (
        _load_adapter(rec.tenant_id, adapter_id) is None  # net-new only
        and _dl_allowed is not None
        and "http" not in _dl_allowed
        and "dsi_v2_http" not in _dl_allowed
    ):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="datasource.http_adapter_registered",
            target_kind="http_adapter",
            target_id=adapter_id,
            reason="license_limit_exceeded",
        )
        raise HTTPException(
            status_code=402,
            detail={
                "error": "license_limit",
                "feature": "datasource_adapters_allowed",
                "msg": "Remote HTTP data-source adapters require a paid tier "
                       "(free tier is local-file only).",
                "upgrade_url": "https://corvin-labs.com/pricing",
            },
        )

    existing = _load_adapter(rec.tenant_id, adapter_id)
    is_update = existing is not None

    manifest: dict[str, Any] = {
        "adapter_id": adapter_id,
        "display_name": body.display_name,
        "base_url_hash": _hash_url(body.base_url),
        "_base_url": body.base_url,
        "auth_type": body.auth_type,
        "auth_env": body.auth_env,
        "auth_header": body.auth_header,
        "locality": body.locality,
        "network_egress": body.network_egress,
        "description": body.description,
        "protocol": "dsi_v2_http",
        "created_at": existing.get("created_at", time.time()) if existing else time.time(),
        "updated_at": time.time(),
    }

    try:
        _write_adapter(rec.tenant_id, adapter_id, manifest)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="datasource.http_adapter_updated" if is_update else "datasource.http_adapter_registered",
        target_kind="http_adapter",
        target_id=adapter_id,
    )
    return {"ok": True, "adapter_id": adapter_id, "updated": is_update}


@router.delete("/data-sources/adapters/http/{adapter_id}")
def remove_http_adapter(
    adapter_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    p = _adapter_path(rec.tenant_id, adapter_id)
    if not p.exists():
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"adapter {adapter_id!r} not found",
        )
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="datasource.http_adapter_removed",
        target_kind="http_adapter",
        target_id=adapter_id,
    )
    return {"ok": True, "adapter_id": adapter_id}


@router.post("/data-sources/adapters/http/{adapter_id}/ping")
def ping_http_adapter(
    adapter_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    manifest = _load_adapter(rec.tenant_id, adapter_id)
    if manifest is None:
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"adapter {adapter_id!r} not found",
        )

    base_url = manifest.get("_base_url", "")

    # Egress fix #5: respect a declared no-egress. An adapter that declares
    # network_egress="none" must not make outbound calls — refuse the ping and
    # tell the operator to declare restricted/full to enable connectivity tests.
    if manifest.get("network_egress", "none") == "none":
        return {
            "ok": False,
            "adapter_id": adapter_id,
            "reachable": False,
            "error": "egress_not_declared",
        }

    # SSRF fix: validate scheme + resolve every address before fetching, and
    # PIN the validated IP so the connect below cannot be rebound to a
    # private/metadata host between this check and the socket connect
    # (PENTEST-7 DNS-rebind TOCTOU).
    try:
        pinned_ip = _validated_pinned_ip(base_url)
    except _UnsafeUrl:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="datasource.http_adapter_pinged",
            target_kind="http_adapter",
            target_id=adapter_id,
            reason="egress_blocked",
        )
        return {
            "ok": False,
            "adapter_id": adapter_id,
            "reachable": False,
            "error": "egress_blocked",
        }

    ping_url = base_url.rstrip("/") + "/ping"

    try:
        req = urllib.request.Request(ping_url, method="GET")
        auth_type = manifest.get("auth_type", "none")
        auth_env = manifest.get("auth_env")
        # Exfil fix: only read env vars matching the adapter-credential
        # convention, even for a manifest written before this gate existed
        # (defense-in-depth against a legacy arbitrary auth_env). Fail-closed:
        # an invalid auth_env means no credential is attached, never os.environ
        # of an arbitrary name.
        if auth_type in ("bearer", "api_key") and auth_env and _AUTH_ENV_RE.match(auth_env):
            token = os.environ.get(auth_env, "")
            if token and auth_type == "bearer":
                req.add_header("Authorization", f"Bearer {token}")
            elif token and auth_type == "api_key":
                header = manifest.get("auth_header", "X-API-Key")
                req.add_header(header, token)

        # No-redirect + IP-pinned opener: a 3xx to a private/metadata host must
        # not be followed, and the connect is pinned to the validated IP.
        opener = _build_no_redirect_opener(pinned_ip)
        with opener.open(req, timeout=5.0) as resp:
            body = json.loads(resp.read())

        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="datasource.http_adapter_pinged",
            target_kind="http_adapter",
            target_id=adapter_id,
        )
        return {
            "ok": True,
            "adapter_id": adapter_id,
            "reachable": True,
            "name": body.get("name", ""),
            "version": body.get("version", ""),
        }
    except _UnsafeUrl:
        # Blocked redirect (3xx to a private/metadata host). Fail-closed.
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="datasource.http_adapter_pinged",
            target_kind="http_adapter",
            target_id=adapter_id,
            reason="egress_blocked",
        )
        return {"ok": False, "adapter_id": adapter_id, "reachable": False, "error": "egress_blocked"}
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "adapter_id": adapter_id,
            "reachable": False,
            "http_status": exc.code,
            "error": "unreachable",
        }
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "adapter_id": adapter_id, "reachable": False, "error": "internal error"}
