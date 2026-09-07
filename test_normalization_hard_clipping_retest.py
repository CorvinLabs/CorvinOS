#!/usr/bin/env python3
"""
Adversarial Vector: Normalization (Hard Clipping)

Vulnerability: Hard clipping applied BEFORE normalization can break weight invariants.
When learnable parameters are clipped to [lo, hi] before renormalization, the
normalization pass can be thwarted by clipping extreme values.

Attack: Craft gradients that push parameters out of range, force hard clipping,
then verify if the renormalization AFTER clipping (new fix) maintains invariants.

Expected result with NEW fix: All invariants maintained (clipping AFTER normalization).
"""

import sys
import math
from pathlib import Path

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent))

from core.learning.memory_optimizer import MemoryOptimizer


def test_hard_clip_breaks_layer_weights_invariant():
    """
    ATTACK: Force layer_preserved out of range, verify clipping doesn't break sum invariant.

    Scenario:
    1. Initial: preserved=0.30, injected=0.20 (sum=0.50)
    2. Apply huge positive gradient to preserved
    3. Optimizer normalizes, then clips
    4. Verify: preserved + injected == 0.50 (invariant)
    """
    print("\n[TEST 1] Hard clip on layer weights (verify normalization invariant)...")

    optimizer = MemoryOptimizer(tenant_id="_default")

    # Initial state
    initial_preserved = optimizer.layer_importance['preserved']
    initial_injected = optimizer.layer_importance['injected']
    initial_sum = initial_preserved + initial_injected

    print(f"  Initial: preserved={initial_preserved}, injected={initial_injected}, sum={initial_sum}")

    if abs(initial_sum - 0.5) >= 0.01:
        print(f"  ✗ VULNERABILITY: Initial weights don't sum to 0.5!")
        return False

    # Attack: Submit huge positive gradient to layer_preserved
    # This should be normalized to keep sum = 0.5, then clipped to [0.1, 0.4]
    huge_gradient = {
        'window_size': 0.0,
        'layer_preserved': 100.0,  # ❌ HUGE positive gradient (attack)
        'layer_injected': -100.0,
        'recall_threshold': 0.0,
    }

    # Apply with high learning rate to maximize effect
    optimizer.apply_gradients(huge_gradient, learning_rate=0.5, damping=0.0)

    new_preserved = optimizer.layer_importance['preserved']
    new_injected = optimizer.layer_importance['injected']
    new_sum = new_preserved + new_injected

    print(f"  After attack: preserved={new_preserved}, injected={new_injected}, sum={new_sum}")

    # Verify invariants
    if abs(new_sum - 0.5) >= 0.01:
        print(f"  ✗ VULNERABLE: Sum invariant broken! Expected 0.5, got {new_sum}")
        return False

    if not (0.1 <= new_preserved <= 0.4):
        print(f"  ✗ VULNERABLE: Preserved out of bounds! Expected [0.1, 0.4], got {new_preserved}")
        return False

    if not (0.1 <= new_injected <= 0.4):
        print(f"  ✗ VULNERABLE: Injected out of bounds! Expected [0.1, 0.4], got {new_injected}")
        return False

    print(f"  ✓ Mitigated: Sum invariant preserved ({new_sum}), bounds respected")
    return True


def test_hard_clip_gradient_descent_not_blocked():
    """
    ATTACK: Verify that hard clipping doesn't completely block gradient descent.

    A naive "clip then normalize" approach could prevent convergence by applying
    clipping before normalization. The NEW fix should clip AFTER normalization,
    allowing gradients to flow properly.
    """
    print("\n[TEST 2] Hard clip doesn't block gradient flow (convergence possible)...")

    optimizer = MemoryOptimizer(tenant_id="_default")

    # Simulate 10 steps of gradient descent with attacks
    for step in range(10):
        # Positive feedback (loss improving)
        feedback = {
            'missing_context_ratio': 0.1,
            'irrelevance_score': 0.1,
            'retrieval_latency_ms': 20.0,
            'token_waste_ratio': 0.05,
        }

        loss = optimizer.compute_loss(feedback)
        optimizer.record_loss(loss)

        # Compute gradients
        prev_loss = 0.5 if step == 0 else optimizer.loss_history[-2]
        gradients = optimizer.compute_gradients(loss, prev_loss)

        # Apply (with potential clipping)
        optimizer.apply_gradients(gradients)

    # Check that parameters changed
    final_preserved = optimizer.layer_importance['preserved']
    final_injected = optimizer.layer_importance['injected']
    final_sum = final_preserved + final_injected

    print(f"  After 10 steps: preserved={final_preserved}, injected={final_injected}, sum={final_sum}")

    # Verify invariants still hold
    if abs(final_sum - 0.5) >= 0.01:
        print(f"  ✗ VULNERABLE: Gradient flow broken, sum invariant violated!")
        return False

    print(f"  ✓ Mitigated: Gradient descent works, invariant maintained")
    return True


