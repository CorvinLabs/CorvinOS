"""
Gradient Validator — Hard Clipping + NaN/Inf Detection (ADR-0647 Fix #4)

Prevents normalization bypass attacks where extreme gradient magnitudes escape
bounds or NaN/Inf values propagate through weight updates.

Implementation: Three-layer fail-closed defense:
  1. NaN/Inf Detection — reject any non-finite value immediately
  2. Hard Clipping — clip all gradients to [-max_gradient, +max_gradient]
  3. Checkpoint Recovery — rollback to last known-good weights on failure

Security Property: Fail-closed. Invalid gradients result in weight rollback,
never silent acceptance or degradation.

GDPR Compliance: All validations audited (ADR-0232/0233). Validation failures
recorded with tenant_id, timestamp, and recovery action.
"""

from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass
import numpy as np
import math
from datetime import datetime


@dataclass
class ValidationResult:
    """Result of gradient validation."""
    is_valid: bool
    clipped_gradients: Dict[str, Dict]
    num_clipped: int
    num_invalid: int
    invalid_loops: List[Dict]
    recovery_action: Optional[str] = None


class GradientValidator:
    """
    Validate and clip gradients with hard bounds + NaN/Inf detection.

    Fail-Closed Invariant:
      - Any NaN/Inf → validation fails, weight update refused
      - Any gradient |g| > max_gradient → clipped, audit logged
      - Checkpoint rollback available if validation fails

    Audit Integration:
      - All validations logged to audit backend
      - Tenant-scoped, immutable audit events
      - Recovery actions tracked for compliance
    """

    def __init__(
        self,
        max_gradient: float = 1.0,
        audit_backend=None,
        tenant_id: str = "default"
    ):
        """
        Initialize gradient validator.

        Args:
            max_gradient: Hard clipping bound ([-max_gradient, +max_gradient])
            audit_backend: Audit backend for logging events (optional)
            tenant_id: Tenant ID for audit isolation (GDPR Art. 5, 6, 32)
        """
        self.max_gradient = max_gradient
        self.audit = audit_backend
        self.tenant_id = tenant_id

        # Checkpoint management (for rollback after validation failure)
        self.last_good_checkpoint: Optional[Dict[str, float]] = None
        self.checkpoint_batch_id: Optional[str] = None

        # Validation statistics
        self.num_validation_failures = 0
        self.num_gradients_clipped = 0
        self.num_invalid_values_detected = 0

    def validate_and_clip_gradients(
        self,
        gradients: Dict[str, Dict],
        batch_id: str
    ) -> ValidationResult:
        """
        Validate and clip gradients to safe bounds.

        Three-stage process:
          1. Detect NaN/Inf (fail-closed if found)
          2. Clip to [-max_gradient, +max_gradient]
          3. Log clipping events to audit

        Args:
            gradients: Dict mapping loop_id → {'grad': float, 'contributors': list}
            batch_id: Batch identifier for audit logging

        Returns:
            ValidationResult with is_valid, clipped_gradients, stats
        """
        clipped_gradients = {}
        is_valid = True
        clipped_count = 0
        invalid_loops = []

        # Stage 1: Detect NaN/Inf (fail-closed)
        for loop_id, grad_dict in gradients.items():
            grad_value = grad_dict.get('grad', 0.0)

            if not np.isfinite(grad_value):
                is_valid = False
                self.num_invalid_values_detected += 1

                invalid_loops.append({
                    'loop': loop_id,
                    'value': float('inf') if np.isinf(grad_value) else float('nan'),
                    'type': 'Inf' if np.isinf(grad_value) else 'NaN'
                })
                # Continue to collect all invalid loops for comprehensive audit

        # If any NaN/Inf found, fail-closed and return empty
        if not is_valid:
            self.num_validation_failures += 1

            # Audit failure
            if self.audit:
                self.audit.write_event({
                    'event_type': 'gradient_invalid_value',
                    'severity': 'error',
                    'tenant_id': self.tenant_id,
                    'batch_id': batch_id,
                    'num_invalid_loops': len(invalid_loops),
                    'invalid_loops': invalid_loops,
                    'action': 'weight_update_refused',
                    'recovery_action': 'rollback_to_checkpoint' if self.last_good_checkpoint else 'reset_to_initial',
                    'timestamp': datetime.now().isoformat(),
                })

            return ValidationResult(
                is_valid=False,
                clipped_gradients={},
                num_clipped=0,
                num_invalid=len(invalid_loops),
                invalid_loops=invalid_loops,
                recovery_action='rollback_to_checkpoint' if self.last_good_checkpoint else 'reset_to_initial'
            )

        # Stage 2: Clip valid gradients to bounds
        for loop_id, grad_dict in gradients.items():
            grad_value = grad_dict.get('grad', 0.0)

            # Clip to hard bounds
            clipped_value = float(np.clip(grad_value, -self.max_gradient, self.max_gradient))

            # Track clipping using epsilon-based tolerance to prevent precision bypass
            # Use math.isclose() instead of direct comparison to handle floating-point precision
            # CRITICAL: rel_tol must be tight enough to catch attacks that craft values
            # just barely over the bound (e.g., 10.0 + 1e-10 clipped to 10.0)
            # rel_tol=1e-11 catches all attacks with ±1e-10 boundary violations
            was_clipped = not math.isclose(clipped_value, grad_value, rel_tol=1e-11, abs_tol=1e-16)
            if was_clipped:
                clipped_count += 1

            clipped_gradients[loop_id] = {
                'grad': clipped_value,
                'original_grad': float(grad_value),
                'was_clipped': was_clipped,
                'contributors': grad_dict.get('contributors', [])
            }

        # Stage 3: Audit clipping events
        if clipped_count > 0:
            self.num_gradients_clipped += clipped_count

            if self.audit:
                self.audit.write_event({
                    'event_type': 'gradient_clipped',
                    'severity': 'warning',
                    'tenant_id': self.tenant_id,
                    'batch_id': batch_id,
                    'num_clipped': clipped_count,
                    'max_gradient': self.max_gradient,
                    'clipped_loops': [
                        {
                            'loop': loop_id,
                            'original': grad_dict['original_grad'],
                            'clipped': grad_dict['grad']
                        }
                        for loop_id, grad_dict in clipped_gradients.items()
                        if grad_dict['was_clipped']
                    ],
                    'timestamp': datetime.now().isoformat(),
                })

        return ValidationResult(
            is_valid=True,
            clipped_gradients=clipped_gradients,
            num_clipped=clipped_count,
            num_invalid=0,
            invalid_loops=[]
        )

    def save_checkpoint(self, weights: Dict[str, float], batch_id: str) -> None:
        """
        Save current weights as last-known-good checkpoint.

        Used for rollback recovery if validation fails.

        Args:
            weights: Current weight dictionary
            batch_id: Batch identifier for audit
        """
        self.last_good_checkpoint = dict(weights)  # Deep copy
        self.checkpoint_batch_id = batch_id

    def recover_to_checkpoint(self) -> Optional[Dict[str, float]]:
        """
        Recover to last known-good checkpoint after validation failure.

        Returns:
            Checkpoint weights (deep copy) or None if no checkpoint exists
        """
        if self.last_good_checkpoint is None:
            return None

        # Audit recovery action
        if self.audit:
            self.audit.write_event({
                'event_type': 'gradient_recovery_from_checkpoint',
                'severity': 'info',
                'tenant_id': self.tenant_id,
                'checkpoint_batch_id': self.checkpoint_batch_id,
                'recovered_loop_count': len(self.last_good_checkpoint),
                'timestamp': datetime.now().isoformat(),
            })

        return dict(self.last_good_checkpoint)  # Deep copy

    def get_stats(self) -> Dict:
        """
        Return validation statistics.

        Returns:
            Dict with validation failure/clipping counts and checkpoint status
        """
        return {
            'num_validation_failures': self.num_validation_failures,
            'num_invalid_values_detected': self.num_invalid_values_detected,
            'num_gradients_clipped': self.num_gradients_clipped,
            'max_gradient_bound': self.max_gradient,
            'has_checkpoint': self.last_good_checkpoint is not None,
        }

    def reset_stats(self) -> None:
        """Reset validation statistics (for testing)."""
        self.num_validation_failures = 0
        self.num_gradients_clipped = 0
        self.num_invalid_values_detected = 0

    def clear_checkpoint(self) -> None:
        """Clear the saved checkpoint."""
        self.last_good_checkpoint = None
        self.checkpoint_batch_id = None
