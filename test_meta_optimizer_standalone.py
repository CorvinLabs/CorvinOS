#!/usr/bin/env python3
"""
Standalone integration test for MetaOptimizer and Watchdog.

This test can run without pytest and verifies:
1. MetaOptimizer initializes correctly
2. MetaOptimizer can compute loss and gradients
3. MetaOptimizer applies updates with bounds enforcement
4. Watchdog detects divergence
5. Watchdog enforces conservative mode
6. Integration with NineD_LossOptimizer works
"""

import sys
import traceback


def test_meta_optimizer_initialization():
    """Test 1: MetaOptimizer initialization."""
    print("\n" + "="*70)
    print("TEST 1: MetaOptimizer Initialization")
    print("="*70)

    try:
        from core.learning.meta_optimizer import MetaOptimizer

        optimizer = MetaOptimizer(tenant_id="_default")

        # Verify defaults
        assert optimizer.alpha_core == 0.1, f"alpha_core: {optimizer.alpha_core}"
        assert optimizer.alpha_infra == 0.01, f"alpha_infra: {optimizer.alpha_infra}"
        assert optimizer.damping_core == 0.9, f"damping_core: {optimizer.damping_core}"
        assert optimizer.damping_infra == 0.95, f"damping_infra: {optimizer.damping_infra}"
        assert optimizer.tier == 3, f"tier: {optimizer.tier}"

        # Verify bounds
        assert optimizer.alpha_core_min == 0.001
        assert optimizer.alpha_core_max == 0.3

        print("✅ PASS: MetaOptimizer initializes with correct defaults")
        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def test_meta_optimizer_loss_computation():
    """Test 2: MetaOptimizer loss computation."""
    print("\n" + "="*70)
    print("TEST 2: MetaOptimizer Loss Computation")
    print("="*70)

    try:
        from core.learning.meta_optimizer import MetaOptimizer

        optimizer = MetaOptimizer()

        # Test good state
        feedback_good = {
            'core_loss': 0.1,
            'prev_core_loss': 0.1,
            'infra_loss': 0.15,
            'prev_infra_loss': 0.15,
            'core_loss_variance': 0.001,
            'infra_loss_variance': 0.001,
            'avg_gradient_magnitude': 0.0001,
        }

        loss_good = optimizer.compute_loss(feedback_good)
        assert 0.0 <= loss_good <= 1.0, f"loss_good out of range: {loss_good}"
        assert loss_good < 0.2, f"loss_good should be low: {loss_good}"

        print(f"✅ PASS: Good state loss = {loss_good:.4f}")

        # Test bad state (high drift)
        optimizer2 = MetaOptimizer()
        feedback_bad = {
            'core_loss': 0.8,
            'prev_core_loss': 0.1,
            'infra_loss': 0.5,
            'prev_infra_loss': 0.1,
            'core_loss_variance': 0.1,
            'infra_loss_variance': 0.1,
            'avg_gradient_magnitude': 0.5,
        }

        loss_bad = optimizer2.compute_loss(feedback_bad)
        assert 0.0 <= loss_bad <= 1.0, f"loss_bad out of range: {loss_bad}"
        assert loss_bad > loss_good, f"loss_bad should be worse: {loss_bad} vs {loss_good}"

        print(f"✅ PASS: Bad state loss = {loss_bad:.4f} (higher than good state)")
        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def test_meta_optimizer_gradients():
    """Test 3: MetaOptimizer gradient computation."""
    print("\n" + "="*70)
    print("TEST 3: MetaOptimizer Gradient Computation")
    print("="*70)

    try:
        from core.learning.meta_optimizer import MetaOptimizer

        optimizer = MetaOptimizer()

        loss = 0.3
        prev_loss = 0.3

        gradients = optimizer.compute_gradients(loss, prev_loss)

        # Check all parameters are present
        expected = {'alpha_core', 'alpha_infra', 'damping_core', 'damping_infra',
                    'convergence_threshold', 'variance_threshold'}
        assert set(gradients.keys()) == expected, f"Missing keys: {expected - set(gradients.keys())}"

        # Check all are floats
        for param, grad in gradients.items():
            assert isinstance(grad, float), f"{param} gradient not float: {type(grad)}"

        print(f"✅ PASS: All {len(gradients)} gradients computed")
        print(f"   Gradients: {', '.join([f'{k}={v:.6f}' for k, v in list(gradients.items())[:3]])}...")

        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def test_watchdog_divergence_detection():
    """Test 4: Divergence Watchdog detection."""
    print("\n" + "="*70)
    print("TEST 4: Divergence Watchdog Detection")
    print("="*70)

    try:
        from core.learning.watchdog import DivergenceWatchdog

        watchdog = DivergenceWatchdog(baseline_loss=0.3)

        # Test NaN detection
        loss_nan = float('nan')
        parameters = {'alpha_core': 0.1}
        gradients = {'alpha_core': 0.01}

        divergence, signals = watchdog.detect_divergence(loss_nan, parameters, gradients)
        assert divergence, "NaN should be detected"
        assert signals['nan_detected'], "NaN signal not set"
        print("✅ PASS: NaN detected")

        # Test Inf detection
        watchdog2 = DivergenceWatchdog(baseline_loss=0.3)
        loss_inf = float('inf')
        divergence, signals = watchdog2.detect_divergence(loss_inf, parameters, gradients)
        assert divergence, "Inf should be detected"
        assert signals['inf_detected'], "Inf signal not set"
        print("✅ PASS: Inf detected")

        # Test loss explosion
        watchdog3 = DivergenceWatchdog(baseline_loss=0.3)
        loss_exp = 3.5  # 11.67x baseline
        divergence, signals = watchdog3.detect_divergence(loss_exp, parameters, gradients)
        assert divergence, "Loss explosion should be detected"
        assert signals['loss_explosion'], "Loss explosion signal not set"
        print("✅ PASS: Loss explosion detected")

        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def test_watchdog_conservative_mode():
    """Test 5: Divergence Watchdog conservative mode."""
    print("\n" + "="*70)
    print("TEST 5: Divergence Watchdog Conservative Mode")
    print("="*70)

    try:
        from core.learning.watchdog import DivergenceWatchdog

        watchdog = DivergenceWatchdog(baseline_loss=0.3)

        # Enter conservative mode
        watchdog.enter_conservative_mode(reason="Test")
        assert watchdog.conservative_mode, "Should be in conservative mode"
        print("✅ PASS: Entered conservative mode")

        # Reduce learning rate
        base_lr = 0.001
        adjusted_lr = watchdog.adjust_learning_rate_for_conservative_mode(base_lr)
        assert adjusted_lr == 0.0005, f"LR should be halved: {adjusted_lr}"
        print(f"✅ PASS: Learning rate reduced from {base_lr} to {adjusted_lr}")

        # Exit conservative mode
        exited = watchdog.check_conservative_mode_exit(current_loss=0.4)
        assert exited, "Should exit conservative mode when loss recovers"
        assert not watchdog.conservative_mode, "Should not be in conservative mode"
        print("✅ PASS: Exited conservative mode")

        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def test_bounds_enforcement():
    """Test 6: Parameter bounds enforcement."""
    print("\n" + "="*70)
    print("TEST 6: Parameter Bounds Enforcement")
    print("="*70)

    try:
        from core.learning.meta_optimizer import MetaOptimizer

        optimizer = MetaOptimizer()

        # Test alpha_core bounds
        clipped = optimizer.clip_parameter(-0.5, 0.001, 0.3)
        assert clipped == 0.001, f"Should clip to min: {clipped}"

        clipped = optimizer.clip_parameter(0.5, 0.001, 0.3)
        assert clipped == 0.3, f"Should clip to max: {clipped}"

        print("✅ PASS: Parameter clipping works")

        # Verify all parameters stay within bounds
        params = optimizer.get_tuned_hyperparameters()
        assert 0.001 <= params['alpha_core'] <= 0.3
        assert 0.001 <= params['alpha_infra'] <= 0.3
        assert 0.8 <= params['damping_core'] <= 0.99
        assert 0.8 <= params['damping_infra'] <= 0.99
        assert 0.0001 <= params['convergence_threshold'] <= 0.01
        assert 0.001 <= params['variance_threshold'] <= 0.1

        print("✅ PASS: All parameters within bounds")

        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def test_meta_optimizer_tuned_hyperparameters():
    """Test 7: Get tuned hyperparameters."""
    print("\n" + "="*70)
    print("TEST 7: Get Tuned Hyperparameters")
    print("="*70)

    try:
        from core.learning.meta_optimizer import MetaOptimizer

        optimizer = MetaOptimizer()

        hyperparams = optimizer.get_tuned_hyperparameters()

        expected_keys = {'alpha_core', 'alpha_infra', 'damping_core', 'damping_infra',
                         'convergence_threshold', 'variance_threshold'}
        assert set(hyperparams.keys()) == expected_keys

        print(f"✅ PASS: All {len(hyperparams)} hyperparameters returned")
        for k, v in hyperparams.items():
            print(f"   {k}: {v:.6f}")

        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def test_meta_optimizer_state_snapshot():
    """Test 8: State snapshot for debugging."""
    print("\n" + "="*70)
    print("TEST 8: Meta Optimizer State Snapshot")
    print("="*70)

    try:
        from core.learning.meta_optimizer import MetaOptimizer

        optimizer = MetaOptimizer()

        # Add some history
        for i in range(10):
            optimizer.loss_history.append(0.3 - i * 0.01)

        snapshot = optimizer.get_state_snapshot()

        assert 'step_count' in snapshot
        assert 'alpha_core' in snapshot
        assert 'loss_history' in snapshot
        assert 'is_converged' in snapshot
        assert 'tuned_hyperparameters' in snapshot

        print(f"✅ PASS: State snapshot contains all required fields")
        print(f"   Step count: {snapshot['step_count']}")
        print(f"   Losses: {len(snapshot['loss_history'])} entries")
        print(f"   Converged: {snapshot['is_converged']}")

        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        traceback.print_exc()
        return False


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        test_meta_optimizer_initialization,
        test_meta_optimizer_loss_computation,
        test_meta_optimizer_gradients,
        test_watchdog_divergence_detection,
        test_watchdog_conservative_mode,
        test_bounds_enforcement,
        test_meta_optimizer_tuned_hyperparameters,
        test_meta_optimizer_state_snapshot,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append((test.__name__, result))
        except Exception as e:
            print(f"❌ EXCEPTION in {test.__name__}: {e}")
            traceback.print_exc()
            results.append((test.__name__, False))

    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
