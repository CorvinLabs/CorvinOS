"""
Meta Loop Optimizer (ADR-0623/0624/0625)

Tier 3 self-tuning: learns optimal hyperparameters for Tier 1/2 loops
- α_core ∈ [0.001, 0.3]: learning rate for Tier 1
- α_infra ∈ [0.001, 0.3]: learning rate for Tier 2
- damping_core ∈ [0.8, 0.99]: exponential smoothing for Tier 1
- damping_infra ∈ [0.8, 0.99]: exponential smoothing for Tier 2

Updates every 100 batches (phase-locked).

Audit-first (CLAUDE.md § Skills 2.0 "no silent optimization"): every change to
a hyperparameter — gradient step, feedback step, restore — is committed to the
core hash chain BEFORE it is applied. If the chain write does not commit the
change is NOT applied and the error propagates (fail-closed).
"""

from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional

from core.learning.base import LearningLoop
from core.learning.watchdog import DivergenceWatchdog
from core.learning.audit_integration import AuditIntegration, TuningOperation, TuningOperationType

#: ``(event_type, *, tenant_id, details) -> audit_ref`` — the core chain writer.
AuditFn = Callable[..., str]

HYPERPARAMETERS = ('α_core', 'α_infra', 'damping_core', 'damping_infra')


def _core_chain_audit(event_type: str, *, tenant_id: str, details: Dict[str, Any]) -> str:
    """Default audit sink: the fail-closed core chain writer (ADR-0232/0233)."""
    from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

    return core_audit_event(event_type, tenant_id=tenant_id, details=details)


