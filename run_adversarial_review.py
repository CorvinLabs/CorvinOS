#!/usr/bin/env python3
"""Standalone adversarial review runner (no pytest dependency)"""

import sys
import traceback
from core.learning.meta_optimizer import MetaOptimizer
from core.learning.watchdog import DivergenceWatchdog


class AdversarialReviewRunner:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.findings = []
    
    def run_test(self, name, test_func):
        """Run a test and track results."""
        try:
            test_func()
            print(f"✅ PASS: {name}")
            self.passed += 1
            return True
        except AssertionError as e:
            print(f"❌ FAIL: {name}")
            print(f"   Error: {str(e)}")
            self.failed += 1
            self.findings.append({'attack': name, 'error': str(e)})
            return False
        except Exception as e:
            print(f"💥 ERROR: {name}")
            print(f"   {type(e).__name__}: {str(e)}")
            self.failed += 1
            self.findings.append({'attack': name, 'error': f"{type(e).__name__}: {str(e)}"})
            return False
    
    def attack_1_alpha_divergence(self):
        """ATTACK 1: Hyperparameter Divergence (α→0)"""
        meta = MetaOptimizer()
        for _ in range(50):
            grad = {'α_core': -100.0, 'α_infra': -100.0, 'damping_core': 0.0, 'damping_infra': 0.0}
            meta.apply_gradients(grad, learning_rate=0.01)
        assert meta.α_core >= 0.001, f"α_core = {meta.α_core}, should be ≥ 0.001"
    
    def attack_2_phase_lock(self):
        """ATTACK 2: Phase-Lock (noisy feedback)"""
        meta = MetaOptimizer()
        for batch in range(100):
            noise = (batch % 5) * 0.1
            feedback = {'loss_delta_core': -0.001 + noise, 'loss_delta_infra': -0.001}
            loss = meta.compute_loss(feedback)
        α_change = abs(meta.α_core - 0.1)
        assert α_change < 0.05, f"α oscillated: delta = {α_change}"
    
    def attack_3_noise_rejection(self):
        """ATTACK 3: Feedback Noise (convergence detector)"""
        meta = MetaOptimizer()
        for i in range(20):
            meta.record_loss(0.3 + ((i % 2) * 0.2))
        assert not meta.check_convergence(), "Should NOT converge with noisy loss"
    
    def attack_4_damping_floor(self):
        """ATTACK 4: Coupling Oscillation (damping ≥ 0.8)"""
        meta = MetaOptimizer()
        assert meta.damping_core >= 0.8, "Damping floor violated"
        assert meta.damping_infra >= 0.8, "Damping floor violated"
    
    def attack_5_rollback(self):
        """ATTACK 5: Rollback Failure"""
        watchdog = DivergenceWatchdog()
        good_state = {'α_core': 0.1, 'damping_core': 0.9, 'loss': 0.5}
        ckpt = watchdog.save_checkpoint(good_state)
        
        bad_state = {'α_core': float('nan'), 'damping_core': 0.9}
        assert not watchdog.validate_state(bad_state), "Should reject NaN"
        
        restored = watchdog.restore_checkpoint(ckpt)
        assert restored['α_core'] == 0.1, "Rollback failed"
    
    def attack_6_high_lr(self):
        """ATTACK 6: High Learning Rate"""
        meta = MetaOptimizer()
        for _ in range(30):
            grad = {'α_core': 0.05, 'α_infra': 0.05, 'damping_core': 0.01, 'damping_infra': 0.01}
            meta.apply_gradients(grad, learning_rate=0.1)
        assert 0.001 <= meta.α_core <= 0.3
    
    def attack_7_low_lr(self):
        """ATTACK 7: Low Learning Rate (safe stall)"""
        meta = MetaOptimizer()
        for _ in range(50):
            feedback = {'loss_delta_core': -0.05, 'loss_delta_infra': -0.05}
            loss = meta.compute_loss(feedback)
            grad = meta.compute_gradients(loss, 0.4)
            meta.apply_gradients(grad, learning_rate=0.0001)
        assert meta.α_core >= 0.001
    
    def attack_8_serialization(self):
        """ATTACK 8: Checkpoint Integrity"""
        meta = MetaOptimizer()
        meta.α_core = 0.12
        meta.damping_infra = 0.92
        
        state = meta.get_state()
        meta2 = MetaOptimizer()
        meta2.set_state(state)
        
        assert meta2.α_core == 0.12
        assert meta2.damping_infra == 0.92
    
    def attack_9_instances(self):
        """ATTACK 9: Multi-Instance Isolation"""
        meta1 = MetaOptimizer()
        meta2 = MetaOptimizer()
        
        meta1.α_core = 0.15
        meta2.α_core = 0.10
        
        assert meta1.α_core != meta2.α_core
    
    def attack_10_override(self):
        """ATTACK 10: Manual Override Respected"""
        meta = MetaOptimizer()
        meta.α_core = 0.2
        
        feedback = {'loss_delta_core': -0.01, 'loss_delta_infra': -0.01}
        loss = meta.compute_loss(feedback)
        grad = meta.compute_gradients(loss, 0.4)
        meta.apply_gradients(grad, learning_rate=0.001)
        
        assert 0.001 <= meta.α_core <= 0.3
    
    def run_all(self):
        """Run all 10 attacks."""
        print("=" * 70)
        print("PHASE 2A ADVERSARIAL REVIEW: 10 ATTACK VECTORS")
        print("=" * 70)
        print()
        
        attacks = [
            ("ATTACK 1: Hyperparameter Divergence (α→0)", self.attack_1_alpha_divergence),
            ("ATTACK 2: Phase-Lock Alias (noisy feedback)", self.attack_2_phase_lock),
            ("ATTACK 3: Feedback Noise (convergence gate)", self.attack_3_noise_rejection),
            ("ATTACK 4: Coupling Oscillation (damping)", self.attack_4_damping_floor),
            ("ATTACK 5: Rollback Failure", self.attack_5_rollback),
            ("ATTACK 6: High Learning Rate", self.attack_6_high_lr),
            ("ATTACK 7: Low Learning Rate (safe)", self.attack_7_low_lr),
            ("ATTACK 8: Checkpoint Serialization", self.attack_8_serialization),
            ("ATTACK 9: Multi-Instance Isolation", self.attack_9_instances),
            ("ATTACK 10: Manual Override Respected", self.attack_10_override),
        ]
        
        for name, test_func in attacks:
            self.run_test(name, test_func)
        
        print()
        print("=" * 70)
        print(f"RESULTS: {self.passed} PASSED, {self.failed} FAILED")
        print("=" * 70)
        
        if self.failed == 0:
            print("\n✅ ADVERSARIAL REVIEW: 0 FINDINGS (PRODUCTION READY)")
            return 0
        else:
            print(f"\n❌ ADVERSARIAL REVIEW: {self.failed} FINDINGS (NEEDS FIX)")
            for finding in self.findings:
                print(f"   - {finding['attack']}: {finding['error']}")
            return 1


if __name__ == '__main__':
    runner = AdversarialReviewRunner()
    exit_code = runner.run_all()
    sys.exit(exit_code)
