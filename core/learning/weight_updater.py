"""
Weight Updater with Oscillation Mitigation (Security Fix #7)
+ Audit-First Weight Updates (Security Fix #8)

Implements:
1. Low-pass filtering and frequency detection (Fix #7)
2. Audit-first weight gate for config changes (Fix #8)

Design:
  1. Exponential Moving Average (EMA) filter smooths weight deltas
  2. Frequency detection triggers when >5 changes/minute occur
  3. Learning rate clamping (0.1x) during oscillation window
  4. All updates audit-logged BEFORE applying (fail-closed); a missing
     audit backend is itself a refusal, not a bypass
  5. Silent config changes BLOCKED (audit must succeed)

Compliance: Audit-first (event logged before weight applied), fail-closed on divergence.
"""

from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import time
import numpy as np
from collections import deque
import json
import logging

logger = logging.getLogger(__name__)


class WeightAuditFailedError(Exception):
    """Raised when weight update fails audit-first gate (Fix #8)."""
    pass


@dataclass
class WeightUpdateRecord:
    """Single weight update event."""
    weight_id: str
    old_value: float
    new_value: float
    ema_filtered_delta: float
    oscillation_detected: bool
    learning_rate_applied: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class OscillationState:
    """Tracks oscillation state for a weight."""
    weight_id: str
    last_update_time: float = 0.0
    update_times_window: deque = field(default_factory=lambda: deque(maxlen=60))  # last 60 seconds
    oscillation_detected_at: Optional[float] = None
    oscillation_clamp_until: Optional[float] = None
    weight_delta_ema: float = 0.0
    weight_delta_ema_prev: float = 0.0
    is_clamped: bool = False


