"""EventStore cryptographic binding (ADR-0541 Fix 1.3, 2.2)."""

import hmac
import hashlib
from typing import Optional
from .models import Snapshot


class CryptoBinding:
    """HMAC-SHA256 signing for snapshots (ADR-0541 Fix 1.3)."""

    def __init__(self, external_key: str):
        """Initialize with external key (e.g., from HSM or env var)."""
        self.key = external_key.encode() if isinstance(external_key, str) else external_key

    def sign_snapshot(self, snapshot: Snapshot) -> str:
        """Sign snapshot with HMAC-SHA256."""
        message = snapshot.snapshot_hash.encode()
        signature = hmac.new(self.key, message, hashlib.sha256).hexdigest()
        return signature

    def verify_snapshot(self, snapshot: Snapshot, signature: str) -> bool:
        """Verify snapshot signature."""
        expected = self.sign_snapshot(snapshot)
        return hmac.compare_digest(expected, signature)


class VerificationCron:
    """Daily verification cron (ADR-0541 Fix 1.2, 5.3)."""

    def __init__(self, event_store, crypto: Optional[CryptoBinding] = None):
        self.event_store = event_store
        self.crypto = crypto
        self.verification_results = []

    def verify_all_tasks(self, min_age_days: int = 30) -> dict:
        """Verify all tasks older than min_age_days (daily cron job)."""
        # In production: query all tasks older than 30d from database
        # For mock: iterate all known tasks
        results = {
            "verified": [],
            "failed": [],
            "total": 0,
        }

        for task_id in self.event_store.event_store.keys():
            chain_valid = self.event_store.verify_chain(task_id)
            results["total"] += 1

            if chain_valid:
                results["verified"].append(task_id)
            else:
                results["failed"].append({
                    "task_id": task_id,
                    "error": "Chain verification failed"
                })

        self.verification_results.append(results)
        return results

    def get_cron_status(self) -> dict:
        """Get last verification cron status."""
        if not self.verification_results:
            return {"status": "never_run"}

        latest = self.verification_results[-1]
        return {
            "status": "ok" if not latest["failed"] else "failed",
            "total": latest["total"],
            "passed": len(latest["verified"]),
            "failed": len(latest["failed"]),
            "last_results": latest,
        }
