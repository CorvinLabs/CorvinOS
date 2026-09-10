"""Savings Store — monthly rollup storage (Phase 4, ADR-0668).

Stores:
1. Monthly savings per user
2. Daily rotation (one file per month)
3. All-time savings queries
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class MonthlySavingsReport:
    """Monthly savings report for a user."""

    def __init__(
        self,
        user_id: str,
        year: int,
        month: int,
        total_baseline: int = 0,
        total_actual: int = 0,
        total_savings: int = 0,
        event_count: int = 0,
    ):
        self.user_id = user_id
        self.year = year
        self.month = month
        self.total_baseline = total_baseline
        self.total_actual = total_actual
        self.total_savings = total_savings
        self.event_count = event_count

    @property
    def credit_usd(self) -> float:
        """Credit in USD (assumes $0.01 per token saved)."""
        return self.total_savings * 0.01

    def to_dict(self) -> dict:
        return {
            "user_id": self.user_id,
            "year": self.year,
            "month": self.month,
            "total_baseline": self.total_baseline,
            "total_actual": self.total_actual,
            "total_savings": self.total_savings,
            "event_count": self.event_count,
            "credit_usd": self.credit_usd,
        }


class SavingsStore:
    """Persistent storage for token savings.

    Responsibilities:
    - Accumulate savings to monthly bucket
    - Monthly rollup queries
    - All-time savings aggregation
    - Daily file rotation
    """

    def __init__(self, corvin_home: str | Path):
        """Initialize savings store.

        Args:
            corvin_home: Path to ~/.corvin or equivalent
        """
        self.corvin_home = Path(corvin_home)
        self.savings_dir = (
            self.corvin_home / "tenants" / "_default" / "global" / "savings"
        )
        self.savings_dir.mkdir(parents=True, exist_ok=True)

    def accumulate(
        self,
        user_id: str,
        tokens: int,
        event_id: str,
        tenant_id: str = "_default"
    ) -> None:
        """Accumulate savings to monthly bucket.

        Args:
            user_id: User ID
            tokens: Number of tokens saved
            event_id: Event ID for deduplication
            tenant_id: Tenant scope
        """
        now = datetime.now(timezone.utc)
        filename = self._get_month_filename(now)
        filepath = self.savings_dir / filename

        # Load existing data
        data = self._load_file(filepath)

        # Initialize user bucket if needed
        if user_id not in data:
            data[user_id] = {
                "total_tokens": 0,
                "event_count": 0,
                "event_ids": [],
            }

        # Add to user bucket (avoid duplicates)
        if event_id not in data[user_id]["event_ids"]:
            data[user_id]["total_tokens"] += tokens
            data[user_id]["event_count"] += 1
            data[user_id]["event_ids"].append(event_id)

        # Save back
        self._save_file(filepath, data)

    def get_monthly_rollup(
        self,
        user_id: str,
        year: int,
        month: int,
    ) -> MonthlySavingsReport:
        """Get monthly savings report for user.

        Args:
            user_id: User ID
            year: Year (2026, etc.)
            month: Month (1-12)

        Returns:
            MonthlySavingsReport object
        """
        filename = self._get_month_filename_explicit(year, month)
        filepath = self.savings_dir / filename

        data = self._load_file(filepath)

        if user_id not in data:
            return MonthlySavingsReport(user_id, year, month)

        user_data = data[user_id]
        return MonthlySavingsReport(
            user_id=user_id,
            year=year,
            month=month,
            total_baseline=0,  # TODO: store baseline in accumulate
            total_actual=0,    # TODO: store actual in accumulate
            total_savings=user_data.get("total_tokens", 0),
            event_count=user_data.get("event_count", 0),
        )

    def get_all_time_savings(self, user_id: str) -> int:
        """Get all-time savings for user.

        Args:
            user_id: User ID

        Returns:
            Total tokens saved across all months
        """
        total = 0

        # Sum across all monthly files
        for filepath in self.savings_dir.glob("savings_*.json"):
            data = self._load_file(filepath)
            if user_id in data:
                total += data[user_id].get("total_tokens", 0)

        return total

    def _get_month_filename(self, dt: datetime) -> str:
        """Get filename for current month."""
        return f"savings_{dt.year}-{dt.month:02d}.json"

    def _get_month_filename_explicit(self, year: int, month: int) -> str:
        """Get filename for specific month."""
        return f"savings_{year}-{month:02d}.json"

    def _load_file(self, filepath: Path) -> dict:
        """Load savings data from file."""
        if not filepath.exists():
            return {}

        try:
            with open(filepath, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load savings file {filepath}: {e}")
            return {}

    def _save_file(self, filepath: Path, data: dict) -> None:
        """Save savings data to file."""
        try:
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
            filepath.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to save savings file {filepath}: {e}")
