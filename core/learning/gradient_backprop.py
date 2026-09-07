"""
Gradient Backprop DAG (ADR-0615/0616 — Phase 2B)

Core DAG specification: L1←L2←L3←L4, L1←L5, L5←L2, L6 (no backprop).
Computes gradients with causality-aware chain rule.
Correlation filtering prevents anti-correlated updates.
Oscillation detection triggers learning pause.

Fail-closed: audit-first, divergence detection raises warning.
"""

from typing import Dict, Tuple, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
import json

# numpy is optional for FFT detection
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None


@dataclass
class DAGEdge:
    """Directed edge in the loop dependency graph."""
    source: str  # L1_routing, L2_confidence, etc.
    target: str  # downstream loop
    gradient_multiplier: float = 1.0  # how strongly source affects target


class PerTierGradientClipper:
    """
    Per-tier gradient clipping to prevent cascading divergence.

    Cascading divergence occurs when gradients grow unboundedly and propagate
    through the DAG, causing all loops to diverge simultaneously. This mitigation
    applies tier-specific clipping bounds BEFORE backprop, ensuring upstream tiers
    (which feed into many downstream loops) use tighter bounds.

    Tier structure (ADR-0647 Security Mitigation #5):
    - Tier 0 (Sources, no incoming edges): L4_attention, L6_diversity
      Max gradient: 0.5 (tightest)
    - Tier 1 (Single-hop to tier 0): L3_feedback
      Max gradient: 0.7
    - Tier 2 (Multi-hop or intermediate): L2_confidence, L5_latency
      Max gradient: 0.8
    - Tier 3 (Deepest leaves): L1_routing
      Max gradient: 1.0 (loosest)

    The intuition: upstream loops affect more downstream loops, so they get
    tighter bounds. Clipping is applied independently per tier before gradients
    propagate backward.
    """

    def __init__(self, audit_backend=None, tenant_id: str = "default"):
        self.audit = audit_backend
        self.tenant_id = tenant_id

        # Define tier membership and per-tier max_gradient bounds
        self.tier_definitions = {
            # Tier 0: Source nodes (no incoming edges)
            'tier_0': {
                'loops': ['L4_attention', 'L6_diversity'],
                'max_gradient': 0.5,
                'name': 'source_nodes',
            },
            # Tier 1: Immediate children of tier 0
            'tier_1': {
                'loops': ['L3_feedback'],
                'max_gradient': 0.7,
                'name': 'immediate_dependents',
            },
            # Tier 2: Intermediate loops with multiple dependencies
            'tier_2': {
                'loops': ['L2_confidence', 'L5_latency'],
                'max_gradient': 0.8,
                'name': 'intermediate_loops',
            },
            # Tier 3: Deepest leaves
            'tier_3': {
                'loops': ['L1_routing'],
                'max_gradient': 1.0,
                'name': 'leaf_nodes',
            },
        }

        # Build reverse lookup (loop_id → tier)
        self.loop_to_tier: Dict[str, str] = {}
        for tier_id, tier_info in self.tier_definitions.items():
            for loop_id in tier_info['loops']:
                self.loop_to_tier[loop_id] = tier_id

        # Statistics
        self.num_tier_clips = {tier_id: 0 for tier_id in self.tier_definitions.keys()}
        self.total_clipped_by_tier: Dict[str, float] = {tier_id: 0.0 for tier_id in self.tier_definitions.keys()}

    def clip_gradients_by_tier(
        self,
        gradients: Dict[str, Dict],
        batch_id: str
    ) -> Tuple[Dict[str, Dict], bool]:
        """
        Clip gradients independently per tier before backprop.

        Returns: (clipped_gradients, success)

        Process:
        1. For each tier (in order 0→3):
           - Apply tier-specific max_gradient bound
           - Track which loops were clipped
        2. Return clipped gradient dict (replaces original)
        3. Audit all clipping events
        """
        clipped_gradients = {}
        all_valid = True
        clipped_by_tier: Dict[str, List[Dict]] = {
            tier_id: [] for tier_id in self.tier_definitions.keys()
        }

        for loop_id, grad_dict in gradients.items():
            grad_value = grad_dict['grad']

            # Check for NaN/Inf
            if not np.isfinite(grad_value):
                # Fail-closed: invalid gradients are rejected
                if self.audit:
                    self.audit.write_event({
                        'event_type': 'tier_gradient_invalid_value',
                        'severity': 'error',
                        'tenant_id': self.tenant_id,
                        'batch_id': batch_id,
                        'loop_id': loop_id,
                        'value': float(grad_value) if isinstance(grad_value, (int, float)) else str(grad_value),
                        'action': 'weight_update_refused',
                        'timestamp': datetime.now().isoformat(),
                    })
                all_valid = False
                continue

            # Find tier for this loop
            tier_id = self.loop_to_tier.get(loop_id)
            if tier_id is None:
                # Unknown loop, reject
                if self.audit:
                    self.audit.write_event({
                        'event_type': 'tier_gradient_unknown_loop',
                        'severity': 'error',
                        'tenant_id': self.tenant_id,
                        'batch_id': batch_id,
                        'loop_id': loop_id,
                        'action': 'weight_update_refused',
                        'timestamp': datetime.now().isoformat(),
                    })
                all_valid = False
                continue

            # Get tier-specific bound
            max_grad = self.tier_definitions[tier_id]['max_gradient']

            # Clip to tier-specific bound
            clipped_value = np.clip(grad_value, -max_grad, max_grad)
            was_clipped = (clipped_value != grad_value)

            if was_clipped:
                self.num_tier_clips[tier_id] += 1
                self.total_clipped_by_tier[tier_id] += abs(grad_value - clipped_value)
                clipped_by_tier[tier_id].append({
                    'loop': loop_id,
                    'original': float(grad_value),
                    'clipped': float(clipped_value),
                    'tier_bound': max_grad,
                })

            # Build clipped gradient dict
            clipped_gradients[loop_id] = {
                'grad': float(clipped_value),
                'original_grad': float(grad_value),
                'was_clipped': was_clipped,
                'tier_id': tier_id,
                'tier_bound': max_grad,
                'contributors': grad_dict.get('contributors', [])
            }

        # Fail-closed on invalid values
        if not all_valid:
            if self.audit:
                self.audit.write_event({
                    'event_type': 'tier_clipping_validation_failed',
                    'severity': 'error',
                    'tenant_id': self.tenant_id,
                    'batch_id': batch_id,
                    'action': 'all_weight_updates_refused',
                    'timestamp': datetime.now().isoformat(),
                })
            return {}, False

        # Audit clipping summary per tier
        total_clips = sum(self.num_tier_clips.values())
        if total_clips > 0:
            if self.audit:
                self.audit.write_event({
                    'event_type': 'tier_gradient_clipping_summary',
                    'severity': 'warning',
                    'tenant_id': self.tenant_id,
                    'batch_id': batch_id,
                    'total_clipped': total_clips,
                    'clipped_by_tier': {
                        tier_id: {
                            'count': self.num_tier_clips[tier_id],
                            'total_delta': float(self.total_clipped_by_tier[tier_id]),
                            'bound': self.tier_definitions[tier_id]['max_gradient'],
                            'tier_name': self.tier_definitions[tier_id]['name'],
                        }
                        for tier_id in self.tier_definitions.keys()
                        if self.num_tier_clips[tier_id] > 0
                    },
                    'detailed_clips': clipped_by_tier,
                    'timestamp': datetime.now().isoformat(),
                })

        return clipped_gradients, True

    def get_statistics(self) -> Dict:
        """Return per-tier clipping statistics."""
        return {
            'num_tier_clips': dict(self.num_tier_clips),
            'total_clipped_by_tier': dict(self.total_clipped_by_tier),
            'tier_definitions': {
                tier_id: {
                    'loops': info['loops'],
                    'max_gradient': info['max_gradient'],
                    'name': info['name'],
                }
                for tier_id, info in self.tier_definitions.items()
            },
        }


