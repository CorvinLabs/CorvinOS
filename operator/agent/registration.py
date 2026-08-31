"""Instance Agent registration with the Management API.

Boot sequence (per ADR-0047):
  1. Agent reads CORVIN_AGENT_PROVISION_TOKEN from env
  2. POST to Management API /register with pubkey PEM + metadata
  3. Management API issues a per-instance mTLS cert (returned in response)
  4. Agent stores cert in agent dir; provisioning token env var is cleared
  5. All subsequent traffic uses mTLS client cert

After registration, CORVIN_AGENT_PROVISION_TOKEN must be treated as
consumed and must NOT be reused.  The caller is responsible for deleting
it from the process environment after this call returns.
"""
from __future__ import annotations

import json
import os
import platform
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import URLError

PROVISION_TOKEN_ENV = "CORVIN_AGENT_PROVISION_TOKEN"
MANAGEMENT_API_URL_ENV = "CORVIN_MANAGEMENT_API_URL"
_DEFAULT_MGMT_URL = "https://api.corvinios.io"


def _mgmt_url() -> str:
    return os.environ.get(MANAGEMENT_API_URL_ENV, _DEFAULT_MGMT_URL).rstrip("/")


def _instance_version() -> str:
    try:
        here = Path(__file__).resolve()
        for parent in [here, *here.parents]:
            vf = parent / "VERSION"
            if vf.exists():
                return vf.read_text().strip()
    except Exception:  # noqa: BLE001
        pass
    return "0.0.0"


def register(
    pubkey_pem: bytes,
    *,
    tenant_id: str | None = None,
    agent_dir: Path | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Register this instance with the Management API.

    Reads the provisioning token from CORVIN_AGENT_PROVISION_TOKEN.
    Returns the registration response dict.  Stores the mTLS cert when
    the response includes one.

    Raises:
      RuntimeError  — missing token, HTTP error, or unexpected response
      URLError      — network unreachable (caller may retry)
    """
    token = os.environ.get(PROVISION_TOKEN_ENV, "").strip()
    if not token:
        raise RuntimeError(
            f"{PROVISION_TOKEN_ENV} is not set; agent cannot register"
        )

    tid = tenant_id or os.environ.get("CORVIN_TENANT_ID", "_default")

    payload = json.dumps({
        "tenant_id": tid,
        "pubkey_pem": pubkey_pem.decode("utf-8"),
        "instance_version": _instance_version(),
        "capabilities": ["byok", "config_push", "health"],
        "platform": platform.system().lower(),
    }).encode("utf-8")

    url = f"{_mgmt_url()}/api/v1/agent/register"
    req = Request(
        url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Corvin-Tenant": tid,
        },
        method="POST",
    )

    # Skip TLS verify only when explicitly set for local dev; never in prod.
    ssl_ctx = None
    if os.environ.get("CORVIN_AGENT_INSECURE_TLS") == "1":
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

    try:
        with urlopen(req, context=ssl_ctx, timeout=timeout) as resp:
            body = resp.read()
            result: dict[str, Any] = json.loads(body)
    except URLError as exc:
        raise URLError(f"Management API unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Management API returned non-JSON: {exc}") from exc

    if not result.get("ok"):
        raise RuntimeError(
            f"Management API registration failed: {result.get('error', result)}"
        )

    # Store mTLS cert if provided.
    if result.get("mtls_cert_pem") and agent_dir is not None:
        _store_mtls_cert(result["mtls_cert_pem"], agent_dir=agent_dir)

    # Consume the provisioning token — remove from env so it can't be reused.
    os.environ.pop(PROVISION_TOKEN_ENV, None)

    from .audit import agent_event
    agent_event("agent.registered", tenant_id=tid, details={
        "instance_version": _instance_version(),
        "capabilities_count": 3,
    })

    return result


def _store_mtls_cert(cert_pem: str, *, agent_dir: Path) -> None:
    """Write the mTLS client cert to agent dir (mode 0600)."""
    cert_path = agent_dir / "mtls_cert.pem"
    tmp = cert_path.with_suffix(".pem.tmp")
    tmp.write_text(cert_pem)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(cert_path)


def build_mtls_context(agent_dir: Path) -> ssl.SSLContext | None:
    """Build an SSL context with the mTLS client cert, or None if absent."""
    cert_path = agent_dir / "mtls_cert.pem"
    key_path = agent_dir / "byok_privkey.pem"
    if not cert_path.exists() or not key_path.exists():
        return None
    ctx = ssl.create_default_context()
    try:
        ctx.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
    except ssl.SSLError:
        return None
    return ctx
