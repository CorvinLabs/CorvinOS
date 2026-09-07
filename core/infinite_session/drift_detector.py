"""Phase C: Drift Detector (ADR-0542).

EMA-based drift detection over numeric configuration series, drift gates, and
the operator revert button (undo via :class:`RollbackManager`).

Tenant-bound: alerts live at ``<tenant_root>/drift/alerts/<alert_id>.json``;
``alert_id`` is validated and the path resolve-checked before any open.

Two entry points:
- :meth:`assess_series` — PURE (no disk side effects): classify a numeric
  series into a :class:`DriftLevel`; used by the dashboard API on every read.
- :meth:`check_drift` — the gate: same assessment plus a persisted
  :class:`DriftAlert` when sustained drift is detected.
"""

from __future__ import annotations

import json
import numbers
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from core.infinite_session.ema_smoother import DriftLevel, EMASmoother
from core.infinite_session.paths import safe_child, tenant_root
from core.infinite_session.rollback_manager import RollbackManager
from core.tenants import validate_tenant_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DriftGateType(str, Enum):
    STRICT = "strict"      # Block changes if drift detected
    WARNING = "warning"    # Warn but allow
    ADVISORY = "advisory"  # Log only


@dataclass(frozen=True)
class DriftAlert:
    """Immutable drift alert record."""

    alert_id: str
    tenant_id: str
    timestamp: str
    config_path: str
    current_value: float
    ema: float
    drift_magnitude: float
    drift_level: DriftLevel
    gate_type: DriftGateType
    action_taken: str  # "blocked" | "warned" | "logged" | "error"
    message: str
    dismissed: bool = False
    dismissal_timestamp: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "alert_id": self.alert_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "config_path": self.config_path,
            "current_value": self.current_value,
            "ema": self.ema,
            "drift_magnitude": self.drift_magnitude,
            "drift_level": self.drift_level.value,
            "gate_type": self.gate_type.value,
            "action_taken": self.action_taken,
            "message": self.message,
            "dismissed": self.dismissed,
            "dismissal_timestamp": self.dismissal_timestamp,
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DriftAlert":
        payload = dict(data)
        payload["drift_level"] = DriftLevel(payload["drift_level"])
        payload["gate_type"] = DriftGateType(payload["gate_type"])
        return cls(**payload)


@dataclass(frozen=True)
class DriftAssessment:
    """Result of a pure series assessment."""
    level: DriftLevel
    magnitude: float
    ema: float
    sustained: bool
    message: str


def numeric_leaves(state: Dict[str, Any], prefix: str = "", max_depth: int = 6) -> Dict[str, float]:
    """Flatten the numeric (non-bool) leaves of a state dict: ``{"a.b": 1.0}``."""
    out: Dict[str, float] = {}
    if max_depth <= 0 or not isinstance(state, dict):
        return out
    for key, value in state.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, numbers.Real):
            out[path] = float(value)
        elif isinstance(value, dict):
            out.update(numeric_leaves(value, path, max_depth - 1))
    return out


