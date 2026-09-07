"""GitHub Sync Worker — Background repository synchronization.

Handles:
- Background sync worker (5-min intervals)
- Webhook event processing
- Audit trail logging
- Error recovery & retry logic
"""

import json
import hashlib
import hmac
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)


class GitHubSyncWorker:
    """Background worker for syncing tenant skills with GitHub."""
    
    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        # Tenant directory through the shared resolver: honours CORVIN_HOME and
        # the tenant axis (the previous ``Path.home()/.corvin`` ignored both —
        # adversarial review E-02, 2026-09-03).
        self.tenant_path = _tenant_path(tenant_id)
        self.config_file = self.tenant_path / 'github-config.json'
        self.audit_file = self.tenant_path / 'github-audit.jsonl'
        self.worker_status_file = self.tenant_path / 'github-worker-status.json'
        
        self.running = False
        self.worker_thread: Optional[threading.Thread] = None
        self.sync_interval = 300  # 5 minutes
        self.last_sync_time: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.sync_count = 0
        self.error_count = 0
    
    def start(self) -> Dict[str, Any]:
        """Start the background sync worker."""
        if self.running:
            return {"success": False, "error": "Worker already running"}
        
        self.running = True
        self.worker_thread = threading.Thread(target=self._run_worker, daemon=True)
        self.worker_thread.start()
        
        self._log_audit("worker_started", {"status": "started"})
        return {
            "success": True,
            "message": "Sync worker started",
            "status": self._get_status()
        }
    
    def stop(self) -> Dict[str, Any]:
        """Stop the background sync worker."""
        if not self.running:
            return {"success": False, "error": "Worker not running"}
        
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5)
        
        self._log_audit("worker_stopped", {"status": "stopped"})
        return {
            "success": True,
            "message": "Sync worker stopped",
            "status": self._get_status()
        }
    
    def _run_worker(self):
        """Main worker loop — runs in background thread."""
        while self.running:
            try:
                # Check if configured
                if not self.config_file.exists():
                    time.sleep(10)
                    continue
                
                config = self._load_config()
                if not config.get('auto_sync'):
                    time.sleep(10)
                    continue
                
                # Run sync
                self._do_sync(config)
                self.last_sync_time = datetime.utcnow()
                self.sync_count += 1
                self._log_audit("sync_completed", {
                    "repo": config.get('url'),
                    "status": "success"
                })
                
            except Exception as e:
                logger.error(f"Sync worker error: {e}")
                self.last_error = str(e)
                self.error_count += 1
                self._log_audit("sync_failed", {
                    "error": str(e)
                })
            
            # Sleep until next sync
            time.sleep(self.sync_interval)
    
    def _do_sync(self, config: Dict[str, Any]):
        """Execute the sync operation."""
        url = config.get('url')
        token = config.get('token')

        if not url:
            return

        try:
            # Import here to avoid circular dependency
            from .github_repo_sync import get_sync

            # Get sync instance and run sync
            sync = get_sync(self.tenant_id)
            result = sync.sync_skills_to_github()

            if result.get('success'):
                logger.info(f"Sync successful: {url}, {len(result.get('files_synced', []))} files")
                self._log_audit("sync_skills_success", {
                    "repo": url,
                    "files_synced": len(result.get('files_synced', [])),
                    "branch": result.get('branch')
                })
            else:
                error = result.get('error', 'Unknown error')
                logger.error(f"Sync failed: {error}")
                self._log_audit("sync_skills_failed", {"error": error})

        except Exception as e:
            logger.error(f"Sync operation error: {e}")
            self._log_audit("sync_operation_error", {"error": str(e)})
    
    def _load_config(self) -> Dict[str, Any]:
        """Load GitHub configuration."""
        try:
            if self.config_file.exists():
                with open(self.config_file) as f:
                    return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
        return {}
    
    def _log_audit(self, event_type: str, details: Dict[str, Any]):
        """Append a hash-chained record to ``github-audit.jsonl`` via the ONE
        canonical writer (``forge.security_events.write_event`` — F-A7). The
        record format is the platform's, so ``voice-audit verify --all`` covers
        this file; the previous private sha256 chain here was a third format
        nothing verified."""
        try:
            from .. import _bootstrap  # noqa: PLC0415
            write_event = _bootstrap.security_events.write_event
        except Exception:  # noqa: BLE001 — fall back to the repo layout
            import sys as _sys
            _forge = Path(__file__).resolve().parents[4] / "operator" / "forge"
            if str(_forge) not in _sys.path:
                _sys.path.append(str(_forge))
            from forge.security_events import write_event  # type: ignore
        try:
            self.tenant_path.mkdir(parents=True, exist_ok=True)
            write_event(self.audit_file, event_type,
                        details={**dict(details), "tenant_id": self.tenant_id})
        except Exception as e:  # noqa: BLE001 — audit is best-effort here; never break a sync
            logger.warning("github audit write failed: %s", type(e).__name__)

    def _get_status(self) -> Dict[str, Any]:
        """Get worker status."""
        uptime = "unknown"
        if self.last_sync_time:
            delta = datetime.utcnow() - self.last_sync_time
            uptime = str(delta).split('.')[0]
        
        return {
            "running": self.running,
            "interval_seconds": self.sync_interval,
            "last_sync": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "last_error": self.last_error,
            "sync_count": self.sync_count,
            "error_count": self.error_count,
            "uptime": uptime,
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Public status API."""
        return self._get_status()


def _tenant_path(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>/`` via ``forge.paths`` (SSOT)."""
    from .. import _bootstrap  # noqa: PLC0415 — routes → console package root

    return Path(_bootstrap.forge_paths.tenant_home(tenant_id))


# One worker PER TENANT. A single process-global worker (the previous shape)
# served whichever tenant asked first to every later caller — a cross-tenant
# control channel.
_workers: Dict[str, GitHubSyncWorker] = {}
_workers_lock = threading.Lock()


def get_worker(tenant_id: str = "_default") -> GitHubSyncWorker:
    """Get or create the sync worker bound to *tenant_id*."""
    with _workers_lock:
        worker = _workers.get(tenant_id)
        if worker is None:
            worker = GitHubSyncWorker(tenant_id)
            _workers[tenant_id] = worker
        return worker
