"""Budget Guardrails for Stories 9-10 (Alerts & Spend Caps).

Provides:
- Budget alert thresholds
- Hard spend cap enforcement
- Alert history tracking

License: Apache-2.0
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    """Budget alert severity."""
    WARNING = "warning"   # 75% of budget
    CRITICAL = "critical"  # 90% of budget
    EXCEEDED = "exceeded"   # 100%+ of budget


class AlertReason(str, Enum):
    """Reason for alert."""
    DAILY_THRESHOLD = "daily_threshold_exceeded"
    MONTHLY_THRESHOLD = "monthly_threshold_exceeded"
    SPEND_CAP_HIT = "spend_cap_hit"
    ANOMALY_DETECTED = "spending_anomaly"


@dataclass(frozen=True)
class BudgetAlert:
    """A budget alert (Story 9)."""

    alert_id: str
    timestamp: str  # ISO 8601
    tenant_id: str

    level: AlertLevel
    reason: AlertReason

    current_spend: str      # Decimal stringified (EUR)
    budget_threshold: str   # Decimal stringified (EUR)
    remaining_budget: str   # Decimal stringified (EUR)

    percentage_used: float  # 0-100+

    message: str           # Human-readable alert message

    def to_dict(self) -> dict:
        return {
            "alert_id": self.alert_id,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "level": self.level.value,
            "reason": self.reason.value,
            "current_spend": self.current_spend,
            "budget_threshold": self.budget_threshold,
            "remaining_budget": self.remaining_budget,
            "percentage_used": self.percentage_used,
            "message": self.message,
        }


@dataclass(frozen=True)
class BudgetStatus:
    """Current budget status."""

    current_spend: str      # Decimal stringified (EUR)
    budget_limit: str       # Decimal stringified (EUR)
    percentage_used: float  # 0-100+
    remaining_budget: str   # Decimal stringified (EUR)

    daily_limit: str        # Daily budget (EUR)
    daily_spend: str        # Today's spend (EUR)

    alert_active: bool
    alert_level: Optional[AlertLevel] = None
    alert_message: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "current_spend": self.current_spend,
            "budget_limit": self.budget_limit,
            "percentage_used": self.percentage_used,
            "remaining_budget": self.remaining_budget,
            "daily_limit": self.daily_limit,
            "daily_spend": self.daily_spend,
            "alert_active": self.alert_active,
            "alert_level": self.alert_level.value if self.alert_level else None,
            "alert_message": self.alert_message,
        }


@dataclass
class BudgetConfig:
    """Budget configuration (Story 9)."""

    monthly_budget: Decimal
    daily_budget: Decimal
    warning_threshold_pct: float = 75.0  # Alert at 75%
    critical_threshold_pct: float = 90.0  # Alert at 90%
    spend_cap_enabled: bool = True       # Hard cap (Story 10)

    def to_dict(self) -> dict:
        return {
            "monthly_budget": str(self.monthly_budget),
            "daily_budget": str(self.daily_budget),
            "warning_threshold_pct": self.warning_threshold_pct,
            "critical_threshold_pct": self.critical_threshold_pct,
            "spend_cap_enabled": self.spend_cap_enabled,
        }


class BudgetGuard:
    """Enforce budget limits and track alerts (Stories 9-10)."""

    def __init__(self, corvin_home: str | Path):
        """Initialize budget guard.

        Args:
            corvin_home: Path to ~/.corvin
        """
        self.corvin_home = Path(corvin_home)
        self.budget_dir = (
            self.corvin_home / "tenants" / "_default" / "global" / "budget"
        )
        self.budget_dir.mkdir(parents=True, exist_ok=True)

        # Load or create default config
        self.config = self._load_config()
        self.alert_id_counter = 0

    def set_budget(
        self,
        monthly_budget: Decimal,
        daily_budget: Optional[Decimal] = None,
    ) -> BudgetConfig:
        """Set budget limits (Story 9).

        Args:
            monthly_budget: Monthly budget in EUR
            daily_budget: Daily budget in EUR (default: monthly / 30)

        Returns:
            Updated BudgetConfig
        """
        if daily_budget is None:
            daily_budget = monthly_budget / Decimal("30")

        self.config = BudgetConfig(
            monthly_budget=monthly_budget,
            daily_budget=daily_budget,
            warning_threshold_pct=75.0,
            critical_threshold_pct=90.0,
            spend_cap_enabled=True,
        )

        self._save_config()
        return self.config

    def check_budget(
        self,
        current_spend: Decimal,
        daily_spend: Decimal,
    ) -> BudgetStatus:
        """Check current budget status (Story 9).

        Args:
            current_spend: Current monthly spend (EUR)
            daily_spend: Today's spend (EUR)

        Returns:
            BudgetStatus with current situation
        """
        remaining = self.config.monthly_budget - current_spend
        pct_used = (
            float(current_spend / self.config.monthly_budget * Decimal("100"))
            if self.config.monthly_budget > 0
            else 0
        )

        # Determine alert level
        alert_active = False
        alert_level = None
        alert_message = None

        if pct_used >= 100:
            alert_active = True
            alert_level = AlertLevel.EXCEEDED
            alert_message = f"Budget exceeded: {pct_used:.0f}% ({current_spend} EUR)"
        elif pct_used >= self.config.critical_threshold_pct:
            alert_active = True
            alert_level = AlertLevel.CRITICAL
            alert_message = (
                f"Critical: {pct_used:.0f}% of monthly budget used. "
                f"Remaining: {remaining} EUR"
            )
        elif pct_used >= self.config.warning_threshold_pct:
            alert_active = True
            alert_level = AlertLevel.WARNING
            alert_message = (
                f"Warning: {pct_used:.0f}% of monthly budget used. "
                f"Remaining: {remaining} EUR"
            )

        daily_pct = (
            float(daily_spend / self.config.daily_budget * Decimal("100"))
            if self.config.daily_budget > 0
            else 0
        )

        status = BudgetStatus(
            current_spend=str(current_spend),
            budget_limit=str(self.config.monthly_budget),
            percentage_used=pct_used,
            remaining_budget=str(max(remaining, Decimal("0"))),
            daily_limit=str(self.config.daily_budget),
            daily_spend=str(daily_spend),
            alert_active=alert_active,
            alert_level=alert_level,
            alert_message=alert_message,
        )

        # Record alert if triggered
        if alert_active:
            self._record_alert(
                current_spend,
                alert_level,
                alert_message,
            )

        return status

    def enforce_spend_cap(
        self,
        current_spend: Decimal,
        requested_cost: Decimal,
    ) -> tuple[bool, Optional[str]]:
        """Enforce hard spend cap (Story 10).

        Returns:
            (allowed, denial_reason)
            - allowed: True if request can proceed, False if denied
            - denial_reason: Reason for denial (if allowed=False)
        """
        if not self.config.spend_cap_enabled:
            return True, None

        if current_spend + requested_cost > self.config.monthly_budget:
            remaining = self.config.monthly_budget - current_spend
            denial_reason = (
                f"Request would exceed monthly budget. "
                f"Remaining: {remaining} EUR, "
                f"Requested: {requested_cost} EUR"
            )

            # Log denial
            self._record_denial(
                current_spend,
                requested_cost,
                denial_reason,
            )

            return False, denial_reason

        return True, None

    def get_alerts(self, hours: int = 24) -> list[BudgetAlert]:
        """Get recent alerts (Story 9).

        Args:
            hours: Hours to look back (default: last 24 hours)

        Returns:
            List of recent BudgetAlert objects
        """
        alerts = []
        cutoff = datetime.now(timezone.utc)
        cutoff = cutoff.replace(tzinfo=timezone.utc) - __import__("datetime").timedelta(hours=hours)

        alerts_file = self.budget_dir / "alerts.jsonl"
        if not alerts_file.exists():
            return alerts

        with open(alerts_file, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line)
                ts = datetime.fromisoformat(data["timestamp"])
                if ts > cutoff:
                    alerts.append(BudgetAlert(
                        alert_id=data["alert_id"],
                        timestamp=data["timestamp"],
                        tenant_id=data["tenant_id"],
                        level=AlertLevel(data["level"]),
                        reason=AlertReason(data["reason"]),
                        current_spend=data["current_spend"],
                        budget_threshold=data["budget_threshold"],
                        remaining_budget=data["remaining_budget"],
                        percentage_used=data["percentage_used"],
                        message=data["message"],
                    ))

        return sorted(alerts, key=lambda a: a.timestamp, reverse=True)

    def _record_alert(
        self,
        current_spend: Decimal,
        level: AlertLevel,
        message: str,
    ) -> None:
        """Record a budget alert."""
        self.alert_id_counter += 1
        alert_id = f"alert-{self.alert_id_counter}"

        # Determine reason from level
        if level == AlertLevel.EXCEEDED:
            reason = AlertReason.MONTHLY_THRESHOLD
        else:
            reason = AlertReason.MONTHLY_THRESHOLD

        remaining = self.config.monthly_budget - current_spend
        pct_used = float(current_spend / self.config.monthly_budget * Decimal("100"))

        alert = BudgetAlert(
            alert_id=alert_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            tenant_id="_default",
            level=level,
            reason=reason,
            current_spend=str(current_spend),
            budget_threshold=str(self.config.monthly_budget),
            remaining_budget=str(max(remaining, Decimal("0"))),
            percentage_used=pct_used,
            message=message,
        )

        self._append_alert_to_file(alert)

    def _record_denial(
        self,
        current_spend: Decimal,
        requested_cost: Decimal,
        reason: str,
    ) -> None:
        """Record a spend cap denial."""
        denials_file = self.budget_dir / "denials.jsonl"

        denial_record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_spend": str(current_spend),
            "requested_cost": str(requested_cost),
            "reason": reason,
            "tenant_id": "_default",
        }

        try:
            with open(denials_file, "a") as f:
                f.write(json.dumps(denial_record, separators=(",", ":")) + "\n")
            denials_file.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to record spend cap denial: {e}")

    def _append_alert_to_file(self, alert: BudgetAlert) -> None:
        """Append alert to alerts.jsonl."""
        alerts_file = self.budget_dir / "alerts.jsonl"

        try:
            with open(alerts_file, "a") as f:
                f.write(json.dumps(alert.to_dict(), separators=(",", ":")) + "\n")
            alerts_file.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to record budget alert: {e}")

    def _load_config(self) -> BudgetConfig:
        """Load budget config from disk."""
        config_file = self.budget_dir / "config.json"

        if not config_file.exists():
            # Create default config
            default = BudgetConfig(
                monthly_budget=Decimal("100"),  # €100/month default
                daily_budget=Decimal("3.33"),   # €3.33/day
            )
            self._save_config_obj(default)
            return default

        try:
            with open(config_file, "r") as f:
                data = json.load(f)
                return BudgetConfig(
                    monthly_budget=Decimal(data["monthly_budget"]),
                    daily_budget=Decimal(data["daily_budget"]),
                    warning_threshold_pct=data.get("warning_threshold_pct", 75.0),
                    critical_threshold_pct=data.get("critical_threshold_pct", 90.0),
                    spend_cap_enabled=data.get("spend_cap_enabled", True),
                )
        except Exception as e:
            logger.error(f"Failed to load budget config: {e}")
            return BudgetConfig(
                monthly_budget=Decimal("100"),
                daily_budget=Decimal("3.33"),
            )

    def _save_config(self) -> None:
        """Save current config to disk."""
        self._save_config_obj(self.config)

    def _save_config_obj(self, config: BudgetConfig) -> None:
        """Save config object to disk."""
        config_file = self.budget_dir / "config.json"

        try:
            with open(config_file, "w") as f:
                json.dump(config.to_dict(), f, indent=2)
            config_file.chmod(0o600)
        except Exception as e:
            logger.error(f"Failed to save budget config: {e}")
