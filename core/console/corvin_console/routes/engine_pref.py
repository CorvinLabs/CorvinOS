"""Per-chat engine preference API — ADR-0067.

Wraps engine_switch.py so the console SPA can read and write the
per-chat engine override without typing /engine in the chat.

Storage: <corvin_home>/global/engine_pref/console__<chat_key>.json
Channel is always "console" for console-web sessions.

Endpoints
---------
  GET    /settings/engine-pref/{chat_key}   → current preference + effective engine
  PUT    /settings/engine-pref/{chat_key}   → set override (engine + optional model)
  DELETE /settings/engine-pref/{chat_key}   → clear override (revert to tenant default)

Resolution order (mirrors adapter dispatch):
  per-chat preference → tenant spec.default_engine → claude_code

MUST NOT import anthropic.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any

import yaml  # type: ignore[import-not-found]
from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))
_FORGE = _REPO / "operator" / "forge"
if str(_FORGE) not in sys.path:
    sys.path.insert(0, str(_FORGE))

import engine_switch as _es  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402

router = APIRouter(prefix="/settings/engine-pref", tags=["console-engine-pref"])

_CONSOLE_CHANNEL = "console"

# ── Helpers ───────────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    return _forge_paths.corvin_home()


def _tenant_default_engine(tenant_id: str) -> str | None:
    """Read tenant.corvin.yaml::spec.default_engine for the given tenant. None = not set."""
    path = _corvin_home() / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return (data.get("spec") or {}).get("default_engine") or None
    except Exception:
        return None


def _effective_engine(per_chat: dict[str, Any] | None, tenant_default: str | None) -> str:
    """Resolve the effective engine in the same order the adapter uses."""
    if per_chat and per_chat.get("engine"):
        return per_chat["engine"]
    if tenant_default:
        return tenant_default
    return "claude_code"


# ── Pydantic models ───────────────────────────────────────────────────────

class EnginePrefResponse(BaseModel):
    chat_key: str
    per_chat_engine: str | None = Field(
        None, description="Per-chat override, or null if using tenant default",
    )
    per_chat_model: str | None = None
    tenant_default: str | None = Field(
        None, description="Tenant-level default engine (from spec.default_engine)",
    )
    effective_engine: str = Field(
        description="What the adapter will actually use (per-chat > tenant > claude_code)",
    )
    source: str = Field(
        description="'per_chat' | 'tenant_default' | 'system_default'",
    )


class EnginePrefUpdate(BaseModel):
    engine: str = Field(description="Engine alias (e.g. 'hermes', 'claude_code', 'codex', 'copilot', 'copilot-shell')")
    model: str | None = Field(None, description="Optional model alias (e.g. 'hermes-fast')")


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("/{chat_key}", response_model=EnginePrefResponse)
def get_engine_pref(
    chat_key: str,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EnginePrefResponse:
    """Return the effective engine for a console chat session."""
    pref = _es.current(_CONSOLE_CHANNEL, chat_key)
    tenant_default = _tenant_default_engine(_rec.tenant_id)
    effective = _effective_engine(pref, tenant_default)
    if pref and pref.get("engine"):
        source = "per_chat"
    elif tenant_default:
        source = "tenant_default"
    else:
        source = "system_default"
    return EnginePrefResponse(
        chat_key=chat_key,
        per_chat_engine=pref.get("engine") if pref else None,
        per_chat_model=pref.get("model") if pref else None,
        tenant_default=tenant_default,
        effective_engine=effective,
        source=source,
    )


@router.put("/{chat_key}", response_model=EnginePrefResponse)
def set_engine_pref(
    chat_key: str,
    body: EnginePrefUpdate,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> EnginePrefResponse:
    """Set per-chat engine override for a console chat session."""
    # Validate engine alias
    spec = _es.resolve_alias(body.engine)
    if spec is None:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown engine alias {body.engine!r}. Supported: {_es.supported_aliases()}",
        )

    engine_id = spec["engine"]
    model = body.model or spec.get("model")

    from .engine import _ENGINE_METADATA, _engine_meta_fallback  # noqa: PLC0415
    _meta = _ENGINE_METADATA.get(engine_id) or _engine_meta_fallback(engine_id)
    if not _meta.get("os_capable", False):
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Engine {engine_id!r} is worker-only and cannot be used as an OS engine. "
                f"Use /engine <alias> in the chat or select a worker engine via default_worker_engine."
            ),
        )

    _es.set_preference(
        _CONSOLE_CHANNEL, chat_key,
        engine=engine_id,
        model=model,
        uid=_rec.sid_fingerprint,
    )

    try:
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="engine_pref_set",
            target_kind="chat_session",
            target_id=chat_key,
        )
    except Exception:
        pass

    tenant_default = _tenant_default_engine(_rec.tenant_id)
    return EnginePrefResponse(
        chat_key=chat_key,
        per_chat_engine=engine_id,
        per_chat_model=model,
        tenant_default=tenant_default,
        effective_engine=engine_id,
        source="per_chat",
    )


@router.delete("/{chat_key}", response_model=EnginePrefResponse)
def clear_engine_pref(
    chat_key: str,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> EnginePrefResponse:
    """Clear per-chat engine override — revert to tenant default."""
    _es.clear_preference(_CONSOLE_CHANNEL, chat_key, uid=_rec.sid_fingerprint)

    try:
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="engine_pref_cleared",
            target_kind="chat_session",
            target_id=chat_key,
        )
    except Exception:
        pass

    tenant_default = _tenant_default_engine(_rec.tenant_id)
    effective = _effective_engine(None, tenant_default)
    source = "tenant_default" if tenant_default else "system_default"
    return EnginePrefResponse(
        chat_key=chat_key,
        per_chat_engine=None,
        per_chat_model=None,
        tenant_default=tenant_default,
        effective_engine=effective,
        source=source,
    )
