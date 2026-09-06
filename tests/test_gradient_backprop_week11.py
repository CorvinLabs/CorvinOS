"""Week 11: Gradient Backprop DAG + Correlation Filter tests"""

from core.learning.gradient_backprop import GradientBackpropDAG, CorrelationFilter, CouplingOscillationDetector


class TestGradientBackpropDAG:
    def test_dag_initialization(self):
        dag = GradientBackpropDAG()
        assert dag.check_dag_validity()

    def test_compute_backprop_gradients(self):
        dag = GradientBackpropDAG()
        grads = dag.compute_backprop_gradients(0.3, 0.25, 0.28, 0.26)
        assert 'memory' in grads
        assert 'skills' in grads
        assert 'plugins' in grads
        assert 0 <= grads['memory'] <= 1

    def test_topological_validity(self):
        dag = GradientBackpropDAG()
        assert dag.check_dag_validity()


class TestCorrelationFilter:
    def test_correlation_same_sign(self):
        filt = CorrelationFilter()
        # Both positive
        corr = filt.compute_correlation(0.1, 0.05, 50)
        assert corr > 0.5, f"Expected high correlation, got {corr}"

    def test_correlation_opposite_sign(self):
        filt = CorrelationFilter()
        # Opposite sign
        corr = filt.compute_correlation(0.1, -0.05, 50)
        assert corr < 0, f"Expected negative correlation, got {corr}"

    def test_filter_accepts_correlated(self):
        filt = CorrelationFilter(threshold=0.5)
        local = {'memory': 0.1}
        backprop = {'memory': 0.08}  # Same sign
        apply, corr = filt.apply_filter('memory', local, backprop)
        assert apply, "Should accept correlated gradient"

    def test_filter_rejects_anticorrelated(self):
        filt = CorrelationFilter(threshold=0.5)
        local = {'memory': 0.1}
        backprop = {'memory': -0.08}  # Opposite sign
        apply, corr = filt.apply_filter('memory', local, backprop)
        assert not apply, "Should reject anti-correlated gradient"


class TestCouplingOscillationDetector:
    def test_oscillation_detection(self):
        detector = CouplingOscillationDetector()
        # Simulate oscillating parameter (alternating high/low)
        for i in range(20):
            value = 0.15 if i % 2 == 0 else 0.10
            is_oscillating = detector.check_for_oscillation('memory', value)
        # After 20 steps of alternation, should detect
        assert is_oscillating, "Should detect oscillation"

    def test_stable_no_oscillation(self):
        detector = CouplingOscillationDetector()
        # Stable parameter (constant)
        for i in range(20):
            is_oscillating = detector.check_for_oscillation('memory', 0.12)
        assert not is_oscillating, "Should NOT detect oscillation on stable param"

    def test_phase_lock_interval(self):
        detector = CouplingOscillationDetector(phase_lock_batches=100)
        assert detector.get_phase_lock_interval() == 100


if __name__ == '__main__':
    # Run all tests manually (no pytest)
    suite = [
        ('DAG Init', TestGradientBackpropDAG().test_dag_initialization),
        ('Backprop Grads', TestGradientBackpropDAG().test_compute_backprop_gradients),
        ('DAG Valid', TestGradientBackpropDAG().test_topological_validity),
        ('Corr Same Sign', TestCorrelationFilter().test_correlation_same_sign),
        ('Corr Opp Sign', TestCorrelationFilter().test_correlation_opposite_sign),
        ('Filter Accept', TestCorrelationFilter().test_filter_accepts_correlated),
        ('Filter Reject', TestCorrelationFilter().test_filter_rejects_anticorrelated),
        ('Oscillation', TestCouplingOscillationDetector().test_oscillation_detection),
        ('No Oscillation', TestCouplingOscillationDetector().test_stable_no_oscillation),
        ('Phase Lock', TestCouplingOscillationDetector().test_phase_lock_interval),
    ]
    
    passed = 0
    for name, test in suite:
        try:
            test()
            print(f"✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"❌ {name}: {e}")
    
    print(f"\n{passed}/{len(suite)} PASSED")
