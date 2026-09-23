#!/usr/bin/env python3
"""
Stream 2 Staging Load Generator
================================

Generates 100K+ realistic audit events for Week 2–3 soak test.

Usage:
  python3 scripts/staging_load_generator.py --duration=7d --target=staging --output=.staging/stream2/audit/

Timeline: 2026-09-27 to 2026-10-03 (7 days continuous)
Target: 14K events/day (100K+ total over 7 days)

ADR-2047: Security Orchestrator Skill
Phase 10, Week 2–3: Staging Soak Test
"""

import argparse
import asyncio
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, asdict, field
from enum import Enum
from pathlib import Path
from typing import List, Dict, Any, Optional
from uuid import uuid4


class EventType(Enum):
    """Stream 2 threat detection event types."""
    AUTH_ATTEMPT = "auth_attempt"
    AUTH_FAILURE = "auth_failure"
    PRIVILEGE_CHANGE = "privilege_change"
    DATA_EXPORT = "data_export"
    TENANT_ACCESS = "tenant_access"
    POLICY_CHANGE = "policy_change"
    THREAT_DETECTED = "threat_detected"
    THREAT_CLEARED = "threat_cleared"
    AUDIT_TRAIL_EVENT = "audit_trail_event"


class ThreatType(Enum):
    """Threat patterns detected by Stream 2."""
    BRUTE_FORCE = "brute_force"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DATA_EXFILTRATION = "data_exfiltration"
    CROSS_TENANT_ACCESS = "cross_tenant_access"
    UNUSUAL_BEHAVIOR = "unusual_behavior"


@dataclass
class AuditEvent:
    """Represents a single audit trail event."""
    tenant_id: str
    timestamp: str
    event_type: str
    user_id: str
    action: str
    resource: str
    result: str  # "success" or "failure"
    details: Dict[str, Any] = field(default_factory=dict)
    threat_type: Optional[str] = None
    severity: Optional[str] = None  # "low", "medium", "high", "critical"
    hash: Optional[str] = None
    prev_hash: Optional[str] = None

    def to_json(self) -> str:
        """Convert to JSON line."""
        return json.dumps(asdict(self))


