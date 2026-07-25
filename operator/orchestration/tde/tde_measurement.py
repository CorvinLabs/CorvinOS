"""ADR-0222 Phase 2 — Real-traffic measurement sampler for the decision gate.

Collects {direct, F5-tier, TDE} trials on the same tasks and aggregates them
into BandEvidence for the gate to consume. The gate upgrades from assumption-sourced
predictions to measured verdicts once min_samples_per_band accumulates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
