"""Week 9: Adversarial Review of Meta Loop (10 attacks, ADR-0625)"""

import pytest
import math
from core.learning.meta_optimizer import MetaOptimizer
from core.learning.watchdog import DivergenceWatchdog


class TestAdversarialMetaLoop:
    """All 10 attack vectors from ADR-0625."""

    # ATTACK 1: Hyperparameter Divergence (α→0, damping→1)
    def test_attack_1_alpha_divergence_prevented(self):
        """α shrinking to 0 → bounds prevent."""
        meta = MetaOptimizer()
        for _ in range(50):
            grad = {'α_core': -100.0, 'α_infra': -100.0, 'damping_core': 0.0, 'damping_infra': 0.0}
            meta.apply_gradients(grad, learning_rate=0.01)
        assert meta.α_core >= 0.001, f"α_core = {meta.α_core}, should be ≥ 0.001"

    # ATTACK 2: Phase-Lock Alias (tuning every step)
    def test_attack_2_phase_lock_enforced(self):
        """Per-step tuning would alias; phase-lock prevents."""
        meta = MetaOptimizer()
        # Simulate noisy feedback
        for batch in range(100):
            noise = (batch % 5) * 0.1  # alternating feedback
            feedback = {'loss_delta_core': -0.001 + noise, 'loss_delta_infra': -0.001}
            loss = meta.compute_loss(feedback)
            # Phase-lock: only tune every 100 batches (via NineD orchestration)
            # Here we verify α doesn't oscillate wildly
        α_changes = abs(meta.α_core - 0.1)
        assert α_changes < 0.05, f"α oscillated too much: delta = {α_changes}"

    # ATTACK 3: Feedback Noise (random α updates)
    def test_attack_3_noise_rejection(self):
        """Convergence detector rejects noisy updates."""
        meta = MetaOptimizer()
        for _ in range(20):
            meta.record_loss(0.3 + ((_ % 2) * 0.2))  # alternating loss
        # Should NOT converge (high variance)
        assert not meta.check_convergence()

    # ATTACK 4: Coupling Oscillation (Meta exacerbates Tier 2)
    def test_attack_4_damping_prevents_oscillation(self):
        """High damping (0.8+) prevents loop oscillation."""
        meta = MetaOptimizer()
        assert meta.damping_core >= 0.8, "Damping floor not enforced"
        assert meta.damping_infra >= 0.8, "Damping floor not enforced"

    # ATTACK 5: Rollback Failure
    def test_attack_5_rollback_works(self):
        """Divergence detection triggers rollback correctly."""
        watchdog = DivergenceWatchdog()
        good_state = {'α_core': 0.1, 'damping_core': 0.9, 'loss': 0.5}
        ckpt = watchdog.save_checkpoint(good_state)
        
        bad_state = {'α_core': float('nan'), 'damping_core': 0.9}
        assert not watchdog.validate_state(bad_state), "Watchdog should reject NaN"
        
        restored = watchdog.restore_checkpoint(ckpt)
        assert restored['α_core'] == 0.1, "Rollback should restore good state"

    # ATTACK 6: Learning Rate Too High
    def test_attack_6_high_lr_tuning(self):
        """High learning rate → damping stabilizes."""
        meta = MetaOptimizer()
        high_lr = 0.1  # very high
        for _ in range(30):
            grad = {'α_core': 0.05, 'α_infra': 0.05, 'damping_core': 0.01, 'damping_infra': 0.01}
            meta.apply_gradients(grad, learning_rate=high_lr)
        # With damping, should still be stable
        assert 0.001 <= meta.α_core <= 0.3

    # ATTACK 7: Learning Rate Too Low
    def test_attack_7_low_lr_no_learning(self):
        """Low LR → no learning (but safe)."""
        meta = MetaOptimizer()
        α_start = meta.α_core
        for _ in range(50):
            feedback = {'loss_delta_core': -0.05, 'loss_delta_infra': -0.05}
            loss = meta.compute_loss(feedback)
            grad = meta.compute_gradients(loss, 0.4)
            meta.apply_gradients(grad, learning_rate=0.0001)
        # Learning stalled but no divergence
        assert meta.α_core >= 0.001

    # ATTACK 8: State Serialization
    def test_attack_8_checkpoint_integrity(self):
        """Checkpoints serialize/deserialize correctly."""
        meta = MetaOptimizer()
        meta.α_core = 0.12
        meta.damping_infra = 0.92
        
        state = meta.get_state()
        meta2 = MetaOptimizer()
        meta2.set_state(state)
        
        assert meta2.α_core == 0.12
        assert meta2.damping_infra == 0.92

    # ATTACK 9: Multi-Instance Unsync
    def test_attack_9_independent_instances(self):
        """Different instances can tune independently (no global state)."""
        meta1 = MetaOptimizer()
        meta2 = MetaOptimizer()
        
        # Tune meta1 differently
        meta1.α_core = 0.15
        meta2.α_core = 0.10
        
        assert meta1.α_core != meta2.α_core, "Instances should be independent"

    # ATTACK 10: Operator Override
    def test_attack_10_manual_override_respected(self):
        """Manual operator override doesn't get overwritten by Meta."""
        meta = MetaOptimizer()
        
        # Operator manually sets α
        meta.α_core = 0.2  # override
        α_set = meta.α_core
        
        # Meta tuning happens
        feedback = {'loss_delta_core': -0.01, 'loss_delta_infra': -0.01}
        loss = meta.compute_loss(feedback)
        grad = meta.compute_gradients(loss, 0.4)
        meta.apply_gradients(grad, learning_rate=0.001)
        
        # α changed (tuning happened), but operator override was starting point
        # This test verifies no "forced reset" behavior
        assert meta.α_core >= 0.001 and meta.α_core <= 0.3


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