class LoadGenerator:
    """Generates realistic audit events for Stream 2 soak test."""

    # User pools
    USERS = [f"user_{i:04d}" for i in range(1, 51)]  # 50 users
    TENANTS = ["_default", "tenant_a", "tenant_b", "tenant_c"]
    ROLES = ["admin", "analyst", "operator", "viewer"]
    RESOURCES = [f"dataset_{i}" for i in range(1, 20)] + ["policy_config", "audit_log", "encryption_key"]

    def __init__(self, duration_days: int = 7, daily_rate: int = 14000, output_dir: str = ".staging/stream2/audit"):
        self.duration_days = duration_days
        self.daily_rate = daily_rate
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Calculate timing
        self.total_events = daily_rate * duration_days
        self.start_time = datetime.now(timezone.utc) - timedelta(days=duration_days)
        self.event_counter = 0
        self.threat_counter = 0

    async def generate_events(self) -> int:
        """Generate all events and write to audit file."""
        audit_file = self.output_dir / "audit_soak_test.jsonl"

        print(f"🔄 Starting load generation...")
        print(f"   Duration: {self.duration_days} days")
        print(f"   Total events: {self.total_events:,}")
        print(f"   Daily rate: {self.daily_rate:,} events/day")
        print(f"   Output: {audit_file}")
        print()

        start_time = datetime.now()
        prev_hash = "0" * 64  # Initial hash

        with open(audit_file, "w") as f:
            for i in range(self.total_events):
                # Distribute events evenly across the 7-day window
                event_timestamp = self.start_time + timedelta(
                    seconds=(self.duration_days * 86400) * (i / self.total_events)
                )

                # Generate event
                event = self._generate_random_event(event_timestamp, prev_hash)
                prev_hash = event.hash

                # Write to file
                f.write(event.to_json() + "\n")
                self.event_counter += 1

                # Progress reporting every 10K events
                if (i + 1) % 10000 == 0:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    rate = (i + 1) / elapsed
                    remaining = (self.total_events - i - 1) / rate if rate > 0 else 0
                    print(f"   [{i + 1:,}/{self.total_events:,}] "
                          f"{rate:.0f} events/sec | "
                          f"~{remaining/60:.1f} min remaining")

        elapsed = (datetime.now() - start_time).total_seconds()
        print()
        print(f"✅ Load generation complete!")
        print(f"   Total events written: {self.event_counter:,}")
        print(f"   Total threats simulated: {self.threat_counter:,}")
        print(f"   Time elapsed: {elapsed:.1f}s")
        print(f"   Rate: {self.event_counter/elapsed:.0f} events/sec")
        print()

        return self.event_counter

    def _generate_random_event(self, timestamp: datetime, prev_hash: str) -> AuditEvent:
        """Generate a single random event."""
        tenant_id = random.choice(self.TENANTS)
        user_id = random.choice(self.USERS)
        event_type = random.choice(list(EventType)).value

        # Create realistic event based on type
        if event_type == EventType.AUTH_ATTEMPT.value:
            event = self._create_auth_event(tenant_id, user_id, timestamp, prev_hash)
        elif event_type == EventType.PRIVILEGE_CHANGE.value:
            event = self._create_privilege_event(tenant_id, user_id, timestamp, prev_hash)
        elif event_type == EventType.DATA_EXPORT.value:
            event = self._create_export_event(tenant_id, user_id, timestamp, prev_hash)
        elif event_type == EventType.THREAT_DETECTED.value:
            event = self._create_threat_event(tenant_id, user_id, timestamp, prev_hash)
            self.threat_counter += 1
        else:
            event = self._create_generic_event(tenant_id, user_id, event_type, timestamp, prev_hash)

        return event

    def _create_auth_event(self, tenant_id: str, user_id: str, timestamp: datetime, prev_hash: str) -> AuditEvent:
        """Create authentication event (90% success, 10% failure for brute force simulation)."""
        success = random.random() > 0.1
        return AuditEvent(
            tenant_id=tenant_id,
            timestamp=timestamp.isoformat() + "Z",
            event_type=EventType.AUTH_ATTEMPT.value if success else EventType.AUTH_FAILURE.value,
            user_id=user_id,
            action="login",
            resource="auth_service",
            result="success" if success else "failure",
            details={
                "method": random.choice(["password", "mfa", "oauth", "saml"]),
                "source_ip": f"192.168.{random.randint(1, 255)}.{random.randint(1, 255)}",
                "session_id": str(uuid4())[:12]
            },
            hash=self._compute_hash(prev_hash, f"{tenant_id}_{user_id}_{timestamp}"),
            prev_hash=prev_hash
        )

    def _create_privilege_event(self, tenant_id: str, user_id: str, timestamp: datetime, prev_hash: str) -> AuditEvent:
        """Create privilege change event (role elevation)."""
        old_role = random.choice(self.ROLES)
        new_role = random.choice([r for r in self.ROLES if r != old_role])
        return AuditEvent(
            tenant_id=tenant_id,
            timestamp=timestamp.isoformat() + "Z",
            event_type=EventType.PRIVILEGE_CHANGE.value,
            user_id=user_id,
            action="role_change",
            resource="iam",
            result="success",
            details={
                "old_role": old_role,
                "new_role": new_role,
                "approver": random.choice(self.USERS)
            },
            hash=self._compute_hash(prev_hash, f"{tenant_id}_{user_id}_privilege_{timestamp}"),
            prev_hash=prev_hash
        )

    def _create_export_event(self, tenant_id: str, user_id: str, timestamp: datetime, prev_hash: str) -> AuditEvent:
        """Create data export event."""
        size_mb = random.randint(10, 1000)
        return AuditEvent(
            tenant_id=tenant_id,
            timestamp=timestamp.isoformat() + "Z",
            event_type=EventType.DATA_EXPORT.value,
            user_id=user_id,
            action="export",
            resource=random.choice(self.RESOURCES),
            result="success",
            details={
                "size_mb": size_mb,
                "format": random.choice(["csv", "json", "parquet", "sql"]),
                "destination": random.choice(["local_download", "s3_bucket", "database", "api"])
            },
            hash=self._compute_hash(prev_hash, f"{tenant_id}_{user_id}_export_{size_mb}_{timestamp}"),
            prev_hash=prev_hash
        )

    def _create_threat_event(self, tenant_id: str, user_id: str, timestamp: datetime, prev_hash: str) -> AuditEvent:
        """Create threat detected event."""
        threat_type = random.choice(list(ThreatType)).value
        severity_map = {
            "brute_force": "high",
            "privilege_escalation": "critical",
            "data_exfiltration": "critical",
            "cross_tenant_access": "critical",
            "unusual_behavior": "medium"
        }
        severity = severity_map.get(threat_type, "high")

        return AuditEvent(
            tenant_id=tenant_id,
            timestamp=timestamp.isoformat() + "Z",
            event_type=EventType.THREAT_DETECTED.value,
            user_id=user_id,
            action="threat_detection",
            resource="security_orchestrator",
            result="success",
            threat_type=threat_type,
            severity=severity,
            details={
                "threat_id": str(uuid4())[:12],
                "confidence": round(random.uniform(0.6, 0.99), 2),
                "evidence": f"{random.randint(3, 10)} suspicious events in 5 min window"
            },
            hash=self._compute_hash(prev_hash, f"{tenant_id}_{threat_type}_{timestamp}"),
            prev_hash=prev_hash
        )

    def _create_generic_event(self, tenant_id: str, user_id: str, event_type: str, timestamp: datetime, prev_hash: str) -> AuditEvent:
        """Create generic audit event."""
        return AuditEvent(
            tenant_id=tenant_id,
            timestamp=timestamp.isoformat() + "Z",
            event_type=event_type,
            user_id=user_id,
            action=random.choice(["read", "write", "delete", "update", "list"]),
            resource=random.choice(self.RESOURCES),
            result=random.choice(["success", "success", "failure"]),  # Mostly success
            details={
                "session_id": str(uuid4())[:12],
                "source_ip": f"10.0.{random.randint(1, 255)}.{random.randint(1, 255)}"
            },
            hash=self._compute_hash(prev_hash, f"{tenant_id}_{user_id}_{event_type}_{timestamp}"),
            prev_hash=prev_hash
        )

    @staticmethod
    def _compute_hash(prev_hash: str, data: str) -> str:
        """Compute SHA256 hash chained to previous hash."""
        import hashlib
        combined = f"{prev_hash}{data}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]


