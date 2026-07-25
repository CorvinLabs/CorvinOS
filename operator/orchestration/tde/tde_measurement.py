"""ADR-0222 Phase 2 — Real-traffic measurement sampler for the decision gate.

Collects {direct, F5-tier, TDE} trials on the same tasks and aggregates them
into BandEvidence for the gate to consume. The gate upgrades from assumption-sourced
predictions to measured verdicts once min_samples_per_band accumulates.
"""

from __future__ import annotations

import os
import json
import threading
import asyncio
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any, Literal
from collections import defaultdict
from operator.orchestration.tde.decision_gate import BandEvidence


@dataclass
class MeasurementSample:
    """One sampled trial of {direct, tier, TDE} on a task."""

    task_id: str                    # run_id or unique task identifier
    task_band: Literal["trivial", "moderate", "complex"]  # task complexity band
    timestamp: float                # unix time

    # Direct turn (user's model, single-call baseline)
    direct_tokens: int
    direct_output: str

    # F5 whole-task-tier baseline
    tier_tokens: int
    tier_output: str
    tier_loss: float                # vs direct (0.0 = identical, 1.0 = unrelated)

    # TDE multi-step decomposition
    tde_tokens: int
    tde_output: str
    tde_loss: float                 # vs direct (0.0 = identical, 1.0 = unrelated)

    # Metadata
    quality_judge_model: str = "haiku"  # the model that scored losses
    data_source: str = "measured"       # always "measured" for real samples

    def __post_init__(self) -> None:
        """Validate sample data integrity (GDPR Art. 32 + ADR-0222)."""
        # Loss values must be in [0.0, 1.0] range (semantic similarity)
        if not (0.0 <= self.tier_loss <= 1.0):
            raise ValueError(f"tier_loss must be in [0.0, 1.0], got {self.tier_loss}")
        if not (0.0 <= self.tde_loss <= 1.0):
            raise ValueError(f"tde_loss must be in [0.0, 1.0], got {self.tde_loss}")

        # Token counts must be positive
        if self.direct_tokens < 0 or self.tier_tokens < 0 or self.tde_tokens < 0:
            raise ValueError("token counts must be non-negative")

        # task_band is validated by Literal type hint


