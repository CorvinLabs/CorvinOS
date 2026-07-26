"""Console model registry API — ADR-0245 (Live Model Discovery).

Endpoints
---------
  GET  /models/registry      → static YAML engine-model registry
  GET  /models/providers     → static provider registry
  GET  /models/live          → live-fetched models (Anthropic + cached fallback)
  GET  /models/live/refresh  → trigger immediate refresh + return result

Live discovery (flag: live_model_discovery):
  - Fetches from engine_providers.fetch_models("anthropic") every 5 minutes
  - Caches result to ~/.corvin/tenants/<tenant>/global/model_catalog_cache.json
  - Returns cached result if fetch fails (network down, timeout, auth error)
  - Console UI polls /models/live every 5 min, shows status badge

Security invariants:
  - Catalog cache is per-tenant (stored in global/ dir)
  - Provider API keys are NEVER returned in responses
  - Each request checks the feature flag; off = falls back to static registry
  - Background refresh is tenant-blind (global cache for all tenants)
  - Audit event logged on every successful fetch

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import yaml  # type: ignore[import-not-found]
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

from .. import audit as console_audit
from .. import feature_flags
from ..deps import require_session
from .. import auth as session_auth

_log = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import engine_models  # type: ignore  # noqa: E402
import engine_providers  # type: ignore  # noqa: E402

from forge import paths as _forge_paths  # type: ignore


router = APIRouter()

# ── Cache management ──────────────────────────────────────────────────────

_CACHE_LOCK = threading.Lock()
_REFRESH_THREAD: threading.Timer | None = None
_REFRESH_INTERVAL = 300  # 5 minutes


def _cache_path(tenant_id: str = "_default") -> Path:
    """Per-tenant model catalog cache file."""
    return _forge_paths.tenant_global_dir(tenant_id) / "model_catalog_cache.json"


def _read_cache(tenant_id: str = "_default") -> dict[str, Any]:
    """Read cached model catalog. Returns empty dict on read/parse error."""
    try:
        path = _cache_path(tenant_id)
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception as e:
        _log.debug(f"[models] cache read failed for tenant {tenant_id}: {e}")
        return {}


def _write_cache(tenant_id: str, data: dict[str, Any]) -> None:
    """Write model catalog cache atomically."""
    try:
        path = _cache_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)
    except Exception as e:
        _log.warning(f"[models] cache write failed for tenant {tenant_id}: {e}")


def _refresh_once_impl(tenant_id: str) -> None:
    """Single refresh iteration. Reschedules itself at the end."""
    global _REFRESH_THREAD  # noqa: PLW0603
    try:
        # Only refresh if feature is enabled for this tenant
        if not feature_flags.is_enabled("live_model_discovery", tenant_id):
            _log.debug(f"[models] live_model_discovery disabled for {tenant_id}")
            return

        # Fetch from Anthropic
        result = engine_providers.fetch_models(
            provider="anthropic",
            base_url="https://api.anthropic.com",
            model_source="anthropic",
            credential_env="ANTHROPIC_API_KEY",
            timeout=8.0,
        )

        if result.get("reachable"):
            with _CACHE_LOCK:
                cache = _read_cache(tenant_id)
                cache["providers"] = {
                    "anthropic": {
                        "models": result.get("models", []),
                        "reachable": result.get("reachable", False),
                        "count": result.get("count", 0),
                        "error": result.get("error"),
                        "fetched_at": time.time(),
                    }
                }
                _write_cache(tenant_id, cache)
                _log.info(
                    f"[models] refreshed {result.get('count', 0)} models from Anthropic"
                )
                console_audit.system_event(
                    tenant_id=tenant_id,
                    event="model_catalog_refreshed",
                    details={
                        "provider": "anthropic",
                        "model_count": result.get("count", 0),
                        "reachable": True,
                    },
                )
        else:
            _log.warning(
                f"[models] Anthropic fetch failed: {result.get('error')}"
            )
            console_audit.system_event(
                tenant_id=tenant_id,
                event="model_catalog_refresh_failed",
                details={
                    "provider": "anthropic",
                    "error": result.get("error", "unknown"),
                    "reachable": False,
                },
            )
    except Exception as e:
        _log.error(f"[models] background refresh crashed: {e}", exc_info=True)
    finally:
        # Reschedule
        _REFRESH_THREAD = threading.Timer(
            _REFRESH_INTERVAL,
            lambda: _refresh_once_impl(tenant_id)
        )
        _REFRESH_THREAD.daemon = True
        _REFRESH_THREAD.start()


def _schedule_background_refresh(tenant_id: str = "_default") -> None:
    """Start a background timer to refresh the model catalog periodically.

    Called once at startup. The timer reschedules itself after each refresh.
    """
    global _REFRESH_THREAD  # noqa: PLW0603
    _REFRESH_THREAD = threading.Timer(_REFRESH_INTERVAL, lambda: _refresh_once_impl(tenant_id))
    _REFRESH_THREAD.daemon = True
    _REFRESH_THREAD.start()
    _log.info(f"[models] scheduled background refresh every {_REFRESH_INTERVAL}s")


# Start background refresh at module load
try:
    _schedule_background_refresh("_default")
except Exception as e:
    _log.warning(f"[models] failed to schedule background refresh: {e}")


# ── API endpoints ─────────────────────────────────────────────────────────


@router.get("/models/registry")
async def get_registry(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Static engine-model registry from YAML."""
    return engine_models.registry_as_dict(force_reload=True)


