"""Webhook dispatch with HMAC-SHA256 signing + at-least-once delivery.

ADR-0007 Phase 2.4.

Webhooks are the Gateway's out-bound callback surface. When a run
transitions to a terminal state (``completed`` / ``failed`` /
``budget_exceeded``) AND the originating ``RunRequest`` carried a
``webhook`` block, this module:

1. Resolves the named ``secret_ref`` against the tenant's
   webhook-secret store (``<tenant_home>/global/gateway/webhook_secrets.json``,
   mode ``0o600``).
2. Builds an event payload with the run's terminal state.
3. POSTs the JSON to the operator-configured URL with an
   ``X-Corvin-Signature: sha256=<hex>`` header
   (HMAC-SHA256 over the raw body bytes; GitHub-style).
4. Retries up to ``max_retries`` times on 5xx / network errors with
   exponential backoff. Successful delivery is anything in the 2xx
   range; 4xx is treated as a permanent failure (no retry).

Audit events
------------

| Event | Severity | When |
|---|---|---|
| ``gateway.webhook_dispatched`` | INFO | Final attempt succeeded |
| ``gateway.webhook_delivery_failed`` | WARNING | Gave up after retries |
| ``gateway.webhook_secret_missing`` | WARNING | ``secret_ref`` unresolvable |

The URL host is logged (without path / query) so an operator can
correlate; the secret value, the signature digest and the request
body are never written to the chain.

What this module does NOT do
----------------------------

* It does not retry indefinitely. Operators that need stronger
  delivery guarantees pull from the SSE stream (Phase 2.5) or query
  the GET endpoint.
* It does not poll the URL for accessibility before sending. The
  retry policy IS the health check.
* It does not store the body of a permanently-failed delivery for
  later replay. Phase 7 may add a dead-letter queue when rate
  limiting + persistent queues land; Phase 2 keeps the simpler
  fire-and-give-up model.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import ssl
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Forge path so we can audit + reuse path helpers.
_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402

from .runs import RunRecord

# ── SSRF / egress guard for the outbound webhook POST (PENTEST-6) ─────
#
# The webhook POST is an authenticated caller's arbitrary URL fetched from
# inside the gateway trust boundary — the same shape as the DSI-v2 adapter
# ``ping``. Rather than reinvent the resolver, REUSE the hardened SSRF guard
# that already ships in the console's datasource route (resolves every A/AAAA
# record and rejects loopback / link-local 169.254/16 / private / reserved /
# IPv4-mapped / cloud-IMDS targets, fail-closed). It lives in a console module;
# add ``core/console`` + ``operator/bridges/shared`` to the path (the gateway
# already mounts the console in-process, so the import is available) and import
# the resolver + the L35 EgressGate engine spawns use.
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
_CONSOLE_PKG = _REPO / "core" / "console"
for _p in (_BRIDGES_SHARED, _CONSOLE_PKG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from corvin_console.routes.datasources_http import (  # noqa: E402
        _validated_pinned_ip as _ssrf_validated_pinned_ip,
        _UnsafeUrl as _SsrfUnsafeUrl,
    )
except Exception:  # noqa: BLE001 — guard module unreachable → fail CLOSED below
    _ssrf_validated_pinned_ip = None  # type: ignore[assignment]

    class _SsrfUnsafeUrl(Exception):  # type: ignore[no-redef]
        pass

try:
    from egress_gate import EgressGate as _EgressGate  # noqa: E402
except Exception:  # noqa: BLE001 — L35 gate unreachable → IP guard is the floor
    _EgressGate = None  # type: ignore[assignment]


class _WebhookBlocked(Exception):
    """Outbound webhook URL failed the SSRF / L35 egress guard (fail-closed)."""


def _load_tenant_cfg(tenant_id: str) -> dict[str, Any] | None:
    """Best-effort read of the tenant's ``tenant.corvin.yaml`` (for the L35
    egress policy). Any error → None (the IP-resolution guard is the hard
    floor; the L35 allowlist is defence-in-depth on top of it)."""
    try:
        import yaml  # type: ignore
        p = _forge_paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"
        if not p.exists():
            return None
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _assert_webhook_url_safe(url: str, tenant_id: str) -> str:
    """Fail-closed pre-POST guard for a caller-supplied webhook URL. Returns the
    single validated IP the caller MUST pin the connection to.

    1. Require ``https://`` (defence-in-depth; ``runs.WebhookSpec`` already
       enforces this at validation, but a legacy on-disk record could predate
       the rule).
    2. Reuse the console datasource SSRF resolver: resolve EVERY address and
       reject loopback / link-local (169.254.0.0/16) / private / reserved /
       IPv4-mapped / cloud-metadata targets, returning the ONE vetted IP to pin.
       If the resolver module is unreachable we cannot verify safety → REJECT
       (fail-closed).
    3. Route the host through the same L35 :class:`EgressGate` engine spawns
       use (from the tenant's egress policy). A disabled policy (the pre-L35
       default) passes through; an enabled deny wins. Best-effort: a
       malformed/unreachable gate degrades to the IP-resolution floor.

    Invariant (PENTEST-7 DNS-rebind TOCTOU): validating the host then letting
    httpx re-resolve it at connect time re-opens the hole (a ~0-TTL record can
    hand a public IP here and 169.254.169.254 at connect). The returned pin is
    the ONLY address the dispatcher may connect to.
    """
    if not isinstance(url, str) or not url.lower().startswith("https://"):
        raise _WebhookBlocked("webhook.url must be an https:// URL")

    if _ssrf_validated_pinned_ip is None:
        raise _WebhookBlocked("ssrf guard unavailable — refusing (fail-closed)")
    try:
        pinned_ip = _ssrf_validated_pinned_ip(url)
    except _SsrfUnsafeUrl as exc:
        raise _WebhookBlocked(f"blocked target: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — any resolver error → fail-closed
        raise _WebhookBlocked(f"guard error: {type(exc).__name__}") from exc

    host = urlparse(url).hostname or ""
    if _EgressGate is not None and host:
        try:
            gate = _EgressGate.from_tenant_config(_load_tenant_cfg(tenant_id))
            decision = gate.validate(
                host, engine_id="gateway-webhook", channel="gateway-webhook",
            )
        except Exception:  # noqa: BLE001 — gate malformed/unreachable → floor only
            decision = None
        if decision is not None and not decision.allowed:
            raise _WebhookBlocked("host denied by L35 egress policy")
    return pinned_ip


# ── Event-type registration ──────────────────────────────────────────


_WEBHOOK_EVENTS = {
    "gateway.webhook_dispatched":       "INFO",
    "gateway.webhook_delivery_failed":  "WARNING",
    "gateway.webhook_secret_missing":   "WARNING",
    "gateway.webhook_blocked":          "WARNING",
}
for _evt, _sev in _WEBHOOK_EVENTS.items():
    _security_events.EVENT_SEVERITY.setdefault(_evt, _sev)


# ── Exceptions ───────────────────────────────────────────────────────


class WebhookSecretStoreMalformed(Exception):
    """Tenant dir missing, mode > 0o600, malformed JSON."""


# ── Configuration constants ──────────────────────────────────────────


DEFAULT_MAX_RETRIES = 3
"""Total attempts = 1 (initial) + DEFAULT_MAX_RETRIES (retries)."""

DEFAULT_BACKOFF_S = (1.0, 4.0, 16.0)
"""Backoff between attempts. Tests override via constructor."""

DEFAULT_TIMEOUT_S = 10.0
"""Per-request timeout. Operators tune via WebhookDispatcher constructor."""

SIGNATURE_HEADER = "X-Corvin-Signature"

_SECRET_REF_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")
"""``secret_ref`` charset — keeps it greppable and shell-safe."""

_REQUIRED_MODE = 0o600


# ── Secret store ─────────────────────────────────────────────────────


def _secrets_dir(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "gateway"


def _secrets_path(tenant_id: str) -> Path:
    return _secrets_dir(tenant_id) / "webhook_secrets.json"


def _audit_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


def _validate_secret_ref(ref: str) -> None:
    if not isinstance(ref, str) or not _SECRET_REF_RE.match(ref):
        raise ValueError(
            f"invalid secret_ref {ref!r} — must match {_SECRET_REF_RE.pattern}"
        )


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    os.chmod(path, _REQUIRED_MODE)


def _load_secret_store(tenant_id: str) -> dict[str, Any]:
    """Return the parsed store. Raises :class:`WebhookSecretStoreMalformed`
    on a defective file (mode > 0o600, malformed JSON, bad shape)."""
    p = _secrets_path(tenant_id)
    if not p.exists():
        return {"secrets": {}}
    try:
        st = p.stat()
    except OSError as e:
        raise WebhookSecretStoreMalformed(f"stat failed for {p}: {e}") from e
    mode = st.st_mode & 0o777
    if mode != _REQUIRED_MODE:
        raise WebhookSecretStoreMalformed(
            f"webhook secret store {p} has mode 0o{mode:o}, "
            f"want 0o{_REQUIRED_MODE:o}"
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise WebhookSecretStoreMalformed(f"malformed JSON in {p}: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("secrets"), dict):
        raise WebhookSecretStoreMalformed(f"{p}: missing 'secrets' dict")
    return data


class WebhookSecretStore:
    """Per-tenant store for HMAC secrets used to sign outbound webhooks.

    Plaintext is stored on disk because both sides (the Gateway and
    the customer's HTTP service) need it to compute / verify the
    HMAC. Mode 0o600 + per-tenant tree is the structural guard.
    """

    def set_secret(self, tenant_id: str, ref: str, value: str) -> None:
        validate_tenant_id(tenant_id)
        _validate_secret_ref(ref)
        if not isinstance(value, str) or not value:
            raise ValueError("webhook secret value must be a non-empty string")
        # Refuse to create the tenant tree — Phase 1.4's job.
        sec_dir = _secrets_dir(tenant_id)
        if not sec_dir.parent.parent.exists():
            raise WebhookSecretStoreMalformed(
                f"tenant directory does not exist: {sec_dir.parent.parent}"
            )
        try:
            store = _load_secret_store(tenant_id)
        except WebhookSecretStoreMalformed:
            store = {"secrets": {}}
        store["secrets"][ref] = {
            "value":      value,
            "created_at": time.time(),
        }
        _atomic_write_json(_secrets_path(tenant_id), store)

    def get_secret(self, tenant_id: str, ref: str) -> str | None:
        validate_tenant_id(tenant_id)
        _validate_secret_ref(ref)
        try:
            store = _load_secret_store(tenant_id)
        except WebhookSecretStoreMalformed:
            return None
        entry = store.get("secrets", {}).get(ref)
        if not isinstance(entry, dict):
            return None
        val = entry.get("value")
        if not isinstance(val, str) or not val:
            return None
        return val

    def list_secrets(self, tenant_id: str) -> list[dict[str, Any]]:
        validate_tenant_id(tenant_id)
        try:
            store = _load_secret_store(tenant_id)
        except WebhookSecretStoreMalformed:
            return []
        out = []
        for ref, entry in (store.get("secrets") or {}).items():
            if not isinstance(entry, dict):
                continue
            out.append({
                "ref":        ref,
                "created_at": entry.get("created_at"),
            })
        return sorted(out, key=lambda r: r["ref"])

    def delete_secret(self, tenant_id: str, ref: str) -> bool:
        validate_tenant_id(tenant_id)
        _validate_secret_ref(ref)
        try:
            store = _load_secret_store(tenant_id)
        except WebhookSecretStoreMalformed:
            return False
        if ref not in store.get("secrets", {}):
            return False
        del store["secrets"][ref]
        _atomic_write_json(_secrets_path(tenant_id), store)
        return True


# ── Signature ────────────────────────────────────────────────────────


def sign_body(secret: str, body: bytes) -> str:
    """Return ``sha256=<hex>`` for the given body + secret."""
    digest = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, body: bytes, header_value: str) -> bool:
    """Constant-time compare of ``header_value`` to the expected sig."""
    expected = sign_body(secret, body)
    if not isinstance(header_value, str):
        return False
    return hmac.compare_digest(expected, header_value)


# ── Event payload ────────────────────────────────────────────────────


_STATUS_TO_EVENT = {
    "completed":        "run.completed",
    "failed":           "run.failed",
    "budget_exceeded":  "run.budget_exceeded",
}


def build_event_payload(record: RunRecord) -> dict[str, Any]:
    """Project a RunRecord to the webhook event body."""
    event = _STATUS_TO_EVENT.get(record.status)
    if event is None:
        raise ValueError(f"non-terminal status: {record.status!r}")
    return {
        "event":      event,
        "tenant_id":  record.tenant_id,
        "run_id":     record.run_id,
        "status":     record.status,
        "result":     record.result,
        "error":      record.error,
        "ts":         time.time(),
    }


# ── Audit helper ─────────────────────────────────────────────────────


def _audit(
    event_type: str,
    *,
    tenant_id: str,
    details: dict[str, Any] | None = None,
    severity: str | None = None,
) -> None:
    try:
        _security_events.write_event(
            _audit_path(tenant_id),
            event_type,
            severity=severity,
            details=dict(details or {}),
            hash_chain=True,
        )
    except Exception:
        pass


# ── HTTP dispatch ────────────────────────────────────────────────────


@dataclass
class _Outcome:
    delivered: bool
    attempts: int
    last_status: int | None
    last_error: str | None


class WebhookDispatcher:
    """Send outbound webhooks. Async by design — the run dispatcher
    drives this from inside its own asyncio loop.

    Construction is cheap; instances are stateless except for the
    httpx client they create on first use (lazy import keeps the
    module testable without httpx in environments where the venv
    isn't bootstrapped).
    """

    def __init__(
        self,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_s: tuple[float, ...] = DEFAULT_BACKOFF_S,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        secret_store: WebhookSecretStore | None = None,
        verify: bool | str | ssl.SSLContext = True,
    ) -> None:
        self._max_retries = max(0, int(max_retries))
        self._backoff_s = tuple(backoff_s)
        self._timeout_s = float(timeout_s)
        self._secret_store = secret_store or WebhookSecretStore()
        # TLS verification for the outbound httpx client. Production default is
        # True (verify against the system trust store). A CA-bundle path or an
        # ``ssl.SSLContext`` is accepted for tests that serve TLS with a
        # self-signed cert; verification stays ON, just pinned to a test CA.
        self._verify = verify

    async def dispatch_for_record(
        self,
        record: RunRecord,
        *,
        url: str,
        secret_ref: str,
    ) -> _Outcome:
        """Build event + sign + POST with retry; return outcome."""
        tenant_id = record.tenant_id
        host = urlparse(url).hostname or "<unknown>"

        # PENTEST-6: SSRF / L35 egress guard BEFORE any outbound work. An
        # authenticated caller could set ``webhook.url`` to an internal /
        # loopback / cloud-metadata endpoint; the gateway would otherwise POST
        # to it from inside its trust boundary. Fail-closed: reject + audit.
        try:
            pinned_ip = _assert_webhook_url_safe(url, tenant_id)
        except _WebhookBlocked as exc:
            _audit(
                "gateway.webhook_blocked",
                tenant_id=tenant_id,
                details={
                    "run_id": record.run_id,
                    "host":   host,
                    "reason": str(exc)[:200],
                },
                severity="WARNING",
            )
            return _Outcome(False, 0, None, "ssrf-blocked")

        secret = self._secret_store.get_secret(tenant_id, secret_ref)
        if secret is None:
            _audit(
                "gateway.webhook_secret_missing",
                tenant_id=tenant_id,
                details={
                    "run_id":     record.run_id,
                    "secret_ref": secret_ref,
                    "host":       host,
                },
                severity="WARNING",
            )
            return _Outcome(False, 0, None, "secret-not-found")

        payload = build_event_payload(record)
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        signature = sign_body(secret, body)

        outcome = await self._post_with_retry(
            url=url, pinned_ip=pinned_ip, body=body, signature=signature,
            event=payload["event"],
        )

        details = {
            "run_id":      record.run_id,
            "event":       payload["event"],
            "host":        host,
            "attempts":    outcome.attempts,
            "last_status": outcome.last_status,
        }
        if outcome.delivered:
            _audit(
                "gateway.webhook_dispatched",
                tenant_id=tenant_id,
                details=details,
            )
        else:
            details["last_error"] = outcome.last_error
            _audit(
                "gateway.webhook_delivery_failed",
                tenant_id=tenant_id,
                details=details,
                severity="WARNING",
            )
        return outcome

    async def _post_with_retry(
        self,
        *,
        url: str,
        pinned_ip: str,
        body: bytes,
        signature: str,
        event: str,
    ) -> _Outcome:
        try:
            import httpx  # type: ignore[import]
        except ImportError:
            return _Outcome(False, 0, None, "httpx-missing")

        total = 1 + self._max_retries
        last_status: int | None = None
        last_error: str | None = None

        # PENTEST-7: pin the connection to the ONE IP the SSRF guard validated.
        # httpx re-resolves the hostname at connect time, so a ~0-TTL DNS-rebind
        # could flip a vetted public IP to 169.254.169.254 between the guard and
        # the socket connect. Build a URL whose host is the pinned IP, but keep
        # the original hostname for the ``Host`` header AND for TLS SNI + cert
        # verification via the httpx ``sni_hostname`` request extension (httpx
        # uses it for both the SNI and the certificate-hostname check, so cert
        # validation stays against the real hostname, not the IP).
        orig = httpx.URL(url)
        orig_hostname = orig.host  # decoded hostname (no port)
        host_header = orig.netloc.decode("ascii")  # host[:port], scheme-default omitted
        connect_url = orig.copy_with(host=pinned_ip)

        headers = {
            "Content-Type":         "application/json",
            SIGNATURE_HEADER:       signature,
            "X-Corvin-Event":      event,
            "User-Agent":           "corvin-gateway/0.1",
            "Host":                 host_header,
        }
        extensions = {"sni_hostname": orig_hostname}

        # follow_redirects stays False (httpx default, pinned here explicitly):
        # a followed 3xx would re-resolve the redirect target and re-open the
        # rebind hole. A 3xx is treated as a non-2xx/non-4xx below (retry/fail).
        async with httpx.AsyncClient(
            timeout=self._timeout_s, verify=self._verify, follow_redirects=False,
        ) as client:
            for attempt in range(1, total + 1):
                try:
                    r = await client.post(
                        connect_url, content=body, headers=headers,
                        extensions=extensions,
                    )
                    last_status = r.status_code
                    last_error = None
                    if 200 <= r.status_code < 300:
                        return _Outcome(True, attempt, last_status, None)
                    if 400 <= r.status_code < 500:
                        # Permanent — receiver said the request is bad
                        # and won't accept a retry. Bail out.
                        return _Outcome(
                            False, attempt, last_status,
                            f"http-{r.status_code}",
                        )
                    last_error = f"http-{r.status_code}"
                except Exception as exc:  # network, DNS, TLS, timeout, …
                    last_status = None
                    last_error = f"{type(exc).__name__}: {exc}"

                if attempt < total:
                    delay_idx = min(attempt - 1, len(self._backoff_s) - 1)
                    await asyncio.sleep(self._backoff_s[delay_idx])

        return _Outcome(False, total, last_status, last_error)
