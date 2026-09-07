"""Phase B: Audit Chain Verification (ADR-0541).

Verifies, for one task of one tenant:
1. the snapshot chain (every content hash re-computed, every prev link,
   contiguous ``seq``) via :meth:`EventStore.verify_snapshot_chain`;
2. tenant isolation — every stored snapshot/bridge carries the bound tenant;
3. every bridge signature (whole-event HMAC) and that each bridge points at a
   stored snapshot whose hash matches.

Results are persisted at ``<tenant_root>/verification/verifications_<day>.jsonl``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from core.infinite_session.crypto_binding import CryptoBinding
from core.infinite_session.event_store import EventStore
from core.infinite_session.paths import validate_id
from core.infinite_session.session_bridger import SessionBridgeEvent, SessionBridger


class VerificationStatus(str, Enum):
    PASS = "pass"
    FAIL_CHAIN_BROKEN = "fail_chain_broken"
    FAIL_TENANT_ISOLATION = "fail_tenant_isolation"
    FAIL_SIGNATURE_MISMATCH = "fail_signature_mismatch"
    FAIL_BRIDGE_CONTENT = "fail_bridge_content"
    ERROR = "error"


@dataclass(frozen=True)
class VerificationResult:
    task_id: str
    tenant_id: str
    status: VerificationStatus
    session_count: int
    event_count: int
    errors: List[str]
    verified_at: str
    verification_duration_ms: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditVerifier:
    """Chain + bridge verification for the bound tenant (ADR-0541)."""

    def __init__(
        self,
        event_store: EventStore,
        crypto_binding: CryptoBinding,
        bridger: Optional[SessionBridger] = None,
    ):
        self.event_store = event_store
        self.crypto_binding = crypto_binding
        self.bridger = bridger or SessionBridger(event_store, crypto_binding)
        self.tenant_id = event_store.tenant_id
        self.verification_log_dir = event_store.root_dir.parent / "verification"
        self.verification_log_dir.mkdir(parents=True, exist_ok=True)

    def verify_task_chain(
        self,
        tenant_id: str,
        task_id: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[VerificationResult], str]:
        start = datetime.now(timezone.utc)
        errors: List[str] = []
        status = VerificationStatus.PASS

        if tenant_id != self.tenant_id:
            if audit_callback:
                audit_callback(
                    event_type="audit_verification_failed",
                    tenant_id=tenant_id or "<invalid>",
                    reason="tenant mismatch",
                )
            return None, "tenant_id does not match the bound tenant (fail-closed)"
        try:
            validate_id(task_id, "task_id")
        except ValueError as exc:
            return None, str(exc)

        metadata_list, error = self.event_store.list_snapshots(tenant_id, task_id)
        if error:
            errors.append(f"Failed to load snapshots: {error}")
            status = VerificationStatus.ERROR
        else:
            ok, chain_error = self.event_store.verify_snapshot_chain(tenant_id, task_id)
            if not ok:
                errors.append(f"Snapshot chain: {chain_error}")
                status = VerificationStatus.FAIL_CHAIN_BROKEN
            for meta in metadata_list:
                if meta.tenant_id != tenant_id:
                    errors.append(
                        f"Tenant isolation violation: snapshot {meta.snapshot_id} "
                        f"has tenant_id={meta.tenant_id}"
                    )
                    status = VerificationStatus.FAIL_TENANT_ISOLATION

        bridges, bridge_error = self.bridger.list_bridges(tenant_id, task_id)
        if bridge_error:
            errors.append(f"Failed to load bridges: {bridge_error}")
            status = VerificationStatus.FAIL_BRIDGE_CONTENT
        known_hashes = {m.snapshot_id: m.content_hash for m in metadata_list} if not error else {}
        for bridge in bridges:
            ok, sig_error = self.verify_bridge_signature(tenant_id, bridge, audit_callback)
            if not ok:
                errors.append(f"Bridge {bridge.bridge_id}: {sig_error}")
                if status == VerificationStatus.PASS:
                    status = VerificationStatus.FAIL_SIGNATURE_MISMATCH
                continue
            if known_hashes.get(bridge.snapshot_id) != bridge.snapshot_hash:
                errors.append(f"Bridge {bridge.bridge_id}: referenced snapshot missing or hash mismatch")
                if status == VerificationStatus.PASS:
                    status = VerificationStatus.FAIL_BRIDGE_CONTENT

        if audit_callback:
            if errors:
                audit_callback(
                    event_type="audit_chain_verification_failed",
                    tenant_id=tenant_id, task_id=task_id,
                    error_count=len(errors), timestamp=_now(),
                )
            else:
                audit_callback(
                    event_type="audit_chain_verified",
                    tenant_id=tenant_id, task_id=task_id,
                    chain_height=len(metadata_list), verification_result="PASS",
                    timestamp=_now(),
                )

        result = VerificationResult(
            task_id=task_id,
            tenant_id=tenant_id,
            status=status,
            session_count=len(bridges) + 1 if metadata_list else 0,
            event_count=len(metadata_list),
            errors=errors,
            verified_at=_now(),
            verification_duration_ms=int(
                (datetime.now(timezone.utc) - start).total_seconds() * 1000
            ),
        )
        self._persist_verification_result(result)
        return result, ""

    def verify_bridge_signature(
        self,
        tenant_id: str,
        bridge: SessionBridgeEvent,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        if bridge.tenant_id != tenant_id:
            if audit_callback:
                audit_callback(
                    event_type="bridge_verification_failed",
                    tenant_id=tenant_id, reason="Bridge tenant_id mismatch",
                )
            return False, "Bridge tenant_id mismatch"
        return self.bridger.verify_bridge(tenant_id, bridge, audit_callback)

    def verify_all_tasks(
        self,
        tenant_id: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[List[VerificationResult], str]:
        """Verify every task the bound store knows about (daily cron entry point)."""
        if tenant_id != self.tenant_id:
            return [], "tenant_id does not match the bound tenant (fail-closed)"
        results: List[VerificationResult] = []
        for task_id in self.event_store.list_tasks():
            result, error = self.verify_task_chain(tenant_id, task_id, audit_callback)
            if result is not None:
                results.append(result)
        return results, ""

    def _persist_verification_result(self, result: VerificationResult) -> bool:
        try:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            log_file = self.verification_log_dir / f"verifications_{day}.jsonl"
            record = {
                "task_id": result.task_id,
                "tenant_id": result.tenant_id,
                "status": result.status.value,
                "session_count": result.session_count,
                "event_count": result.event_count,
                "errors": result.errors,
                "verified_at": result.verified_at,
                "verification_duration_ms": result.verification_duration_ms,
            }
            with open(log_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
            return True
        except OSError:
            return False

    def get_verification_status(
        self, tenant_id: str, task_id: str
    ) -> Tuple[Optional[VerificationResult], str]:
        if tenant_id != self.tenant_id:
            return None, "tenant_id does not match the bound tenant (fail-closed)"
        for log_file in sorted(self.verification_log_dir.glob("verifications_*.jsonl"), reverse=True):
            with open(log_file, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            for line in reversed(lines):
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if data.get("task_id") == task_id:
                    return VerificationResult(
                        task_id=data["task_id"],
                        tenant_id=data["tenant_id"],
                        status=VerificationStatus(data["status"]),
                        session_count=data["session_count"],
                        event_count=data["event_count"],
                        errors=data["errors"],
                        verified_at=data["verified_at"],
                        verification_duration_ms=data["verification_duration_ms"],
                    ), ""
        return None, "No verification records found for task"
