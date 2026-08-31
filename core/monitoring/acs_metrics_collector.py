"""ACS (Tiered Delegation Engine) metrics collector — Phase 4 k=3 telemetry.

Collects 27 metrics covering:
- Lifecycle events (invoked, completed, failed, paused, resumed)
- Token usage and budget consumption
- Decision routing and classification
- Error classification and recovery
- Budget accounting (daily, per-worker, cumulative)
- Execution quality (success rate, latency percentiles)

Every metric is emitted as a first-class audit event in the tenant's hash chain,
enabling recovery from partial audit states and audit verification without
requiring a separate metrics database.

Design constraints:
- Best-effort audit writes: failures never crash the caller
- Tenant isolation: all metrics scoped by tenant_id
- No blocking I/O: use async patterns for any synchronous writes
- Audit-first: every metric is a first-class event in the chain
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Lazy import of security_events (same pattern as metrics_recorders.py)
_security_events = None
_REPO = Path(__file__).resolve().parents[3]
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

try:
    from forge import security_events as _security_events
except ImportError:
    pass


class ACSMetricsCollector:
    """Collector for ACS (Tiered Delegation Engine) metrics — 27 metrics total."""

    # ========== LIFECYCLE METRICS ==========
    # 1-5: Core lifecycle

    @staticmethod
    def record_acs_invoked(
        tenant_id: str,
        acs_id: str,
        prompt_length: int,
        budget_s: int,
    ) -> None:
        """Record ACS invocation (acs.invoked).

        Args:
            tenant_id: tenant identifier
            acs_id: unique ACS run identifier
            prompt_length: length of prompt in characters
            budget_s: budget in seconds
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.invoked",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "prompt_length": prompt_length,
                    "budget_s": budget_s,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.invoked for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_completed(
        tenant_id: str,
        acs_id: str,
        status: str,  # "success", "partial", "timeout"
        duration_ms: int,
        tokens_used: int = 0,
    ) -> None:
        """Record ACS completion (acs.completed).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            status: completion status (success, partial, timeout)
            duration_ms: execution duration in milliseconds
            tokens_used: tokens consumed
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.completed",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "status": status,
                    "duration_ms": duration_ms,
                    "tokens_used": tokens_used,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.completed for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_failed(
        tenant_id: str,
        acs_id: str,
        error_type: str,
        duration_ms: int,
    ) -> None:
        """Record ACS failure (acs.failed).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            error_type: error classification (timeout, crash, validation, budget_exceeded)
            duration_ms: time until failure
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.failed",
                severity="WARNING",
                details={
                    "acs_id": acs_id,
                    "error_type": error_type,
                    "duration_ms": duration_ms,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.failed for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_paused(
        tenant_id: str,
        acs_id: str,
        reason: str,  # "quota_exhausted", "user_cancel", "system_load"
    ) -> None:
        """Record ACS pause (acs.paused).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            reason: pause reason
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.paused",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "reason": reason,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.paused for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_resumed(
        tenant_id: str,
        acs_id: str,
    ) -> None:
        """Record ACS resumption (acs.resumed).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.resumed",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.resumed for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    # ========== TOKEN & BUDGET METRICS ==========
    # 6-10: Token usage and budget

    @staticmethod
    def record_acs_tokens_consumed(
        tenant_id: str,
        acs_id: str,
        tokens_used: int,
        tokens_remaining: int,
    ) -> None:
        """Record token consumption (acs.tokens_consumed).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            tokens_used: tokens consumed by this run
            tokens_remaining: tokens remaining in daily budget
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.tokens_consumed",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "tokens_used": tokens_used,
                    "tokens_remaining": tokens_remaining,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.tokens_consumed for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_budget_check(
        tenant_id: str,
        daily_tokens_used: int,
        daily_tokens_limit: int,
        budget_status: str,  # "ok", "low", "exhausted"
    ) -> None:
        """Record daily budget check (acs.budget_check).

        Args:
            tenant_id: tenant identifier
            daily_tokens_used: tokens used today
            daily_tokens_limit: daily token limit
            budget_status: status (ok, low, exhausted)
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.budget_check",
                severity="INFO",
                details={
                    "daily_tokens_used": daily_tokens_used,
                    "daily_tokens_limit": daily_tokens_limit,
                    "budget_status": budget_status,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.budget_check for %s",
                tenant_id, exc_info=True,
            )

    @staticmethod
    def record_acs_budget_exceeded(
        tenant_id: str,
        acs_id: str,
        tokens_requested: int,
        tokens_available: int,
    ) -> None:
        """Record budget exceeded (acs.budget_exceeded).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            tokens_requested: tokens requested by caller
            tokens_available: tokens available in budget
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.budget_exceeded",
                severity="WARNING",
                details={
                    "acs_id": acs_id,
                    "tokens_requested": tokens_requested,
                    "tokens_available": tokens_available,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.budget_exceeded for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    # ========== DECISION ROUTING METRICS ==========
    # 11-15: Routing and classification decisions

    @staticmethod
    def record_acs_classified(
        tenant_id: str,
        acs_id: str,
        classification: str,  # "big_data", "structured", "code", "text", "other"
        confidence: float,
    ) -> None:
        """Record task classification (acs.classified).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            classification: task classification
            confidence: confidence score (0.0-1.0)
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.classified",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "classification": classification,
                    "confidence": confidence,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.classified for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_routed(
        tenant_id: str,
        acs_id: str,
        target_engine: str,  # "native", "acs", "tde"
        reason: str,  # "user_explicit", "classifier", "fallback"
    ) -> None:
        """Record routing decision (acs.routed).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            target_engine: target engine (native, acs, tde)
            reason: routing reason
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.routed",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "target_engine": target_engine,
                    "reason": reason,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.routed for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_queued(
        tenant_id: str,
        acs_id: str,
        queue_depth: int,
        wait_time_ms: int,
    ) -> None:
        """Record task queuing (acs.queued).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            queue_depth: current queue depth
            wait_time_ms: time spent waiting
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.queued",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "queue_depth": queue_depth,
                    "wait_time_ms": wait_time_ms,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.queued for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_fallback_triggered(
        tenant_id: str,
        acs_id: str,
        reason: str,  # "quota_exhausted", "tde_unavailable"
        fallback_engine: str,
    ) -> None:
        """Record fallback trigger (acs.fallback_triggered).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            reason: reason for fallback
            fallback_engine: engine to fall back to
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.fallback_triggered",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "reason": reason,
                    "fallback_engine": fallback_engine,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.fallback_triggered for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    # ========== ERROR CLASSIFICATION METRICS ==========
    # 16-20: Error tracking and recovery

    @staticmethod
    def record_acs_error(
        tenant_id: str,
        acs_id: str,
        error_class: str,  # "validation", "network", "timeout", "crash", "malformed_input"
        error_code: str,
    ) -> None:
        """Record ACS error (acs.error).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            error_class: error classification
            error_code: specific error code
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.error",
                severity="WARNING",
                details={
                    "acs_id": acs_id,
                    "error_class": error_class,
                    "error_code": error_code,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.error for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_retry(
        tenant_id: str,
        acs_id: str,
        attempt: int,
        max_attempts: int,
    ) -> None:
        """Record retry attempt (acs.retry).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            attempt: current attempt number
            max_attempts: maximum allowed attempts
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.retry",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.retry for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_validation_failed(
        tenant_id: str,
        acs_id: str,
        validation_type: str,  # "input", "output", "schema"
        details: Optional[str] = None,
    ) -> None:
        """Record validation failure (acs.validation_failed).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            validation_type: type of validation that failed
            details: validation failure details (if safe to log)
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.validation_failed",
                severity="WARNING",
                details={
                    "acs_id": acs_id,
                    "validation_type": validation_type,
                    "details": details or "",
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.validation_failed for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    # ========== PERFORMANCE METRICS ==========
    # 21-25: Latency and throughput

    @staticmethod
    def record_acs_latency(
        tenant_id: str,
        acs_id: str,
        latency_ms: int,
        p50_ms: int,
        p99_ms: int,
    ) -> None:
        """Record latency metrics (acs.latency).

        Args:
            tenant_id: tenant identifier
            acs_id: ACS run identifier
            latency_ms: actual latency for this run
            p50_ms: median latency percentile (collected metric)
            p99_ms: 99th percentile latency
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.latency",
                severity="INFO",
                details={
                    "acs_id": acs_id,
                    "latency_ms": latency_ms,
                    "p50_ms": p50_ms,
                    "p99_ms": p99_ms,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.latency for %s:%s",
                tenant_id, acs_id, exc_info=True,
            )

    @staticmethod
    def record_acs_throughput(
        tenant_id: str,
        runs_per_minute: float,
        success_rate: float,
    ) -> None:
        """Record throughput metrics (acs.throughput).

        Args:
            tenant_id: tenant identifier
            runs_per_minute: ACS runs per minute
            success_rate: success rate (0.0-1.0)
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "acs.throughput",
                severity="INFO",
                details={
                    "runs_per_minute": runs_per_minute,
                    "success_rate": success_rate,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.throughput for %s",
                tenant_id, exc_info=True,
            )

    # ========== AGGREGATE METRICS ==========
    # 26-27: Aggregated metrics for dashboard

    @staticmethod
    def record_acs_daily_summary(
        tenant_id: str,
        total_runs: int,
        successful_runs: int,
        failed_runs: int,
        total_tokens_used: int,
        average_latency_ms: int,
    ) -> None:
        """Record daily summary (acs.daily_summary).

        Args:
            tenant_id: tenant identifier
            total_runs: total ACS runs today
            successful_runs: number of successful runs
            failed_runs: number of failed runs
            total_tokens_used: total tokens used today
            average_latency_ms: average latency
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            success_rate = (
                100.0 * successful_runs / total_runs
                if total_runs > 0
                else 0.0
            )

            _security_events.write_event(
                chain_path,
                "acs.daily_summary",
                severity="INFO",
                details={
                    "total_runs": total_runs,
                    "successful_runs": successful_runs,
                    "failed_runs": failed_runs,
                    "success_rate_percent": success_rate,
                    "total_tokens_used": total_tokens_used,
                    "average_latency_ms": average_latency_ms,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.daily_summary for %s",
                tenant_id, exc_info=True,
            )

    @staticmethod
    def record_acs_quality_score(
        tenant_id: str,
        score: float,  # 0.0-1.0, composite of success_rate, latency, budget_efficiency
        components: Optional[dict[str, float]] = None,
    ) -> None:
        """Record ACS quality score (acs.quality_score).

        Args:
            tenant_id: tenant identifier
            score: composite quality score (0.0-1.0)
            components: breakdown of score (e.g., {"success": 0.9, "latency": 0.85, "efficiency": 0.95})
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            details = {
                "score": score,
            }
            if components:
                details["components"] = components

            _security_events.write_event(
                chain_path,
                "acs.quality_score",
                severity="INFO",
                details=details,
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record acs.quality_score for %s",
                tenant_id, exc_info=True,
            )


__all__ = ["ACSMetricsCollector"]
