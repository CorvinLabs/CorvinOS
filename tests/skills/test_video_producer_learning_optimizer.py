"""Phase 4c Tests: Video Producer Learning Optimizer (18+ tests).

Covers:
- Optimizer initialization and state persistence
- Config tuning heuristics
- Feedback processing
- Convergence tracking
- Per-worker config management
- Metrics aggregation for Vibe dashboard
"""

import json
import tempfile
import pytest
from pathlib import Path
from datetime import datetime

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.os_skills.video_producer.learning_optimizer import (
    VideoProducerLearningOptimizer,
    OptimizerState,
    WorkerConfig,
    ConfigDelta,
)


class TestOptimizerInitialization:
    """Optimizer setup and state management tests."""

    def test_optimizer_init_creates_workdir(self):
        """Test optimizer creates working directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)
            assert Path(tmpdir).exists()
            assert optimizer.workdir == Path(tmpdir)

    def test_optimizer_loads_defaults(self):
        """Test optimizer initializes with default worker configs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            # Should have all default workers
            assert "voice_synthesizer" in optimizer.state.worker_configs
            assert "screenshot_capturer" in optimizer.state.worker_configs
            assert "video_assembler" in optimizer.state.worker_configs

    def test_optimizer_persists_state(self):
        """Test optimizer saves state to disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            opt1 = VideoProducerLearningOptimizer(tmpdir)
            opt1.state.total_videos_produced = 5
            opt1._save_state()

            # Load in new instance
            opt2 = VideoProducerLearningOptimizer(tmpdir)
            assert opt2.state.total_videos_produced == 5

    def test_optimizer_recovers_from_corrupted_state(self):
        """Test optimizer handles corrupted state file gracefully."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "optimizer_state.json"
            state_path.write_text("{invalid json")

            # Should use defaults instead of crashing
            optimizer = VideoProducerLearningOptimizer(tmpdir)
            assert "voice_synthesizer" in optimizer.state.worker_configs


class TestWorkerConfigManagement:
    """Worker configuration retrieval and updates."""

    def test_get_worker_config_existing(self):
        """Test retrieving config for known worker."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)
            config = optimizer.get_worker_config("voice_synthesizer")

            assert "voice_speed" in config
            assert config["voice_speed"] == 1.0

    def test_get_worker_config_unknown_worker(self):
        """Test retrieving config for unknown worker returns empty."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)
            config = optimizer.get_worker_config("unknown_worker")

            assert config == {}

    def test_config_initialization_on_first_access(self):
        """Test unknown worker config is created on first access."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            # Access voice config first time
            config1 = optimizer.get_worker_config("voice_synthesizer")
            assert config1["voice_speed"] == 1.0

            # Modify it
            optimizer.state.worker_configs["voice_synthesizer"].params["voice_speed"] = 0.9
            optimizer._save_state()

            # Load in new instance and verify it persists
            opt2 = VideoProducerLearningOptimizer(tmpdir)
            config2 = opt2.get_worker_config("voice_synthesizer")
            assert config2["voice_speed"] == 0.9


class TestFeedbackProcessing:
    """Feedback processing and config tuning."""

    def test_process_feedback_voice_too_fast(self):
        """Test feedback processing tunes voice speed down."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            deltas = optimizer.process_feedback(
                job_id="vp_001",
                scene_id="s01",
                quality_score=0.6,  # Low quality
                feedback_notes="voice_too_fast",
            )

            assert deltas is not None
            assert len(deltas) == 1
            assert deltas[0].worker_id == "voice_synthesizer"
            assert deltas[0].param_name == "voice_speed"
            assert deltas[0].old_value == 1.0
            assert deltas[0].new_value < 1.0  # Reduced

    def test_process_feedback_crop_too_tight(self):
        """Test feedback processing tunes screenshot crop margin up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            deltas = optimizer.process_feedback(
                job_id="vp_002",
                scene_id="s02",
                quality_score=0.65,
                feedback_notes="cropped_too_tight",
            )

            assert deltas is not None
            assert len(deltas) == 1
            assert deltas[0].worker_id == "screenshot_capturer"
            assert deltas[0].param_name == "crop_margin"
            assert deltas[0].new_value > deltas[0].old_value

    def test_process_feedback_bitrate_low(self):
        """Test feedback processing tunes bitrate up."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            deltas = optimizer.process_feedback(
                job_id="vp_003",
                scene_id="s03",
                quality_score=0.55,
                feedback_notes="bitrate too low, quality_issues",
            )

            assert deltas is not None
            assert len(deltas) >= 1  # At least bitrate delta
            bitrate_delta = [d for d in deltas if d.param_name == "bitrate_kbps"]
            assert len(bitrate_delta) == 1
            assert bitrate_delta[0].new_value > bitrate_delta[0].old_value

    def test_process_feedback_high_quality_no_tuning(self):
        """Test that high quality feedback doesn't trigger tuning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            deltas = optimizer.process_feedback(
                job_id="vp_004",
                scene_id="s04",
                quality_score=0.95,  # High quality
                feedback_notes="voice_too_fast",  # Even with feedback
            )

            # High quality should not tune (already good)
            # Depending on heuristic, might be None or empty
            if deltas:
                assert len(deltas) == 0 or all(d.confidence < 0.75 for d in deltas)

    def test_multiple_feedback_items(self):
        """Test feedback with multiple issues produces multiple deltas."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            deltas = optimizer.process_feedback(
                job_id="vp_005",
                scene_id="s05",
                quality_score=0.55,
                feedback_notes="voice too fast and cropped too tight",
            )

            # Should tune both voice and screenshot
            assert deltas is not None
            assert len(deltas) >= 2
            worker_ids = {d.worker_id for d in deltas}
            assert "voice_synthesizer" in worker_ids
            assert "screenshot_capturer" in worker_ids