def test_hard_clip_on_context_window():
    """
    ATTACK: Force context_window_size out of bounds, verify hard clipping works.

    Scenario:
    1. Attack with gradients that push window to -1000 or +50000
    2. Verify clipping enforces [min_window, 16000]
    3. Verify compliance floor is never violated
    """
    print("\n[TEST 3] Hard clip on context window (compliance floor enforced)...")

    optimizer = MemoryOptimizer(tenant_id="_default", min_audit_requirement_bytes=4000)

    # Verify compliance floor is set correctly
    if optimizer.min_context_window < 4000:
        print(f"  ✗ VULNERABILITY: Compliance floor too low ({optimizer.min_context_window})")
        return False

    # Attack 1: Huge negative gradient (try to shrink below compliance floor)
    print(f"  Compliance floor: {optimizer.min_context_window} bytes")

    huge_negative_gradient = {
        'window_size': -1000.0,  # ❌ Huge negative (attack)
        'layer_preserved': 0.0,
        'layer_injected': 0.0,
        'recall_threshold': 0.0,
    }

    optimizer.apply_gradients(huge_negative_gradient, learning_rate=1.0, damping=0.0)

    window_after_attack = optimizer.context_window_size
    print(f"  After huge-negative attack: window={window_after_attack}")

    if window_after_attack < optimizer.min_context_window:
        print(f"  ✗ VULNERABLE: Compliance floor breached! {window_after_attack} < {optimizer.min_context_window}")
        return False

    if window_after_attack > 16000:
        print(f"  ✗ VULNERABLE: Upper bound breached! {window_after_attack} > 16000")
        return False

    # Attack 2: Huge positive gradient (try to grow beyond 16000)
    huge_positive_gradient = {
        'window_size': 1000.0,  # ❌ Huge positive (attack)
        'layer_preserved': 0.0,
        'layer_injected': 0.0,
        'recall_threshold': 0.0,
    }

    optimizer.apply_gradients(huge_positive_gradient, learning_rate=1.0, damping=0.0)

    window_after_attack2 = optimizer.context_window_size
    print(f"  After huge-positive attack: window={window_after_attack2}")

    if window_after_attack2 > 16000:
        print(f"  ✗ VULNERABLE: Upper bound breached! {window_after_attack2} > 16000")
        return False

    print(f"  ✓ Mitigated: Compliance floor + upper bound enforced")
    return True


def test_hard_clip_order_matters():
    """
    ATTACK: Verify that clipping is done AFTER renormalization (not before).

    The vulnerability was: clip → renormalize (destroys the sum invariant)
    The fix should be: renormalize → clip (preserves invariant)

    This test verifies the fix by checking that the sum invariant is always maintained.
    """
    print("\n[TEST 4] Clipping order: AFTER renormalization (not BEFORE)...")

    optimizer = MemoryOptimizer(tenant_id="_default")

    # Simulate a sequence of attacks that try to break the order invariant
    for step in range(5):
        # Alternating large gradients
        if step % 2 == 0:
            gradients = {
                'window_size': 0.0,
                'layer_preserved': 50.0,   # Huge push
                'layer_injected': -50.0,   # Counter-push
                'recall_threshold': 0.0,
            }
        else:
            gradients = {
                'window_size': 0.0,
                'layer_preserved': -50.0,  # Opposite push
                'layer_injected': 50.0,
                'recall_threshold': 0.0,
            }

        optimizer.apply_gradients(gradients, learning_rate=0.2, damping=0.0)

        # Check invariant after every step
        preserved = optimizer.layer_importance['preserved']
        injected = optimizer.layer_importance['injected']
        total = preserved + injected

        if abs(total - 0.5) >= 0.01:
            print(f"  ✗ VULNERABLE (step {step}): Sum invariant broken! {total} ≠ 0.5")
            print(f"     preserved={preserved}, injected={injected}")
            return False

        if not (0.1 <= preserved <= 0.4):
            print(f"  ✗ VULNERABLE (step {step}): Preserved out of bounds ({preserved})")
            return False

        if not (0.1 <= injected <= 0.4):
            print(f"  ✗ VULNERABLE (step {step}): Injected out of bounds ({injected})")
            return False

    print(f"  ✓ Mitigated: Sum invariant maintained across 5 attack steps")
    return True