class SoakTestMetricsCollector:
    """Collects and reports soak test metrics."""

    def __init__(self, output_dir: str = ".staging/stream2"):
        self.output_dir = Path(output_dir)
        self.metrics_file = self.output_dir / "soak_test_metrics.json"

    async def collect_baseline_metrics(self, event_count: int) -> Dict[str, Any]:
        """Collect baseline metrics after load generation."""
        metrics = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "phase": "pre_soak_test",
            "total_events_generated": event_count,
            "daily_rate": 14000,
            "duration_days": 7,
            "expected_total": 100000,
            "audit_file": str(self.output_dir / "audit_soak_test.jsonl"),
            "memory_usage_mb": self._get_memory_usage(),
        }

        # Write baseline metrics
        with open(self.metrics_file, "w") as f:
            json.dump(metrics, f, indent=2)

        return metrics

    @staticmethod
    def _get_memory_usage() -> float:
        """Get current process memory usage in MB."""
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        except ImportError:
            return 0.0


async def main():
    parser = argparse.ArgumentParser(
        description="Generate 100K+ realistic audit events for Stream 2 soak test",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate 7-day load (100K events)
  python3 scripts/staging_load_generator.py --duration=7d --target=staging

  # Generate 3-day test run (42K events)
  python3 scripts/staging_load_generator.py --duration=3d --rate=14000

  # Write to custom directory
  python3 scripts/staging_load_generator.py --duration=7d --output=/path/to/audit
        """
    )

    parser.add_argument("--duration", default="7d",
                        help="Duration: 1d, 3d, 7d, 14d (default: 7d)")
    parser.add_argument("--rate", type=int, default=14000,
                        help="Events per day (default: 14000)")
    parser.add_argument("--target", default="staging",
                        help="Target environment: staging, production (default: staging)")
    parser.add_argument("--output", default=".staging/stream2/audit",
                        help="Output directory (default: .staging/stream2/audit)")

    args = parser.parse_args()

    # Parse duration
    duration_map = {"1d": 1, "3d": 3, "7d": 7, "14d": 14}
    duration_days = duration_map.get(args.duration, 7)

    print("=" * 60)
    print("Stream 2 Staging Load Generator")
    print("=" * 60)
    print(f"Duration: {args.duration} ({duration_days} days)")
    print(f"Daily rate: {args.rate:,} events/day")
    print(f"Total events: {args.rate * duration_days:,}")
    print(f"Target: {args.target}")
    print(f"Output: {args.output}")
    print("=" * 60)
    print()

    # Generate load
    generator = LoadGenerator(
        duration_days=duration_days,
        daily_rate=args.rate,
        output_dir=args.output
    )

    event_count = await generator.generate_events()

    # Collect metrics
    metrics_collector = SoakTestMetricsCollector(output_dir=args.output.rsplit("/audit", 1)[0])
    metrics = await metrics_collector.collect_baseline_metrics(event_count)

    print("📊 Baseline Metrics:")
    print(f"   Total events: {metrics['total_events_generated']:,}")
    print(f"   Memory usage: {metrics['memory_usage_mb']:.1f} MB")
    print(f"   Audit file: {metrics['audit_file']}")
    print()
    print("✅ Load generation and metrics collection complete!")
    print()
    print("📝 Next Steps:")
    print("   1. Verify audit file: wc -l .staging/stream2/audit/audit_soak_test.jsonl")
    print("   2. Start soak test: pytest tests/ops/test_staging_soak_smoke.py -v")
    print("   3. Monitor threats: python3 scripts/stream2_threat_monitor.py")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
