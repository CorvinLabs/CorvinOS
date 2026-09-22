"""Stream 2: A/B Test Framework (Story 11 — Two-variant testing).

Runs A vs B variants, measures improvement, decides winner.
Manual trigger (not continuous).
"""

import logging
import json
import random
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

logger = logging.getLogger(__name__)


class TestStatus(str, Enum):
    """Test status."""
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class ABTest:
    """Immutable A/B test."""
    test_id: str
    skill_id: str
    variant_a_config: dict  # Current config
    variant_b_config: dict  # New config
    sample_size_per_variant: int = 100
    status: TestStatus = TestStatus.RUNNING
    winner: Optional[str] = None  # "A" or "B"


class ABTestFramework:
    """Simple A/B testing framework (manual trigger)."""

    def __init__(self, test_home: Path, tenant_id: str):
        """Initialize framework.

        Args:
            test_home: Root directory for test storage
            tenant_id: Tenant scope
        """
        if not tenant_id:
            raise ValueError("tenant_id required")

        self.test_home = Path(test_home)
        self.tenant_id = tenant_id
        self.tests_dir = self.test_home / tenant_id / "ab_tests"
        self.results_dir = self.test_home / tenant_id / "test_results"

        # Create directories
        for d in [self.tests_dir, self.results_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def create_test(
        self,
        skill_id: str,
        variant_a_config: dict,
        variant_b_config: dict,
        sample_size: int = 100,
    ) -> str:
        """Create and start an A/B test.

        Args:
            skill_id: Skill under test
            variant_a_config: Current (control) config
            variant_b_config: New (experimental) config
            sample_size: Samples per variant

        Returns:
            test_id
        """
        test_id = str(uuid4())

        test_data = {
            "test_id": test_id,
            "skill_id": skill_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "variant_a_config": variant_a_config,
            "variant_b_config": variant_b_config,
            "sample_size": sample_size,
            "status": "running",
            "results": {
                "a_successes": 0,
                "a_failures": 0,
                "b_successes": 0,
                "b_failures": 0,
            },
        }

        test_file = self.tests_dir / f"{test_id}.json"
        with open(test_file, "w") as f:
            json.dump(test_data, f, indent=2)

        logger.info(f"ab_test_created: {test_id}: {skill_id}")
        return test_id

    def record_result(
        self,
        test_id: str,
        variant: str,  # "A" or "B"
        success: bool,
    ) -> None:
        """Record a test result.

        Args:
            test_id: Test ID
            variant: "A" or "B"
            success: True if variant succeeded
        """
        test_file = self.tests_dir / f"{test_id}.json"

        try:
            with open(test_file, "r") as f:
                test_data = json.load(f)

            # Record result
            if variant.upper() == "A":
                if success:
                    test_data["results"]["a_successes"] += 1
                else:
                    test_data["results"]["a_failures"] += 1
            elif variant.upper() == "B":
                if success:
                    test_data["results"]["b_successes"] += 1
                else:
                    test_data["results"]["b_failures"] += 1
            else:
                raise ValueError(f"Invalid variant: {variant}")

            # Check if test is complete
            total_a = test_data["results"]["a_successes"] + test_data["results"]["a_failures"]
            total_b = test_data["results"]["b_successes"] + test_data["results"]["b_failures"]
            sample_size = test_data["sample_size"]

            if total_a >= sample_size and total_b >= sample_size:
                test_data["status"] = "completed"
                test_data["completed_at"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

            # Write back
            with open(test_file, "w") as f:
                json.dump(test_data, f, indent=2)

        except Exception as e:
            logger.error(f"record_result_error: {test_id}: {e}")

    def get_winner(self, test_id: str) -> Optional[str]:
        """Determine test winner (once completed).

        Returns:
            "A" if A wins, "B" if B wins, None if test incomplete
        """
        test_file = self.tests_dir / f"{test_id}.json"

        try:
            with open(test_file, "r") as f:
                test_data = json.load(f)

            if test_data["status"] != "completed":
                return None

            results = test_data["results"]
            a_rate = results["a_successes"] / (results["a_successes"] + results["a_failures"] + 1)
            b_rate = results["b_successes"] / (results["b_successes"] + results["b_failures"] + 1)

            # B wins if improvement > 5%
            if b_rate > a_rate * 1.05:
                return "B"
            else:
                return "A"  # A wins (or tie)

        except Exception as e:
            logger.error(f"get_winner_error: {test_id}: {e}")
            return None

    def get_test_status(self, test_id: str) -> Optional[dict]:
        """Get test status and current results."""
        test_file = self.tests_dir / f"{test_id}.json"

        try:
            with open(test_file, "r") as f:
                test_data = json.load(f)

            results = test_data["results"]
            total_a = results["a_successes"] + results["a_failures"]
            total_b = results["b_successes"] + results["b_failures"]

            return {
                "test_id": test_id,
                "skill_id": test_data["skill_id"],
                "status": test_data["status"],
                "progress_a": f"{total_a}/{test_data['sample_size']}",
                "progress_b": f"{total_b}/{test_data['sample_size']}",
                "rate_a": total_a / (total_a + 1) if total_a > 0 else 0,
                "rate_b": total_b / (total_b + 1) if total_b > 0 else 0,
                "winner": self.get_winner(test_id),
            }

        except Exception as e:
            logger.error(f"get_status_error: {test_id}: {e}")
            return None

    def list_tests(self, skill_id: Optional[str] = None) -> list:
        """List all tests (optionally filtered by skill_id)."""
        tests = []
        for test_file in sorted(self.tests_dir.glob("*.json")):
            try:
                with open(test_file, "r") as f:
                    test_data = json.load(f)
                    if skill_id is None or test_data["skill_id"] == skill_id:
                        tests.append(self.get_test_status(test_data["test_id"]))
            except Exception as e:
                logger.error(f"list_tests_error: {test_file.name}: {e}")

        return [t for t in tests if t is not None]


__all__ = ["ABTestFramework", "ABTest", "TestStatus"]
