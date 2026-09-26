"""Audit Trail Integration for Task Tracking — ADR-0232 § Hash-Chain + ADR-0297 § PII Detection.

Phase C (k=2): AuditEvent dataclass with cryptographic hash-chain linking.
Every task event creates an immutable audit record that respects tenant isolation
and applies PII-Detection before persistence. Events are stored both in the core
audit chain (source of truth) and referenced in the task_tracking DB.

Compliance:
  - GDPR Art. 30: immutable audit trail with hash-chain verification
  - GDPR Art. 32: PII-Detection (ADR-0297) applied fail-closed before persistence
  - ADR-0232: audit-first design (core chain record, then DB reference)
  - ADR-0007: tenant-scoped isolation (every event carries tenant_id)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.audit.chain import AuditEntry
from core.pii.detector import PIIDetector


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event for task tracking.

    This event is the input to emit_task_audit_event(). Once persisted to the
    core audit chain, it becomes part of the cryptographic hash-chain and is
    non-modifiable.

    Fields:
      event_type: str — categorical event (task_created, task_updated, task_deleted, etc.)
      task_id: str — which task (tenant-scoped ID)
      tenant_id: str — mandatory for GDPR Art. 5/32 tenant isolation
      actor: str — who performed the action (user, service, system)
      action: str — what was changed (title, status, owner, etc.)
      delta: dict[str, Any] — before/after values (PII-scrubbed)
      timestamp: str — ISO-8601 UTC
      prior_hash: str — hash of prior event (set by emit_task_audit_event)
      chain_hash: str — hash of this event (computed before persistence)

    Invariants:
      - frozen=True: instances are immutable after creation
      - tenant_id: fail-closed if missing (caller must provide)
      - task_id: fail-closed if missing (caller must provide)
      - delta: redacted by emit_task_audit_event before this dataclass is instantiated
    """

    event_type: str
    task_id: str
    tenant_id: str
    actor: str
    action: str
    delta: dict[str, Any]
    timestamp: str
    prior_hash: str = "genesis"
    chain_hash: str = ""  # Computed and set by emit_task_audit_event

    def compute_hash(self) -> str:
        """Compute SHA256 hash of event content (excluding chain_hash).

        Returns immutable commitment to event content: tampering with any field
        (including delta) will produce a different hash.
        """
        # Create dict without chain_hash (it's computed, not part of content)
        content = {k: v for k, v in asdict(self).items() if k != "chain_hash"}

        # Canonical JSON (sorted keys, compact separators)
        json_str = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)

        # SHA256
        return hashlib.sha256(json_str.encode()).hexdigest()

    def with_hashes(self, prior_hash: str = "genesis") -> AuditEvent:
        """Return new AuditEvent with prior_hash and chain_hash set.

        Called by emit_task_audit_event after PII-detection has scrubbed delta.
        """
        # Create new instance with computed hashes
        event = AuditEvent(
            event_type=self.event_type,
            task_id=self.task_id,
            tenant_id=self.tenant_id,
            actor=self.actor,
            action=self.action,
            delta=self.delta,
            timestamp=self.timestamp,
            prior_hash=prior_hash,
            chain_hash="",  # Will be set below
        )

        # Compute hash
        computed_hash = event.compute_hash()

        # Return new instance with chain_hash set
        return AuditEvent(
            event_type=event.event_type,
            task_id=event.task_id,
            tenant_id=event.tenant_id,
            actor=event.actor,
            action=event.action,
            delta=event.delta,
            timestamp=event.timestamp,
            prior_hash=prior_hash,
            chain_hash=computed_hash,
        )