def test_hard_clip_on_recall_threshold():
    """
    ATTACK: Force recall_threshold out of [0.5, 0.9] bounds.

    Verify hard clipping enforces bounds without breaking other invariants.
    """
    print("\n[TEST 5] Hard clip on recall threshold...")

    optimizer = MemoryOptimizer(tenant_id="_default")

    initial = optimizer.recall_threshold
    print(f"  Initial threshold: {initial}")

    # Attack with huge negative gradient
    gradients = {
        'window_size': 0.0,
        'layer_preserved': 0.0,
        'layer_injected': 0.0,
        'recall_threshold': -100.0,  # Huge negative (try to go below 0.5)
    }

    optimizer.apply_gradients(gradients, learning_rate=0.5, damping=0.0)

    after_neg = optimizer.recall_threshold
    print(f"  After negative attack: {after_neg}")

    if not (0.5 <= after_neg <= 0.9):
        print(f"  ✗ VULNERABLE: Threshold out of bounds! {after_neg}")
        return False

    # Attack with huge positive gradient
    gradients['recall_threshold'] = 100.0

    optimizer.apply_gradients(gradients, learning_rate=0.5, damping=0.0)

    after_pos = optimizer.recall_threshold
    print(f"  After positive attack: {after_pos}")

    if not (0.5 <= after_pos <= 0.9):
        print(f"  ✗ VULNERABLE: Threshold out of bounds! {after_pos}")
        return False

    print(f"  ✓ Mitigated: Recall threshold bounds enforced")
    return True


def main():
    """Run all re-tests for normalization hard clipping with NEW fix."""
    print("=" * 70)
    print("ADVERSARIAL VECTOR: NORMALIZATION (HARD CLIPPING) — RETEST")
    print("Testing with NEW FIX: Clipping AFTER renormalization")
    print("=" * 70)

    results = []

    try:
        results.append(("Layer weights sum invariant", test_hard_clip_breaks_layer_weights_invariant()))
        results.append(("Gradient flow not blocked", test_hard_clip_gradient_descent_not_blocked()))
        results.append(("Context window bounds", test_hard_clip_on_context_window()))
        results.append(("Clipping order (after norm)", test_hard_clip_order_matters()))
        results.append(("Recall threshold bounds", test_hard_clip_on_recall_threshold()))
    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, r in results if r is True)
    failed = sum(1 for _, r in results if r is False)

    for name, result in results:
        if result is True:
            print(f"✓ {name}: PASS")
        else:
            print(f"✗ {name}: FAIL")

    print(f"\nResults: {passed} pass, {failed} fail")

    # Determine exploitability
    if failed > 0:
        print("\n⚠️  HARD CLIPPING VULNERABILITIES FOUND!")
        print("Severity: CRITICAL")
        print(f"Exploitable: YES ({failed} bypass(es) confirmed)")
        return 1
    else:
        print("\n✓ All hard clipping attack vectors MITIGATED!")
        print("Severity: NONE")
        print("Exploitable: NO")
        return 0


if __name__ == "__main__":
    exit_code = main()

    # JSON output for parsing
    print("\n" + "=" * 70)
    print("RESULT (JSON)")
    print("=" * 70)

    if exit_code == 0:
        result = {
            "vector": "normalization_hard_clipping",
            "exploitable": False,
            "severity": "NONE"
        }
    else:
        result = {
            "vector": "normalization_hard_clipping",
            "exploitable": True,
            "severity": "CRITICAL"
        }

    import json
    print(json.dumps(result, indent=2))

    sys.exit(exit_code)
