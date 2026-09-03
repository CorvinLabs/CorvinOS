"""
Multi-Instance Learning Routes (ADR-0275/0277)

Cross-device learning dashboard, sync status, patterns, overrides.
"""

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional, List, Dict, Any
import json
from pathlib import Path
from datetime import datetime
import logging
from corvin_core.feature_flags import is_enabled

from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/multi-instance", tags=["multi-instance"])


def tenant_learning_dir(tenant_id: str) -> Path:
    """The tenant's learnable-state directory (grades, learning-event JSONL, …).
    Tenant-isolated — no hardcoded 'shumway-corvin' (G5, ADR-0369)."""
    try:
        from forge.paths import tenant_home  # noqa: PLC0415
        return Path(tenant_home(tenant_id)) / "learning"
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin" / "tenants" / tenant_id / "learning"


def _configured_remote(tenant_id: str) -> "str | None":
    """Resolve spec.cross_device.sync_remote — a git URL (https:// or file://). Returns
    None when unconfigured; no hardcoded path/repo (G5, ADR-0369)."""
    try:
        from forge.paths import tenant_home  # noqa: PLC0415
        cfg = Path(tenant_home(tenant_id)) / "tenant.corvin.yaml"
        if not cfg.is_file():
            return None
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        remote = (((data.get("spec") or {}).get("cross_device") or {})
                  .get("sync_remote"))
        return str(remote) if remote else None
    except Exception:  # noqa: BLE001
        return None


def _sync_cache_dir(tenant_id: str) -> Path:
    """Per-tenant working cache for the git clone + decrypted remote state."""
    try:
        from forge.paths import tenant_home  # noqa: PLC0415
        return Path(tenant_home(tenant_id)) / "cross_device" / "cache"
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin" / "tenants" / tenant_id / "cross_device" / "cache"


def _vault_item(name: str) -> "str | None":
    """Read a secret from the Vault (PAT / GPG passphrase). None if absent."""
    try:
        from vault import get_item  # noqa: PLC0415
        v = get_item(name, source="cross_device_sync")
        return str(v) if v else None
    except Exception:  # noqa: BLE001
        return None


# Data Models
class InstanceStatus:
    """Instance sync status."""
    instance_id: str
    name: str
    status: str  # active, inactive, retired, archived
    last_sync: datetime
    voting_weight: float


class MergedPattern:
    """A learned pattern from merged state."""
    pattern_id: str
    recommended_choice: str
    confidence: float
    sources: List[str]  # instance IDs that contributed


class SyncHealth:
    """Overall sync health metrics."""
    total_decisions: int
    decisions_today: int
    github_status: str  # ok, error, unreachable
    merged_state_freshness: str  # "1 hour ago", "2 days ago", etc
    next_scheduled_sync: datetime


def _merged_state_dir(tenant_id: str) -> Path:
    """``<tenant_home>/cross_device/merged-state/`` — the tenant's own merge output.

    Adversarial review E-10 (2026-09-03): this used to be a hard-coded
    operator-personal checkout path under ``~/projects/`` shipped as product
    code, and the registry loader fabricated personal instance names + a user
    id when the file was absent. Both are gone: an absent file now reports an
    EMPTY, uninitialised state.
    """
    return _sync_cache_dir(tenant_id).parent / "merged-state"


def load_merged_state(tenant_id: str) -> Dict[str, Any]:
    """Load user-profile.json from the tenant's merged-state."""
    profile_path = _merged_state_dir(tenant_id) / "user-profile.json"

    if not profile_path.exists():
        return {
            "merged_at": datetime.utcnow().isoformat() + "Z",
            "patterns": {},
            "n_patterns": 0,
            "status": "uninitialized"
        }

    with open(profile_path, encoding="utf-8") as f:
        return json.load(f)


def load_instance_registry(tenant_id: str) -> Dict[str, Any]:
    """Load instance-registry.json; an absent file is an EMPTY registry."""
    registry_path = _merged_state_dir(tenant_id) / "instance-registry.json"

    if not registry_path.exists():
        return {"user_id": None, "instances": []}

    with open(registry_path, encoding="utf-8") as f:
        return json.load(f)


# Routes

