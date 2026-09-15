"""Instance Agent — FastAPI application.

Endpoints:
  GET  /health                      liveness + readiness probe
  GET  /metrics                     Prometheus text metrics
  GET  /pubkey                      RSA public key PEM
  POST /secrets/{key_name}          receive encrypted BYOK blob, decrypt, vault-write
  POST /config-push                 receive Management API push events (batch)

Bind address: CORVIN_AGENT_HOST (default 127.0.0.1)
Port:         CORVIN_AGENT_PORT (default 8766)

Must NOT import anthropic.
"""
from __future__ import annotations

import base64
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# Make shared/ modules importable when running as a standalone process.
_HERE = Path(__file__).resolve()
for _p in [_HERE, *_HERE.parents]:
    if (_p / "operator").is_dir():
        _shared = _p / "operator" / "bridges" / "shared"
        if str(_shared) not in sys.path:
            sys.path.insert(0, str(_shared))
        _forge = _p / "operator" / "forge"
        if str(_forge) not in sys.path:
            sys.path.insert(0, str(_forge))
        break

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel, Field

from .keypair import generate_or_load_keypair, get_public_key_pem, _agent_dir
from .health import health_payload, metrics_text
from .config_push import handle as handle_push

logger = logging.getLogger("corvin.agent")

# Resolved at startup via lifespan.
_AGENT_DIR: Path | None = None
_VAULT_DIR: Path | None = None


@asynccontextmanager
async def _lifespan(application: "FastAPI"):  # type: ignore[name-defined]
    global _AGENT_DIR, _VAULT_DIR
    tid = os.environ.get("CORVIN_TENANT_ID", "_default")
    if _AGENT_DIR is None:
        _AGENT_DIR = _agent_dir(tid)
    _VAULT_DIR = None  # defaults to vault.py resolution

    _, pub_pem = generate_or_load_keypair(_AGENT_DIR, tenant_id=tid)
    logger.info("Instance Agent started: tenant=%s keypair_ready=true", tid)

    token = os.environ.get("CORVIN_AGENT_PROVISION_TOKEN", "").strip()
    if token:
        _attempt_registration(pub_pem, tid)

    yield


app = FastAPI(
    title="Corvin Instance Agent",
    description="Data-plane bridge for hosted-mode tenant management (ADR-0047)",
    version="1.0.0",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
)


def _attempt_registration(pub_pem: bytes, tid: str) -> None:
    from .registration import register
    try:
        result = register(pub_pem, tenant_id=tid, agent_dir=_AGENT_DIR)
        logger.info("Registered with Management API: %s", result.get("instance_id"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Registration failed (will retry on next boot): %s", exc)


# ── Endpoints ─────────────────────────────────────────────────────────────

@app.get("/health")
async def get_health() -> dict[str, Any]:
    return health_payload(agent_dir=_AGENT_DIR)


@app.get("/metrics", response_class=PlainTextResponse)
async def get_metrics() -> str:
    return metrics_text(agent_dir=_AGENT_DIR)


@app.get("/pubkey", response_class=PlainTextResponse)
async def get_pubkey() -> str:
    """Return the instance RSA public key PEM for BYOK encryption."""
    tid = os.environ.get("CORVIN_TENANT_ID", "_default")
    pem = get_public_key_pem(_AGENT_DIR, tenant_id=tid)
    return pem.decode("utf-8")


class SecretPayload(BaseModel):
    ciphertext: str = Field(..., description="RSA-OAEP-SHA256 ciphertext, base64-encoded")
    algorithm: str = Field(default="RSA-OAEP-SHA256")
    updated_by: str = Field(default="console")
    model_config = {"extra": "forbid"}


@app.post("/secrets/{key_name}", status_code=200)
async def post_secret(key_name: str, body: SecretPayload) -> dict[str, Any]:
    """Receive an encrypted BYOK secret, decrypt, write to vault.

    The ciphertext must have been encrypted with this instance's RSA
    public key using RSA-OAEP-SHA256 (Web Crypto API compatible).
    """
    if body.algorithm not in ("RSA-OAEP-SHA256", "RSA-OAEP"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported algorithm: {body.algorithm!r} (only RSA-OAEP-SHA256)",
        )

    from .byok import validate_key_name, apply_byok_secret
    try:
        validate_key_name(key_name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    tid = os.environ.get("CORVIN_TENANT_ID", "_default")
    try:
        result = apply_byok_secret(
            key_name,
            body.ciphertext,
            agent_dir=_AGENT_DIR,
            vault_dir=_VAULT_DIR,
            tenant_id=tid,
            updated_by=body.updated_by,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"keypair not ready: {exc}")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return result


@app.get("/secrets")
async def list_secrets() -> dict[str, Any]:
    """Return vault key inventory — presence only, no values.

    Used by the console's GET /byok/secrets to determine which BYOK
    keys are already configured in the vault.  Never returns key
    values or ciphertext.

    Only returns keys tagged "byok" to prevent internal vault items
    (provision tokens, etc.) from leaking into the BYOK UI.
    """
    known_keys = [
        "anthropic_api_key",
        "openai_api_key",
        "stt_openai_api_key",
        "stt_local_whisper_api_key",
    ]
    key_map: dict[str, Any] = {}
    try:
        import vault as _vault  # type: ignore
        # Only include items tagged "byok" — filters out internal vault entries.
        key_map = {
            it["name"]: it
            for it in _vault.list_items()
            if "byok" in (it.get("tags") or [])
        }
    except Exception:
        pass

    # Include custom BYOK keys (custom_<slug>) stored in the vault.
    custom_key_names = sorted(k for k in key_map if k.startswith("custom_"))
    all_keys = known_keys + custom_key_names

    return {
        "ok": True,
        "keys": [
            {
                "key_name": k,
                "present": k in key_map,
                "algorithm": "RSA-OAEP-SHA256",
            }
            for k in all_keys
        ],
        "ts": time.time(),
    }


class ConfigPushPayload(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)
    model_config = {"extra": "allow"}


@app.post("/config-push", status_code=200)
async def post_config_push(body: ConfigPushPayload) -> dict[str, Any]:
    """Receive one or more config-push events from the Management API."""
    tid = os.environ.get("CORVIN_TENANT_ID", "_default")
    results = []
    for ev in body.events:
        r = handle_push(ev, agent_dir=_AGENT_DIR, vault_dir=_VAULT_DIR, tenant_id=tid)
        results.append(r)
    all_ok = all(r.get("ok") for r in results)
    return {"ok": all_ok, "results": results}


# ── Entry point ────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    host = os.environ.get("CORVIN_AGENT_HOST", "127.0.0.1")
    port = int(os.environ.get("CORVIN_AGENT_PORT", "8766"))
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("operator.agent.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
