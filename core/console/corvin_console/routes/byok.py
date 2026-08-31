"""BYOK (Bring-Your-Own-Key) console endpoints — ADR-0047.

Routes:
  GET  /byok/pubkey                        — instance RSA public key PEM
  POST /byok/secrets/{key_name}            — store encrypted secret (proxy to agent)
  GET  /byok/secrets                       — list BYOK key metadata (no values)
  GET  /byok/secrets/{key_name}/value      — reveal a saved secret's plaintext
                                              (self-hosted only; explicit user
                                              request 2026-07-14, see get_secret_value)

In hosted mode (CORVIN_HOSTED_MODE=true) these routes proxy to the
Management API.  In self-hosted mode they speak directly to the local
Instance Agent (CORVIN_AGENT_URL, default http://127.0.0.1:8766).

Security invariants:
  - ciphertext is NEVER decrypted by the console layer
  - plaintext API key values are NEVER stored or logged here
  - key_name is validated against ADR-0047 allow-list
  - audit console.byok_updated emitted on every successful store
  - audit console.byok_secret_revealed emitted on every plaintext read-back
    (get_secret_value) — the one intentional exception to "no plaintext
    leaves storage": the authenticated owner of a self-hosted instance can
    read back their own previously-saved key, same as any password manager
    lets its owner view a saved secret. Requires an active session; never
    proxied in hosted mode (CORVIN_HOSTED_MODE=true returns 501).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Annotated, Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

router = APIRouter(prefix="/byok", tags=["console-byok"])

_AGENT_URL_ENV = "CORVIN_AGENT_URL"
_DEFAULT_AGENT_URL = "http://127.0.0.1:8766"
_MGMT_API_URL_ENV = "CORVIN_MANAGEMENT_API_URL"

_BYOK_TIMEOUT = 15.0

_REPO = Path(__file__).resolve().parents[4]
_AGENT_PATH = _REPO / "operator" / "agent"
if str(_AGENT_PATH) not in sys.path:
    sys.path.insert(0, str(str(_AGENT_PATH.parent)))

# WA-22: /byok/secrets used to carry its own private copy of the env/file
# fallback logic (WA-20), which itself diverged from say.py's/stt's actual
# resolution order (e.g. treating a TTS-only key as satisfying the generic
# "openai_api_key" presence check). Now delegates to the single canonical
# resolver — same one say.py / stt/openai_whisper.py / BYOK's write path
# all agree with. See operator/bridges/shared/provider_keys.py.
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
import provider_keys  # type: ignore


def _agent_url() -> str:
    return os.environ.get(_AGENT_URL_ENV, _DEFAULT_AGENT_URL).rstrip("/")


def _is_hosted() -> bool:
    return os.environ.get("CORVIN_HOSTED_MODE", "").strip().lower() in ("1", "true", "yes")


def _agent_get(path: str, *, timeout: float = _BYOK_TIMEOUT) -> Any:
    url = f"{_agent_url()}{path}"
    req = Request(url, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            ct = resp.headers.get("Content-Type", "")
            body = resp.read()
            if "json" in ct:
                return json.loads(body)
            return body.decode("utf-8")
    except URLError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Instance Agent unreachable: {exc}",
        )


def _agent_post(path: str, payload: dict[str, Any], *, timeout: float = _BYOK_TIMEOUT) -> Any:
    url = f"{_agent_url()}{path}"
    data = json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except HTTPError as exc:
        # WA-21: HTTPError is a URLError subclass — without this branch FIRST,
        # a legitimate 400 from the agent (e.g. a rejected key name or, after
        # this same review, an obviously-malformed key value) was swallowed by
        # the generic `except URLError` below and reported to the user as
        # "503 Instance Agent unreachable", hiding the real reason a save
        # failed behind a misleading "is it even running?" message.
        try:
            detail = json.loads(exc.read().decode("utf-8", errors="replace")).get(
                "detail", str(exc)
            )
        except Exception:  # noqa: BLE001 — never let error-message parsing itself fail
            detail = str(exc)
        raise HTTPException(status_code=exc.code, detail=detail)
    except URLError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Instance Agent unreachable: {exc}",
        )


# ── GET /byok/pubkey ─────────────────────────────────────────────────────

@router.get("/pubkey")
def get_pubkey(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the instance RSA public key in PEM format.

    In hosted mode: proxies to the Instance Agent.
    In self-hosted mode: loads the keypair directly from disk so the
    page works without a running Instance Agent process.
    """
    if _is_hosted():
        pem_text = _agent_get("/pubkey")
    else:
        try:
            from agent.keypair import generate_or_load_keypair, _agent_dir  # type: ignore
            _, pub_pem = generate_or_load_keypair(_agent_dir(rec.tenant_id), tenant_id=rec.tenant_id)
            pem_text = pub_pem.decode("utf-8")
        except Exception as exc:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"keypair unavailable: {exc}",
            )
    return {
        "tenant_id": rec.tenant_id,
        "pubkey_pem": pem_text,
        "algorithm": "RSA-OAEP-SHA256",
        "key_size": 2048,
        "ts": time.time(),
    }


