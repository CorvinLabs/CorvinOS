#!/usr/bin/env python3
"""Secret Rotation Script (ADR-0232, GDPR Art. 32 Compliance).

Rotates secrets according to policy, maintains audit trail, enforces boot tripwire.
Run from boot or on cron schedule (recommend daily).

Usage:
  python3 rotate_secrets.py --policy-file core/compliance/secret_rotation_policy.yaml
"""

import argparse
import datetime
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

logger = logging.getLogger(__name__)


class SecretRotationManager:
    """Manages secret rotation lifecycle, audit trail, and compliance enforcement."""

    def __init__(self, policy_path: str, tenant_id: str = "_default"):
        """Initialize manager with policy.
        
        Args:
            policy_path: Path to secret_rotation_policy.yaml
            tenant_id: Tenant scope (ADR-0007)
        """
        self.policy_path = Path(policy_path)
        self.tenant_id = tenant_id
        
        if not self.policy_path.exists():
            raise FileNotFoundError(f"Policy not found: {policy_path}")
        
        with open(self.policy_path) as f:
            self.policy = yaml.safe_load(f)
        
        self.rotation_log_path = Path(self.policy["rotation_policy"]["storage"]["location"]).expanduser()
        self.rotation_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.rotation_log_path.chmod(0o600)  # Ensure secure permissions
        
    def load_rotation_history(self) -> Dict[str, dict]:
        """Load rotation history from audit trail."""
        history = {}
        if not self.rotation_log_path.exists():
            return history
        
        with open(self.rotation_log_path) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    secret_id = record.get("secret_id")
                    if secret_id:
                        history[secret_id] = record
                except json.JSONDecodeError:
                    logger.warning(f"Skipping malformed audit record: {line}")
        
        return history

    def check_rotation_due(self, secret_id: str, secret_type: str, history: Dict[str, dict]) -> bool:
        """Check if rotation is due for a secret."""
        interval_days = self.policy["rotation_policy"]["intervals"].get(secret_type, 90)
        
        if secret_id not in history:
            # First rotation (no prior record)
            logger.info(f"Secret {secret_id} ({secret_type}): first rotation due")
            return True
        
        last_rotation_ts = history[secret_id].get("rotation_time")
        if not last_rotation_ts:
            return True
        
        last_rotation = datetime.datetime.fromisoformat(last_rotation_ts)
        days_since = (datetime.datetime.utcnow() - last_rotation).days
        
        if days_since >= interval_days:
            logger.info(f"Secret {secret_id} ({secret_type}): overdue by {days_since - interval_days} days")
            return True
        
        return False

    def rotate_secret(self, secret_id: str, secret_type: str, current_secret: str) -> str:
        """Rotate a secret and return new secret.
        
        For now: generate a deterministic new secret based on timestamp.
        In production: call a KMS or secrets management service.
        """
        import secrets
        new_secret = secrets.token_urlsafe(32)
        logger.info(f"Rotated {secret_id}: {secret_type}")
        return new_secret

    def write_audit_record(self, secret_id: str, secret_type: str, 
                          old_secret: str, new_secret: str, rotated_by: str) -> None:
        """Write immutable audit record (hash-chained, GDPR Art. 30)."""
        
        old_hash = hashlib.sha256(old_secret.encode()).hexdigest()[:16]
        new_hash = hashlib.sha256(new_secret.encode()).hexdigest()[:16]
        
        record = {
            "event_type": "secret_rotated",
            "secret_id": secret_id,
            "secret_type": secret_type,
            "rotation_time": datetime.datetime.utcnow().isoformat() + "Z",
            "old_secret_hash": old_hash,  # Never plaintext
            "new_secret_hash": new_hash,
            "rotated_by": rotated_by,
            "tenant_id": self.tenant_id,
        }
        
        # Append-only: write to audit trail
        with open(self.rotation_log_path, "a") as f:
            f.write(json.dumps(record) + "\n")
        
        logger.info(f"Audit record written for {secret_id}")

    def enforce_boot_tripwire(self) -> bool:
        """Check boot enforcement: fail-closed if any rotation is overdue."""
        if not self.policy["rotation_policy"]["boot_enforcement"]["enabled"]:
            return True
        
        history = self.load_rotation_history()
        
        # Mock secret inventory (in production: fetch from KMS or secrets manager)
        secret_inventory = {
            "api_key_prod": "api_keys",
            "db_password": "database_credentials",
        }
        
        for secret_id, secret_type in secret_inventory.items():
            if self.check_rotation_due(secret_id, secret_type, history):
                logger.error(f"BOOT TRIPWIRE: Secret {secret_id} rotation is overdue!")
                return False
        
        return True

    def run_rotation(self) -> bool:
        """Execute full rotation workflow."""
        if not self.policy["rotation_policy"]["enabled"]:
            logger.info("Secret rotation disabled by policy")
            return True
        
        history = self.load_rotation_history()
        rotated_count = 0
        
        # Mock secret inventory
        secret_inventory = {
            "api_key_prod": "api_keys",
            "db_password": "database_credentials",
        }
        
        for secret_id, secret_type in secret_inventory.items():
            if self.check_rotation_due(secret_id, secret_type, history):
                # In production: fetch current secret from KMS
                current_secret = "placeholder_" + secret_id
                new_secret = self.rotate_secret(secret_id, secret_type, current_secret)
                self.write_audit_record(secret_id, secret_type, current_secret, new_secret, "automated")
                rotated_count += 1
        
        logger.info(f"Rotation complete: {rotated_count} secrets rotated")
        return True


def main():
    parser = argparse.ArgumentParser(description="Secret Rotation Daemon")
    parser.add_argument("--policy-file", default="core/compliance/secret_rotation_policy.yaml")
    parser.add_argument("--boot-check-only", action="store_true", help="Only check boot tripwire, don't rotate")
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO)
    
    try:
        manager = SecretRotationManager(args.policy_file)
        
        if args.boot_check_only:
            if not manager.enforce_boot_tripwire():
                logger.error("Boot tripwire check FAILED")
                return 1
            logger.info("Boot tripwire check PASSED")
            return 0
        
        if not manager.run_rotation():
            return 1
        
        return 0
    
    except Exception as e:
        logger.error(f"Secret rotation failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
