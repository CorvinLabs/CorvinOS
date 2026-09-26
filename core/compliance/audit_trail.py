"""L16: Audit Trail — Hash-chained, immutable, fsync'd."""

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from .exceptions import AuditChainError


@dataclass(frozen=True)
class AuditRecord:
    """Immutable audit event."""
    timestamp: str  # ISO-8601 UTC
    event_type: str  # e.g., "consent_granted", "data_flow_blocked"
    tenant_id: str
    actor: str
    action: str
    resource: str
    result: str  # "allowed" | "denied" | "error"
    details: dict  # Metadata (no PII)
    
    def to_json(self) -> str:
        """Serialize to JSON (sorted for determinism)."""
        return json.dumps(asdict(self), sort_keys=True)
    
    def hash(self) -> str:
        """SHA256 of record content."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()


class AuditTrail:
    """Hash-chained, immutable audit log (GDPR Art. 30, 32)."""
    
    def __init__(self, path: Path):
        """Initialize trail at given path."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        # Ensure strict permissions (read-only after write)
        os.chmod(self.path, 0o600)
    
    def last_hash(self) -> str:
        """Get last record's hash (chain anchor)."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return hashlib.sha256(b'').hexdigest()  # Genesis hash
        
        with open(self.path, 'r') as f:
            lines = f.readlines()
            if not lines:
                return hashlib.sha256(b'').hexdigest()
            
            last_line = lines[-1].strip()
            try:
                record_json = json.loads(last_line)
                return record_json.get('hash', hashlib.sha256(b'').hexdigest())
            except json.JSONDecodeError:
                raise AuditChainError("Corrupted audit trail")
    
    def append(self, record: AuditRecord) -> str:
        """Append record to trail (hash-chained). Returns hash."""
        # Compute hash
        record_hash = record.hash()
        prev_hash = self.last_hash()
        
        # Create chain record
        chain_record = {
            "record": asdict(record),
            "hash": record_hash,
            "prev_hash": prev_hash,
        }
        
        # Write (atomic via line append)
        try:
            with open(self.path, 'a') as f:
                f.write(json.dumps(chain_record) + '\n')
                f.flush()
                os.fsync(f.fileno())  # Force disk write
        except (IOError, OSError) as e:
            raise AuditChainError(f"Failed to write audit trail: {e}")
        
        return record_hash
    
    def verify_chain(self) -> bool:
        """Verify hash-chain integrity."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return True  # Empty chain is valid
        
        expected_prev = hashlib.sha256(b'').hexdigest()  # Genesis
        
        with open(self.path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    chain_record = json.loads(line.strip())
                    record_data = chain_record['record']
                    record_hash = chain_record['hash']
                    prev_hash = chain_record['prev_hash']
                    
                    # Verify prev_hash matches expected
                    if prev_hash != expected_prev:
                        raise AuditChainError(
                            f"Chain broken at line {line_num}: "
                            f"expected prev={expected_prev}, got {prev_hash}"
                        )
                    
                    # Verify hash matches record content
                    expected_hash = hashlib.sha256(
                        json.dumps(record_data, sort_keys=True).encode()
                    ).hexdigest()
                    if record_hash != expected_hash:
                        raise AuditChainError(
                            f"Hash mismatch at line {line_num}: "
                            f"expected {expected_hash}, got {record_hash}"
                        )
                    
                    expected_prev = record_hash
                    
                except json.JSONDecodeError as e:
                    raise AuditChainError(f"Invalid JSON at line {line_num}: {e}")
        
        return True
    
    def records(self):
        """Iterate all records (read-only)."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return
        
        with open(self.path, 'r') as f:
            for line in f:
                try:
                    chain_record = json.loads(line.strip())
                    record_data = chain_record['record']
                    # Convert back to AuditRecord
                    yield AuditRecord(**record_data)
                except (json.JSONDecodeError, TypeError):
                    continue


def new_audit_record(
    event_type: str,
    tenant_id: str,
    actor: str,
    action: str,
    resource: str,
    result: str,
    details: Optional[dict] = None,
) -> AuditRecord:
    """Create a new audit record."""
    return AuditRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        resource=resource,
        result=result,
        details=details or {},
    )
