#!/usr/bin/env python3
"""Orchestrate batch migrations across tenants with canary staging.

Executes migrations in stages:
  Stage 1: 2 early adopter tenants (validation)
  Stage 2: 20% of tenants (scale testing)
  Stage 3: 100% rollout (full migration)

Collects metrics (success rate, duration, latency delta, audit delivery).
Decides continue vs. rollback per stage based on error rate threshold.

ADR-0039 (Workflow Builder) — Phase 6: Plugin Integration.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
_log = logging.getLogger(__name__)

# Import migration module
sys.path.insert(0, str(Path(__file__).parent))
from migrate_workflows_to_plugin import migrate_workflows_to_plugin, MigrationError


class MigrationMetrics:
    """Collect and report migration metrics per stage."""

    def __init__(self, stage: str):
        self.stage = stage
        self.start_time = datetime.now(timezone.utc)
        self.results: List[Dict[str, Any]] = []
        self.errors: List[str] = []

    def add_result(self, result: Dict[str, Any]) -> None:
        """Add successful migration result."""
        self.results.append(result)

    def add_error(self, tenant_id: str, error: str) -> None:
        """Add migration error."""
        self.errors.append(f"{tenant_id}: {error}")

    def to_dict(self) -> Dict[str, Any]:
        """Export metrics as dict."""
        total = len(self.results) + len(self.errors)
        success_rate = len(self.results) / total if total > 0 else 0.0

        return {
            "stage": self.stage,
            "total_tenants": total,
            "successful": len(self.results),
            "failed": len(self.errors),
            "success_rate": success_rate,
            "duration_seconds": (datetime.now(timezone.utc) - self.start_time).total_seconds(),
            "avg_duration_per_tenant_seconds": (
                sum(r.get("duration_seconds", 0) for r in self.results) / len(self.results)
                if self.results else 0.0
            ),
            "errors": self.errors,
        }

    def print_summary(self) -> None:
        """Print summary to log."""
        data = self.to_dict()
        _log.info(f"=== Stage {data['stage']} Summary ===")
        _log.info(f"  Total: {data['total_tenants']} tenants")
        _log.info(f"  Success: {data['successful']}")
        _log.info(f"  Failed: {data['failed']}")
        _log.info(f"  Success rate: {data['success_rate']:.1%}")
        _log.info(f"  Duration: {data['duration_seconds']:.1f}s")
        _log.info(f"  Avg per tenant: {data['avg_duration_per_tenant_seconds']:.2f}s")

        if data["errors"]:
            _log.warning("  Errors:")
            for error in data["errors"][:5]:  # Show first 5
                _log.warning(f"    {error}")
            if len(data["errors"]) > 5:
                _log.warning(f"    ... and {len(data['errors']) - 5} more")


class CohortManager:
    """Manage tenant cohorts for canary deployment."""

    def __init__(self, config_dir: Optional[str] = None):
        """Initialize with config directory."""
        if config_dir is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            config_dir = os.path.join(corvin_home, "tenants", "_default", "workflows", "cohorts")

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def list_tenants_in_cohort(self, cohort: str) -> List[str]:
        """List all tenants in a cohort.

        Args:
            cohort: Cohort name (early, staged, control).

        Returns:
            List of tenant IDs.
        """
        # Placeholder: In production, read from DualRunningRouter
        cohort_file = self.config_dir / f"{cohort}_tenants.json"

        if not cohort_file.exists():
            return []

        try:
            with open(cohort_file) as f:
                data = json.load(f)
            return data.get("tenants", [])
        except Exception as e:
            _log.warning(f"Failed to read cohort {cohort}: {e}")
            return []


async def migrate_stage(
    stage: str,
    tenant_ids: List[str],
    source_dir_fn,  # Function: tenant_id → source_dir
    target_dir_fn,  # Function: tenant_id → target_dir
    error_rate_threshold: float = 0.05,  # Fail if >5% error rate
) -> Tuple[MigrationMetrics, bool]:
    """Execute migration for a stage.

    Args:
        stage: Stage name (1, 2, 3).
        tenant_ids: Tenant IDs to migrate.
        source_dir_fn: Function to compute source directory.
        target_dir_fn: Function to compute target directory.
        error_rate_threshold: Fail if error rate exceeds this.

    Returns:
        (metrics, continue_flag) — continue_flag = True if should proceed to next stage.
    """
    metrics = MigrationMetrics(stage)

    _log.info(f"=== Stage {stage}: Migrating {len(tenant_ids)} tenants ===")

    # Migrate in parallel (with concurrency limit)
    tasks = [
        _migrate_single_tenant(
            tenant_id,
            source_dir_fn(tenant_id),
            target_dir_fn(tenant_id),
            metrics,
        )
        for tenant_id in tenant_ids
    ]

    # Run with concurrency limit (5 parallel)
    semaphore = asyncio.Semaphore(5)

    async def bounded_task(task):
        async with semaphore:
            await task

    await asyncio.gather(*[bounded_task(t) for t in tasks], return_exceptions=True)

    # Print summary
    metrics.print_summary()

    # Decide continue
    data = metrics.to_dict()
    error_rate = 1.0 - data["success_rate"]
    should_continue = error_rate <= error_rate_threshold

    if not should_continue:
        _log.error(f"Stage {stage} error rate {error_rate:.1%} exceeds threshold {error_rate_threshold:.1%}")
        _log.error("HALTING migration; manual review required")

    return metrics, should_continue


async def _migrate_single_tenant(
    tenant_id: str,
    source_dir,
    target_dir,
    metrics: MigrationMetrics,
) -> None:
    """Migrate single tenant, collecting metrics."""
    try:
        result = await migrate_workflows_to_plugin(
            tenant_id=tenant_id,
            source_dir=source_dir,
            target_dir=target_dir,
        )
        metrics.add_result(result)
        _log.info(f"✓ {tenant_id}: {result['workflows_count']} workflows")
    except MigrationError as e:
        metrics.add_error(tenant_id, str(e))
        _log.error(f"✗ {tenant_id}: {e}")
    except Exception as e:
        metrics.add_error(tenant_id, str(e))
        _log.error(f"✗ {tenant_id}: Unexpected error: {e}")


async def orchestrate_migrations(
    stages: List[Dict[str, Any]],
    source_dir_fn,
    target_dir_fn,
) -> List[MigrationMetrics]:
    """Execute canary deployment across stages.

    Args:
        stages: List of stage configs (name, tenant_ids, error_rate_threshold).
        source_dir_fn: Function to compute source directory per tenant.
        target_dir_fn: Function to compute target directory per tenant.

    Returns:
        List of MigrationMetrics per stage.
    """
    all_metrics: List[MigrationMetrics] = []

    for stage_config in stages:
        stage_name = stage_config["name"]
        tenant_ids = stage_config["tenant_ids"]
        error_rate_threshold = stage_config.get("error_rate_threshold", 0.05)

        metrics, should_continue = await migrate_stage(
            stage=stage_name,
            tenant_ids=tenant_ids,
            source_dir_fn=source_dir_fn,
            target_dir_fn=target_dir_fn,
            error_rate_threshold=error_rate_threshold,
        )

        all_metrics.append(metrics)

        if not should_continue:
            _log.error(f"Stage {stage_name} failed; halting deployment")
            break

    return all_metrics


async def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Orchestrate batch workflows migrations"
    )
    parser.add_argument(
        "--stage",
        choices=["1", "2", "3", "all"],
        default="1",
        help="Migration stage (default: 1)",
    )
    parser.add_argument(
        "--tenant-file",
        help="JSON file with tenant lists per stage",
        default=None,
    )

    args = parser.parse_args()

    # Default tenant lists for canary
    stages = {
        "1": {
            "name": "1_early",
            "tenant_ids": ["tenant_early_1", "tenant_early_2"],
            "error_rate_threshold": 0.0,  # Zero tolerance for early adopters
        },
        "2": {
            "name": "2_staged",
            "tenant_ids": [f"tenant_staged_{i}" for i in range(1, 21)],  # 20 tenants
            "error_rate_threshold": 0.05,  # Max 5% error rate
        },
        "3": {
            "name": "3_full",
            "tenant_ids": [f"tenant_{i}" for i in range(1, 101)],  # 100 tenants (simulated)
            "error_rate_threshold": 0.01,  # Max 1% error rate
        },
    }

    # Load custom tenant file if provided
    if args.tenant_file:
        try:
            with open(args.tenant_file) as f:
                stages = json.load(f)
        except Exception as e:
            print(f"Failed to load tenant file: {e}")
            sys.exit(1)

    # Select stages to run
    if args.stage == "all":
        stages_to_run = list(stages.values())
    else:
        stage_key = args.stage
        if stage_key not in stages:
            print(f"Unknown stage: {stage_key}")
            sys.exit(1)
        stages_to_run = [stages[stage_key]]

    # Define directory resolution functions
    def source_dir_fn(tenant_id: str):
        corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        return Path(corvin_home) / "tenants" / tenant_id / "forge" / "workflows"

    def target_dir_fn(tenant_id: str):
        corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        return Path(corvin_home) / "tenants" / tenant_id / "workflows_plugin"

    # Execute
    try:
        all_metrics = asyncio.run(orchestrate_migrations(
            stages=stages_to_run,
            source_dir_fn=source_dir_fn,
            target_dir_fn=target_dir_fn,
        ))

        # Print final report
        print("\n=== Migration Report ===\n")
        for metrics in all_metrics:
            print(json.dumps(metrics.to_dict(), indent=2))

        sys.exit(0)

    except Exception as e:
        _log.error(f"Orchestration failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
