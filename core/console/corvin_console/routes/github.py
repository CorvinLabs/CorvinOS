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
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Annotated, Tuple

import httpx
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
    """Save configuration with tenant isolation.

    The token is never persisted (only its hash). Written via temp file +
    ``os.replace`` at mode 0600 so a crash cannot leave a half-written config
    and the file is not world-readable."""
    tenant_path = get_tenant_path(tenant_id)
    tenant_path.mkdir(parents=True, exist_ok=True)
    config_file = tenant_path / _CONFIG_FILE

    config = dict(config)
    if config.get("token"):
        config["token_hash"] = hashlib.sha256(config["token"].encode()).hexdigest()
    config.pop("token", None)

    fd, tmp = tempfile.mkstemp(dir=str(tenant_path), prefix=".github-config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, config_file)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


_GITHUB_API = "https://api.github.com"
_VERIFY_TIMEOUT_S = 8.0

# Closed set of verdicts ``check_repository`` can return. Everything except
# ``verified`` means "not verified": it is never reported as connected.
_VERDICT_MESSAGES = {
    "not_found": "Repository not found, or it is private and the token cannot see it.",
    "auth_failed": "GitHub rejected the token (401/403).",
    "rate_limited": "GitHub rate limit reached; try again later.",
    "unreachable": "GitHub could not be reached from this install.",
    "egress_blocked": "The network egress policy blocks api.github.com.",
    "error": "GitHub answered with an unexpected response.",
}


def _egress_denied(tenant_id: str) -> str | None:
    """L35: a block message when the tenant's egress policy denies api.github.com.

    Fail-open on operational error / no policy (same semantics as the model
    catalog fetch); an explicit denial is honoured."""
    try:
        from egress_gate import load_egress_gate_for_tenant  # type: ignore[import]

        gate = load_egress_gate_for_tenant(tenant_id or "_default")
        if gate is None:
            return None
        try:
            gate.validate_or_raise("api.github.com", engine_id="github_verify")
            return None
        except Exception as e:  # noqa: BLE001 - explicit denial
            return str(e)[:120]
    except Exception:  # noqa: BLE001 - gate unavailable
        return None


async def check_repository(owner: str, repo: str, token: str | None, *, tenant_id: str) -> dict:
    """Really ask GitHub whether ``owner/repo`` exists and is reachable.

    One authenticated (when a token is given) ``GET /repos/{owner}/{repo}``.
    The token goes only into the request header; it is never logged, stored
    or echoed. Returns ``{"verdict": "verified" | <failure code>, ...}`` and
    never raises: a transport error is the verdict ``unreachable``, not a
    success."""
    if _egress_denied(tenant_id):
        return {"verdict": "egress_blocked"}
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "corvinos-console"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with httpx.AsyncClient(timeout=_VERIFY_TIMEOUT_S, follow_redirects=False) as client:
            resp = await client.get(f"{_GITHUB_API}/repos/{owner}/{repo}", headers=headers)
    except (httpx.HTTPError, OSError):
        return {"verdict": "unreachable"}

    remaining = resp.headers.get("x-ratelimit-remaining")
    limit = resp.headers.get("x-ratelimit-limit")
    rate = f"{remaining}/{limit}" if remaining and limit else None
    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            return {"verdict": "error"}
        if not isinstance(data, dict) or str(data.get("name", "")).lower() != repo.lower():
            return {"verdict": "error"}
        perms = data.get("permissions") if isinstance(data.get("permissions"), dict) else {}
        return {
            "verdict": "verified",
            "private": bool(data.get("private")),
            "description": data.get("description") or None,
            "can_push": perms.get("push") if perms else None,
            "rate_limit": rate,
        }
    if resp.status_code == 404:
        return {"verdict": "not_found", "rate_limit": rate}
    if resp.status_code == 429 or (resp.status_code == 403 and remaining == "0"):
        return {"verdict": "rate_limited", "rate_limit": rate}
    if resp.status_code in (401, 403):
        return {"verdict": "auth_failed", "rate_limit": rate}
    return {"verdict": "error", "rate_limit": rate}


@router.post("/verify")
async def verify_github_connection(
    body: VerifyRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
):
    """Verify the repository against the real GitHub API.

    The config is saved in every case where the URL is well-formed, with
    ``verified`` set to what GitHub actually said. Only a verified config may
    sync (``GitHubSyncWorker.start`` refuses otherwise); a failed check never
    reports ``connected`` and never starts a worker."""
    url = body.url.strip()
    valid, error = validate_github_url(url)
    if not valid:
        return {"connected": False, "details": {"status": "error", "error": error}}

    url = url.rstrip("/")
    owner, repo = url.split("/")[-2], url.split("/")[-1]
    result = await check_repository(owner, repo, body.token, tenant_id=rec.tenant_id)
    verified = result["verdict"] == "verified"

    config = {
        "url": url,
        "verified": verified,
        # Sync only ever starts for a verified config.
        "auto_sync": verified,
        "last_verified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) if verified else "",
        "owner": owner,
        "repo": repo,
    }
    if body.token:
        config["token"] = body.token
    try:
        save_config(config, tenant_id=rec.tenant_id)
    except OSError as e:
        logger.error("GitHub verify: could not persist config: %s", type(e).__name__)
        raise HTTPException(status_code=500, detail="failed to persist GitHub config")

    if not verified:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="github.verify",
            target_kind="github_integration",
            target_id="github-config",
            reason=result["verdict"],
        )
        return {
            "connected": False,
            "verified": False,
            "details": {
                "status": "error",
                "error": _VERDICT_MESSAGES.get(result["verdict"], _VERDICT_MESSAGES["error"]),
                "verdict": result["verdict"],
                "rate_limit": result.get("rate_limit"),
                "saved": True,
            },
        }

    # Verified: auto-sync is on by default (operator decision c2c8ce6f7), but
    # only now that GitHub confirmed the repository. start() is idempotent.
    get_worker(rec.tenant_id).start()
    _audit(rec, "github.verify", "github-config")

    return {
        "connected": True,
        "verified": True,
        "details": {
            "status": "success",
            "repo_exists": True,
            "repo_name": repo,
            "repo_url": url,
            "repo_private": result["private"],
            "repo_description": result["description"],
            "can_push": result["can_push"],
            "rate_limit": result["rate_limit"],
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
            verified = config.get("verified") is True
            return {
                **config,
                # "Connected" means GitHub confirmed the repository (S-9i). A
                # config that merely exists is "saved, not verified".
                "connected": verified,
                "configured": True,
                "verified": verified,
                "state": "connected" if verified else "saved_not_verified",
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
    if result.get("refused"):
        raise HTTPException(status_code=409, detail=result.get("error", "GitHub config is not verified"))
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

    if body.enabled and config.get("verified") is not True:
        console_audit.action_denied(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="github.auto_sync_toggled",
            target_kind="github_integration",
            target_id="enabled",
            reason="unverified",
        )
        raise HTTPException(
            status_code=409,
            detail="GitHub config is not verified; run Verify first.",
        )

    config["auto_sync"] = body.enabled
    save_config(config, tenant_id=rec.tenant_id)

    worker = get_worker(rec.tenant_id)
    if body.enabled:
        worker.start()
    elif worker.running:
        worker.stop()

    _audit(rec, "github.auto_sync_toggled", "enabled" if body.enabled else "disabled")
    return {"auto_sync": body.enabled, "worker_status": worker.get_status()}
