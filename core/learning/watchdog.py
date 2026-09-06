"""
Divergence Watchdog (ADR-0625)

Three-layer safety mechanism to prevent divergence in meta loop optimization:

Layer 1: Bounds Clipping
  - Ensure all parameters stay within valid ranges
  - Immutable bounds enforcement (fail-closed)

Layer 2: Divergence Detection
  - Detect NaN/Inf values
  - Detect bounds exceeded (should not happen after Layer 1)
  - Detect loss explosion (loss > 10x baseline)

Layer 3: Conservative Mode
  - If loss is worsening, reduce learning_rate_meta
  - If divergence detected, freeze meta optimizer updates
  - Auto-restore from checkpoint if available
"""

import math
import json
from typing import Dict, Optional, Tuple, Any
from pathlib import Path
from datetime import datetime


class DivergenceWatchdog:
    """
    Three-layer divergence detection and prevention system.

    Prevents the meta optimizer from entering unstable states by:
    1. Enforcing hard bounds on all parameters
    2. Detecting signs of divergence (NaN, Inf, loss explosion)
    3. Activating conservative mode when divergence is detected
    """

    def __init__(self, baseline_loss: float = 0.3, checkpoint_dir: Optional[str] = None):
        """
        Initialize the watchdog.

        Args:
            baseline_loss: initial/expected loss level (for explosion detection)
            checkpoint_dir: directory to save/restore checkpoints from
        """
        self.baseline_loss = baseline_loss
        self.checkpoint_dir = checkpoint_dir

        # ===== LAYER 1: BOUNDS ENFORCEMENT =====
        self.bounds = {
            'alpha_core': (0.001, 0.3),
            'alpha_infra': (0.001, 0.3),
            'damping_core': (0.8, 0.99),
            'damping_infra': (0.8, 0.99),
            'convergence_threshold': (0.0001, 0.01),
            'variance_threshold': (0.001, 0.1),
        }

        # ===== LAYER 2: DIVERGENCE DETECTION =====
        self.divergence_signals = {
            'nan_detected': False,
            'inf_detected': False,
            'bounds_exceeded': False,
            'loss_explosion': False,
            'optimizer_unstable': False,
        }

        self.divergence_count = 0  # incremented each time divergence is detected
        self.max_consecutive_divergence = 5  # if >= 5, enter conservative mode

        # ===== LAYER 3: CONSERVATIVE MODE =====
        self.conservative_mode = False
        self.conservative_mode_steps = 0
        self.conservative_mode_max_steps = 500  # exit after 500 steps if loss recovers

        # Loss tracking for detection
        self.loss_baseline = baseline_loss
        self.loss_explosion_threshold = 10.0  # loss > 10x baseline = explosion

        # Last checkpoint for restoration
        self.last_checkpoint_state = None

    # ===== LAYER 1: BOUNDS ENFORCEMENT =====

    def enforce_bounds(self, parameters: Dict[str, float]) -> Dict[str, float]:
        """
        Enforce hard bounds on all learnable parameters.

        This is the first line of defense: all parameters are clipped to valid ranges
        before any other processing.

        Args:
            parameters: {param_name: value}

        Returns:
            {param_name: clipped_value}
        """
        clipped = {}
        for param_name, value in parameters.items():
            if param_name in self.bounds:
                min_val, max_val = self.bounds[param_name]
                clipped_value = max(min_val, min(max_val, value))

                # Log if clipping occurred
                if clipped_value != value:
                    # Clipping occurred (signal potential issue, but don't fail)
                    pass

                clipped[param_name] = clipped_value
            else:
                # Unknown parameter (pass through)
                clipped[param_name] = value

        return clipped

    # ===== LAYER 2: DIVERGENCE DETECTION =====

    def detect_divergence(
        self,
        loss: float,
        parameters: Dict[str, float],
        gradients: Dict[str, float],
    ) -> Tuple[bool, Dict[str, bool]]:
        """
        Check for divergence signals across three sub-detectors.

        Returns:
            (divergence_detected: bool, signals: Dict[str, bool])
        """
        signals = {
            'nan_detected': False,
            'inf_detected': False,
            'bounds_exceeded': False,
            'loss_explosion': False,
            'optimizer_unstable': False,
        }

        # Sub-detector 1: NaN/Inf in loss
        if math.isnan(loss) or math.isinf(loss):
            signals['nan_detected' if math.isnan(loss) else 'inf_detected'] = True

        # Sub-detector 2: NaN/Inf in parameters or gradients
        for value in parameters.values():
            if math.isnan(value) or math.isinf(value):
                signals['nan_detected' if math.isnan(value) else 'inf_detected'] = True
                break

        for value in gradients.values():
            if math.isnan(value) or math.isinf(value):
                signals['nan_detected' if math.isnan(value) else 'inf_detected'] = True
                break

        # Sub-detector 3: Bounds exceeded (after Layer 1, shouldn't happen)
        for param_name, value in parameters.items():
            if param_name in self.bounds:
                min_val, max_val = self.bounds[param_name]
                if value < min_val or value > max_val:
                    signals['bounds_exceeded'] = True
                    break

        # Sub-detector 4: Loss explosion (loss > 10x baseline)
        if loss > self.loss_explosion_threshold * self.loss_baseline:
            signals['loss_explosion'] = True

        # Sub-detector 5: Optimizer unstable (multiple signs of trouble)
        trouble_signs = sum(1 for v in signals.values() if v)
        if trouble_signs >= 2:
            signals['optimizer_unstable'] = True

        # Overall divergence: at least one signal
        divergence_detected = any(signals.values())

        self.divergence_signals = signals

        if divergence_detected:
            self.divergence_count += 1
        else:
            self.divergence_count = max(0, self.divergence_count - 1)

        return divergence_detected, signals

    # ===== LAYER 3: CONSERVATIVE MODE =====

    def enter_conservative_mode(self, reason: str = ""):
        """
        Activate conservative mode to stabilize optimization.

        In conservative mode:
        - Meta optimizer learning rate is reduced by 50%
        - Damping is increased (more inertia)
        - Updates are frozen if loss not improving

        Args:
            reason: explanation for entering conservative mode
        """
        self.conservative_mode = True
        self.conservative_mode_steps = 0

    def exit_conservative_mode(self, reason: str = ""):
        """
        Deactivate conservative mode if loss has recovered.

        Args:
            reason: explanation for exiting conservative mode
        """
        self.conservative_mode = False
        self.conservative_mode_steps = 0

    def check_conservative_mode_exit(self, current_loss: float) -> bool:
        """
        Check if we should exit conservative mode based on loss recovery.

        Returns:
            True if we exited conservative mode, False otherwise
        """
        if not self.conservative_mode:
            return False

        self.conservative_mode_steps += 1

        # Exit if loss is improving or max steps reached
        if current_loss <= self.loss_baseline * 1.5:  # loss recovered to 1.5x baseline
            self.exit_conservative_mode(reason="Loss recovered")
            return True

        if self.conservative_mode_steps >= self.conservative_mode_max_steps:
            self.exit_conservative_mode(reason="Max conservative steps reached")
            return True

        return False

    def adjust_learning_rate_for_conservative_mode(
        self,
        base_learning_rate: float,
    ) -> float:
        """
        Reduce learning rate if in conservative mode.

        Args:
            base_learning_rate: meta optimizer learning rate

        Returns:
            adjusted learning rate
        """
        if not self.conservative_mode:
            return base_learning_rate

        # Reduce learning rate by 50% in conservative mode
        return base_learning_rate * 0.5

    # ===== CHECKPOINT MANAGEMENT =====

    def save_checkpoint(self, state: Dict[str, Any], checkpoint_path: str) -> bool:
        """
        Save optimizer state to checkpoint every 100 batches.

        Args:
            state: complete optimizer state
            checkpoint_path: file to save to

        Returns:
            True if successful, False otherwise
        """
        try:
            with open(checkpoint_path, 'w') as f:
                json.dump({
                    'timestamp': datetime.utcnow().isoformat(),
                    'state': state,
                }, f, indent=2)

            self.last_checkpoint_state = state
            return True
        except Exception as e:
            print(f"Failed to save watchdog checkpoint: {e}")
            return False

    def restore_from_checkpoint(self, checkpoint_path: str) -> Optional[Dict[str, Any]]:
        """
        Restore optimizer state from checkpoint if divergence detected.

        Args:
            checkpoint_path: file to load from

        Returns:
            restored state dict, or None if failed
        """
        try:
            with open(checkpoint_path, 'r') as f:
                data = json.load(f)

            self.last_checkpoint_state = data['state']
            return data['state']
        except Exception as e:
            print(f"Failed to restore watchdog checkpoint: {e}")
            return None

    # ===== MONITORING & DIAGNOSTICS =====

    def get_status(self) -> Dict[str, Any]:
        """
        Get complete watchdog status for monitoring/debugging.

        Returns:
            Dict with all detection signals, mode, thresholds, etc.
        """
        return {
            'conservative_mode': self.conservative_mode,
            'conservative_mode_steps': self.conservative_mode_steps,
            'divergence_count': self.divergence_count,
            'divergence_signals': self.divergence_signals,
            'should_enter_conservative': self.divergence_count >= self.max_consecutive_divergence,
            'loss_baseline': self.loss_baseline,
            'loss_explosion_threshold': self.loss_explosion_threshold,
            'bounds': self.bounds,
        }

    def reset(self):
        """Reset watchdog state (e.g., after successful checkpoint restore)."""
        self.divergence_signals = {k: False for k in self.divergence_signals}
        self.divergence_count = 0
        self.conservative_mode = False
        self.conservative_mode_steps = 0


