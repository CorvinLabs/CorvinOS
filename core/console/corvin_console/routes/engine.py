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
import sys
import threading
import time
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

# ADR-0181 — model-provider registry/detection modules live outside the
# console package (operator/bridges/shared/), shared with the adapter's own
# provider-redirect resolution (resolve_claude_code_provider_env). Restored
# 2026-09-10: these routes were dropped in 243690e8 ("rewrite engine.py for
# Claude Code only") while the console's "Model Providers" UI kept calling
# them — the modules themselves were never deleted, only the route wiring.
_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

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

# Anthropic Claude API models (official releases).
#
# This list is a static snapshot, curated by hand — the auto-refresh machinery
# that used to keep it current was removed in 243690e8 ("rewrite engine.py for
# Claude Code only") and was NOT rebuilt here (that is a scope expansion; see
# ADR discussion). The canonical, already-current source for this same catalog
# is `operator/bundle/config-templates/engine_model_registry.yaml`
# (`engines.claude_code.os_models`), read via `engine_models.registry_as_dict()`
# and served live at GET /models/registry — this list is kept in sync with it
# by hand until a follow-up wires this route to that source directly.
#
# Default: Sonnet 5 — the general-purpose balanced model (matches the
# "balanced" tier in engine_model_registry.yaml); Opus 5 is reserved for
# max-capability tasks, Haiku 4.5 for fast/economical ones, so neither is a
# good blanket default for a picker with no other context.
_CLAUDE_MODELS = [
    {"id": "claude-opus-5", "label": "Claude Opus 5", "default": False},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "default": True},
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

class EngineModelConfig(BaseModel):
    """Per-engine model + provider override (ADR-0119/0181)."""
    model_config = {"extra": "forbid"}

    os_model: str | None = Field(None, description="OS-turn model id; null = adaptive default")
    worker_model: str | None = Field(None, description="Worker-turn model id; null = default")
    provider: str | None = Field(
        None,
        description="ADR-0181 model provider id (anthropic/openai/ollama_local/"
                    "ollama_cloud/openrouter); null = engine's native provider",
    )


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
    # ADR-0181 — which provider + model serve each engine's OS/worker turn.
    # Restored 2026-09-10 alongside /registry, /providers, /models, /detect:
    # this field existed pre-243690e8 and is what resolve_claude_code_provider_env()
    # (operator/bridges/shared/engine_models.py) reads at spawn time — writing it
    # here is not cosmetic, it is the one persistence path that function consults.
    engine_models: dict[str, EngineModelConfig] = Field(
        default_factory=dict,
        description="Per-engine OS/worker model + provider overrides, keyed by engine_id",
    )
    # ADR-0181 — non-blocking L34/L35 advisories for any engine currently
    # pointed at a CLOUD provider (recomputed on every read, not just on save,
    # so a reload still shows it and an egress-policy change is reflected).
    compliance_warnings: list[str] = Field(default_factory=list)


