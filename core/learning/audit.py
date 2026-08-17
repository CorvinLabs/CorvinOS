"""Immutable audit trail for learning events (Phase 5)."""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timedelta
import json
import hashlib


class AuditTrail:
    """Append-only, hash-chained audit log for learning events."""
    
    def __init__(self, audit_dir: Path = None):
        if audit_dir is None:
            audit_dir = Path.home() / ".corvin" / "learning" / "audit"
        self.audit_dir = audit_dir
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.current_hash = self._load_latest_hash()
    
    def _load_latest_hash(self) -> str:
        """Load the hash of the last record in the chain."""
        chain_file = self.audit_dir / "chain.txt"
        if chain_file.exists():
            content = chain_file.read_text().strip()
            if content:
                lines = [l for l in content.split('\n') if l.strip()]
                if lines:
                    parts = lines[-1].split(' ')
                    if len(parts) > 0 and parts[0]:
                        return parts[0]
        return "0" * 64
    
    def write(self, event_type: str, subject_id: str, payload: dict) -> str:
        """Write event to audit log with hash chain.
        
        Returns: SHA256 hash of this record
        """
        timestamp = datetime.utcnow().isoformat()
        
        # Create record
        record = {
            "timestamp": timestamp,
            "event_type": event_type,
            "subject_id": subject_id,
            "payload": payload,
            "previous_hash": self.current_hash,
        }
        
        # Compute hash
        record_json = json.dumps(record, sort_keys=True)
        record_hash = hashlib.sha256(record_json.encode()).hexdigest()
        
        # Append to daily file
        date_str = datetime.utcnow().strftime("%Y-%m-%d")
        daily_file = self.audit_dir / f"audit-{date_str}.jsonl"
        
        with open(daily_file, "a") as f:
            f.write(record_json + "\n")
        
        # Update chain
        chain_file = self.audit_dir / "chain.txt"
        with open(chain_file, "a") as f:
            f.write(f"{record_hash} {timestamp}\n")
        
        self.current_hash = record_hash
        return record_hash
    
    def verify(self) -> bool:
        """Verify audit chain integrity against persisted chain.txt.

        Returns: True if all hashes chain correctly, False if corrupted
        """
        chain_file = self.audit_dir / "chain.txt"
        if not chain_file.exists():
            return True

        # Load persisted chain
        try:
            persisted_hashes = []
            for line in chain_file.read_text().strip().split('\n'):
                if line.strip():
                    parts = line.split(' ')
                    if len(parts) > 0:
                        persisted_hashes.append(parts[0])
        except Exception:
            return False

        # Read and verify all records
        all_records = []
        for daily_file in sorted(self.audit_dir.glob("audit-*.jsonl")):
            try:
                for line in daily_file.read_text().strip().split('\n'):
                    if line.strip():
                        all_records.append(json.loads(line))
            except (json.JSONDecodeError, IOError):
                return False

        if len(all_records) != len(persisted_hashes):
            return False

        # Verify each record's hash against chain.txt
        prev_hash = "0" * 64
        for i, record in enumerate(all_records):
            if record.get("previous_hash") != prev_hash:
                return False

            # Recompute hash
            record_copy = {k: v for k, v in record.items() if k != "previous_hash"}
            record_copy["previous_hash"] = prev_hash
            json_str = json.dumps(record_copy, sort_keys=True)
            computed_hash = hashlib.sha256(json_str.encode()).hexdigest()

            # Verify against persisted hash
            if computed_hash != persisted_hashes[i]:
                return False

            prev_hash = computed_hash

        return True
    
    def get_events_in_range(self, start: datetime, end: datetime) -> list[dict]:
        """Retrieve events in a date range."""
        events = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            daily_file = self.audit_dir / f"audit-{date_str}.jsonl"
            if daily_file.exists():
                for line in daily_file.read_text().strip().split('\n'):
                    if line.strip():
                        events.append(json.loads(line))
            current += timedelta(days=1)
        return events
    
    def compact(self, older_than_days: int = 365) -> None:
        """Archive old records (future: migrate to Parquet)."""
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)
        current = datetime.utcnow() - timedelta(days=older_than_days)
        
        summary = {}
        while current <= cutoff:
            date_str = current.strftime("%Y-%m-%d")
            daily_file = self.audit_dir / f"audit-{date_str}.jsonl"
            
            if daily_file.exists():
                try:
                    events = []
                    for line in daily_file.read_text().strip().split('\n'):
                        if line.strip():
                            events.append(json.loads(line))

                    # Aggregate by event_type and subject_id
                    for event in events:
                        key = (event["event_type"], event["subject_id"])
                        if key not in summary:
                            summary[key] = {"count": 0, "first_timestamp": event["timestamp"]}
                        summary[key]["count"] += 1

                    # Archive the daily file
                    daily_file.rename(daily_file.with_suffix('.archived'))
                except (json.JSONDecodeError, IOError, KeyError):
                    # Skip corrupted files; they remain for manual recovery
                    pass
            
            current += timedelta(days=1)
        
        # Write summary
        summary_file = self.audit_dir / f"summary-{cutoff.strftime('%Y-%m')}.json"
        summary_file.write_text(json.dumps(summary, indent=2))
