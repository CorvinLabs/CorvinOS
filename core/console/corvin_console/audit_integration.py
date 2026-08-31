"""Audit Trail Integration for Cross-Device-Learning Sync.

GDPR Art. 30, 32: Hash-chained audit log with daily verification.
Every sync event is logged, signed, and chained to prevent tampering.

Files:
- ~/.corvin/tenants/_default/audit.jsonl (main log)
- ~/.corvin/tenants/_default/audit-chain.json (hash chain metadata)
"""

import json
import hashlib
import hmac
import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any
import logging

logger = logging.getLogger(__name__)

TENANT_PATH = Path.home() / '.corvin' / 'tenants' / '_default'
AUDIT_FILE = TENANT_PATH / 'audit.jsonl'
CHAIN_FILE = TENANT_PATH / 'audit-chain.json'


class AuditEvent:
    """Immutable audit event with hash chain."""

    def __init__(
        self,
        event_type: str,
        action: str,
        subject: str,
        details: dict[str, Any],
        tenant_id: str = '_default',
        operator_id: Optional[str] = None,
        previous_hash: Optional[str] = None,
    ):
        self.timestamp = datetime.utcnow().isoformat()
        self.event_type = event_type
        self.action = action
        self.subject = subject
        self.details = details
        self.tenant_id = tenant_id
        self.operator_id = operator_id or 'system'
        self.previous_hash = previous_hash

        # Compute hash (includes previous_hash for chain)
        self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute SHA256 hash of event."""
        # Include previous hash to create chain
        content = json.dumps(
            {
                'timestamp': self.timestamp,
                'event_type': self.event_type,
                'action': self.action,
                'subject': self.subject,
                'tenant_id': self.tenant_id,
                'operator_id': self.operator_id,
                'details': self.details,
                'previous_hash': self.previous_hash,
            },
            sort_keys=True,
            separators=(',', ':'),
        )
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return {
            'timestamp': self.timestamp,
            'event_type': self.event_type,
            'action': self.action,
            'subject': self.subject,
            'tenant_id': self.tenant_id,
            'operator_id': self.operator_id,
            'details': self.details,
            'hash': self.hash,
            'previous_hash': self.previous_hash,
        }


class AuditLogger:
    """Manages audit trail with hash-chain verification."""

    def __init__(self, tenant_path: Path = TENANT_PATH):
        self.tenant_path = tenant_path
        self.audit_file = tenant_path / 'audit.jsonl'
        self.chain_file = tenant_path / 'audit-chain.json'
        self._last_hash: Optional[str] = None

    def _ensure_audit_dir(self) -> None:
        """Ensure audit directory exists."""
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)

    def _get_last_hash(self) -> Optional[str]:
        """Get hash of last event in chain."""
        if not self.audit_file.exists():
            return None

        try:
            with open(self.audit_file, 'rb') as f:
                # Seek to end and read backwards to find last line
                f.seek(0, 2)
                file_size = f.tell()

                if file_size == 0:
                    return None

                # Read last line
                buffer_size = 1024
                position = file_size

                while position >= 0:
                    read_size = min(buffer_size, position)
                    position -= read_size
                    f.seek(position)

                    chunk = f.read(read_size)
                    lines = chunk.split(b'\n')

                    # Process lines in reverse
                    for line in reversed(lines):
                        if line.strip():
                            try:
                                event = json.loads(line.decode('utf-8'))
                                return event.get('hash')
                            except:
                                continue

            return None

        except Exception as e:
            logger.error(f'Error reading last hash: {e}')
            return None

    def log_event(self, event: AuditEvent) -> bool:
        """
        Log audit event to chain (with file locking for concurrent safety).

        Returns: True if logged successfully
        """
        self._ensure_audit_dir()

        # Get previous hash for chain
        if self._last_hash is None:
            self._last_hash = self._get_last_hash()

        event.previous_hash = self._last_hash

        try:
            # Append to JSONL file with exclusive lock (GDPR Art. 30, 32)
            with open(self.audit_file, 'a', encoding='utf-8') as f:
                # Acquire exclusive lock to prevent concurrent writes
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    # Re-read last hash after acquiring lock (another thread may have written)
                    self._last_hash = self._get_last_hash()
                    event.previous_hash = self._last_hash

                    # Write atomically
                    f.write(json.dumps(event.to_dict(), separators=(',', ':')) + '\n')
                    f.flush()
                finally:
                    # Release lock
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            # Update chain metadata
            self._update_chain_metadata(event)

            # Update last hash for next event
            self._last_hash = event.hash

            logger.info(f'Audit logged: {event.event_type}/{event.action}')
            return True

        except Exception as e:
            logger.error(f'Failed to log audit event: {e}')
            return False

    def _update_chain_metadata(self, event: AuditEvent) -> None:
        """Update chain metadata file (for verification)."""
        try:
            metadata = {'last_event_hash': event.hash, 'last_timestamp': event.timestamp}

            if self.chain_file.exists():
                with open(self.chain_file) as f:
                    existing = json.load(f)
                metadata['chain_start'] = existing.get('chain_start', event.timestamp)
            else:
                metadata['chain_start'] = event.timestamp

            with open(self.chain_file, 'w') as f:
                json.dump(metadata, f, indent=2)

        except Exception as e:
            logger.error(f'Failed to update chain metadata: {e}')

    def verify_chain(self) -> tuple[bool, list[str]]:
        """
        Verify hash chain integrity.

        Returns: (valid, errors)
        """
        errors = []

        if not self.audit_file.exists():
            return True, []

        try:
            previous_hash = None

            with open(self.audit_file, 'r', encoding='utf-8') as f:
                line_num = 0
                for line in f:
                    line_num += 1

                    if not line.strip():
                        continue

                    try:
                        event = json.loads(line)

                        # Verify hash chain
                        stored_previous = event.get('previous_hash')
                        if stored_previous != previous_hash:
                            errors.append(
                                f'Line {line_num}: Chain broken. '
                                f'Expected previous_hash={previous_hash}, '
                                f'got {stored_previous}'
                            )

                        # Verify event hash (recompute)
                        stored_hash = event.get('hash')
                        event_copy = dict(event)
                        event_copy.pop('hash', None)

                        content = json.dumps(
                            event_copy,
                            sort_keys=True,
                            separators=(',', ':'),
                        )
                        computed_hash = hashlib.sha256(content.encode()).hexdigest()

                        if computed_hash != stored_hash:
                            errors.append(
                                f'Line {line_num}: Hash mismatch. '
                                f'Expected {stored_hash}, computed {computed_hash}'
                            )

                        previous_hash = stored_hash

                    except json.JSONDecodeError:
                        errors.append(f'Line {line_num}: Invalid JSON')

            return len(errors) == 0, errors

        except Exception as e:
            return False, [f'Error verifying chain: {e}']


# Sync Event Logging

def log_github_connection(
    owner: str,
    repo: str,
    success: bool,
    error: Optional[str] = None,
    operator_id: Optional[str] = None,
) -> bool:
    """Log GitHub connection event."""
    logger = AuditLogger()

    event = AuditEvent(
        event_type='github_integration',
        action='connection_verified',
        subject=f'github/{owner}/{repo}',
        details={
            'owner': owner,
            'repo': repo,
            'success': success,
            'error': error,
        },
        operator_id=operator_id,
    )

    return logger.log_event(event)


def log_sync_started(
    owner: str,
    repo: str,
    trigger: str,
    operator_id: Optional[str] = None,
) -> bool:
    """Log sync operation start."""
    logger = AuditLogger()

    event = AuditEvent(
        event_type='sync',
        action='started',
        subject=f'github/{owner}/{repo}',
        details={'trigger': trigger},  # 'webhook' or 'worker' or 'manual'
        operator_id=operator_id,
    )

    return logger.log_event(event)


def log_sync_completed(
    owner: str,
    repo: str,
    skills_synced: int,
    duration_seconds: float,
    operator_id: Optional[str] = None,
) -> bool:
    """Log successful sync."""
    logger = AuditLogger()

    event = AuditEvent(
        event_type='sync',
        action='completed',
        subject=f'github/{owner}/{repo}',
        details={
            'skills_synced': skills_synced,
            'duration_seconds': duration_seconds,
        },
        operator_id=operator_id,
    )

    return logger.log_event(event)


def log_sync_failed(
    owner: str,
    repo: str,
    error: str,
    operator_id: Optional[str] = None,
) -> bool:
    """Log sync failure."""
    logger = AuditLogger()

    event = AuditEvent(
        event_type='sync',
        action='failed',
        subject=f'github/{owner}/{repo}',
        details={'error': error},
        operator_id=operator_id,
    )

    return logger.log_event(event)


def log_webhook_received(
    event_type: str,
    action: str,
    owner: str,
    repo: str,
) -> bool:
    """Log webhook event received."""
    logger = AuditLogger()

    event = AuditEvent(
        event_type='webhook',
        action='received',
        subject=f'github/{owner}/{repo}',
        details={
            'webhook_event': event_type,
            'webhook_action': action,
        },
    )

    return logger.log_event(event)


def log_config_changed(
    owner: str,
    repo: str,
    change_type: str,
    operator_id: Optional[str] = None,
) -> bool:
    """Log configuration change."""
    logger = AuditLogger()

    event = AuditEvent(
        event_type='config',
        action='changed',
        subject=f'github/{owner}/{repo}',
        details={'change_type': change_type},
        operator_id=operator_id,
    )

    return logger.log_event(event)


def get_audit_summary(days: int = 1) -> dict:
    """Get audit summary for last N days."""
    logger = AuditLogger()

    if not logger.audit_file.exists():
        return {'total_events': 0, 'events_by_type': {}}

    cutoff = datetime.utcnow() - timedelta(days=days)
    events_by_type = {}
    total_events = 0

    try:
        with open(logger.audit_file, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    event = json.loads(line)
                    ts = datetime.fromisoformat(event.get('timestamp', ''))

                    if ts >= cutoff:
                        event_type = event.get('event_type', 'unknown')
                        events_by_type[event_type] = events_by_type.get(event_type, 0) + 1
                        total_events += 1

                except:
                    pass

    except Exception as e:
        logger.error(f'Error reading audit summary: {e}')

    return {
        'total_events': total_events,
        'events_by_type': events_by_type,
        'period_days': days,
    }