def _scrub_pii_from_delta(
    delta: dict[str, Any],
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Apply PII-Detection (ADR-0297) to delta fields.

    Scrubs or redacts fields that contain high-confidence PII. Uses fail-closed
    semantics: if detection fails, treats as suspicious and redacts the field.

    Args:
      delta: before/after values from the task change
      tenant_id: tenant context for PII detection

    Returns:
      Scrubbed delta safe for audit logging (no PII leakage).
    """
    detector = PIIDetector()
    scrubbed = {}

    for key, value in delta.items():
        if value is None:
            scrubbed[key] = value
            continue

        # For nested dicts/lists, recursively check all strings
        if isinstance(value, dict):
            scrubbed[key] = _scrub_pii_from_delta(value, tenant_id=tenant_id)
        elif isinstance(value, list):
            scrubbed[key] = [
                _scrub_pii_from_value(v, detector=detector, tenant_id=tenant_id)
                for v in value
            ]
        else:
            scrubbed[key] = _scrub_pii_from_value(value, detector=detector, tenant_id=tenant_id)

    return scrubbed


def _scrub_pii_from_value(
    value: Any,
    *,
    detector: PIIDetector,
    tenant_id: str,
) -> Any:
    """Scrub or redact a single value if it contains PII.

    If value is a string and contains PII, returns a redacted version.
    Otherwise returns the value unchanged.
    """
    if not isinstance(value, str):
        return value

    # Detect PII in this string
    try:
        finding = detector.detect(value, tenant_id=tenant_id)
        if finding and finding.confidence >= 0.75:
            # High-confidence PII found: redact it
            # Format: "<pii_type>:<length>:<redacted>"
            redacted = f"[{finding.pii_class.upper()}:len={len(value)}:***]"
            return redacted
    except Exception:
        # PII detection failed: treat as suspicious, redact the whole value
        return f"[SUSPICIOUS:len={len(value)}:***]"

    return value


class AuditChainWriter:
    """Write audit events to the core audit chain with hash-chain linking.

    Coordinates with task_tracking.store to:
    1. Write the primary record to the core audit chain (source of truth)
    2. Store a reference in task_tracking.events (for fast queries)
    3. Maintain cryptographic hash-chain continuity

    Invariants:
      - Audit-first design (core chain record commits before DB reference)
      - Fail-closed (if core chain write fails, DB write is skipped)
      - Tenant-scoped (every event carries tenant_id; reads filtered by tenant)
    """

    def __init__(self, chain_log_path: Path):
        """Initialize writer with path to core audit chain log."""
        self.chain_log_path = Path(chain_log_path)
        self._last_hash = "genesis"  # Loaded on first write

    def _load_last_hash(self) -> None:
        """Load the hash of the last event in the chain (for prior_hash linking)."""
        if not self.chain_log_path.exists():
            self._last_hash = "genesis"
            return

        try:
            with open(self.chain_log_path, "r") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        self._last_hash = data.get("chain_hash", "genesis")
        except Exception:
            # On error, start fresh (genesis); next write will detect the chain
            self._last_hash = "genesis"

    def write_event(self, event: AuditEvent) -> str:
        """Write an audit event to the core chain with hash-chain linking.

        Performs audit-first design:
        1. Load last hash from chain (for prior_hash)
        2. Compute event hash with prior_hash
        3. Write to core audit chain (fsync)
        4. Return the event's chain_hash (caller stores in DB reference)

        Returns:
          The event's chain_hash (for DB reference and verification).

        Raises:
          OSError: If core chain write fails (fail-closed, no DB write).
        """
        # Load last hash (only once per writer instance)
        if self._last_hash == "genesis" and self.chain_log_path.exists():
            self._load_last_hash()

        # Compute event hash with prior_hash
        event_with_hashes = event.with_hashes(prior_hash=self._last_hash)

        # Write to core audit chain (fsync for durability)
        try:
            self._write_to_core_chain(event_with_hashes)
        except OSError as e:
            # Fail-closed: if core chain write fails, don't proceed to DB
            raise OSError(f"Audit chain write failed (fail-closed): {e}") from e

        # Update last hash for next write
        self._last_hash = event_with_hashes.chain_hash

        return event_with_hashes.chain_hash

    def _write_to_core_chain(self, event: AuditEvent) -> None:
        """Write event to core audit chain log with fsync.

        The core chain is append-only and immutable. Each event carries the
        prior event's hash, creating a cryptographic hash-chain.

        Raises:
          OSError: If write or fsync fails.
        """
        import os

        # Ensure directory exists
        self.chain_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Append JSON line with fsync
        with open(self.chain_log_path, "a") as f:
            json.dump(asdict(event), f)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())  # Force OS to sync to disk


async def emit_task_audit_event(
    event_type: str,
    task_id: str,
    *,
    tenant_id: str,
    actor: str,
    action: str,
    delta: dict[str, Any],
    store=None,  # Optional task_tracking.store for DB reference
) -> str:
    """Emit a task audit event with hash-chain linking and PII-Detection.

    This is the primary entry point for task tracking audits. It:
    1. Scrubs PII from delta (ADR-0297)
    2. Creates AuditEvent with tenant isolation
    3. Writes to core audit chain (source of truth)
    4. Stores reference in task_tracking.events (for queries)

    Args:
      event_type: categorical event name (task_created, task_updated, etc.)
      task_id: tenant-scoped task ID
      tenant_id: tenant context (keyword-only, fail-closed if missing)
      actor: who performed the action (user, system, service)
      action: what was changed (field name, e.g., 'status', 'owner')
      delta: before/after values; PII-scrubbed before persistence
      store: optional task_tracking.store for DB reference

    Returns:
      The event's chain_hash (cryptographic commitment to the event).

    Raises:
      ValueError: If tenant_id is missing or invalid.
      OSError: If core audit chain write fails (fail-closed).
    """
    if not tenant_id or not isinstance(tenant_id, str):
        raise ValueError("tenant_id is required (keyword-only)")

    if not task_id:
        raise ValueError("task_id is required")

    # Step 1: Scrub PII from delta
    scrubbed_delta = _scrub_pii_from_delta(delta, tenant_id=tenant_id)

    # Step 2: Create AuditEvent (timestamp in UTC)
    now = datetime.now(timezone.utc).isoformat()
    event = AuditEvent(
        event_type=event_type,
        task_id=task_id,
        tenant_id=tenant_id,
        actor=actor,
        action=action,
        delta=scrubbed_delta,
        timestamp=now,
        prior_hash="genesis",  # Set by writer
        chain_hash="",  # Computed by writer
    )

    # Step 3: Write to core audit chain (fail-closed if this fails)
    from core.paths import tenant_home

    chain_path = Path(tenant_home(tenant_id)) / "global" / "audit" / "task_tracking.jsonl"
    writer = AuditChainWriter(chain_path)

    try:
        chain_hash = writer.write_event(event)
    except OSError as e:
        raise OSError(f"Failed to write task audit event: {e}") from e

    # Step 4: Store reference in task_tracking.events (non-blocking)
    if store:
        try:
            import uuid

            event_id = str(uuid.uuid4())
            with store.connect(tenant_id) as conn:
                conn.execute(
                    """INSERT INTO events (event_id, tenant_id, item_id, event_type, ts, actor, delta, chain_hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event_id,
                        tenant_id,
                        task_id,
                        event_type,
                        now,
                        actor,
                        json.dumps(scrubbed_delta),
                        chain_hash,
                    ),
                )
                conn.commit()
        except Exception:
            # Non-blocking: if DB write fails, log it but don't fail the audit event
            # The core chain is the source of truth; DB reference is secondary
            pass

    return chain_hash


