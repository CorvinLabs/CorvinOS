"""
Divergence Watchdog (ADR-0625)

Three-layer safeguard:
1. Bounds enforcement (immutable)
2. Divergence detection (NaN, Inf, loss explosion)
3. Conservative mode (adaptive learning rate reduction)
"""

import math
import json
from typing import Dict, Any
from datetime import datetime


class DivergenceWatchdog:
    """Monitor Meta Loop state for divergence."""

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.checkpoint_count = 0
        self.last_checkpoint = None
        self.rollback_count = 0

        # Bounds (immutable)
        self.bounds = {
            'α_core': (0.001, 0.3),
            'α_infra': (0.001, 0.3),
            'damping_core': (0.8, 0.99),
            'damping_infra': (0.8, 0.99),
        }

    def validate_state(self, state: Dict[str, float]) -> bool:
        """
        Layer 2: Divergence detection.
        Returns False if divergence detected.
        """
        for param_name, (min_val, max_val) in self.bounds.items():
            value = state.get(param_name)
            if value is None:
                continue

            # Check for NaN, Inf
            if math.isnan(value) or math.isinf(value):
                print(f"ERROR: {param_name} = {value} (NaN/Inf)")
                return False

            # Check bounds
            if value < min_val or value > max_val:
                print(f"ERROR: {param_name} = {value} outside [{min_val}, {max_val}]")
                return False

        # Check loss magnitude
        loss = state.get('loss', 0.0)
        if loss > 10.0:
            print(f"ERROR: loss = {loss} (> 10x threshold)")
            return False

        return True

    def save_checkpoint(self, state: Dict[str, Any]) -> str:
        """Save state for rollback."""
        checkpoint_id = f"ckpt_{self.checkpoint_count:04d}_{int(datetime.now().timestamp())}"
        self.last_checkpoint = {
            'id': checkpoint_id,
            'state': state.copy(),
            'timestamp': datetime.now().isoformat(),
        }
        self.checkpoint_count += 1
        return checkpoint_id

    def restore_checkpoint(self, checkpoint_id: str) -> Dict[str, Any]:
        """Restore from checkpoint."""
        if self.last_checkpoint and self.last_checkpoint['id'] == checkpoint_id:
            self.rollback_count += 1
            return self.last_checkpoint['state'].copy()
        return None

    def on_divergence(self, reason: str):
        """Handle divergence event."""
        print(f"⚠️  DIVERGENCE: {reason}")
        print(f"   Rollback #{self.rollback_count + 1}")
        if self.last_checkpoint:
            print(f"   Restoring checkpoint: {self.last_checkpoint['id']}")