@router.get("/models/providers")
async def get_providers(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Static provider registry from YAML."""
    return engine_models.providers_as_dict(force_reload=True)


@router.get("/models/live")
async def get_live_models(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Live model catalog with Anthropic provider, or fallback to cache/static.

    Returns:
    {
      "providers": {
        "anthropic": {
          "models": [{id, label}, ...],
          "reachable": bool,
          "count": int,
          "error": str | null,
          "fetched_at": timestamp | null
        }
      },
      "registry": {<static engine-model registry>},
      "cache_status": {
        "cached": bool,          # is this cached or freshly fetched?
        "cached_at": timestamp,  # when was the cache last written?
        "cache_age_sec": int,    # how many seconds old is the cache?
      }
    }

    If live_model_discovery flag is off, returns the static registry with
    empty providers (cache is ignored).
    """
    tenant_id = rec.tenant_id
    feature_on = feature_flags.is_enabled("live_model_discovery", tenant_id)

    # Always return the static registry
    registry = engine_models.registry_as_dict(force_reload=True)

    if not feature_on:
        # Feature off: return static data only
        return {
            "providers": {},
            "registry": registry,
            "cache_status": {
                "cached": False,
                "reason": "feature_disabled",
            },
        }

    # Feature on: try to return cached data
    with _CACHE_LOCK:
        cache = _read_cache(tenant_id)

    providers_data = cache.get("providers", {})
    anthropic_entry = providers_data.get("anthropic", {})
    fetched_at = anthropic_entry.get("fetched_at")

    cache_status: dict[str, Any] = {"cached": bool(fetched_at)}
    if fetched_at:
        now = time.time()
        age = int(now - fetched_at)
        cache_status["cached_at"] = fetched_at
        cache_status["cache_age_sec"] = age

    return {
        "providers": providers_data,
        "registry": registry,
        "cache_status": cache_status,
    }


@router.post("/models/live/refresh")
async def trigger_live_refresh(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Trigger an immediate fetch from Anthropic and return the result.

    This is a synchronous fetch (not queued in the background timer).
    Useful for the console UI to "refresh now" button.
    """
    tenant_id = rec.tenant_id
    feature_on = feature_flags.is_enabled("live_model_discovery", tenant_id)

    if not feature_on:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="live_model_discovery feature is not enabled",
        )

    # Fetch from Anthropic (synchronous)
    result = engine_providers.fetch_models(
        provider="anthropic",
        base_url="https://api.anthropic.com",
        model_source="anthropic",
        credential_env="ANTHROPIC_API_KEY",
        timeout=8.0,
    )

    # Update cache if successful
    if result.get("reachable"):
        with _CACHE_LOCK:
            cache = _read_cache(tenant_id)
            cache["providers"] = {
                "anthropic": {
                    "models": result.get("models", []),
                    "reachable": result.get("reachable", False),
                    "count": result.get("count", 0),
                    "error": result.get("error"),
                    "fetched_at": time.time(),
                }
            }
            _write_cache(tenant_id, cache)
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="console.model_catalog_manual_refresh",
            target_kind="model_catalog",
            target_id=f"anthropic_{result.get('count', 0)}_models",
        )
    else:
        console_audit.action_failed(
            tenant_id=tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="console.model_catalog_manual_refresh",
            target_kind="model_catalog",
            target_id="anthropic",
            reason=result.get("error", "unknown"),
        )

    registry = engine_models.registry_as_dict(force_reload=True)
    return {
        "providers": {
            "anthropic": result,
        },
        "registry": registry,
        "manual_refresh": True,
        "fetched_at": time.time(),
    }
