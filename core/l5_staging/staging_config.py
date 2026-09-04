"""L5 Staging Configuration — Week 1 Deployment.

Separate config from production:
- Lower SLA thresholds (5s operator latency, not 300s)
- Monitoring enabled
- Audit to separate backend
- Learning enabled with synthetic data
"""

from dataclasses import dataclass
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class StagingL5Config:
    """Staging-specific L5 configuration."""

    # Feedback Stability Gate (from L5 k=1)
    ema_alpha: float = 0.3  # Responsive but smooth
    drift_threshold: float = 0.15  # Absolute delta triggers alert
    drift_window: int = 3  # Consecutive high-deltas

    # Operator Approval Gate (from L5 k=2)
    auto_approval_confidence_threshold: float = 0.8  # Auto-approve if confidence > 80%
    approval_ttl_hours: int = 12  # Approval expires after 12h
    operator_latency_sla_seconds: int = 5  # Lower for staging (faster feedback)
    operator_latency_warn_seconds: int = 3

    # Feedback Collection
    synthetic_decisions_per_hour: int = 20
    operator_response_time_mean: float = 2.0  # seconds
    operator_response_time_stddev: float = 0.5

    # Learning
    learning_enabled: bool = True
    confidence_learning_alpha: float = 0.2  # EMA for confidence growth
    threshold_adjustment_rate: float = 0.01  # Per-decision threshold change

    # Monitoring
    metrics_enabled: bool = True
    grafana_port: int = 3001
    prometheus_port: int = 9091

    # Audit
    audit_backend_staging: bool = True
    audit_file_path: str = "~/.corvin/staging/audit.jsonl"

    # Convergence detection
    convergence_window: int = 10  # Check last 10 decisions
    convergence_threshold: float = 0.02  # Threshold stable within ±0.02
    convergence_cycles_min: int = 80  # At least 80 cycles before declaring convergence


def get_staging_config() -> StagingL5Config:
    """Get staging configuration."""
    return StagingL5Config()


def staging_config_as_dict() -> Dict[str, Any]:
    """Convert config to dict for logging/monitoring."""
    config = get_staging_config()
    return {
        'ema_alpha': config.ema_alpha,
        'drift_threshold': config.drift_threshold,
        'drift_window': config.drift_window,
        'auto_approval_confidence_threshold': config.auto_approval_confidence_threshold,
        'approval_ttl_hours': config.approval_ttl_hours,
        'operator_latency_sla_seconds': config.operator_latency_sla_seconds,
        'synthetic_decisions_per_hour': config.synthetic_decisions_per_hour,
        'learning_enabled': config.learning_enabled,
        'metrics_enabled': config.metrics_enabled,
        'audit_backend_staging': config.audit_backend_staging,
    }


logger.info(f"[L5 Staging] Configuration loaded: {staging_config_as_dict()}")
