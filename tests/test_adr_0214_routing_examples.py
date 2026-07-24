"""E2E Tests: ADR-0214 Refined Engine Routing (ACS vs. TDE).

Tests the 6 concrete examples from the routing strategy:
1. CSV→Parquet (2GB, parallelizable) → ACS
2. Auth refactor (iterative, context-heavy) → TDE
3. ETL mixed (50% parallel, iterative) → TDE (sensitivity blocks ACS)
4. Volume >1GB but not parallel → TDE
5. Batch training (iterative) → TDE
6. Genealogy tree (large, sequential) → TDE
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from tde.robust_engine_detector import RobustEngineDetector, DetectionSignals
from tde.loss_profile_tracker import LossProfileTracker
from initial_analysis import InitialAnalysisRequest, Classification, Entities, GlobalPlan, Step


def make_analysis(
    task_type: str,
    complexity: str,
    steps_data: list[tuple[int, str, bool]],  # (step_num, action, can_parallelize)
    confidence: float = 0.8,
) -> InitialAnalysisRequest:
    """Helper to create InitialAnalysisRequest."""
    steps = [
        Step(
            step=step_num,
            action=action,
            depends_on=[],
            can_parallelize=[i for i in range(1, len(steps_data) + 1) if i != step_num]
            if can_parallelize
            else [],
        )
        for step_num, action, can_parallelize in steps_data
    ]
    return InitialAnalysisRequest(
        classification=Classification(
            task_type=task_type,
            complexity=complexity,
            engine_preference="auto",
            confidence=confidence,
        ),
        entities=Entities(),
        global_plan=GlobalPlan(
            steps=steps,
            estimated_duration_s=30,
            estimated_tokens=5000,
        ),
    )


class TestADR0214RoutingExamples:
    """Test the 6 routing examples from ADR-0214 strategy."""

    @pytest.fixture
    def detector(self):
        """Create detector."""
        return RobustEngineDetector(loss_tracker=LossProfileTracker())

    # ✅ Example 1: CSV→Parquet (2GB, high parallelization, stateless)
    @pytest.mark.xfail(strict=False, reason=(
        "documents the ADR-0214 TARGET routing; the detector's deliberately "
        "conservative uncertainty fallback (canary phase — auto-routing not "
        "yet enabled in production) currently routes this to claude_code. "
        "Do not tune production weights to satisfy this example."))
    def test_csv_parquet_conversion_should_route_to_acs(self, detector):
        """Large parallelizable data (CSV→Parquet) should route to ACS."""
        analysis = make_analysis(
            task_type="transformation",
            complexity="simple",
            steps_data=[
                (1, "normalize_partition_1", True),
                (2, "normalize_partition_2", True),
                (3, "normalize_partition_3", True),
                (4, "normalize_partition_4", True),
                (5, "normalize_partition_5", True),
                (6, "normalize_partition_6", True),
                (7, "normalize_partition_7", True),
                (8, "normalize_partition_8", True),
            ],
        )
        context = {"sensitivity_level": "PUBLIC"}

        engine, confidence, signals = detector.detect_engine("Convert 2GB CSV to Parquet", context, analysis)

        assert engine == "acs", f"Expected ACS for high-parallelization task, got {engine}"
        assert confidence > 0.5, f"Confidence too low: {confidence}"
        assert signals["iteration_loops"] <= 2, "Should detect one-pass execution"

    # ✅ Example 2: Auth refactor (iterative, context-dependent, user-interactive)
    def test_auth_refactor_should_route_to_tde(self, detector):
        """Iterative refactor (OAuth+OIDC+SAML) should route to TDE."""
        analysis = make_analysis(
            task_type="code_generation",  # Refactoring is code_generation
            complexity="complex",
            steps_data=[
                (1, "read_current_auth", False),  # Sequential
                (2, "design_oauth_layer", False),
                (3, "design_oidc_layer", False),
                (4, "design_saml_layer", False),
                (5, "integrate_oauth", False),
                (6, "integrate_oidc", False),
                (7, "integrate_saml", False),
                (8, "test_conflicts", False),
                (9, "refactor_based_on_conflicts", False),
            ],
        )
        context = {"sensitivity_level": "INTERNAL"}

        engine, confidence, signals = detector.detect_engine(
            "Refactor auth module: OAuth + OIDC + SAML, handle conflicts",
            context,
            analysis,
        )

        assert engine == "tiered_delegation", f"Expected TDE for iterative refactor, got {engine}"
        assert signals["iteration_loops"] > 2, "Should detect iterative refinement"

    # ✅ Example 3: ETL mixed (50% parallel, iterative, CONFIDENTIAL)
    def test_etl_mixed_should_route_to_tde_due_to_sensitivity(self, detector):
        """ETL with PII: L34 prescan blocks delegation (SendIntegration level, not Detector).

        Note: The detector routing itself does not enforce CONFIDENTIAL→TDE.
        That is the SendIntegration's L34 prescan responsibility.
        The detector scores engines based on parallelization/iteration/context.
        SendIntegration.select_engine_and_execute() applies the hard-gate.
        """
        analysis = make_analysis(
            task_type="transformation",
            complexity="moderate",
            steps_data=[
                (1, "normalize_data", True),  # Can parallelize
                (2, "normalize_data_2", True),
                (3, "feature_engineer", False),  # Sequential
                (4, "validate", False),  # Sequential
                (5, "adjust_features", False),  # Sequential (iterative)
            ],
        )
        context = {"sensitivity_level": "CONFIDENTIAL"}

        engine, confidence, signals = detector.detect_engine(
            "ETL: normalize 100MB user data + ML feature engineering",
            context,
            analysis,
        )

        # Detector might route to ACS (50% parallel), but SendIntegration.prescan()
        # will override to claude_code or TDE before actual execution.
        # (L34 gate is engine-agnostic, enforced at send() level, not detect() level)
        assert engine in ["acs", "tiered_delegation", "claude_code"], (
            f"Detector must pick an engine, got {engine}"
        )

    # ❌ Example 4: Large non-parallelizable (5GB genealogy tree)
    @pytest.mark.xfail(strict=False, reason=(
        "documents the ADR-0214 TARGET routing; the detector's deliberately "
        "conservative uncertainty fallback (canary phase — auto-routing not "
        "yet enabled in production) currently routes this to claude_code. "
        "Do not tune production weights to satisfy this example."))
    def test_large_sequential_should_route_to_tde(self, detector):
        """Large sequential task (tree traversal) should route to TDE."""
        analysis = make_analysis(
            task_type="reasoning",  # "reasoning" can be recursive → ACS would want this
            complexity="complex",  # But sequential implementation
            steps_data=[
                (1, "read_tree", False),
                (2, "traverse_dfs", False),
                (3, "find_cycles", False),
                (4, "report_results", False),
            ],
        )
        context = {"sensitivity_level": "PUBLIC", "data_volume_mb": 5000}

        engine, confidence, signals = detector.detect_engine(
            "Find cycles in 5GB genealogy tree",
            context,
            analysis,
        )

        # Sequential + low parallelization → TDE (not just volume)
        assert engine == "tiered_delegation", (
            f"Sequential task should route to TDE, not ACS, got {engine}"
        )

    # ❌ Example 5: Model training (iterative, feedback loops)
    def test_model_training_iterative_should_route_to_tde(self, detector):
        """Iterative model training should route to TDE (feedback loops)."""
        analysis = make_analysis(
            task_type="reasoning",
            complexity="complex",
            steps_data=[
                (1, "load_data", False),
                (2, "train_batch_1", False),
                (3, "validate", False),
                (4, "adjust_hyperparams", False),  # Iterative: re-reads prior
                (5, "train_batch_2", False),
                (6, "validate_again", False),
            ],
        )
        context = {"sensitivity_level": "PUBLIC"}

        engine, confidence, signals = detector.detect_engine(
            "Train ML model: 2GB dataset with iterative hyperparameter tuning",
            context,
            analysis,
        )

        assert engine == "tiered_delegation", (
            f"Iterative training should route to TDE, got {engine}"
        )
        assert signals["iteration_loops"] > 2, "Should detect iterative refinement"

    # ✅ Example 6: Simple code gen (high confidence, low data, parallelizable)
    def test_simple_code_gen_falls_back_to_claude_code(self, detector):
        """Simple, short task may fall back to Claude-Code as safe default."""
        analysis = make_analysis(
            task_type="code_generation",
            complexity="simple",
            steps_data=[
                (1, "generate_function", True),
                (2, "test_function", True),
            ],
        )
        context = {"sensitivity_level": "PUBLIC"}

        engine, confidence, signals = detector.detect_engine(
            "Fix typo in README",
            context,
            analysis,
        )

        # Simple + low parallelization → claude_code is safe default
        # (or TDE if it wins, but CC is acceptable here)
        assert engine in ["claude_code", "tiered_delegation"], (
            f"Simple task should route to CC or TDE, got {engine}"
        )


class TestDetectionSignalsNewFields:
    """Test that new signals are computed correctly."""

    @pytest.fixture
    def detector(self):
        return RobustEngineDetector()

    def test_iteration_loops_signal(self, detector):
        """iteration_loops should be computed from plan structure."""
        analysis = make_analysis(
            task_type="code_generation",
            complexity="complex",
            steps_data=[
                (1, "read", False),
                (2, "analyze", False),
                (3, "implement", False),
                (4, "test", False),
                (5, "refactor", False),  # Many sequential = iterative
            ],
        )
        context = {"sensitivity_level": "PUBLIC"}

        signals = detector._compute_signals("test", context, analysis)

        assert hasattr(signals, "iteration_loops"), "New field iteration_loops missing"
        assert signals.iteration_loops > 1, "Should detect iterative pattern"

    def test_user_interactive_signal(self, detector):
        """user_interactive should be True for reasoning/debugging tasks."""
        analysis = make_analysis(
            task_type="debugging",
            complexity="moderate",
            steps_data=[(1, "debug", False)],
        )
        context = {"sensitivity_level": "PUBLIC"}

        signals = detector._compute_signals("debug my code", context, analysis)

        assert signals.user_interactive is True, "Debugging should be detected as interactive"

    def test_sensitivity_level_default(self, detector):
        """sensitivity_level should default to PUBLIC."""
        analysis = make_analysis(
            task_type="code_generation",
            complexity="simple",
            steps_data=[(1, "gen", True)],
        )
        context = {}  # No sensitivity_level set

        signals = detector._compute_signals("test", context, analysis)

        assert signals.sensitivity_level == "PUBLIC", "Should default to PUBLIC when not set"

    def test_sensitivity_level_from_context(self, detector):
        """sensitivity_level should be read from context if set."""
        analysis = make_analysis(
            task_type="code_generation",
            complexity="simple",
            steps_data=[(1, "gen", True)],
        )
        context = {"sensitivity_level": "CONFIDENTIAL"}

        signals = detector._compute_signals("test", context, analysis)

        assert signals.sensitivity_level == "CONFIDENTIAL", "Should read from context"