@router.get("/status")
async def get_multi_instance_status(
    rec: "session_auth.SessionRecord" = Depends(require_session),
) -> Dict[str, Any]:
    """Get overall multi-instance sync status."""

    try:
        merged = load_merged_state(rec.tenant_id)
        registry = load_instance_registry(rec.tenant_id)

        # Compute freshness
        merged_at = merged.get("merged_at", "unknown")
        if merged_at != "unknown":
            merged_time = datetime.fromisoformat(merged_at.replace("Z", "+00:00"))
            delta = datetime.utcnow().replace(tzinfo=merged_time.tzinfo) - merged_time
            freshness = f"{delta.days}d {delta.seconds // 3600}h ago" if delta.days > 0 else f"{delta.seconds // 3600}h ago"
        else:
            freshness = "unknown"

        return {
            "enabled": True,
            "instances": registry.get("instances", []),
            "merged_patterns": merged.get("n_patterns", 0),
            "merged_at": merged.get("merged_at"),
            "freshness": freshness,
            "github_repo": _configured_remote(rec.tenant_id),
            "sync_frequency": "weekly",
            "auto_load_repo_enabled": is_enabled("auto_load_github_repo", rec.tenant_id)
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("multi-instance status failed")
        raise HTTPException(status_code=500, detail="multi-instance status unavailable") from e


@router.get("/patterns")
async def get_learned_patterns(
    rec: "session_auth.SessionRecord" = Depends(require_session),
) -> Dict[str, Any]:
    """Get all learned patterns from merged-state."""

    try:
        merged = load_merged_state(rec.tenant_id)
        patterns = []

        for pattern_id, pattern_data in merged.get("patterns", {}).items():
            patterns.append({
                "pattern_id": pattern_id,
                "recommended": pattern_data.get("recommended_model"),
                "confidence": pattern_data.get("confidence"),
                "sources": pattern_data.get("sources", []),
                "candidates": pattern_data.get("candidates", {})
            })

        return {
            "patterns": patterns,
            "count": len(patterns),
            "merged_at": merged.get("merged_at")
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("multi-instance patterns failed")
        raise HTTPException(status_code=500, detail="learned patterns unavailable") from e


@router.get("/instances")
async def get_instance_status(
    rec: "session_auth.SessionRecord" = Depends(require_session),
) -> Dict[str, Any]:
    """Get status of all paired instances."""

    try:
        registry = load_instance_registry(rec.tenant_id)

        instances = []
        for inst in registry.get("instances", []):
            instances.append({
                "instance_id": inst.get("instance_id"),
                "name": inst.get("instance_name"),
                "status": inst.get("status", "unknown"),
                "last_seen": inst.get("last_seen"),
                "is_primary": inst.get("is_primary", False)
            })

        return {
            "user_id": registry.get("user_id"),
            "instances": instances,
            "count": len(instances)
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("multi-instance instances failed")
        raise HTTPException(status_code=500, detail="instance registry unavailable") from e


@router.get("/conflicts")
async def get_merge_conflicts(
    rec: "session_auth.SessionRecord" = Depends(require_session),
) -> Dict[str, Any]:
    """Get recent merge conflicts from merge-log."""

    try:
        merge_log_path = _merged_state_dir(rec.tenant_id) / "merge-log.jsonl"

        conflicts = []
        if merge_log_path.exists():
            with open(merge_log_path) as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        # Filter to conflict events (where candidates differ)
                        candidates = event.get("candidates", {})
                        if len(candidates) > 1:
                            conflicts.append({
                                "timestamp": event.get("timestamp"),
                                "category": event.get("decision_category"),
                                "candidates": candidates,
                                "winner": event.get("winner"),
                                "confidence": event.get("confidence")
                            })

        return {
            "conflicts": conflicts[-10:] if conflicts else [],  # Last 10
            "count": len(conflicts)
        }
    except Exception as e:  # noqa: BLE001
        logger.exception("multi-instance conflicts failed")
        raise HTTPException(status_code=500, detail="merge conflicts unavailable") from e


@router.post("/sync")
async def trigger_manual_sync(
    rec: "session_auth.SessionRecord" = Depends(require_csrf),
) -> Dict[str, Any]:
    """Run a cross-device tenant sync (G5, ADR-0369). Ship-dark: gated on the
    ``cross_device_sync`` flag (default off). When on, it runs the LIVE transport —
    pull → GPG-decrypt → type-specific merge INTO the tenant learning dir → re-encrypt
    → push — against the configured git remote. The remote only ever holds ciphertext.
    Auth + CSRF required; GPG is mandatory (the passphrase comes from the Vault)."""
    if not is_enabled("cross_device_sync", rec.tenant_id):
        return {
            "status": "disabled",
            "message": ("Cross-device sync is off. Enable it in Settings → Features "
                        "(cross_device_sync) after configuring a remote + passphrase."),
        }
    remote_url = _configured_remote(rec.tenant_id)
    if not remote_url:
        raise HTTPException(
            status_code=400,
            detail=("No sync remote configured. Set spec.cross_device.sync_remote to a "
                    "git URL (https or file://)."),
        )
    passphrase = _vault_item("cross_device_sync_passphrase")
    if not passphrase:
        raise HTTPException(
            status_code=400,
            detail=("GPG is mandatory for tenant sync. Store a "
                    "'cross_device_sync_passphrase' secret in the Vault first."),
        )
    pat = _vault_item("cross_device_sync_pat")  # optional (for https remotes)
    try:
        from core.cross_device import tenant_sync as _ts  # noqa: PLC0415
        local_dir = tenant_learning_dir(rec.tenant_id)
        cache_dir = _sync_cache_dir(rec.tenant_id)
        report = _ts.run_git_sync(local_dir, remote_url, cache_dir, passphrase, pat=pat)
        return {"status": "synced", "tenant_id": rec.tenant_id, **report.as_dict()}
    except Exception as e:  # noqa: BLE001 — SyncError et al.; never leak the PAT
        raise HTTPException(status_code=502, detail=f"sync failed: {str(e)[:200]}")