class TestConfigTuning:
    """Config delta application and versioning."""

    def test_delta_applied_to_config(self):
        """Test that applied deltas update the config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)
            old_speed = optimizer.get_worker_config("voice_synthesizer")["voice_speed"]

            optimizer.process_feedback(
                job_id="vp_006",
                scene_id="s06",
                quality_score=0.6,
                feedback_notes="voice_too_fast",
            )

            new_speed = optimizer.get_worker_config("voice_synthesizer")["voice_speed"]
            assert new_speed < old_speed

    def test_version_incremented_on_delta(self):
        """Test that config version increments when delta is applied."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)
            v1 = optimizer.state.worker_configs["voice_synthesizer"].version

            optimizer.process_feedback(
                job_id="vp_007",
                scene_id="s07",
                quality_score=0.6,
                feedback_notes="voice_too_fast",
            )

            v2 = optimizer.state.worker_configs["voice_synthesizer"].version
            assert v2 > v1

    def test_delta_history_tracked(self):
        """Test that applied deltas are tracked in config history."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            optimizer.process_feedback(
                job_id="vp_008",
                scene_id="s08",
                quality_score=0.6,
                feedback_notes="voice_too_fast",
            )

            config = optimizer.state.worker_configs["voice_synthesizer"]
            assert len(config.applied_deltas) > 0
            assert config.applied_deltas[0].param_name == "voice_speed"

    def test_param_clamping(self):
        """Test that config params are clamped to valid ranges."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            # Apply many deltas to try to go past clamps
            for i in range(10):
                optimizer.process_feedback(
                    job_id=f"vp_{i}",
                    scene_id=f"s{i:02d}",
                    quality_score=0.6,
                    feedback_notes="voice_too_fast",
                )

            speed = optimizer.get_worker_config("voice_synthesizer")["voice_speed"]
            assert speed >= 0.8  # Should not go below default minimum


class TestConvergence:
    """Convergence tracking and metrics."""

    def test_convergence_improves_with_quality(self):
        """Test convergence rate increases as quality improves."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            # Process low quality
            optimizer.process_feedback("vp_1", "s1", 0.5, "voice_too_fast")
            conv1 = optimizer.state.convergence_rate

            # Process high quality
            optimizer.process_feedback("vp_2", "s2", 0.9, "great")
            conv2 = optimizer.state.convergence_rate

            assert conv2 > conv1

    def test_convergence_bounds(self):
        """Test convergence rate stays in [0.0, 1.0]."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            for i in range(20):
                optimizer.process_feedback(
                    f"vp_{i}",
                    f"s{i:02d}",
                    0.95,  # Very high quality
                    "excellent",
                )

            assert 0.0 <= optimizer.state.convergence_rate <= 1.0


class TestMetrics:
    """Metrics for Vibe dashboard."""

    def test_get_optimizer_metrics(self):
        """Test metrics aggregation for dashboard."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            optimizer.process_feedback("vp_1", "s1", 0.8, "good")
            optimizer.process_feedback("vp_2", "s2", 0.85, "good")

            metrics = optimizer.get_optimizer_metrics()

            assert "skill_id" in metrics
            assert metrics["skill_id"] == "os.video_producer"
            assert "average_quality_score" in metrics
            assert "convergence_rate" in metrics
            assert "workers" in metrics
            assert "voice_synthesizer" in metrics["workers"]

    def test_metrics_quality_calculation(self):
        """Test quality score averaging in metrics."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            scores = [0.7, 0.8, 0.9]
            for i, score in enumerate(scores):
                optimizer.process_feedback(f"vp_{i}", f"s{i}", score, "ok")

            metrics = optimizer.get_optimizer_metrics()
            expected_avg = sum(scores) / len(scores)
            assert abs(metrics["average_quality_score"] - expected_avg) < 0.01


class TestErrorHandling:
    """Error handling and edge cases."""

    def test_feedback_with_no_notes(self):
        """Test feedback processing without explicit notes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            deltas = optimizer.process_feedback(
                "vp_001",
                "s01",
                0.5,
                feedback_notes=None,  # No notes
            )

            # Should not crash, might return None or empty
            assert deltas is None or len(deltas) == 0

    def test_feedback_with_unknown_phrases(self):
        """Test feedback with unrecognized keywords."""
        with tempfile.TemporaryDirectory() as tmpdir:
            optimizer = VideoProducerLearningOptimizer(tmpdir)

            deltas = optimizer.process_feedback(
                "vp_002",
                "s02",
                0.5,
                feedback_notes="something is weird but I don't know what",
            )

            # Should not crash
            assert deltas is None or isinstance(deltas, list)


# ============================================================================
# Run all tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