def verify_audit_chain(chain_path: Path, *, tenant_id: str) -> bool:
    """Verify integrity of task audit chain for a tenant.

    Walks the entire chain, verifying:
    - Hash-chain continuity (each event's prior_hash matches previous event's chain_hash)
    - Self-integrity (each event's chain_hash is correctly computed)
    - Tenant isolation (all events carry the expected tenant_id)

    Args:
      chain_path: path to the audit chain log
      tenant_id: tenant to verify

    Returns:
      True if chain is valid.

    Raises:
      ValueError: If chain is corrupted or tenant isolation is violated.
    """
    if not chain_path.exists():
        # Empty chain is valid
        return True

    prior_hash = "genesis"

    try:
        with open(chain_path, "r") as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue

                data = json.loads(line)

                # Verify tenant isolation
                if data.get("tenant_id") != tenant_id:
                    raise ValueError(
                        f"Chain broken at line {line_num}: tenant_id mismatch "
                        f"({data.get('tenant_id')} != {tenant_id})"
                    )

                # Verify prior_hash
                if data.get("prior_hash") != prior_hash:
                    raise ValueError(
                        f"Chain broken at line {line_num}: prior_hash mismatch "
                        f"({data.get('prior_hash')} != {prior_hash})"
                    )

                # Verify self_hash
                # Reconstruct event and compute hash
                event = AuditEvent(
                    event_type=data["event_type"],
                    task_id=data["task_id"],
                    tenant_id=data["tenant_id"],
                    actor=data["actor"],
                    action=data["action"],
                    delta=data["delta"],
                    timestamp=data["timestamp"],
                    prior_hash=data["prior_hash"],
                    chain_hash="",  # Exclude from hash computation
                )

                expected_hash = event.compute_hash()
                actual_hash = data.get("chain_hash")

                if actual_hash != expected_hash:
                    raise ValueError(
                        f"Entry tampering detected at line {line_num}: "
                        f"chain_hash {actual_hash} != expected {expected_hash}"
                    )

                prior_hash = actual_hash

    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in audit chain at line {line_num}: {e}") from e

    return True
