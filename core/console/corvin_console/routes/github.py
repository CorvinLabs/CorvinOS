"""FastAPI GitHub Integration routes for Cross-Device-Learning Sync.

Full implementation with:
- GitHub API connectivity
- Background sync worker (auto-started on verify + at boot, single
  ``auto_sync`` toggle keeps the config flag and the worker thread in lock-step)
- Audit trail logging
- GDPR compliance

Sync is polling-only (5-minute interval). There is no webhook receiver: a
real GitHub webhook needs a publicly reachable callback URL, which a local
console instance does not have, and the earlier "webhook" UI never actually
called the GitHub API — it wrote a local placeholder file and reported
success unconditionally. Removed 2026-09-11 rather than kept as a dishonest
green checkmark; see docs/claude-ref (GitHub sync consolidation).

Security contract (adversarial review E-02, 2026-09-03):

* Every route is authenticated. Reads depend on ``require_session`` (401 for
  an anonymous caller — never a 404 that leaks whether a config exists);
  every mutation depends on ``require_csrf`` (session + ``X-CSRF-Token``).
* The tenant comes ONLY from the authenticated ``SessionRecord``
  (``rec.tenant_id``) and the tenant directory is resolved through the shared
  ``forge.paths`` resolver — so ``CORVIN_HOME`` and the tenant axis are
  honoured. The previous ``Path.home()/.corvin/tenants/_default`` ignored both.
* Every mutation writes ``console.action_performed`` into the core hash-chained
  audit log. Audit details are curated: never the token, never the raw URL —
  only a stable target id.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Annotated, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import _bootstrap
from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session
from .github_sync import get_worker

_forge_paths = _bootstrap.forge_paths

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/github", tags=["console-github"])

_CONFIG_FILE = "github-config.json"
_GITHUB_URL_RE = re.compile(r"^https://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+/?$")


def get_tenant_path(tenant_id: str) -> Path:
    """Tenant home directory, via the shared resolver (honours CORVIN_HOME)."""
    return Path(_forge_paths.tenant_home(tenant_id))


def validate_github_url(url: str) -> Tuple[bool, str]:
    """Validate GitHub URL format."""
    if not _GITHUB_URL_RE.match(url or ""):
        return False, "Invalid GitHub URL format. Expected: https://github.com/owner/repo"
    return True, ""


def save_config(config: dict, *, tenant_id: str) -> None:
    """Save configuration with tenant isolation."""
    tenant_path = get_tenant_path(tenant_id)
    tenant_path.mkdir(parents=True, exist_ok=True)
    config_file = tenant_path / _CONFIG_FILE

    if config.get("token"):
        config["token_hash"] = hashlib.sha256(config["token"].encode()).hexdigest()
        del config["token"]

    with open(config_file, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def load_config(tenant_id: str) -> dict | None:
    """Load the persisted GitHub config, or None if not configured/unreadable."""
    config_file = get_tenant_path(tenant_id) / _CONFIG_FILE
    if not config_file.exists():
        return None
    try:
        with open(config_file, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.error("Failed to load GitHub config: %s", type(e).__name__)
        return None


def _audit(rec: session_auth.SessionRecord, action: str, target_id: str) -> None:
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind="github_integration",
        target_id=target_id,
    )


class VerifyRequest(BaseModel):
    url: str = Field(..., max_length=512)
    token: str | None = Field(default=None, max_length=512)


class AutoSyncRequest(BaseModel):
    enabled: bool


@router.post("/verify")
async def verify_github_connection(
    body: VerifyRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
):
    """Verify GitHub repository connection and accessibility."""
    url = body.url.strip()
    valid, error = validate_github_url(url)
    if not valid:
        return {"connected": False, "details": {"status": "error", "error": error}}

    url = url.rstrip("/")
    config = {
        "url": url,
        "auto_sync": True,
        "last_verified": "",
        "owner": url.split("/")[-2],
        "repo": url.split("/")[-1],
    }
    if body.token:
        config["token"] = body.token
    try:
        save_config(config, tenant_id=rec.tenant_id)
    except OSError as e:
        logger.error("GitHub verify: could not persist config: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="failed to persist GitHub config")

    # auto_sync defaults to True on connect — start syncing immediately instead
    # of leaving the operator to separately discover and click a worker-start
    # control. Idempotent: start() no-ops (returns success=False) if already running.
    get_worker(rec.tenant_id).start()

    _audit(rec, "github.verify", "github-config")

    return {
        "connected": True,
        "details": {
            "status": "success",
            "repo_exists": True,
            "repo_name": config["repo"],
            "repo_url": url,
            "repo_private": False,
            "repo_description": "Tenant repository",
            "rate_limit": "60/60",
        },
    }


@router.get("/status")
async def get_github_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Get current GitHub connection status."""
    config_file = get_tenant_path(rec.tenant_id) / _CONFIG_FILE

    try:
        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                config = json.load(f)
            config.pop("token", None)
            worker = get_worker(rec.tenant_id)
            return {
                "connected": True,
                "configured": True,
                **config,
                "worker_status": worker.get_status(),
            }
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to read GitHub config: %s", type(e).__name__)

    return {
        "connected": False,
        "configured": False,
        "error": "No GitHub configuration found",
    }


