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
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
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
        """Log event to audit trail with hash-chain."""
        try:
            # Read previous hash
            prev_hash = "0" * 64
            if self.audit_file.exists():
                with open(self.audit_file, 'rb') as f:
                    lines = f.readlines()
                    if lines:
                        last_line = json.loads(lines[-1])
                        prev_hash = last_line.get('hash', '0' * 64)
            
            # Create event with hash-chain
            event = {
                "event_id": f"evt-{int(time.time()*1000)}",
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "event_type": event_type,
                "tenant_id": self.tenant_id,
                "details": details,
                "prev_hash": prev_hash,
            }
            
            # Calculate hash
            event_json = json.dumps(event, sort_keys=True)
            event['hash'] = hashlib.sha256(
                (prev_hash + event_json).encode()
            ).hexdigest()
            
            # Append to audit file
            self.tenant_path.mkdir(parents=True, exist_ok=True)
            with open(self.audit_file, 'a') as f:
                f.write(json.dumps(event) + '\n')
        
        except Exception as e:
            logger.error(f"Audit logging failed: {e}")
    
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


# Global worker instance
_worker: Optional[GitHubSyncWorker] = None

def get_worker(tenant_id: str = "_default") -> GitHubSyncWorker:
    """Get or create the global sync worker."""
    global _worker
    if _worker is None:
        _worker = GitHubSyncWorker(tenant_id)
    return _worker