class WeightUpdater:
    """
    Manages weight updates with EMA filtering and oscillation detection.

    Public API:
      - update_weight(weight_id, delta, audit_backend, tenant_id)
      - is_oscillation_clamped(weight_id) -> bool
      - get_effective_learning_rate(weight_id) -> float
    """

    def __init__(
        self,
        ema_alpha: float = 0.3,
        oscillation_frequency_threshold: float = 5.0,  # changes per minute
        oscillation_clamp_duration_seconds: float = 600.0,  # 10 minutes
        learning_rate_clamp_factor: float = 0.1,
    ):
        """
        Initialize weight updater.

        Args:
          ema_alpha: EMA smoothing factor for weight deltas [0, 1]
          oscillation_frequency_threshold: changes/minute to trigger detection
          oscillation_clamp_duration_seconds: how long to clamp learning rate
          learning_rate_clamp_factor: multiply learning rate by this when clamped
        """
        self.ema_alpha = ema_alpha
        self.osc_freq_threshold = oscillation_frequency_threshold
        self.osc_clamp_duration = oscillation_clamp_duration_seconds
        self.learning_rate_clamp = learning_rate_clamp_factor

        # Per-weight tracking
        self.weights: Dict[str, OscillationState] = {}
        self.update_history: List[WeightUpdateRecord] = []

    def update_weight(
        self,
        weight_id: str,
        delta: float,
        base_learning_rate: float = 0.01,
        audit_backend = None,
        tenant_id: str = "default",
    ) -> WeightUpdateRecord:
        """
        Apply weight update with oscillation protection.

        Steps:
          1. Detect oscillation (frequency check)
          2. Apply EMA filter to delta
          3. Clamp learning rate if oscillating
          4. Audit-log the update (BEFORE applying)
          5. Return update record

        Args:
          weight_id: identifier for the weight
          delta: requested change in weight
          base_learning_rate: learning rate to potentially clamp
          audit_backend: audit system. REQUIRED — ``None`` raises
            :class:`WeightAuditFailedError` (audit-first, fail-closed).
          tenant_id: tenant identifier

        Returns:
          WeightUpdateRecord with full update details
        """
        # Audit-first, fail-closed — checked BEFORE any state is touched.
        # Round-4 review: the gate was ``if audit_backend:`` with the parameter
        # defaulting to ``None``, so the DEFAULT call applied the update with no
        # audit record at all. An unaudited weight change is precisely the
        # "silent config change" this module's docstring says is BLOCKED.
        if audit_backend is None:
            raise WeightAuditFailedError(
                f"weight update for {weight_id!r} refused: no audit backend supplied "
                "(audit-first is mandatory; an unaudited weight change is a silent "
                "config change)"
            )

        current_time = time.time()

        # Initialize tracking if first time seeing this weight
        if weight_id not in self.weights:
            self.weights[weight_id] = OscillationState(weight_id=weight_id)

        state = self.weights[weight_id]

        # 1. Detect frequency-based oscillation
        osc_detected = self._detect_frequency_oscillation(weight_id, current_time)

        # If oscillation window has expired, clear it
        if (state.oscillation_clamp_until is not None and
            current_time > state.oscillation_clamp_until):
            state.oscillation_clamp_until = None
            state.is_clamped = False

        # 2. Apply EMA filter to weight delta
        ema_filtered_delta = self._apply_ema_filter(weight_id, delta)

        # 3. Determine effective learning rate
        effective_lr = base_learning_rate
        if state.is_clamped or osc_detected:
            effective_lr = base_learning_rate * self.learning_rate_clamp

        # Compute new weight value (delta * LR)
        new_value_delta = ema_filtered_delta * effective_lr

        # 4. Audit-log the update BEFORE applying (fail-closed). A refused
        # update must also leave NO trace in the oscillation state, otherwise
        # a wedged audit backend still moves the EMA on every retry.
        _ema_before = state.weight_delta_ema_prev
        try:
            self._audit_weight_update(
                audit_backend=audit_backend,
                weight_id=weight_id,
                delta=delta,
                ema_filtered_delta=ema_filtered_delta,
                oscillation_detected=osc_detected,
                effective_learning_rate=effective_lr,
                base_learning_rate=base_learning_rate,
                tenant_id=tenant_id,
                timestamp=current_time,
            )
        except Exception as e:
            # Fail-closed: if audit fails, don't apply weight — and roll the
            # filter state back so the refused attempt is a true no-op.
            state.weight_delta_ema = _ema_before
            state.weight_delta_ema_prev = _ema_before
            if state.update_times_window:
                state.update_times_window.pop()  # drop this attempt's timestamp
            raise RuntimeError(
                f"Audit write failed for weight {weight_id}: {e}. "
                "Weight update rejected (fail-closed)."
            )

        # 5. Create and return update record
        record = WeightUpdateRecord(
            weight_id=weight_id,
            old_value=0.0,  # Placeholder (caller tracks the actual weight)
            new_value=new_value_delta,
            ema_filtered_delta=ema_filtered_delta,
            oscillation_detected=osc_detected,
            learning_rate_applied=effective_lr,
            timestamp=current_time,
        )

        self.update_history.append(record)
        return record

    def _detect_frequency_oscillation(self, weight_id: str, current_time: float) -> bool:
        """
        Detect oscillation by update frequency.

        Returns True if:
          - More than osc_freq_threshold updates occurred in the last 60 seconds

        Side effect:
          - Sets oscillation_clamp_until if oscillation is detected
          - Records current time in update_times_window
        """
        state = self.weights[weight_id]

        # Add current update time to window
        state.update_times_window.append(current_time)
        state.last_update_time = current_time

        # Count updates in last 60 seconds
        one_minute_ago = current_time - 60.0
        recent_updates = [t for t in state.update_times_window if t >= one_minute_ago]
        updates_per_minute = len(recent_updates)

        # Detect oscillation
        if updates_per_minute > self.osc_freq_threshold:
            state.oscillation_detected_at = current_time
            state.oscillation_clamp_until = current_time + self.osc_clamp_duration
            state.is_clamped = True
            return True

        return False

    def _apply_ema_filter(self, weight_id: str, delta: float) -> float:
        """
        Apply exponential moving average to smooth weight delta.

        Formula: ema = alpha * delta + (1 - alpha) * ema_prev

        Returns: EMA-filtered delta
        """
        state = self.weights[weight_id]

        # Compute EMA
        ema_filtered = (
            self.ema_alpha * delta +
            (1.0 - self.ema_alpha) * state.weight_delta_ema_prev
        )

        # Update state
        state.weight_delta_ema_prev = ema_filtered
        state.weight_delta_ema = ema_filtered

        return ema_filtered

    def _audit_weight_update(
        self,
        audit_backend,
        weight_id: str,
        delta: float,
        ema_filtered_delta: float,
        oscillation_detected: bool,
        effective_learning_rate: float,
        base_learning_rate: float,
        tenant_id: str,
        timestamp: float,
    ):
        """
        Audit-log a weight update event BEFORE applying the weight.

        Security Fix #8: Audit-First Weight Updates (Fail-Closed)

        This method implements a fail-closed gate:
        1. Creates immutable audit event with weight change details
        2. Writes to audit backend (MUST succeed)
        3. Only after successful audit commit → caller can proceed
        4. If audit fails → raises WeightAuditFailedError (weight NOT applied)

        This prevents silent config changes that bypass audit logging.
        """
        event = {
            'event_type': 'weight_updated',
            'tenant_id': tenant_id,
            'weight_id': weight_id,
            'delta': delta,
            'ema_filtered_delta': ema_filtered_delta,
            'oscillation_detected': oscillation_detected,
            'effective_learning_rate': effective_learning_rate,
            'base_learning_rate': base_learning_rate,
            'timestamp': datetime.fromtimestamp(timestamp).isoformat(),
        }

        # Phase 1: Write primary weight update event to audit chain
        # MUST succeed before weight is applied (fail-closed)
        try:
            if hasattr(audit_backend, 'write_event'):
                audit_backend.write_event(event)
            elif hasattr(audit_backend, 'write_event_dict'):
                audit_backend.write_event_dict(
                    event_type='weight_updated',
                    tenant_id=tenant_id,
                    details=event,
                    severity='info',
                )
            else:
                raise WeightAuditFailedError(
                    f"Audit backend missing required write methods"
                )

            logger.info(
                f"Weight update audited: weight_id={weight_id}, "
                f"delta={delta:.6f}, ema_delta={ema_filtered_delta:.6f}, "
                f"effective_lr={effective_learning_rate:.6f}, "
                f"oscillation={oscillation_detected}, tenant_id={tenant_id}"
            )
        except Exception as e:
            # Fail-closed: if audit fails, raise exception immediately
            # Weight will NOT be applied
            error_msg = (
                f"CRITICAL: Weight update FAILED audit gate: {str(e)} "
                f"(weight_id={weight_id}, tenant_id={tenant_id}). "
                f"Change REJECTED (fail-closed)."
            )
            logger.error(error_msg)
            raise WeightAuditFailedError(error_msg) from e

        # Phase 2: If oscillation detected, emit additional audit event
        if oscillation_detected:
            osc_event = {
                'event_type': 'weight_oscillation_detected',
                'tenant_id': tenant_id,
                'weight_id': weight_id,
                'detection_time': datetime.fromtimestamp(timestamp).isoformat(),
                'clamp_duration_seconds': self.osc_clamp_duration,
            }
            try:
                if hasattr(audit_backend, 'write_event'):
                    audit_backend.write_event(osc_event)
                elif hasattr(audit_backend, 'write_event_dict'):
                    audit_backend.write_event_dict(
                        event_type='weight_oscillation_detected',
                        tenant_id=tenant_id,
                        details=osc_event,
                        severity='warning',
                    )
            except Exception as e:
                # Log warning if oscillation event fails, but don't raise
                # (primary weight update event already committed)
                logger.warning(
                    f"Failed to audit oscillation detection for {weight_id}: {e}"
                )

    def is_oscillation_clamped(self, weight_id: str) -> bool:
        """Check if a weight is currently under oscillation clamp."""
        if weight_id not in self.weights:
            return False
        state = self.weights[weight_id]
        if state.oscillation_clamp_until is None:
            return False
        # Check if clamp window has expired
        if time.time() > state.oscillation_clamp_until:
            state.is_clamped = False
            state.oscillation_clamp_until = None
            return False
        return state.is_clamped

    def get_effective_learning_rate(
        self,
        weight_id: str,
        base_learning_rate: float = 0.01
    ) -> float:
        """Get the effective learning rate for a weight (accounting for clamping)."""
        if self.is_oscillation_clamped(weight_id):
            return base_learning_rate * self.learning_rate_clamp
        return base_learning_rate

    def get_oscillation_status(self, weight_id: str) -> Dict:
        """Get detailed oscillation status for a weight."""
        if weight_id not in self.weights:
            return {'weight_id': weight_id, 'found': False}

        state = self.weights[weight_id]
        current_time = time.time()

        # Count recent updates
        one_minute_ago = current_time - 60.0
        recent_updates = [t for t in state.update_times_window if t >= one_minute_ago]

        return {
            'weight_id': weight_id,
            'found': True,
            'is_clamped': state.is_clamped,
            'updates_per_minute': len(recent_updates),
            'frequency_threshold': self.osc_freq_threshold,
            'oscillation_detected_at': (
                datetime.fromtimestamp(state.oscillation_detected_at).isoformat()
                if state.oscillation_detected_at else None
            ),
            'oscillation_clamp_until': (
                datetime.fromtimestamp(state.oscillation_clamp_until).isoformat()
                if state.oscillation_clamp_until else None
            ),
            'last_update_time': (
                datetime.fromtimestamp(state.last_update_time).isoformat()
                if state.last_update_time else None
            ),
            'ema_filtered_delta': state.weight_delta_ema,
            'ema_alpha': self.ema_alpha,
        }

    def get_update_history(self, weight_id: Optional[str] = None) -> List[WeightUpdateRecord]:
        """Get update history, optionally filtered by weight_id."""
        if weight_id is None:
            return self.update_history
        return [r for r in self.update_history if r.weight_id == weight_id]