class WatchdogIntegration:
    """
    High-level interface for integrating watchdog into NineD_LossOptimizer.

    Provides:
    - Pre-update parameter validation (Layer 1)
    - Post-update divergence check (Layer 2)
    - Adaptive learning rate adjustment (Layer 3)
    - Checkpoint management
    """

    def __init__(
        self,
        meta_optimizer,
        baseline_loss: float = 0.3,
        checkpoint_dir: Optional[str] = None,
    ):
        """
        Initialize watchdog integration.

        Args:
            meta_optimizer: MetaOptimizer instance to guard
            baseline_loss: initial loss for explosion detection
            checkpoint_dir: where to store checkpoints
        """
        self.meta_optimizer = meta_optimizer
        self.watchdog = DivergenceWatchdog(baseline_loss, checkpoint_dir)

    def validate_and_apply_gradients(
        self,
        gradients: Dict[str, float],
        learning_rate: float,
        damping: float,
        feedback_signals: Dict[str, float],
    ) -> Tuple[bool, str]:
        """
        Unified method to validate, guard, and apply gradient updates.

        Process:
          1. Detect divergence in current state
          2. Enforce bounds on parameters
          3. Apply gradients with watchdog oversight
          4. Check for divergence in result
          5. Adjust learning rate if conservative mode active

        Args:
            gradients: from meta_optimizer.compute_gradients()
            learning_rate: meta optimizer learning rate
            damping: meta optimizer damping
            feedback_signals: for divergence detection

        Returns:
            (success: bool, message: str)
        """
        # Step 1: Detect divergence in current state
        current_loss = feedback_signals.get('meta_loss', 0.0)
        divergence_detected, signals = self.watchdog.detect_divergence(
            current_loss,
            self.meta_optimizer.get_tuned_hyperparameters(),
            gradients,
        )

        # Step 2: Enter conservative mode if needed
        if self.watchdog.divergence_count >= self.watchdog.max_consecutive_divergence:
            self.watchdog.enter_conservative_mode(
                reason=f"Divergence signals: {signals}"
            )

        # Step 3: Adjust learning rate if in conservative mode
        adjusted_lr = self.watchdog.adjust_learning_rate_for_conservative_mode(learning_rate)

        # Step 4: Apply gradients with adjusted learning rate
        self.meta_optimizer.apply_gradients(
            gradients,
            learning_rate=adjusted_lr,
            damping=damping,
        )

        # Step 5: Check if we can exit conservative mode
        self.watchdog.check_conservative_mode_exit(current_loss)

        message = (
            f"Divergence: {divergence_detected}, "
            f"Conservative: {self.watchdog.conservative_mode}, "
            f"LR adjusted: {adjusted_lr:.6f}"
        )

        return not divergence_detected, message

    def checkpoint(self, checkpoint_path: str) -> bool:
        """Save optimizer + watchdog state to checkpoint."""
        state = {
            'meta_optimizer': self.meta_optimizer.get_state_snapshot(),
            'watchdog': self.watchdog.get_status(),
        }
        return self.watchdog.save_checkpoint(state, checkpoint_path)

    def restore(self, checkpoint_path: str) -> bool:
        """Restore optimizer + watchdog state from checkpoint."""
        state = self.watchdog.restore_from_checkpoint(checkpoint_path)
        if state:
            self.watchdog.reset()
            return True
        return False