class MetaOptimizer(LearningLoop):
    """Tier 3: self-tuning hyperparameters for Tier 1/2 loops."""

    def __init__(self, tenant_id: str = "_default", audit: Optional[AuditFn] = None):
        super().__init__(loop_id="meta", tier=3)
        self.tenant_id = tenant_id
        self._audit: AuditFn = audit or _core_chain_audit
        self._bounds = DivergenceWatchdog(tenant_id).bounds

        # Audit-first integration (Fix #8 — Audit Bypass)
        self._audit_integration = AuditIntegration(
            tenant_id=tenant_id,
            loop_id="meta",
            audit_fn=self._audit,
        )

        # Tunable parameters (Tier 1/2 hyperparameters)
        self.α_core = 0.1
        self.α_infra = 0.01
        self.damping_core = 0.9
        self.damping_infra = 0.95
        self.convergence_threshold = 0.001
        self.variance_threshold = 0.01

        # Meta-level constants (never tuned)
        self.learning_rate_meta = 0.001  # fixed
        self.phase_lock_interval = 100   # update every 100 batches only

        # Tracking
        self.update_count = 0
        self.conservative_mode = False
        self.consecutive_worsening = 0
        self.non_finite_inputs = 0

    # ── loss / gradients ────────────────────────────────────────────────────

    @staticmethod
    def _delta(signals: Dict[str, float], tier: str) -> float:
        """Loss delta for one tier from either key convention.

        ``nine_d_loss`` hands in ``core_loss``/``prev_core_loss``; the older
        callers hand in ``loss_delta_core``. The optimizer only read the latter,
        so from the 9D loop the meta loss was ALWAYS 0 (F-L9).
        """
        explicit = signals.get(f'loss_delta_{tier}')
        if explicit is not None:
            value = float(explicit)
        else:
            current = signals.get(f'{tier}_loss')
            previous = signals.get(f'prev_{tier}_loss')
            if current is None or previous is None:
                return 0.0
            value = float(current) - float(previous)
        return value

    def compute_loss(self, feedback_signals: Dict[str, float]) -> float:
        """Meta loss in [0, 1] from the Tier 1/2 loss deltas.

        A non-finite delta (NaN/Inf from a diverged downstream loop) is scored
        as the WORST case (1.0) and counted in ``non_finite_inputs`` — never as
        0.0, which is what ``max(0.0, nan)`` silently produced before (a
        diverged loop looked perfect to the meta loop).
        """
        deltas = [self._delta(feedback_signals, 'core'), self._delta(feedback_signals, 'infra')]
        if any(not math.isfinite(d) for d in deltas):
            self.non_finite_inputs += 1
            meta_loss = 1.0
        else:
            meta_loss = min(1.0, max(0.0, sum(deltas) / 0.1))
        self.record_loss(meta_loss)
        return float(meta_loss)

    def compute_gradients(self, loss: float, prev_loss: float) -> Dict[str, float]:
        delta_loss = loss - prev_loss
        grad_α = -delta_loss * 0.01
        variance = self.get_loss_variance(last_n=10) if len(self.loss_history) > 10 else 0.01
        grad_damping = max(0.0, (variance - 0.01)) * 0.1

        gradients = {
            'α_core': grad_α,
            'α_infra': grad_α,
            'damping_core': grad_damping,
            'damping_infra': grad_damping,
        }
        self.record_gradients(gradients)
        return gradients

    # ── audited parameter mutation ──────────────────────────────────────────

    def _commit(self, new_values: Dict[str, float], *, reason: str, operation_type: Optional[TuningOperationType] = None) -> bool:
        """Audit-first assignment of hyperparameters; returns True if anything changed.

        Uses AuditIntegration to ensure fail-closed behavior: if audit commit fails,
        the change is NOT applied and a RuntimeError is raised.
        """
        def _changed(old: float, new: float) -> bool:
            # a non-finite live value is ALWAYS a change (``abs(x - nan) > eps`` is
            # False, which would have left a NaN in place during a restore)
            return (not math.isfinite(old)) or abs(new - old) > 1e-12

        old_values = {name: float(getattr(self, name)) for name in HYPERPARAMETERS}

        # Check if anything actually changed
        if not any(_changed(old_values[name], float(new_values[name])) for name in HYPERPARAMETERS):
            return False

        # Determine operation type if not provided
        if operation_type is None:
            if reason == "rollback":
                operation_type = TuningOperationType.ROLLBACK
            elif reason == "feedback":
                operation_type = TuningOperationType.FEEDBACK_SIGNAL
            else:
                operation_type = TuningOperationType.GRADIENT_STEP

        # Create tuning operation
        operation = TuningOperation(
            operation_type=operation_type,
            reason=reason,
            old_values=old_values,
            new_values={name: float(new_values[name]) for name in HYPERPARAMETERS},
            details={
                "update_count": self.update_count,
                "conservative_mode": self.conservative_mode,
            },
        )

        # Audit-first apply via AuditIntegration (fail-closed)
        def apply_new_values(values: Dict[str, float]) -> None:
            for name in HYPERPARAMETERS:
                if name in values:
                    setattr(self, name, values[name])

        try:
            self._audit_integration.audit_and_apply(operation, apply_new_values)
            return True
        except RuntimeError as e:
            # Fail-closed: if audit fails, raise and do not apply
            raise RuntimeError(f"Cannot apply tuning: {e}") from e

    def apply_gradients(self, gradients: Dict[str, float], learning_rate: float = None, damping: float = None, feedback_confidence: float = None):
        if learning_rate is None:
            learning_rate = self.learning_rate_meta
        if damping is None:
            damping = 0.95

        # Stability gate
        avg_gradient = self.get_avg_gradient_magnitude(last_n=10) if len(self.loss_history) > 10 else 0.01
        if avg_gradient < 0.0001:
            return

        # Conservative mode
        effective_learning_rate = learning_rate * (0.5 if self.conservative_mode else 1.0)

        def _step(old: float, grad: float, sign: float, lo: float, hi: float) -> float:
            raw = old + sign * effective_learning_rate * grad
            new = damping * old + (1 - damping) * raw
            return self.clip_parameter(new, lo, hi)

        new_values = {
            'α_core': _step(self.α_core, gradients['α_core'], -1.0, *self._bounds['α_core']),
            'α_infra': _step(self.α_infra, gradients['α_infra'], -1.0, *self._bounds['α_infra']),
            'damping_core': _step(self.damping_core, gradients['damping_core'], +1.0, *self._bounds['damping_core']),
            'damping_infra': _step(self.damping_infra, gradients['damping_infra'], +1.0, *self._bounds['damping_infra']),
        }

        # Determine reason based on whether we're applying feedback or gradients
        reason = "feedback" if feedback_confidence is not None else "gradient_step"

        # Commit with audit integration (fail-closed on audit failure)
        try:
            if feedback_confidence is not None:
                # For feedback-driven changes, include confidence in audit details
                operation_type = TuningOperationType.FEEDBACK_SIGNAL
                operation = TuningOperation(
                    operation_type=operation_type,
                    reason=reason,
                    old_values={name: float(getattr(self, name)) for name in HYPERPARAMETERS},
                    new_values=new_values,
                    details={
                        "update_count": self.update_count,
                        "conservative_mode": self.conservative_mode,
                        "feedback_confidence": float(feedback_confidence),
                    },
                )
                def apply_fn(values: Dict[str, float]) -> None:
                    for name in HYPERPARAMETERS:
                        if name in values:
                            setattr(self, name, values[name])
                self._audit_integration.audit_and_apply(operation, apply_fn)
            else:
                self._commit(new_values, reason=reason, operation_type=TuningOperationType.GRADIENT_STEP)
        except RuntimeError:
            # Re-raise audit failures (fail-closed)
            raise

        # Detect worsening (conservative mode)
        if len(self.loss_history) > 10:
            recent_losses = self.loss_history[-10:]
            avg_recent = sum(recent_losses) / len(recent_losses)
            if avg_recent > 0.5:
                self.consecutive_worsening += 1
                if self.consecutive_worsening > 5:
                    self.conservative_mode = True
            else:
                self.consecutive_worsening = 0
                self.conservative_mode = False

        self.record_parameters(self.get_state_params())
        self.update_count += 1

    def check_convergence(self, gradient_history=None) -> bool:
        if len(self.loss_history) < 50:
            return False
        avg_gradient = self.get_avg_gradient_magnitude(last_n=50)
        param_stability = self.get_parameter_stability(last_n=50)
        return avg_gradient < 0.0001 and param_stability < 0.01

    # ── state ───────────────────────────────────────────────────────────────

    def get_state_params(self) -> Dict[str, float]:
        return {name: float(getattr(self, name)) for name in HYPERPARAMETERS}

    def get_state(self) -> Dict:
        state: Dict[str, Any] = self.get_state_params()
        state['update_count'] = self.update_count
        return state

    def validate_state(self, state: Dict) -> Dict[str, float]:
        """Return the validated hyperparameters of ``state`` or raise ``ValueError``.

        Every hyperparameter must be present, finite and inside the watchdog's
        immutable bounds. ``set_state`` used to assign whatever it was handed
        (NaN, 1e9, a string), so a corrupt checkpoint became live tuning (F-L9).
        """
        if not isinstance(state, dict):
            raise ValueError("state must be a dict")
        missing = [name for name in HYPERPARAMETERS if name not in state]
        if missing:
            raise ValueError(f"state is missing hyperparameters: {missing}")
        clean: Dict[str, float] = {}
        for name in HYPERPARAMETERS:
            value = state[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} is not a number: {value!r}")
            value = float(value)
            lo, hi = self._bounds[name]
            if not math.isfinite(value) or value < lo or value > hi:
                raise ValueError(f"{name} = {value!r} outside [{lo}, {hi}]")
            clean[name] = value
        return clean

    def set_state(self, state: Dict, *, reason: str = "set_state", operation_type: Optional[TuningOperationType] = None):
        clean = self.validate_state(state)
        self._commit(clean, reason=reason, operation_type=operation_type)
        self.update_count = int(state.get('update_count', 0))

    def process_feedback_signal(self, feedback_outcomes: list[Dict[str, float]]) -> bool:
        """Process user feedback outcomes and adjust tuning conservatively.

        Args:
            feedback_outcomes: List of feedback dicts with:
                - outcome_feedback: "yes"|"no"|"unknown"
                - quality_rating: 1-5 or None
                - confidence: 0-1
                - preference_feedback: "llm"|"deterministic"|"either"

        Returns:
            True if feedback was processed and parameters updated (audit successful)
            False if insufficient samples or no high-confidence feedback

        Raises:
            RuntimeError: If audit of feedback-driven changes fails (fail-closed)
        """
        if not feedback_outcomes or len(feedback_outcomes) < 10:
            return False  # Buffer until we have >= 10 samples

        # Compute weighted average of feedback
        total_confidence = 0.0
        yes_count = 0
        no_count = 0

        for fb in feedback_outcomes:
            confidence = fb.get('confidence', 0.5)
            outcome = fb.get('outcome_feedback', 'unknown')

            total_confidence += confidence
            if outcome == 'yes':
                yes_count += confidence
            elif outcome == 'no':
                no_count += confidence

        avg_confidence = total_confidence / len(feedback_outcomes) if feedback_outcomes else 0.0

        # Weighted consensus: positive if yes_count > no_count
        consensus_signal = yes_count - no_count

        # Detect contradictions (both yes and no present)
        has_contradiction = (yes_count > 0 and no_count > 0)
        if has_contradiction:
            self.conservative_mode = True

        # Apply signal as loss delta (audit-first for any changes)
        if avg_confidence >= 0.6:  # Only apply if confident
            feedback_loss_delta = -consensus_signal * 0.01  # Normalize signal
            feedback_gradients = {
                'α_core': -feedback_loss_delta * 0.5,
                'α_infra': -feedback_loss_delta * 0.3,
                'damping_core': 0.0,
                'damping_infra': 0.0,
            }
            try:
                # apply_gradients now uses audit-first internally
                self.apply_gradients(
                    feedback_gradients,
                    learning_rate=self.learning_rate_meta * 0.5,
                    feedback_confidence=avg_confidence,
                )
                return True
            except RuntimeError as e:
                # Audit failed — fail-closed, don't apply feedback
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Failed to apply feedback-driven tuning: {e}")
                return False

        return False

    def detect_feedback_divergence(self, old_loss: float, new_loss: float) -> bool:
        """Detect if feedback-guided optimization made things worse.

        If loss increased after applying feedback, enable conservative mode
        and potentially rollback.

        Returns:
            True if loss worsened (divergence detected)
        """
        if new_loss > old_loss + 0.05:  # Significant worsening
            self.conservative_mode = True
            self.consecutive_worsening += 1
            return True

        self.consecutive_worsening = max(0, self.consecutive_worsening - 1)
        if self.consecutive_worsening == 0:
            self.conservative_mode = False

        return False

    def rollback_to_state(self, saved_state: Dict):
        """Rollback parameters to a previously saved (validated, audited) state.

        Used when feedback-guided optimization diverges.
        Audit is emitted for the rollback operation (fail-closed).

        Raises:
            RuntimeError: If audit of rollback fails
        """
        self.set_state(saved_state, reason="rollback", operation_type=TuningOperationType.ROLLBACK)
        self.conservative_mode = True

    def emit_event(self, collector_integration, **event_data):
        """Emit the current tuning to the Live-Collector.

        ``on_meta_decision(α_core=...)`` used to be called with keyword names
        the collector's signature does not have (TypeError on every emit, F-L9);
        the collector's contract is ``on_meta_tuning(...)``.
        """
        if collector_integration:
            collector_integration.on_meta_tuning(
                step_count=int(event_data.get('step_count', self.update_count)),
                alpha_core=float(self.α_core),
                alpha_infra=float(self.α_infra),
                damping_core=float(self.damping_core),
                damping_infra=float(self.damping_infra),
                convergence_threshold=float(self.convergence_threshold),
                variance_threshold=float(self.variance_threshold),
                is_converged=bool(self.check_convergence()),
            )
