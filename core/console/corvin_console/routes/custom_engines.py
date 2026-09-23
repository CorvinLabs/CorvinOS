"""Custom Engine Registry (ADR-0124 M1).

Operators register their own engines (OpenAI-compat, Anthropic-compat,
Ollama) without modifying source code.

Routes:
  GET  /engines/custom                   list all registered custom engines
  PUT  /engines/custom/{engine_id}       register or update engine manifest
  DELETE /engines/custom/{engine_id}     remove engine manifest
  POST /engines/custom/{engine_id}/ping  test connectivity
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any

import yaml  # type: ignore[import-not-found]

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

import logging
_log = logging.getLogger(__name__)

router = APIRouter()

_VALID_TRANSPORTS = frozenset({"openai_compat", "anthropic", "ollama"})
_VALID_LOCALITIES = frozenset({"local", "eu_cloud", "us_cloud"})
_VALID_EGRESS = frozenset({"none", "restricted", "full"})
_VALID_CLASSIFICATIONS = frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"})
_ENGINE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ── Storage ───────────────────────────────────────────────────────────────────

def _load_tenant_yaml(tid: str) -> dict[str, Any]:
    path = _forge_paths.tenant_global_dir(tid) / "tenant.corvin.yaml"
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        return {}


def _engines_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "engines" / "custom"


def _engine_path(tid: str, engine_id: str) -> Path:
    return _engines_dir(tid) / f"{engine_id}.json"


def _load_engine(tid: str, engine_id: str) -> dict[str, Any] | None:
    p = _engine_path(tid, engine_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_engines(tid: str) -> list[dict[str, Any]]:
    d = _engines_dir(tid)
    if not d.is_dir():
        return []
    results = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Mask the raw URL in list output
                masked = {k: v for k, v in data.items() if k != "_base_url"}
                results.append(masked)
        except (OSError, json.JSONDecodeError):
            pass
    return results


def _write_engine(tid: str, engine_id: str, data: dict[str, Any]) -> None:
    p = _engine_path(tid, engine_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.chmod(tmp, 0o600)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _hash_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


# ── Models ────────────────────────────────────────────────────────────────────

class ModelEntry(BaseModel):
    id: str = Field(..., min_length=1, max_length=200)
    context_length: int = Field(8192, ge=1024, le=2_000_000)
    model_config = {"extra": "forbid"}


class EngineManifestRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    transport: str = Field(..., description="openai_compat | anthropic | ollama")
    base_url: str = Field(..., min_length=1, max_length=500)
    auth_env: str | None = Field(None, description="Vault env-var name for API key")
    locality: str = Field("local")
    network_egress: str = Field("none")
    models: list[ModelEntry] = Field(default_factory=list)
    data_classification: str = Field("PUBLIC")
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/engines/custom")
def list_custom_engines(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    engines = _list_engines(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(engines), "engines": engines}


@router.put("/engines/custom/{engine_id}")
def register_custom_engine(
    engine_id: str,
    body: EngineManifestRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _ENGINE_ID_RE.match(engine_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "engine_id must be lowercase alphanumeric with _ or - (max 64 chars)",
        )
    for field, valid in [
        ("transport", _VALID_TRANSPORTS),
        ("locality", _VALID_LOCALITIES),
        ("network_egress", _VALID_EGRESS),
        ("data_classification", _VALID_CLASSIFICATIONS),
    ]:
        val = getattr(body, field)
        if val not in valid:
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST,
                f"{field} must be one of {sorted(valid)}",
            )

    _tenant_data = _load_tenant_yaml(rec.tenant_id)
    _dr = (_tenant_data.get("spec") or {}).get("data_residency") or {}
    _allowed_engines = _dr.get("allowed_engines", [])
    _dr_zone = _dr.get("zone", "")
    if _allowed_engines and _dr_zone == "eu" and body.locality == "us_cloud":
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Tenant data_residency zone {_dr_zone!r} does not permit locality {body.locality!r}. "
            f"Allowed engines: {_allowed_engines}",
        )

    existing = _load_engine(rec.tenant_id, engine_id)
    is_update = existing is not None

    manifest: dict[str, Any] = {
        "engine_id": engine_id,
        "display_name": body.display_name,
        "transport": body.transport,
        "base_url_hash": _hash_url(body.base_url),
        "_base_url": body.base_url,
        "auth_env": body.auth_env,
        "locality": body.locality,
        "network_egress": body.network_egress,
        "models": [m.model_dump() for m in body.models],
        "data_classification": body.data_classification,
        "created_at": existing.get("created_at", time.time()) if existing else time.time(),
        "updated_at": time.time(),
    }

    try:
        _write_engine(rec.tenant_id, engine_id, manifest)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="engine.custom_updated" if is_update else "engine.custom_registered",
        target_kind="custom_engine",
        target_id=engine_id,
    )
    return {"ok": True, "engine_id": engine_id, "updated": is_update}


@router.delete("/engines/custom/{engine_id}")
def remove_custom_engine(
    engine_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    p = _engine_path(rec.tenant_id, engine_id)
    if not p.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"engine {engine_id!r} not found")
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="engine.custom_removed",
        target_kind="custom_engine",
        target_id=engine_id,
    )
    return {"ok": True, "engine_id": engine_id}


@router.post("/engines/custom/{engine_id}/ping")
def ping_custom_engine(
    engine_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    manifest = _load_engine(rec.tenant_id, engine_id)
    if manifest is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"engine {engine_id!r} not found")

    base_url = manifest.get("_base_url", "")
    transport = manifest.get("transport", "openai_compat")

    if transport == "ollama":
        probe_url = base_url.rstrip("/") + "/api/tags"
    else:
        probe_url = base_url.rstrip("/") + "/models"

    try:
        req = urllib.request.Request(probe_url, method="GET")
        auth_env = manifest.get("auth_env")
        if auth_env:
            token = os.environ.get(auth_env, "")
            if token:
                req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read())
        model_list = data.get("models", data.get("data", []))
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="engine.custom.ping",
            target_kind="custom_engine",
            target_id=engine_id,
        )
        return {
            "ok": True,
            "engine_id": engine_id,
            "reachable": True,
            "model_count": len(model_list) if isinstance(model_list, list) else 0,
        }
    except urllib.error.HTTPError as exc:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="engine.custom.ping",
            target_kind="custom_engine",
            target_id=engine_id,
            reason="unreachable",
        )
        return {
            "ok": False,
            "engine_id": engine_id,
            "reachable": False,
            "http_status": exc.code,
            "error": "unreachable",
        }
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="engine.custom.ping",
            target_kind="custom_engine",
            target_id=engine_id,
            reason="internal error",
        )
        return {"ok": False, "engine_id": engine_id, "reachable": False, "error": "internal error"}
