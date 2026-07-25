"""ADR-0222 Phase 2 — Real-traffic measurement sampler for the decision gate.

Collects {direct, F5-tier, TDE} trials on the same tasks and aggregates them
into BandEvidence for the gate to consume. The gate upgrades from assumption-sourced
predictions to measured verdicts once min_samples_per_band accumulates.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any
from collections import defaultdict
from operator.orchestration.tde.decision_gate import BandEvidence


@dataclass
class MeasurementSample:
    """One sampled trial of {direct, tier, TDE} on a task."""

    task_id: str                    # run_id or unique task identifier
    task_band: str                  # "trivial" | "moderate" | "complex"
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
    provides aggregated BandEvidence to the decision gate.
    """

    _instance: MeasurementRecorder | None = None

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

        # Ensure directory exists
        if self.enabled:
            log_dir = os.path.dirname(self.log_path)
            if log_dir:
                Path(log_dir).mkdir(parents=True, exist_ok=True)

    @classmethod
    def get_instance(
        cls, measurement_log_path: str | None = None
    ) -> "MeasurementRecorder":
        """Get or create the singleton instance."""
        if cls._instance is None:
            cls._instance = cls(measurement_log_path)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None

    async def record_sample(self, sample: MeasurementSample) -> None:
        """Append a sample to the in-memory buffer and persist to log.

        Args:
            sample: The measurement sample to record.
        """
        if not self.enabled:
            return

        self.samples.append(sample)

        # Persist to measurement.jsonl
        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(asdict(sample), default=str) + "\n")
        except (IOError, OSError) as e:
            # Log but don't crash; measurement failure shouldn't block chat
            print(f"Warning: Failed to write measurement sample to {self.log_path}: {e}")

    def get_aggregated_evidence(self) -> list[BandEvidence]:
        """Return current measured evidence aggregated by band.

        Returns:
            List of BandEvidence ready for decision_gate.evaluate_tde_verdict().
        """
        return aggregate_measured_evidence(self.samples)

    def load_from_log(self) -> None:
        """Load all samples from measurement.jsonl into memory.

        Useful for resuming a measurement week or analysis after restart.
        """
        if not os.path.exists(self.log_path):
            return

        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                        sample = MeasurementSample(**data)
                        self.samples.append(sample)
                    except (json.JSONDecodeError, TypeError) as e:
                        print(f"Warning: Failed to parse measurement line: {e}")
        except (IOError, OSError) as e:
            print(f"Warning: Failed to load measurements from {self.log_path}: {e}")

    def clear_samples(self) -> None:
        """Clear in-memory samples (for testing)."""
        self.samples.clear()