class EngineSettingUpdate(BaseModel):
    """Engine setting update request."""
    model_config = {"extra": "forbid"}

    default_engine: str | None = Field(
        "claude_code",
        description="Engine selection; must be 'claude_code' or None",
    )
    engine_models: dict[str, EngineModelConfig] | None = Field(
        None,
        description="Per-engine OS/worker model + provider overrides to save; "
                    "null leaves the existing config unchanged",
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

    raw_em = spec.get("engine_models") or {}
    engine_models = {
        eid: EngineModelConfig(
            os_model=cfg.get("os_model") or None,
            worker_model=cfg.get("worker_model") or None,
            provider=cfg.get("provider") or None,
        )
        for eid, cfg in raw_em.items()
        if isinstance(cfg, dict)
    }

    return EngineSettingResponse(
        default_engine=default,
        valid_engines=["claude_code"],
        engine_models=engine_models,
        compliance_warnings=_assess_model_compliance(engine_models, _rec.tenant_id),
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

    # ADR-0181 — validate provider ids against the live registry (never trust
    # a client-supplied provider id silently: an unknown one would resolve to
    # no redirect at spawn time and the operator would see native Anthropic
    # keep running with no explanation of why their choice didn't take).
    if body.engine_models is not None:
        try:
            from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
            known_providers = set(load_providers(force_reload=True).keys())
        except Exception:  # noqa: BLE001
            known_providers = set()
        for eid, cfg in body.engine_models.items():
            if cfg.provider is not None and known_providers and cfg.provider not in known_providers:
                console_audit.action_failed(
                    tenant_id=_rec.tenant_id,
                    sid_fingerprint=_rec.sid_fingerprint,
                    action="engine.setting.update",
                    target_kind="engine_setting",
                    target_id=f"engine_models.{eid}.provider",
                    reason="unknown_provider",
                )
                raise HTTPException(
                    status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Unknown provider {cfg.provider!r} for engine {eid!r}. "
                          f"Choose from: {sorted(known_providers)}",
                )

    # Load, update, and save tenant config
    data = _load_tenant_yaml(_rec.tenant_id)
    if "spec" not in data or not isinstance(data.get("spec"), dict):
        data["spec"] = {}

    data["spec"]["default_engine"] = "claude_code"
    if body.engine_models is not None:
        data["spec"]["engine_models"] = {
            eid: {k: v for k, v in cfg.model_dump().items() if v is not None}
            for eid, cfg in body.engine_models.items()
        }
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

    saved_spec = data.get("spec") or {}
    raw_em = saved_spec.get("engine_models") or {}
    engine_models = {
        eid: EngineModelConfig(
            os_model=cfg.get("os_model") or None,
            worker_model=cfg.get("worker_model") or None,
            provider=cfg.get("provider") or None,
        )
        for eid, cfg in raw_em.items()
        if isinstance(cfg, dict)
    }
    return EngineSettingResponse(
        default_engine="claude_code",
        valid_engines=["claude_code"],
        engine_models=engine_models,
        compliance_warnings=_assess_model_compliance(engine_models, _rec.tenant_id),
    )


def _assess_model_compliance(engine_models: "dict[str, EngineModelConfig]", tenant_id: str) -> list[str]:
    """ADR-0181 — non-blocking advisories when an engine is pointed at a CLOUD
    provider. Cloud egress happens both when the engine runs a cloud MODEL
    STRING at spawn and via cross-engine PROXY routing (Claude Code ->
    OpenRouter/Ollama, the built-in translating proxy) — either way the
    pre-spawn L34/L35 gates are the hard enforcement; this is advisory only."""
    warnings: list[str] = []
    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        from egress_gate import load_egress_gate_for_tenant  # type: ignore[import]  # noqa: PLC0415
        from urllib.parse import urlparse
        providers = load_providers()
        gate = load_egress_gate_for_tenant(tenant_id or "_default")
    except Exception:  # noqa: BLE001
        return warnings
    for eid, cfg in (engine_models or {}).items():
        spec = providers.get(cfg.provider) if cfg.provider else None
        if spec is None or spec.kind != "cloud":
            continue
        host = urlparse(spec.base_url).hostname or ""
        if gate is not None and host:
            try:
                gate.validate_or_raise(host)
            except Exception as e:  # noqa: BLE001 — explicit denial
                warnings.append(
                    f"{eid}: egress to '{host}' is blocked by the L35 policy — "
                    f"add it to allowed_hosts. ({str(e)[:120]})"
                )
        warnings.append(
            f"{eid}: '{spec.label}' is a cloud provider. Store your API key under "
            f"{spec.credential_env or 'the provider env var'} in Settings -> API Keys. Cloud "
            f"egress occurs when the engine runs a cloud model or is proxy-routed to this "
            f"provider at spawn, where L34/L35 apply."
        )
    return warnings


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


# ---------------------------------------------------------------------------
# ADR-0119/0181 — Model registry, providers, live model fetch (restored)
# ---------------------------------------------------------------------------
# Auto-refresh gating for the live model catalogue (ADR-0181). SHIPS DARK by
# default (feature_flags fail-dark on an unregistered id) — a page load must
# not start reaching out to Anthropic just because the tenant upgraded.
_REFRESH_RETRY_FLOOR_SECONDS = 3600.0
_refresh_inflight: set[str] = set()
_refresh_last_attempt: dict[str, float] = {}
_refresh_lock = threading.Lock()
_AUTO_REFRESH_PROVIDERS = ("anthropic",)


def _maybe_refresh_model_catalog(tenant_id: str) -> None:
    """Start a background refresh for every stale auto-refresh provider."""
    import model_catalog  # type: ignore[import]  # noqa: PLC0415
    now = time.time()
    for provider in _AUTO_REFRESH_PROVIDERS:
        if not model_catalog.is_stale(provider):
            continue
        with _refresh_lock:
            if provider in _refresh_inflight:
                continue
            last = _refresh_last_attempt.get(provider)
            if last is not None and (now - last) < _REFRESH_RETRY_FLOOR_SECONDS:
                continue
            _refresh_inflight.add(provider)
            _refresh_last_attempt[provider] = now
        threading.Thread(target=_refresh_model_catalog_now,
                         args=(provider, tenant_id), daemon=True).start()


def _refresh_model_catalog_now(provider: str, tenant_id: str) -> None:
    """Fetch one provider's model list and store it. NEVER raises; always
    releases the in-flight guard."""
    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        from engine_providers import fetch_models  # type: ignore[import]  # noqa: PLC0415
        import model_catalog  # type: ignore[import]  # noqa: PLC0415

        spec = load_providers(force_reload=True).get(provider)
        if spec is None:
            return
        if spec.kind == "cloud" and spec.base_url:
            if _egress_denied(spec.base_url, tenant_id):
                return  # L35 applies to the background refresh too
        result = fetch_models(provider, base_url=spec.base_url,
                              model_source=spec.model_source,
                              credential_env=spec.credential_env) or {}
        models = result.get("models") or []
        if models:
            model_catalog.store_models(provider, models)
    except Exception:  # noqa: BLE001 — a broken refresh never reaches the route
        pass
    finally:
        with _refresh_lock:
            _refresh_inflight.discard(provider)


def _egress_denied(base_url: str, tenant_id: str) -> str | None:
    """L35: return a block message if the active egress policy denies this
    host, else None (allow). Fail-OPEN on operational error / disabled policy,
    but honours an EXPLICIT policy denial."""
    try:
        from urllib.parse import urlparse
        from egress_gate import load_egress_gate_for_tenant  # type: ignore[import]
        host = urlparse(base_url).hostname or ""
        gate = load_egress_gate_for_tenant(tenant_id or "_default")
        if gate is None or not host:
            return None
        try:
            gate.validate_or_raise(host)
            return None
        except Exception as e:  # noqa: BLE001 — explicit denial
            return (f"Egress to '{host}' is blocked by the L35 policy — add it to "
                    f"allowed_hosts to fetch this provider's models. ({str(e)[:120]})")
    except Exception:  # noqa: BLE001 — gate unavailable → don't break the model list
        return None


@router.get("/registry")
def get_engine_model_registry(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Return the engine model registry — available model choices per engine.

    ADR-0119: the registry defines selectable OS-turn and worker-turn models
    for each engine, plus (ADR-0181) which providers each engine can drive.
    """
    try:
        from .. import feature_flags as _ff  # noqa: PLC0415
        if _ff.is_enabled("model_catalog_auto_refresh",
                          getattr(_rec, "tenant_id", "_default")):
            _maybe_refresh_model_catalog(getattr(_rec, "tenant_id", "_default"))
    except Exception:  # noqa: BLE001 — a refresh must never take this page down
        pass
    try:
        from engine_models import registry_as_dict  # type: ignore[import]
        # force_reload → a registry edit or package upgrade shows up on a
        # browser refresh without a server restart.
        return registry_as_dict(force_reload=True)
    except Exception:  # noqa: BLE001
        return {}


@router.get("/providers")
def get_engine_providers(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """ADR-0181 — model providers (Anthropic, OpenAI, Ollama local/cloud,
    OpenRouter). ``credential_env`` is the env-var NAME only, never a secret."""
    try:
        from engine_models import providers_as_dict  # type: ignore[import]
        return providers_as_dict(force_reload=True)
    except Exception:  # noqa: BLE001
        return {}


@router.get("/models")
def get_provider_models(
    provider: str,
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """ADR-0181 — live-fetch the models a provider offers right now (Ollama
    /api/tags, OpenRouter /models). Cloud providers are network egress: the
    host must pass the L35 gate before we reach out to it."""
    try:
        from engine_models import load_providers  # type: ignore[import]
        from engine_providers import fetch_models  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001
        return {"provider": provider, "reachable": False, "models": [],
                "count": 0, "error": f"module error: {exc}"}
    spec = load_providers(force_reload=True).get(provider)
    if spec is None:
        return {"provider": provider, "reachable": False, "models": [],
                "count": 0, "error": f"unknown provider '{provider}'"}
    if spec.kind == "cloud" and spec.base_url:
        denied = _egress_denied(spec.base_url, getattr(_rec, "tenant_id", "_default"))
        if denied:
            return {"provider": provider, "reachable": False, "models": [],
                     "count": 0, "error": denied}
    return fetch_models(provider, base_url=spec.base_url,
                        model_source=spec.model_source, credential_env=spec.credential_env)


@router.get("/detect")
def detect_engines(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    """Probe engines and return their installation/authentication state.

    ADR-0125 — Zero-Config Engine Onboarding. Detection results are NOT
    written to the audit chain — they contain no secrets, PII, or credential
    values (credential_source is an enum string only).
    """
    try:
        from engine_detection import detect_all, recommended_engine  # type: ignore[import]

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(detect_all)
            try:
                results = future.result(timeout=15)
            except concurrent.futures.TimeoutError:
                _log.warning("engine detect_all timed out")
                results = []
        rec_engine = recommended_engine(results)
        needs_bootstrap = rec_engine is None

        serialised = [
            {
                "engine_id": r.engine_id,
                "installed": r.installed,
                "authenticated": r.authenticated,
                "credential_source": r.credential_source,
                "version": r.version,
                "models": r.models,
                "detail": r.detail,
            }
            for r in results
        ]
        return {
            "results": serialised,
            "recommended_engine": rec_engine,
            "needs_bootstrap": needs_bootstrap,
        }
    except Exception as exc:  # noqa: BLE001
        # Fail gracefully — detection errors must not break the console.
        # L16 compliance: never leak exception details to the HTTP response.
        _log.warning(f"engine detect_all failed: {type(exc).__name__}", exc_info=True)
        return {
            "results": [],
            "recommended_engine": None,
            "needs_bootstrap": True,
            "error": "detection_failed",
        }