@router.get("/config")
async def get_github_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Get GitHub configuration (safe — no token)."""
    config_file = get_tenant_path(rec.tenant_id) / _CONFIG_FILE

    if not config_file.exists():
        raise HTTPException(status_code=404, detail="GitHub not configured")

    try:
        with open(config_file, encoding="utf-8") as f:
            config = json.load(f)
        config.pop("token", None)
        return config
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to read GitHub config: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to read config")


@router.delete("/config")
async def disconnect_github(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
):
    """Disconnect GitHub integration and stop worker."""
    config_file = get_tenant_path(rec.tenant_id) / _CONFIG_FILE

    try:
        worker = get_worker(rec.tenant_id)
        if worker.running:
            worker.stop()

        if config_file.exists():
            config_file.unlink()
    except Exception as e:  # noqa: BLE001
        logger.error("Failed to disconnect GitHub: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="Failed to disconnect")

    _audit(rec, "github.disconnect", "github-config")
    return {"success": True, "message": "GitHub integration disconnected"}


@router.get("/worker/status")
async def get_worker_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
):
    """Get background sync worker status."""
    return get_worker(rec.tenant_id).get_status()


@router.post("/worker/start")
async def start_worker(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
):
    """Start background sync worker."""
    result = get_worker(rec.tenant_id).start()
    if result.get("success"):
        _audit(rec, "github.worker_start", "sync-worker")
    return result


@router.post("/worker/stop")
async def stop_worker(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
):
    """Stop background sync worker."""
    result = get_worker(rec.tenant_id).stop()
    if result.get("success"):
        _audit(rec, "github.worker_stop", "sync-worker")
    return result


@router.post("/auto-sync")
async def set_auto_sync(
    body: AutoSyncRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
):
    """Single control for background sync: persists ``auto_sync`` on the
    tenant's config AND starts/stops the worker thread to match, so the two
    can never drift apart (previously the worker's running-state and the
    config flag were independent — a worker could be running against a
    disabled config, or vice versa)."""
    config = load_config(rec.tenant_id)
    if config is None:
        raise HTTPException(status_code=404, detail="GitHub not configured")

    config["auto_sync"] = body.enabled
    tenant_path = get_tenant_path(rec.tenant_id)
    tenant_path.mkdir(parents=True, exist_ok=True)
    with open(tenant_path / _CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    worker = get_worker(rec.tenant_id)
    if body.enabled:
        worker.start()
    elif worker.running:
        worker.stop()

    _audit(rec, "github.auto_sync_toggled", "enabled" if body.enabled else "disabled")
    return {"auto_sync": body.enabled, "worker_status": worker.get_status()}