class DriftDetector:
    """Tenant-bound drift detection (ADR-0542)."""

    def __init__(
        self,
        tenant_id: str,
        corvin_home: Optional[str | Path] = None,
        smoother: Optional[EMASmoother] = None,
    ):
        self.tenant_id = validate_tenant_id(tenant_id)
        root = tenant_root(self.tenant_id, corvin_home) / "drift"
        self.alerts_dir = root / "alerts"
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        self.smoother = smoother or EMASmoother()

    def _bind(self, tenant_id: Any) -> Optional[str]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return "tenant_id is required"
        try:
            validate_tenant_id(tenant_id)
        except ValueError as exc:
            return str(exc)
        if tenant_id != self.tenant_id:
            return f"Tenant mismatch: detector is bound to {self.tenant_id!r}, got {tenant_id!r}"
        return None

    # ── pure assessment ──────────────────────────────────────────────────

    def assess_series(self, samples: List[Tuple[str, float]]) -> DriftAssessment:
        """Classify a ``[(timestamp, value), ...]`` series; no side effects."""
        if not samples:
            return DriftAssessment(DriftLevel.NORMAL, 0.0, 0.0, False, "no samples")
        ema_samples = self.smoother.process_samples(samples)
        latest = ema_samples[-1]
        sustained, recommendation = self.smoother.detect_sustained_drift(
            ema_samples, min_critical_samples=3
        )
        level = latest.drift_level
        if sustained and level != DriftLevel.CRITICAL:
            level = DriftLevel.CRITICAL
        message = recommendation or f"drift={latest.drift:.3f} ({level.value})"
        return DriftAssessment(level, latest.drift, latest.ema, sustained, message)

    def assess_states(self, states: List[Tuple[str, Dict[str, Any]]]) -> DriftAssessment:
        """Worst drift across every numeric key of an ordered ``[(ts, state)]`` list."""
        series: Dict[str, List[Tuple[str, float]]] = {}
        for ts, state in states:
            for key, value in numeric_leaves(state).items():
                series.setdefault(key, []).append((ts, value))
        worst = DriftAssessment(DriftLevel.NORMAL, 0.0, 0.0, False, "no numeric state")
        rank = {DriftLevel.NORMAL: 0, DriftLevel.WARNING: 1, DriftLevel.CRITICAL: 2}
        for key, samples in series.items():
            a = self.assess_series(samples)
            if (rank[a.level], a.magnitude) > (rank[worst.level], worst.magnitude):
                worst = DriftAssessment(a.level, a.magnitude, a.ema, a.sustained, f"{key}: {a.message}")
        return worst

    # ── gate ─────────────────────────────────────────────────────────────

    def check_drift(
        self,
        tenant_id: str,
        config_path: str,
        samples: List[Tuple[str, float]],
        gate_type: DriftGateType = DriftGateType.STRICT,
    ) -> Tuple[bool, Optional[DriftAlert]]:
        """``(should_block, alert)`` — alert persisted when sustained drift is found."""
        if self._bind(tenant_id) or not samples:
            return False, None
        try:
            ema_samples = self.smoother.process_samples(samples)
            sustained, recommendation = self.smoother.detect_sustained_drift(
                ema_samples, min_critical_samples=3
            )
            if not sustained:
                return False, None
            anomaly_score, anom_recommendation = self.smoother.get_anomaly_score(
                ema_samples, window_size=5
            )
            should_block = gate_type == DriftGateType.STRICT and anomaly_score > 0.7
            if should_block:
                action_taken = "blocked"
            elif gate_type == DriftGateType.WARNING:
                action_taken = "warned"
            else:
                action_taken = "logged"
            latest = ema_samples[-1]
            alert = DriftAlert(
                alert_id=str(uuid4()),
                tenant_id=tenant_id,
                timestamp=latest.timestamp,
                config_path=config_path,
                current_value=latest.value,
                ema=latest.ema,
                drift_magnitude=latest.drift,
                drift_level=latest.drift_level,
                gate_type=gate_type,
                action_taken=action_taken,
                message=f"Drift detected: {anom_recommendation or recommendation}",
            )
            self._log_alert(alert)
            return should_block, alert
        except (TypeError, ValueError) as exc:
            # Fail-closed: an unassessable series blocks.
            alert = DriftAlert(
                alert_id=str(uuid4()),
                tenant_id=tenant_id,
                timestamp=_now(),
                config_path=config_path,
                current_value=0.0,
                ema=0.0,
                drift_magnitude=0.0,
                drift_level=DriftLevel.CRITICAL,
                gate_type=gate_type,
                action_taken="error",
                message=f"Drift check failed (fail-closed): {exc}",
            )
            self._log_alert(alert)
            return True, alert

    # ── revert button ────────────────────────────────────────────────────

    def create_revert_button(
        self,
        tenant_id: str,
        alert_id: str,
        rollback_manager: RollbackManager,
        audit_callback=None,
    ) -> Tuple[bool, Optional[str]]:
        """Operator revert: roll back the latest transaction of the alert's config_path."""
        error = self._bind(tenant_id)
        if error:
            return False, error
        if not alert_id:
            return False, "alert_id is required"
        alert = self._load_alert(tenant_id, alert_id)
        if not alert:
            return False, f"Alert {alert_id} not found"
        history = rollback_manager.get_transaction_history(
            tenant_id, config_path=alert.config_path, limit=1
        )
        if not history:
            return False, f"No transaction to revert for {alert.config_path}"
        transaction_id = history[0].get("transaction_id")
        success, error = rollback_manager.rollback_transaction(
            tenant_id=tenant_id,
            transaction_id_to_undo=transaction_id,
            audit_callback=audit_callback,
        )
        if success:
            self._dismiss_alert(tenant_id, alert_id)
            if audit_callback:
                audit_callback(
                    event_type="infinite_session.drift_revert_button_pressed",
                    alert_id=alert_id,
                    transaction_id=transaction_id,
                    tenant_id=tenant_id,
                    config_path=alert.config_path,
                    timestamp=_now(),
                )
        return success, error

    # ── alert storage ────────────────────────────────────────────────────

    def _alert_file(self, alert_id: str) -> Path:
        return safe_child(self.alerts_dir, f"{alert_id}.json")

    def get_active_alerts(
        self, tenant_id: str, config_path: Optional[str] = None
    ) -> List[DriftAlert]:
        if self._bind(tenant_id):
            return []
        alerts: List[DriftAlert] = []
        for alert_file in sorted(self.alerts_dir.glob("*.json")):
            try:
                with open(alert_file, "r", encoding="utf-8") as fh:
                    alert = DriftAlert.from_dict(json.load(fh))
            except (OSError, ValueError, TypeError, KeyError):
                continue
            if alert.dismissed or alert.tenant_id != tenant_id:
                continue
            if config_path and alert.config_path != config_path:
                continue
            alerts.append(alert)
        return alerts

    def _log_alert(self, alert: DriftAlert) -> None:
        target = self._alert_file(alert.alert_id)
        tmp = target.with_name(target.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(alert.to_dict(), fh, indent=2, sort_keys=True)
        tmp.replace(target)

    def _load_alert(self, tenant_id: str, alert_id: str) -> Optional[DriftAlert]:
        if self._bind(tenant_id):
            return None
        try:
            target = self._alert_file(alert_id)
        except ValueError:
            return None
        if not target.exists():
            return None
        try:
            with open(target, "r", encoding="utf-8") as fh:
                alert = DriftAlert.from_dict(json.load(fh))
        except (OSError, ValueError, TypeError, KeyError):
            return None
        return alert if alert.tenant_id == tenant_id else None

    def _dismiss_alert(self, tenant_id: str, alert_id: str) -> None:
        alert = self._load_alert(tenant_id, alert_id)
        if alert is None:
            return
        dismissed = DriftAlert(**{
            **alert.to_dict(),
            "drift_level": alert.drift_level,
            "gate_type": alert.gate_type,
            "dismissed": True,
            "dismissal_timestamp": _now(),
        })
        self._log_alert(dismissed)