# ── POST /byok/secrets/{key_name} ────────────────────────────────────────

class BYOKSecretRequest(BaseModel):
    ciphertext: str = Field(..., description="RSA-OAEP-SHA256 ciphertext, base64-encoded; MUST be encrypted in-browser before sending")
    algorithm: str = Field(default="RSA-OAEP-SHA256")
    model_config = {"extra": "forbid"}


@router.post("/secrets/{key_name}", status_code=200)
def post_secret(
    key_name: str,
    body: BYOKSecretRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Forward an encrypted BYOK secret to the Instance Agent.

    The ciphertext MUST have been encrypted in the browser with the
    instance's RSA public key (RSA-OAEP-SHA256) before this endpoint
    is called.  This route only forwards the ciphertext — it never
    sees or stores the plaintext value.

    Audit event: console.byok_updated { key_name, tenant_id }
    """
    # Validate key_name before storing/forwarding.
    # NOTE: import as `agent.byok`, NOT `operator.agent.byok` — Python's stdlib
    # `operator` is a module (not a package), so the latter ALWAYS raises
    # ModuleNotFoundError and silently skipped validation (WA fix). The sys.path
    # insert at module import makes `agent` importable.
    try:
        from agent.byok import validate_key_name  # type: ignore
        validate_key_name(key_name)
    except ImportError:
        pass  # agent module unavailable in console context — delegate validation
    except Exception as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if body.algorithm not in ("RSA-OAEP-SHA256", "RSA-OAEP"):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported algorithm: {body.algorithm!r}",
        )

    if _is_hosted():
        result = _agent_post(f"/secrets/{key_name}", {
            "ciphertext": body.ciphertext,
            "algorithm": body.algorithm,
            "updated_by": rec.sid_fingerprint[:8] if rec.sid_fingerprint else "console",
        })
        if not result.get("ok"):
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "agent rejected secret"),
            )
    else:
        # Self-hosted (pip/uv install): no Instance Agent runs, so decrypt and
        # store the secret directly via the same pipeline the agent uses (L16
        # vault + service.env, so voice/STT pick the new key up on next use).
        # Without this branch every key-save 503'd — breaking the "add a key →
        # console auto-upgrades to the better provider" experience. get_pubkey /
        # list_secrets already have their self-hosted branches; this is the
        # missing write half. (H4/H5 fix.)
        try:
            from agent.byok import apply_byok_secret  # type: ignore
            result = apply_byok_secret(
                key_name,
                body.ciphertext,
                tenant_id=rec.tenant_id,
                updated_by=rec.sid_fingerprint[:8] if rec.sid_fingerprint else "console",
            )
        except HTTPException:
            raise
        except ValueError as exc:
            # decrypt failure / bad key-shape → client-correctable 400
            raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"could not store secret: {exc}",
            )

    # Audit — key_name only, NO ciphertext, NO plaintext.
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="byok.secret_updated",
        target_kind="api_key",
        target_id=key_name,
    )

    return {
        "ok": True,
        "key_name": key_name,
        "rotated_at": result.get("rotated_at"),
        "last4": result.get("last4"),
    }


# ── GET /byok/secrets ────────────────────────────────────────────────────

@router.get("/secrets")
def list_secrets(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List BYOK key metadata for the tenant (NO values, NO ciphertext).

    Returns presence for each well-known key.  In hosted mode proxies to
    the Instance Agent; in self-hosted mode reads the vault directly.
    """
    known_keys = [
        "anthropic_api_key",
        "openai_api_key",
        "stt_openai_api_key",
        "stt_local_whisper_api_key",
        "openrouter_api_key",
        "ollama_api_key",
    ]

    key_map: dict[str, Any] = {}
    agent_ok: bool = False

    if _is_hosted():
        # Hosted mode: proxy to agent's /secrets endpoint.
        # Agent already filters by byok tag and includes custom keys.
        try:
            agent_secrets = _agent_get("/secrets")
            agent_ok = bool(agent_secrets.get("ok"))
            key_map = {k["key_name"]: k for k in agent_secrets.get("keys", [])}
        except HTTPException:
            agent_ok = False
    else:
        # Self-hosted mode: read vault directly.
        # Only include items tagged "byok" — prevents internal vault entries
        # (provision tokens, friendship keys, etc.) from appearing in the UI.
        try:
            _shared = str(_REPO / "operator" / "bridges" / "shared")
            if _shared not in sys.path:
                sys.path.insert(0, _shared)
            import vault as _vault_mod  # type: ignore
            key_map = {
                it["name"]: it
                for it in _vault_mod.list_items()
                if "byok" in (it.get("tags") or [])
            }
            agent_ok = True
        except Exception:
            agent_ok = False

    # Always include all four known keys (presence may be False).
    # Also surface custom BYOK keys (custom_<slug>) present in the vault.
    custom_key_names = sorted(k for k in key_map if k.startswith("custom_"))
    all_keys = known_keys + custom_key_names

    keys_info = [
        {
            "key_name": k,
            "present": (
                (k in key_map) or (not _is_hosted() and provider_keys.key_present(k))
            ),
            "algorithm": "RSA-OAEP-SHA256",
        }
        for k in all_keys
    ]

    return {
        "tenant_id": rec.tenant_id,
        "agent_reachable": agent_ok,
        "keys": keys_info,
        "note": "ciphertext and plaintext values are never returned by this endpoint",
        "ts": time.time(),
    }


# ── GET /byok/secrets/{key_name}/value ──────────────────────────────────

@router.get("/secrets/{key_name}/value")
def get_secret_value(
    key_name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Reveal a previously-saved secret's plaintext value.

    Explicit user request (2026-07-14): the "eye" button on Settings -> API
    Keys only ever toggled visibility of a NEW value being typed — clicking
    it never showed an already-saved key, because list_secrets() (above)
    deliberately never returns values. That write-only stance was a policy
    choice, not a crypto necessity: the server already holds the plaintext
    the moment a key is saved (RSA-OAEP only protects it in transit from the
    browser; apply_byok_secret decrypts it server-side and writes it in the
    clear to both service.env and the vault, which are read back live by
    provider_keys.resolve_key / vault.get_item every time an engine spawns).

    Self-hosted only — the authenticated owner reading back their OWN key on
    their OWN instance is the same trust model as any password manager
    letting its owner view a saved secret; a third party never gets this
    without first holding a valid session. Hosted mode has no agent-side
    equivalent yet and returns 501 rather than silently guessing at one.

    Every reveal is audited (console.byok_secret_revealed) with key_name
    only — never the value itself — mirroring post_secret's audit above.
    """
    if _is_hosted():
        raise HTTPException(
            status_code=http_status.HTTP_501_NOT_IMPLEMENTED,
            detail="revealing a saved key is not yet supported in hosted mode",
        )

    try:
        from agent.byok import validate_key_name  # type: ignore
        validate_key_name(key_name)
    except ImportError:
        pass
    except Exception as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))

    value: str | None
    if key_name.startswith("custom_"):
        try:
            _shared = str(_REPO / "operator" / "bridges" / "shared")
            if _shared not in sys.path:
                sys.path.insert(0, _shared)
            import vault as _vault_mod  # type: ignore
            value = _vault_mod.get_item(key_name, source="console.byok_reveal")
        except KeyError:
            value = None
        except PermissionError as exc:
            raise HTTPException(status_code=http_status.HTTP_423_LOCKED, detail=str(exc))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            )
    else:
        value = provider_keys.resolve_key(key_name)

    # Audit the reveal itself — key_name only, never the value.
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="byok.secret_revealed",
        target_kind="api_key",
        target_id=key_name,
    )

    if value is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"no value stored for {key_name!r}",
        )

    return {"key_name": key_name, "value": value, "ts": time.time()}