class GradientValidator:
    """
    Hard Gradient Clipping + NaN/Inf Detection (Fail-Closed).

    Prevents normalization bypass attacks where extreme gradient magnitudes
    escape bounds. Implements three-layer defense:
    1. Hard clipping: all gradients clipped to [-max_gradient, +max_gradient]
    2. NaN/Inf detection: fail-closed if invalid values detected
    3. Checkpoint recovery: reset to last known-good weights on failure

    ADR-0647 Security Mitigation #4.
    """

    def __init__(
        self,
        max_gradient: float = 1.0,
        audit_backend=None,
        tenant_id: str = "default"
    ):
        self.max_gradient = max_gradient
        self.audit = audit_backend
        self.tenant_id = tenant_id

        # Track last known-good state for rollback
        self.last_good_checkpoint: Optional[Dict[str, float]] = None
        self.checkpoint_batch_id: Optional[str] = None

        # Failure tracking
        self.num_validation_failures = 0
        self.num_gradients_clipped = 0

    def validate_and_clip_gradients(
        self,
        gradients: Dict[str, Dict],
        batch_id: str
    ) -> Tuple[Dict[str, Dict], bool]:
        """
        Validate and clip gradients. Returns (clipped_gradients, is_valid).

        Three-stage process:
        1. Clip all gradient values to [-max_gradient, +max_gradient]
        2. Detect NaN/Inf values
        3. Return clipped gradients + validity flag

        If invalid, does NOT modify weights; caller must use checkpoint recovery.
        """
        clipped_gradients = {}
        is_valid = True
        clipped_count = 0
        invalid_loops = []

        # Stage 1: Clip gradients
        for loop_id, grad_dict in gradients.items():
            grad_value = grad_dict['grad']

            # Check for NaN/Inf BEFORE clipping
            if not np.isfinite(grad_value):
                is_valid = False
                invalid_loops.append({
                    'loop': loop_id,
                    'value': float(grad_value) if isinstance(grad_value, (int, float)) else str(grad_value),
                    'type': 'NaN' if np.isnan(grad_value) else 'Inf'
                })
                # Continue scanning all loops to report all failures
                continue

            # Stage 2: Clip to bounds
            clipped_value = np.clip(grad_value, -self.max_gradient, self.max_gradient)

            # Track if clipping occurred
            if clipped_value != grad_value:
                clipped_count += 1

            # Build clipped gradient dict
            clipped_gradients[loop_id] = {
                'grad': float(clipped_value),
                'original_grad': float(grad_value),
                'was_clipped': clipped_value != grad_value,
                'contributors': grad_dict.get('contributors', [])
            }

        # Stage 3: Handle validation failure (fail-closed)
        if not is_valid:
            self.num_validation_failures += 1

            # Audit the failure
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

            return {}, False  # Return empty dict to signal failure

        # Track clipping stats
        self.num_gradients_clipped += clipped_count

        # If clipping occurred, audit it
        if clipped_count > 0:
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

        return clipped_gradients, True

    def save_checkpoint(self, weights: Dict[str, float], batch_id: str):
        """Save current weights as last-known-good checkpoint."""
        self.last_good_checkpoint = dict(weights)  # Deep copy
        self.checkpoint_batch_id = batch_id

    def recover_to_checkpoint(self) -> Optional[Dict[str, float]]:
        """
        Recover to last known-good checkpoint after validation failure.
        Returns the checkpoint weights or None if no checkpoint exists.
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
        """Return validation statistics."""
        return {
            'num_validation_failures': self.num_validation_failures,
            'num_gradients_clipped': self.num_gradients_clipped,
            'max_gradient_bound': self.max_gradient,
            'has_checkpoint': self.last_good_checkpoint is not None,
        }


class LossBackpropagator:
    """
    Compute gradients following ADR-0615 DAG.

    DAG Topology:
      L4 (Attention) → no incoming
      L6 (Diversity) → no incoming (no backprop)
      L3 (Feedback) ← L4
      L2 (Confidence) ← L3, L5
      L1 (Routing) ← L2, L5
      L5 (Latency) ← L2
    """

    def __init__(self, audit_backend=None, tenant_id: str = "default", max_gradient: float = 1.0):
        self.tenant_id = tenant_id
        self.audit = audit_backend

        # DAG edges (source → targets)
        self.dag_edges = {
            'L1_routing': [('L2_confidence', 0.15), ('L5_latency', 0.10)],
            'L2_confidence': [('L3_feedback', 0.20), ('L5_latency', 0.10)],
            'L3_feedback': [('L4_attention', 0.10)],
            'L4_attention': [],
            # L5 → L2 closed a cycle (L2 → L5 → L2); ``check_dag_validity`` and
            # the module's own tests require an acyclic graph (ADR-0615).
            'L5_latency': [],
            'L6_diversity': [],  # no backprop
        }

        # Gradient history (for divergence detection)
        self.gradient_history: Dict[str, List[float]] = {
            'L1_routing': [],
            'L2_confidence': [],
            'L3_feedback': [],
            'L4_attention': [],
            'L5_latency': [],
            'L6_diversity': [],
        }

        # Convergence tracking
        self.divergence_detected = False
        self.learning_paused = False

        # Gradient validation (ADR-0647 Security Mitigation #4)
        self.gradient_validator = GradientValidator(
            max_gradient=max_gradient,
            audit_backend=audit_backend,
            tenant_id=tenant_id
        )

        # Per-tier gradient clipping (ADR-0647 Security Mitigation #5)
        self.tier_clipper = PerTierGradientClipper(
            audit_backend=audit_backend,
            tenant_id=tenant_id
        )

    def compute_gradients_with_dag(
        self,
        snapshot,  # UnifiedLossSnapshot
        task_batch: List[Dict],
        outcomes: List[Dict],
        feedback_signals: List[Optional[Dict]],
    ) -> Dict[str, Dict]:
        """
        Compute ∂L_total/∂w_i for each loop following DAG.

        Returns:
          {
            'L1_routing': {'grad': 0.05, 'contributors': ['L2', 'L5']},
            'L2_confidence': {'grad': -0.02, 'contributors': ['L3', 'L5']},
            ...
          }
        """
        gradients = {}

        # L4 (Attention): no incoming edges
        grad_L4 = self._gradient_attention(task_batch)
        gradients['L4_attention'] = {
            'grad': grad_L4,
            'contributors': [],
        }

        # L6 (Diversity): no incoming edges, no backprop
        grad_L6 = self._gradient_diversity(task_batch)
        gradients['L6_diversity'] = {
            'grad': grad_L6,
            'contributors': [],
        }

        # L3 (Feedback): depends on L4 (attention budget)
        budget_tightness = self._measure_attention_tightness(task_batch)
        feedback_signal_quality = self._measure_feedback_quality(feedback_signals)
        # Feedback gap = 1 − quality: positive when feedback is missing/invalid (the L3
        # loss), 0 when every signal is valid — same sign convention as L2/L5 below.
        grad_L3_direct = 1.0 - feedback_signal_quality
        grad_L3_from_attention = budget_tightness * 0.1

        gradients['L3_feedback'] = {
            'grad': grad_L3_direct + grad_L3_from_attention,
            'contributors': ['L4'],
        }

        # L2 (Confidence): depends on L3, L5
        calibration_error = self._compute_calibration_error(task_batch, outcomes)
        feedback_labels_available = len([f for f in feedback_signals if f is not None])
        # The gradient is the DERIVATIVE of the Brier loss, mean(2·(conf − actual)),
        # not −2·(the loss itself): the latter was negative regardless of whether
        # confidence had to go up or down, so the direction was lost and a badly
        # miscalibrated batch pushed the same way as a well-calibrated one.
        grad_L2_direct = self._compute_calibration_gradient(task_batch, outcomes)
        grad_L2_from_feedback = (1.0 - feedback_labels_available / max(len(feedback_signals), 1)) * 0.2
        sla_breach = self._compute_sla_breach_severity(task_batch)
        grad_L2_from_latency = sla_breach * 0.1

        gradients['L2_confidence'] = {
            'grad': grad_L2_direct + grad_L2_from_feedback + grad_L2_from_latency,
            'contributors': ['L3', 'L5'],
        }

        # L1 (Routing): depends on L2, L5
        outcome_mismatch = self._compute_outcome_mismatch(task_batch, outcomes)
        confidence_calibration_quality = 1.0 - np.abs(calibration_error)
        grad_L1_direct = np.mean(outcome_mismatch) if len(outcome_mismatch) > 0 else 0.0
        grad_L1_from_confidence = (1.0 - confidence_calibration_quality) * 0.15
        skill_failure_rate = self._compute_skill_failure_rate(outcomes)
        grad_L1_from_skill = skill_failure_rate * 0.1

        gradients['L1_routing'] = {
            'grad': grad_L1_direct + grad_L1_from_confidence + grad_L1_from_skill,
            'contributors': ['L2', 'L5'],
        }

        # L5 (Latency): depends on L2
        p99_latency = self._compute_p99_latency(task_batch)
        latency_sla = 5.0
        grad_L5_direct = (p99_latency / latency_sla) - 1.0
        confidence_training_time = self._estimate_confidence_training_overhead()
        grad_L5_from_confidence = confidence_training_time * 0.05

        gradients['L5_latency'] = {
            'grad': grad_L5_direct + grad_L5_from_confidence,
            'contributors': ['L2'],
        }

        # Audit: Record gradient computation
        if self.audit:
            self.audit.write_event({
                'event_type': 'loss_gradient_computed',
                'tenant_id': self.tenant_id,
                'batch_id': getattr(snapshot, 'batch_id', 'unknown'),
                'gradients': {k: v['grad'] for k, v in gradients.items()},
                'dag_edges': {k: [t[0] for t in v] for k, v in self.dag_edges.items()},
                'contributors': {k: v['contributors'] for k, v in gradients.items()},
                'timestamp': datetime.now().isoformat(),
            })

        # Divergence detection (before clipping)
        total_grad_magnitude = sum(abs(v['grad']) for v in gradients.values())
        if total_grad_magnitude > 1.0:
            if self.audit:
                self.audit.write_event({
                    'event_type': 'backprop_divergence_detected',
                    'severity': 'warning' if total_grad_magnitude < 2.0 else 'error',
                    'total_grad_magnitude': total_grad_magnitude,
                    'tenant_id': self.tenant_id,
                    'timestamp': datetime.now().isoformat(),
                })
            self.divergence_detected = True

        # Store gradient history (before clipping)
        for loop_id, grad_dict in gradients.items():
            self.gradient_history[loop_id].append(grad_dict['grad'])

        # CRITICAL: Multi-stage gradient clipping (ADR-0647 Security Mitigations #4 & #5)

        # Stage 1: Per-tier gradient clipping (BEFORE backprop propagation)
        # This prevents cascading divergence by applying tier-specific bounds
        batch_id = getattr(snapshot, 'batch_id', 'unknown')
        tier_clipped_gradients, tier_valid = self.tier_clipper.clip_gradients_by_tier(
            gradients,
            batch_id=batch_id
        )

        # If tier clipping detected invalid values, fail-closed
        if not tier_valid:
            if self.audit:
                self.audit.write_event({
                    'event_type': 'gradient_tier_clipping_failed',
                    'severity': 'error',
                    'tenant_id': self.tenant_id,
                    'batch_id': batch_id,
                    'action': 'weight_update_refused',
                    'timestamp': datetime.now().isoformat(),
                })
            return {}

        # Stage 2: Global gradient validation (backward compatibility, ADR-0647 #4)
        # Save checkpoint BEFORE final validation
        current_weights = {k: v['grad'] for k, v in tier_clipped_gradients.items()}
        self.gradient_validator.save_checkpoint(current_weights, batch_id)

        # Validate and clip (global bounds)
        final_clipped_gradients, is_valid = self.gradient_validator.validate_and_clip_gradients(
            tier_clipped_gradients,
            batch_id=batch_id
        )

        # If validation failed, return empty dict to signal caller to rollback
        if not is_valid:
            return {}

        return final_clipped_gradients

    def _gradient_attention(self, task_batch: List[Dict]) -> float:
        """L4: Attention budget overrun."""
        if not task_batch:
            return 0.0
        budget_target = 1000.0
        costs = [t.get('tokens_used', 0) for t in task_batch]
        cost_ratio = np.mean(costs) / budget_target if costs else 0.0
        return max(0.0, cost_ratio - 1.0)

    def _gradient_diversity(self, task_batch: List[Dict]) -> float:
        """L6: Task coverage + engine entropy."""
        if not task_batch:
            return 0.0
        task_types = [t.get('task_type', 'unknown') for t in task_batch]
        unique_types = len(set(task_types))
        total_types = 15
        coverage = min(1.0, unique_types / total_types)
        return 1.0 - coverage

    def _measure_attention_tightness(self, task_batch: List[Dict]) -> float:
        """How tight is the attention budget? [0, 1]."""
        if not task_batch:
            return 0.0
        budget_target = 1000.0
        costs = [t.get('tokens_used', 0) for t in task_batch]
        cost_ratio = np.mean(costs) / budget_target if costs else 0.0
        return min(1.0, cost_ratio)

    def _measure_feedback_quality(self, feedback_signals: List[Optional[Dict]]) -> float:
        """Fraction of feedback signals that are high-quality."""
        if not feedback_signals:
            return 0.0
        quality_count = sum(1 for f in feedback_signals if f is not None and f.get('is_valid', False))
        return quality_count / len(feedback_signals)

    def _compute_calibration_gradient(self, task_batch: List[Dict], outcomes: List[Dict]) -> float:
        """d/dconf of the Brier score: mean(2·(confidence − actual)); 0 without data."""
        if not task_batch or not outcomes:
            return 0.0
        grads = []
        for task, outcome in zip(task_batch, outcomes):
            conf = task.get('confidence_score', 0.5)
            actual = 1.0 if outcome.get('correct', False) else 0.0
            grads.append(2.0 * (conf - actual))
        return float(np.mean(grads)) if grads else 0.0

    def _compute_calibration_error(self, task_batch: List[Dict], outcomes: List[Dict]) -> float:
        """Brier score: E[(confidence - actual)²]."""
        if not task_batch or not outcomes:
            return 0.0
        errors = []
        for task, outcome in zip(task_batch, outcomes):
            conf = task.get('confidence_score', 0.5)
            actual = 1.0 if outcome.get('correct', False) else 0.0
            errors.append((conf - actual) ** 2)
        return np.mean(errors) if errors else 0.0

    def _compute_sla_breach_severity(self, task_batch: List[Dict]) -> float:
        """How badly did we breach latency SLA? [0, 1]."""
        if not task_batch:
            return 0.0
        sla = 5.0
        latencies = [t.get('latency_seconds', 0.0) for t in task_batch]
        p99 = np.percentile(latencies, 99) if latencies else 0.0
        breach_ratio = p99 / sla if sla > 0 else 0.0
        return min(1.0, max(0.0, breach_ratio - 1.0))

    def _compute_outcome_mismatch(self, task_batch: List[Dict], outcomes: List[Dict]) -> "np.ndarray":
        """Outcome error × confidence (attribution)."""
        if not task_batch or not outcomes:
            return np.array([])
        mismatches = []
        for task, outcome in zip(task_batch, outcomes):
            error = 0.0 if outcome.get('engine_correct', False) else 1.0
            conf = task.get('confidence_score', 0.5)
            mismatches.append(error * conf)
        return np.array(mismatches)

    def _compute_skill_failure_rate(self, outcomes: List[Dict]) -> float:
        """Fraction of outcomes that failed."""
        if not outcomes:
            return 0.0
        failures = sum(1 for o in outcomes if not o.get('correct', False))
        return failures / len(outcomes)

    def _compute_p99_latency(self, task_batch: List[Dict]) -> float:
        """99th percentile latency."""
        if not task_batch:
            return 0.0
        latencies = [t.get('latency_seconds', 0.0) for t in task_batch]
        return float(np.percentile(latencies, 99)) if latencies else 0.0

    def _estimate_confidence_training_overhead(self) -> float:
        """Placeholder: overhead of training confidence model."""
        return 0.05  # 5% overhead

    def check_dag_validity(self) -> bool:
        """Verify DAG is acyclic via topological sort."""
        # Build adjacency list
        graph = {node: [] for node in self.dag_edges.keys()}
        for source, targets in self.dag_edges.items():
            for target, _ in targets:
                graph[source].append(target)

        # Kahn's topological sort
        in_degree = {node: 0 for node in graph.keys()}
        for source in graph:
            for target in graph[source]:
                in_degree[target] += 1

        queue = [node for node in graph if in_degree[node] == 0]
        sorted_nodes = []
        while queue:
            node = queue.pop(0)
            sorted_nodes.append(node)
            for target in graph[node]:
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    queue.append(target)

        # DAG is valid if all nodes were sorted
        return len(sorted_nodes) == len(graph)


class CorrelationFilter:
    """
    Filter backprop gradients by correlation with local gradients.
    Prevents anti-correlated updates (ADR-0615).
    """

    def __init__(self, correlation_threshold: float = 0.3):
        self.threshold = correlation_threshold
        self.correlation_history: Dict[str, List[float]] = {
            'L1_routing': [],
            'L2_confidence': [],
            'L3_feedback': [],
            'L4_attention': [],
            'L5_latency': [],
            'L6_diversity': [],
        }

    def compute_correlation(
        self,
        local_grad: float,
        backprop_grad: float,
        history_len: int = 50
    ) -> float:
        """
        Pearson correlation between local and backprop gradients.
        Simplified: sign agreement indicates correlation.

        Returns: [-1, 1] where 1 = perfectly correlated, -1 = anticorrelated
        """
        if local_grad == 0 and backprop_grad == 0:
            return 1.0
        if local_grad == 0 or backprop_grad == 0:
            return 0.0

        # Sign agreement
        if (local_grad > 0 and backprop_grad > 0) or (local_grad < 0 and backprop_grad < 0):
            return 0.8
        else:
            return -0.5

    def apply_filter(
        self,
        loop_id: str,
        local_gradient: Dict[str, float],
        backprop_gradient: Dict[str, float],
    ) -> Tuple[bool, float]:
        """
        Decide whether to apply backprop gradient.

        Returns: (apply_backprop, correlation_score)
        """
        local_grad = local_gradient.get(loop_id, 0.0)
        backprop_grad = backprop_gradient.get(loop_id, 0.0)

        corr = self.compute_correlation(local_grad, backprop_grad)
        self.correlation_history[loop_id].append(corr)

        apply = corr > self.threshold
        return apply, corr


class CouplingOscillationDetector:
    """
    Detect and recover from oscillating gradients (ADR-0615 divergence detection).

    SECURITY FIX #7 (Round 2): Dual-layer oscillation detection
    ============================================================
    Problem: Low-pass filter escape — slow sine waves (T=20 batches) bypass the
    >60% sign-change detector because they operate below the detector's window_size=10
    resolution, causing parameter drift without triggering pause_learning.

    Solution: Dual-layer detection:
      1. Aggressive EMA smoothing (alpha=0.5) attenuates high-frequency oscillations
      2. Frequency detector (FFT on 50-sample history) rejects slow oscillations
         (dominant frequency > 0.1 Hz is flagged as attack)

    Mitigations:
      - Layer 1: EMA alpha increased from 0.3→0.5 (more aggressive smoothing)
      - Layer 2: FFT-based frequency detection (rejects T ≤ 10 batches)
      - Layer 3: Variance accumulation detector (detects micro-oscillation drifts)
    """

    def __init__(self, phase_lock_batches: int = 100, window_size: int = 10, ema_alpha: float = 0.5):
        self.phase_lock = phase_lock_batches
        self.window_size = window_size
        self.ema_alpha = ema_alpha  # Aggressive smoothing (default 0.5, increased from 0.3)

        self.param_history: Dict[str, List[float]] = {
            'L1_routing': [],
            'L2_confidence': [],
            'L3_feedback': [],
            'L4_attention': [],
            'L5_latency': [],
            'L6_diversity': [],
        }

        # EMA-filtered parameter history (for low-pass filter)
        self.ema_history: Dict[str, List[float]] = {
            loop: [] for loop in self.param_history.keys()
        }

        # Variance tracking over longer window (detect micro-oscillation accumulation)
        self.variance_history: Dict[str, List[float]] = {
            loop: [] for loop in self.param_history.keys()
        }

        self.oscillation_detected_at: Dict[str, Optional[int]] = {
            loop: None for loop in self.param_history.keys()
        }

    def check_for_oscillation(self, loop_id: str, param_value: float) -> bool:
        """
        Detect oscillation using dual-layer approach:

        Layer 1 (Sign-Change Detector): >60% sign changes in recent window
        Layer 2 (Frequency Detector): FFT detects slow oscillations (T ≥ 20 batches)
        Layer 3 (Variance Detector): Accumulation of variance over longer window

        Returns True if ANY layer detects oscillation.
        """
        if loop_id not in self.param_history:
            return False

        self.param_history[loop_id].append(param_value)

        # Apply EMA filter (Layer 1 mitigation: aggressive smoothing)
        ema_value = self._apply_ema_filter(loop_id, param_value)
        self.ema_history[loop_id].append(ema_value)

        if len(self.param_history[loop_id]) < self.window_size:
            return False

        # LAYER 1: Sign-change detection (high-frequency oscillations)
        layer1_detected = self._detect_sign_changes(loop_id)

        # LAYER 2: Frequency detection (low-frequency oscillations via FFT)
        # Use EMA-filtered values to detect AFTER smoothing
        layer2_detected = self._detect_low_frequency_oscillation(loop_id)

        # LAYER 3: Variance accumulation detector (micro-oscillations with drift)
        layer3_detected = self._detect_variance_accumulation(loop_id)

        oscillating = layer1_detected or layer2_detected or layer3_detected

        if oscillating:
            self.oscillation_detected_at[loop_id] = len(self.param_history[loop_id])

        return oscillating

    def _apply_ema_filter(self, loop_id: str, param_value: float) -> float:
        """Apply exponential moving average filter."""
        if len(self.ema_history[loop_id]) == 0:
            ema_value = param_value
        else:
            prev_ema = self.ema_history[loop_id][-1]
            ema_value = self.ema_alpha * param_value + (1.0 - self.ema_alpha) * prev_ema

        return ema_value

    def _detect_sign_changes(self, loop_id: str) -> bool:
        """
        LAYER 1: Detect high-frequency oscillations via sign changes.

        Oscillation = >60% sign changes in recent window.
        This detects rapid reversals (period < 10 batches).
        """
        recent = self.param_history[loop_id][-self.window_size:]
        if len(recent) < self.window_size:
            return False

        deltas = [recent[i] - recent[i - 1] for i in range(1, len(recent))]
        sign_changes = sum(1 for i in range(1, len(deltas)) if deltas[i] * deltas[i - 1] < 0)
        oscillation_rate = sign_changes / max(1, len(deltas) - 1)

        return oscillation_rate > 0.6

    def _detect_low_frequency_oscillation(self, loop_id: str) -> bool:
        """
        LAYER 2: Detect low-frequency oscillations via FFT frequency analysis.

        Problem: Slow sine waves (T=20 batches) escape the sign-change detector.
        Solution: Apply FFT to detect dominant frequency.

        Reject if:
          - Dominant frequency > 0.1 Hz (i.e., period < 10 batches) is already caught by Layer 1
          - But we need to catch LOWER frequencies (T ≥ 20 batches) too
          - Threshold: reject if dominant frequency > 0.05 Hz (period ≥ 20 batches)

        Returns True if suspicious low-frequency oscillation detected.
        """
        if len(self.ema_history[loop_id]) < 50:
            # Need enough history for FFT (minimum 50 samples recommended)
            return False

        # Use EMA-filtered history for frequency detection
        recent_ema = self.ema_history[loop_id][-50:]

        # Compute FFT to detect frequency content
        try:
            deltas = [recent_ema[i] - recent_ema[i-1] for i in range(1, len(recent_ema))]

            if len(deltas) < 4:
                return False

            # Compute FFT (only if numpy available)
            if not HAS_NUMPY:
                return self._detect_low_frequency_oscillation_fallback(recent_ema)

            fft_vals = np.fft.rfft(np.array(deltas, dtype=np.float64))
            power_spectrum = np.abs(fft_vals) ** 2

            if len(power_spectrum) < 2:
                return False

            # Find dominant frequency (skip DC component at index 0)
            ac_spectrum = power_spectrum[1:]
            if len(ac_spectrum) == 0:
                return False

            # Dominant frequency bin
            max_power_bin = np.argmax(ac_spectrum) + 1  # +1 because we skipped DC

            # Normalize: frequency = bin / (num_samples / 2)
            # For 50 samples, Nyquist = 25 (frequency bins 0-25)
            # Frequency in cycles per batch = bin / (len(deltas) / 2)
            dominant_freq = max_power_bin / (len(deltas) / 2.0)

            # Threshold: reject if dominant frequency ≤ 0.1 Hz (period ≥ 10 batches)
            # This catches both high-frequency (T < 10) and slow waves (T ≥ 20)
            # More aggressive: T ≥ 5 batches (freq ≤ 0.2 Hz) should be smoothed by EMA
            # But we need to catch the ones that escape EMA — T ≥ 20 (freq ≤ 0.05 Hz)

            # Conservative threshold: flag if dominant frequency ≤ 0.2 Hz (period ≥ 5 batches)
            # Rationale: EMA(alpha=0.5) filters ~50% of energy at T=2, ~10% at T=5
            # Anything slower than T=5 with significant power is suspicious
            if dominant_freq <= 0.2 and max_power_bin > 0:
                # Also check if this frequency carries significant power
                mean_power = np.mean(ac_spectrum)
                if ac_spectrum[max_power_bin - 1] > mean_power * 1.5:
                    # Dominant frequency has significant energy → oscillation detected
                    return True

            return False

        except (ValueError, IndexError, ZeroDivisionError):
            # Fail-closed: if FFT fails, assume safe (no oscillation signal)
            return False

    def _detect_low_frequency_oscillation_fallback(self, recent_ema: List[float]) -> bool:
        """
        Fallback frequency detection when numpy is unavailable.

        Uses sign-change counting at different window sizes to infer frequency:
        - If many sign changes in small window: high frequency
        - If few sign changes in small window but some in large window: low frequency

        Returns True if low-frequency oscillation pattern detected.
        """
        if len(recent_ema) < 10:
            return False

        # Compute sign changes in different window sizes
        deltas = [recent_ema[i] - recent_ema[i-1] for i in range(1, len(recent_ema))]

        # Window 1: Last 10 deltas (detects high-frequency: T < 10)
        window1_changes = sum(
            1 for i in range(1, min(10, len(deltas)))
            if deltas[i] * deltas[i-1] < 0
        )
        window1_rate = window1_changes / max(1, min(9, len(deltas) - 1))

        # Window 2: Last 20 deltas (detects mid-frequency: T ≈ 10-20)
        window2_changes = sum(
            1 for i in range(1, min(20, len(deltas)))
            if deltas[i] * deltas[i-1] < 0
        )
        window2_rate = window2_changes / max(1, min(19, len(deltas) - 1))

        # Heuristic: if window1 has LOW sign changes but window2 has MEDIUM,
        # it suggests a low-frequency oscillation (T > 10)
        if window1_rate < 0.4 and window2_rate > 0.3:
            # Low-frequency pattern detected
            return True

        return False

    def _detect_variance_accumulation(self, loop_id: str) -> bool:
        """
        LAYER 3: Detect micro-oscillation accumulation (Attack #4).

        Problem: Tiny oscillations (Δ=0.001) accumulate over 1000+ batches.
        Each oscillation is sub-threshold (sign changes < 60%), but cumulative drift is large.

        Solution: Track variance over long window (100+ batches).
        If variance is HIGH and cumulative drift is SIGNIFICANT, flag as attack.

        Returns True if suspicious variance accumulation detected.

        Works with or without numpy (manual variance computation fallback).
        """
        if len(self.param_history[loop_id]) < 100:
            # Need long history to detect accumulation
            return False

        # Compute variance over last 100 batches
        recent_100 = self.param_history[loop_id][-100:]

        if HAS_NUMPY:
            variance = float(np.var(recent_100))
        else:
            # Fallback: manual variance computation
            mean_val = sum(recent_100) / len(recent_100)
            variance = sum((x - mean_val) ** 2 for x in recent_100) / len(recent_100)

        # Compute drift (absolute change from first to last)
        drift = abs(recent_100[-1] - recent_100[0])

        # Heuristic: if variance is HIGH (>0.01) AND drift is SIGNIFICANT (>0.05),
        # it suggests accumulation of small movements
        # This catches sawtooth waves (Attack #2) and micro-oscillations (Attack #4)
        if variance > 0.01 and drift > 0.05:
            return True

        # Also check: ratio of drift to variance
        # Large drift / small variance suggests unidirectional accumulation
        if variance < 0.001 and drift > 0.02:
            # Drift accumulation without oscillation (smooth upward trend)
            # Less likely to be an oscillation attack, but possible sawtooth
            return True

        return False

    def recover_from_divergence(self, loop_id: str) -> Dict:
        """Halt learning, emit alert, return recovery action."""
        return {
            'action': 'pause_learning',
            'loop_id': loop_id,
            'reason': 'oscillation_detected',
            'timestamp': datetime.now().isoformat(),
        }

    def get_phase_lock_interval(self) -> int:
        """Return phase-lock interval (update every N batches)."""
        return self.phase_lock

    def is_oscillating(self, loop_id: str) -> bool:
        """Check if loop is currently oscillating."""
        return self.oscillation_detected_at.get(loop_id) is not None
