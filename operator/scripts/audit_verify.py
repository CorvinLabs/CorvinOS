#!/usr/bin/env python3
"""Daily Audit Chain Verification Script (Phase B, ADR-0541).

Verifies audit chain integrity for all completed tasks.
Detects tampering, hash mismatches, and tenant isolation violations.
Outputs results to JSON log file and exits with status code.

Usage:
    python3 audit_verify.py --corvin-home ~/.corvin --tenant-id _default --min-age-days 30

Exit codes:
    0 - All verifications passed
    1 - One or more verifications failed
    2 - Script error
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

# Add CorvinOS to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.infinite_session import (
    EventStore,
    CryptoBinding,
    AuditVerifier,
)


def setup_logging(log_file: str) -> logging.Logger:
    """Setup logging to file and stdout."""
    logger = logging.getLogger("audit_verify")
    logger.setLevel(logging.INFO)

    # File handler
    fh = logging.FileHandler(log_file, mode="a")
    fh.setLevel(logging.INFO)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(ch)

    return logger


def main():
    """Run audit verification."""
    parser = argparse.ArgumentParser(
        description="Daily Audit Chain Verification (ADR-0541)"
    )
    parser.add_argument(
        "--corvin-home",
        type=str,
        default=str(Path.home() / ".corvin"),
        help="Corvin home directory",
    )
    parser.add_argument(
        "--tenant-id",
        type=str,
        default="_default",
        help="Tenant identifier",
    )
    parser.add_argument(
        "--min-age-days",
        type=int,
        default=30,
        help="Minimum age of task to verify (days)",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        help="Log file path",
    )

    args = parser.parse_args()

    # Setup logging
    log_file = args.log_file or str(
        Path(args.corvin_home) / "verification_logs" / f"audit-verify-{datetime.utcnow().strftime('%Y-%m-%d')}.log"
    )
    logger = setup_logging(log_file)

    logger.info(f"Starting audit verification (tenant={args.tenant_id})")

    try:
        # Initialize components
        event_store = EventStore(corvin_home=args.corvin_home)
        crypto_binding = CryptoBinding(corvin_home=args.corvin_home)
        verifier = AuditVerifier(
            event_store=event_store,
            crypto_binding=crypto_binding,
            corvin_home=args.corvin_home,
        )

        # Find all tasks in snapshots directory
        snapshots_dir = Path(args.corvin_home) / "tenants" / args.tenant_id / "snapshots"

        if not snapshots_dir.exists():
            logger.info("No snapshots found (snapshots directory does not exist)")
            print("✅ Verification complete (no snapshots)")
            return 0

        # Enumerate tasks
        tasks = []
        for task_dir in snapshots_dir.iterdir():
            if task_dir.is_dir():
                tasks.append(task_dir.name)

        if not tasks:
            logger.info("No tasks found")
            print("✅ Verification complete (no tasks)")
            return 0

        logger.info(f"Found {len(tasks)} task(s)")

        # Verify each task
        results: List[Dict[str, Any]] = []
        failed_count = 0

        for task_id in sorted(tasks):
            logger.info(f"Verifying task: {task_id}")

            result, error = verifier.verify_task_chain(
                tenant_id=args.tenant_id,
                task_id=task_id,
            )

            if error:
                logger.error(f"Verification error for task {task_id}: {error}")
                failed_count += 1
                results.append({
                    "task_id": task_id,
                    "status": "error",
                    "error": error,
                })
            elif result:
                if result.status.value == "pass":
                    logger.info(f"✅ Task {task_id} verification passed (events={result.event_count}, duration={result.verification_duration_ms}ms)")
                    results.append({
                        "task_id": task_id,
                        "status": result.status.value,
                        "session_count": result.session_count,
                        "event_count": result.event_count,
                        "duration_ms": result.verification_duration_ms,
                    })
                else:
                    logger.warning(f"❌ Task {task_id} verification failed: {result.status.value}")
                    failed_count += 1
                    results.append({
                        "task_id": task_id,
                        "status": result.status.value,
                        "errors": result.errors,
                    })
            else:
                logger.error(f"No result for task {task_id}")
                failed_count += 1

        # Summary
        logger.info(f"Verification complete: {len(results)} tasks, {failed_count} failures")

        # Write summary to JSON
        summary = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "tenant_id": args.tenant_id,
            "total_tasks": len(results),
            "failed_tasks": failed_count,
            "results": results,
        }

        summary_file = Path(args.corvin_home) / "verification_logs" / f"summary-{datetime.utcnow().strftime('%Y-%m-%dT%H-%M-%SZ')}.json"
        summary_file.write_text(json.dumps(summary, indent=2))
        logger.info(f"Summary written to {summary_file}")

        # Exit with appropriate code
        if failed_count == 0:
            print("✅ Verification complete (all tasks passed)")
            return 0
        else:
            print(f"❌ Verification complete ({failed_count} failures)")
            return 1

    except Exception as e:
        logger.exception(f"Script error: {str(e)}")
        print(f"❌ Script error: {str(e)}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