@dataclass
class AggregatedBandEvidence:
    """Rolled-up stats for a band (avg tokens, losses, sample count)."""

    band: str
    samples: list[MeasurementSample] = field(default_factory=list)

    @property
    def direct_tokens(self) -> float:
        """Average tokens for direct turn on this band."""
        if not self.samples:
            return 0.0
        return sum(s.direct_tokens for s in self.samples) / len(self.samples)

    @property
    def tier_tokens(self) -> float:
        """Average tokens for F5 tier baseline on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tier_tokens for s in self.samples) / len(self.samples)

    @property
    def tier_loss(self) -> float:
        """Average quality loss for F5 tier baseline on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tier_loss for s in self.samples) / len(self.samples)

    @property
    def tde_tokens(self) -> float:
        """Average tokens for TDE decomposition on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tde_tokens for s in self.samples) / len(self.samples)

    @property
    def tde_loss(self) -> float:
        """Average quality loss for TDE decomposition on this band."""
        if not self.samples:
            return 0.0
        return sum(s.tde_loss for s in self.samples) / len(self.samples)

    @property
    def n_measured(self) -> int:
        """Number of measured samples backing this aggregation."""
        return len(self.samples)


def aggregate_measured_evidence(
    samples: list[MeasurementSample],
) -> list[BandEvidence]:
    """Rolls MeasurementSamples into the BandEvidence format decision_gate expects.

    Groups samples by task_band and aggregates {tokens, losses} within each band.
    Every output has data_source="measured" so the gate knows this is real data,
    not assumptions.

    Args:
        samples: List of real-traffic MeasurementSample trials.

    Returns:
        List of BandEvidence ready for decision_gate.evaluate_tde_verdict().
    """
    by_band: dict[str, list[MeasurementSample]] = defaultdict(list)
    for sample in samples:
        by_band[sample.task_band].append(sample)

    evidence_list: list[BandEvidence] = []
    for band, band_samples in by_band.items():
        agg = AggregatedBandEvidence(band=band, samples=band_samples)
        evidence = BandEvidence(
            band=band,
            direct_tokens=agg.direct_tokens,
            tde_tokens=agg.tde_tokens,
            tde_loss=agg.tde_loss,
            tier_tokens=agg.tier_tokens,
            tier_loss=agg.tier_loss,
            n_measured=agg.n_measured,
            data_source="measured",  # <- THE KEY: upgrades from assumptions
        )
        evidence_list.append(evidence)

    return evidence_list


class MeasurementRecorder:
    """Singleton that records {direct, tier, TDE} samples during measurement week.

    Persists samples to measurement.jsonl (separate from audit chain) and
    provides aggregated BandEvidence to the decision gate. Thread-safe and
    async-safe for concurrent turns during measurement week.

    NOTE on session isolation (k=4 enhancement): This singleton mixes samples
    from all concurrent sessions. For multi-tenant isolation, implement per-session
    recorders keyed by (tenant_id, session_id). Current design fine for k=3 where
    measurement week is opt-in feature; k=4 should add session-scoped storage.
    """

    _instance: MeasurementRecorder | None = None
    _instance_lock = threading.Lock()

    def __init__(self, measurement_log_path: str | None = None):
        """Initialize the recorder.

        Args:
            measurement_log_path: Path to write JSONL samples. If None, uses default
                ~/.corvin/measurement-week/measurement.jsonl.
        """
        self.enabled = os.getenv("TDE_MEASUREMENT_ENABLED") == "1"

        if measurement_log_path is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            measurement_log_path = os.path.join(
                corvin_home, "measurement-week", "measurement.jsonl"
            )

        self.log_path = measurement_log_path
        self.samples: list[MeasurementSample] = []
        self._write_lock = threading.Lock()

        # Ensure directory exists
        if self.enabled:
            log_dir = os.path.dirname(self.log_path)
            if log_dir:
                Path(log_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(
        cls, measurement_log_path: str | None = None
    ) -> "MeasurementRecorder":
        """Get or create the singleton instance (thread-safe TOCTOU fix)."""
        if cls._instance is None:
            with cls._instance_lock:
                # Double-check inside lock to avoid TOCTOU
                if cls._instance is None:
                    cls._instance = cls(measurement_log_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        with cls._instance_lock:
            cls._instance = None

    async def record_sample(self, sample: MeasurementSample) -> None:
        """Append a sample to the in-memory buffer and persist to log (async-safe).

        Uses asyncio.to_thread() to avoid blocking the event loop on file I/O.

        Args:
            sample: The measurement sample to record.
        """
        if not self.enabled:
            return

        self.samples.append(sample)

        # Persist to measurement.jsonl asynchronously (no event loop blocking)
        try:
            await asyncio.to_thread(self._write_sample_sync, sample)
        except (IOError, OSError) as e:
            # Log but don't crash; measurement failure shouldn't block chat
            print(f"Warning: Failed to write measurement sample to {self.log_path}: {e}")

    def _write_sample_sync(self, sample: MeasurementSample) -> None:
        """Synchronous file write with lock to prevent JSONL corruption.

        Executed in thread pool via asyncio.to_thread() to avoid blocking event loop.

        Args:
            sample: The measurement sample to write.
        """
        with self._write_lock:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(asdict(sample), default=str) + "\n")

    def get_aggregated_evidence(self) -> list[BandEvidence]:
        """Return current measured evidence aggregated by band.

        Returns:
            List of BandEvidence ready for decision_gate.evaluate_tde_verdict().
        """
        return aggregate_measured_evidence(self.samples)

    def load_from_log(self) -> None:
        """Load all samples from measurement.jsonl into memory (thread-safe).

        Useful for resuming a measurement week or analysis after restart.
        """
        if not os.path.exists(self.log_path):
            return

        with self._write_lock:
            try:
                with open(self.log_path, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            sample = MeasurementSample(**data)
                            self.samples.append(sample)
                        except (json.JSONDecodeError, TypeError, ValueError) as e:
                            print(f"Warning: Failed to parse measurement line: {e}")
            except (IOError, OSError) as e:
                print(f"Warning: Failed to load measurements from {self.log_path}: {e}")

    def clear_samples(self) -> None:
        """Clear in-memory samples (for testing)."""
        self.samples.clear()


# ============================================================================
# k=4 Mock Orchestrator (for Phase 1 integration testing)
# ============================================================================

class MockTdeOrchestrator:
    """Stub orchestrator for measurement week Phase 1.

    k=4 Phase 1: Mock {direct, tier, TDE} execution for testing hook.
    k=5: Real orchestration (parallel direct + tier + TDE runs).
    """

    @staticmethod
    def classify_band(task_complexity: str | None) -> Literal["trivial", "moderate", "complex"]:
        """Classify task into measurement band."""
        if task_complexity == "trivial":
            return "trivial"
        elif task_complexity == "complex":
            return "complex"
        else:
            return "moderate"

    @staticmethod
    def mock_direct_execution(prompt: str) -> dict[str, Any]:
        """Stub: direct turn (user model, single-call baseline)."""
        return {
            "tokens": 4500,
            "output": f"direct_answer_to_{prompt[:20]}",
            "loss": 0.0,  # Direct is reference
        }

    @staticmethod
    def mock_tier_execution(prompt: str) -> dict[str, Any]:
        """Stub: F5 whole-task-tier baseline."""
        return {
            "tokens": 4200,
            "output": f"tier_answer_to_{prompt[:20]}",
            "loss": 0.02,  # ~2% loss vs direct
        }

    @staticmethod
    async def orchestrate_measurement(
        prompt: str,
        tde_tokens: int,
        tde_output: str,
        task_complexity: str | None = None,
    ) -> MeasurementSample | None:
        """Orchestrate {direct, tier, TDE} and return sample for recording.

        k=4 Phase 1: Mock execution.
        k=5: Real parallel execution of direct + tier variants.
        """
        band = MockTdeOrchestrator.classify_band(task_complexity)
        direct = MockTdeOrchestrator.mock_direct_execution(prompt)
        tier = MockTdeOrchestrator.mock_tier_execution(prompt)

        # Synthetic TDE loss (worse than tier for demo)
        tde_loss = 0.05

        return MeasurementSample(
            task_id=f"tde-sample-{int(time.time())}",
            task_band=band,
            timestamp=time.time(),
            direct_tokens=direct["tokens"],
            direct_output=direct["output"],
            tier_tokens=tier["tokens"],
            tier_output=tier["output"],
            tier_loss=tier["loss"],
            tde_tokens=tde_tokens,
            tde_output=tde_output,
            tde_loss=tde_loss,
            quality_judge_model="haiku",
        )
