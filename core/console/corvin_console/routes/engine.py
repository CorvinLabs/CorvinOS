"""Console engine-selector settings — Claude Code only (v2.0).

ADR-0067 M2.4 — simplified single-engine deployment.

Endpoints
---------
  GET  /settings/engine        → {default_engine, valid_engines}
  PUT  /settings/engine        → body {default_engine} → saves to tenant YAML
  GET  /settings/engine/health → {healthy, message}
  GET  /settings/engine/catalog → {engines, models} (Claude models only)
  GET  /settings/engine/capabilities → engine capability profile

Settings are stored in tenant.corvin.yaml::spec.default_engine.
Graceful degradation: old config with "hermes" → auto-corrects to "claude_code".

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Annotated, Any

import yaml  # type: ignore[import-not-found]
from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

_log = logging.getLogger(__name__)

_TENANT_YAML_FILENAME = "tenant.corvin.yaml"

router = APIRouter(prefix="/settings/engine", tags=["console-engine"])


# ---------------------------------------------------------------------------
# Engine & Model metadata (Claude Code only)
# ---------------------------------------------------------------------------

_ENGINE_METADATA = {
    "claude_code": {
        "id": "claude_code",
        "label": "Claude Code",
        "description": (
            "Full-featured AI assistant: /btw, hooks, skills, Forge MCP, "
            "all permission modes. Best for complex reasoning and code tasks."
        ),
        "local": False,
        "requires": "Anthropic API key (claude auth login)",
        "os_capable": True,
    }
}

# Anthropic Claude API models (official releases)
_CLAUDE_MODELS = [
    {"id": "claude-opus-4-1", "label": "Claude Opus 4.1", "default": False},
    {"id": "claude-sonnet-4-20250514", "label": "Claude Sonnet 4", "default": True},
    {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5", "default": False},
]


# ---------------------------------------------------------------------------
# Tenant YAML helpers
# ---------------------------------------------------------------------------

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    return Path.home() / ".corvin"


def _tenant_yaml_path(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / _TENANT_YAML_FILENAME


def _load_tenant_yaml(tenant_id: str) -> dict[str, Any]:
    """Load tenant configuration from YAML, gracefully handling missing/corrupt files."""
    path = _tenant_yaml_path(tenant_id)
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except Exception:
        _log.warning(f"Failed to load tenant YAML at {path}", exc_info=True)
        return {}


def _save_tenant_yaml(tenant_id: str, data: dict[str, Any]) -> None:
    """Save tenant configuration to YAML atomically."""
    path = _tenant_yaml_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via temp file
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(data, default_flow_style=False))
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class EngineSettingResponse(BaseModel):
    """Engine setting response: always Claude Code."""
    default_engine: str = Field(
        "claude_code",
        description="OS engine (always 'claude_code')",
    )
    valid_engines: list[str] = Field(
        default_factory=lambda: ["claude_code"],
        description="Engines available in the console",
    )


class EngineSettingUpdate(BaseModel):
    """Engine setting update request."""
    model_config = {"extra": "forbid"}

    default_engine: str | None = Field(
        "claude_code",
        description="Engine selection; must be 'claude_code' or None",
    )


class EngineHealthResponse(BaseModel):
    """Health check response."""
    healthy: bool = Field(True, description="Always True for Claude Code")
    message: str = Field("Claude Code is ready", description="Status message")


class EngineCatalogResponse(BaseModel):
    """Engine catalog with models."""
    engines: list[dict[str, Any]]
    models: list[dict[str, Any]]


class EngineCapabilitiesResponse(BaseModel):
    """Claude Code capability profile."""
    engine_id: str
    capabilities: dict[str, bool]
    eaos_gaps: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=EngineSettingResponse)
def get_engine_setting(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EngineSettingResponse:
    """Return the current tenant-level engine settings.

    ADR-0007: tenant_id from SessionRecord, never env var.
    Graceful fallback: old config with "hermes" → auto-corrects to "claude_code".
    """
    data = _load_tenant_yaml(_rec.tenant_id)
    spec = data.get("spec") or {}

    # Read configured engine; auto-correct if it's an old legacy value
    default = spec.get("default_engine", "claude_code")
    if default not in ("claude_code",):
        _log.info(f"Auto-correcting legacy engine {default!r} → claude_code")
        default = "claude_code"

    return EngineSettingResponse(
        default_engine=default,
        valid_engines=["claude_code"],
    )


@router.put("", response_model=EngineSettingResponse)
def put_engine_setting(
    body: EngineSettingUpdate,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> EngineSettingResponse:
    """Update the tenant-level default engine.

    Claude Code only — accepts "claude_code" or None (defaults to claude_code).
    Writes to tenant.corvin.yaml::spec.default_engine.

    ADR-0007: tenant_id from SessionRecord, never env var.
    """
    # Validate engine value — only claude_code allowed
    engine = body.default_engine or "claude_code"
    if engine != "claude_code":
        console_audit.action_failed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="engine.setting.update",
            target_kind="engine_setting",
            target_id="default_engine",
            reason="invalid_engine_value",
        )
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only 'claude_code' is supported. Got: {engine!r}",
        )

    # Load, update, and save tenant config
    data = _load_tenant_yaml(_rec.tenant_id)
    if "spec" not in data or not isinstance(data.get("spec"), dict):
        data["spec"] = {}

    data["spec"]["default_engine"] = "claude_code"
    _save_tenant_yaml(_rec.tenant_id, data)

    # Audit the change
    try:
        console_audit.action_performed(
            tenant_id=_rec.tenant_id,
            sid_fingerprint=_rec.sid_fingerprint,
            action="engine_setting_updated",
            target_kind="engine",
            target_id="claude_code",
        )
    except Exception:  # noqa: BLE001
        pass

    return EngineSettingResponse(
        default_engine="claude_code",
        valid_engines=["claude_code"],
    )


@router.get("/health", response_model=EngineHealthResponse)
def get_engine_health(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EngineHealthResponse:
    """Health check for Claude Code engine."""
    return EngineHealthResponse(
        healthy=True,
        message="Claude Code is ready",
    )


@router.get("/catalog", response_model=EngineCatalogResponse)
def get_engine_catalog(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EngineCatalogResponse:
    """Return available Claude Code engine and Anthropic models.

    Each model entry: {id, label, default}
    """
    return EngineCatalogResponse(
        engines=[_ENGINE_METADATA["claude_code"]],
        models=_CLAUDE_MODELS,
    )


@router.get("/capabilities", response_model=EngineCapabilitiesResponse)
def get_engine_capabilities(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> EngineCapabilitiesResponse:
    """Return Claude Code capability profile.

    Claude Code supports all major OS-layer features: streaming, tool use,
    vision, mid-stream injection, plan mode, skills, hooks, context
    compaction, and session pinning. No gaps.
    """
    return EngineCapabilitiesResponse(
        engine_id="claude_code",
        capabilities={
            "streaming": True,
            "tool_use": True,
            "vision": True,
            "mid_stream_inject": True,
            "plan_mode": True,
            "skills": True,
            "hooks": True,
            "context_compaction": True,
            "session_pinning": True,
        },
        eaos_gaps=[],
    )
