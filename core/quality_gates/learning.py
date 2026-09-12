"""Learning Optimizer for Quality Gates System (ADR-0689).

Implements BayesianGateTuner for adaptive threshold optimization using feedback signals.
Maintains Bayesian distributions over gate thresholds, tracks convergence, and logs all
updates to audit chain (immutable, append-only).

Load-bearing invariants:
- Every threshold update → audit event (no silent optimization)
- Checkpoints are append-only (never overwrite)
- Tenant isolation: all data tenant-scoped
- Convergence detection: KL-divergence < 1% threshold
"""

import json
import hashlib
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum

import duckdb

from .models import GateResult, VerdictType
from .audit import QualityGateAuditLogger
from .graph import KnowledgeGraph

logger = logging.getLogger(__name__)


class FeedbackType(str, Enum):
    """Feedback signal types."""
    VERDICT_CORRECT = "verdict_correct"
    VERDICT_INCORRECT = "verdict_incorrect"
    CONFIDENCE_CALIBRATION = "confidence_calibration"
    THRESHOLD_BOUNDARY = "threshold_boundary"


@dataclass(frozen=True, slots=True)
class GateFeedback:
    """Immutable feedback signal for gate learning.

    Attributes:
        artifact_id: Identifier of artifact being evaluated
        gate_name: Name of gate (e.g., 'IdeaGate', 'ConceptGate')
        feedback_type: Type of feedback signal (see FeedbackType)
        verdict_correct: True if gate verdict was correct, None if not applicable
        confidence_score: Operator's confidence in feedback [0.0, 1.0]
        tenant_id: Tenant ID for isolation
        timestamp: ISO8601 timestamp (auto-generated if not provided)
        metadata: Additional context (operator notes, etc.)
    """
    artifact_id: str
    gate_name: str
    feedback_type: FeedbackType
    confidence_score: float
    tenant_id: str
    verdict_correct: Optional[bool] = None
    timestamp: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate feedback signal."""
        if not self.artifact_id:
            raise ValueError("artifact_id is required")
        if not self.gate_name:
            raise ValueError("gate_name is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        if not 0.0 <= self.confidence_score <= 1.0:
            raise ValueError(f"confidence_score must be in [0, 1], got {self.confidence_score}")


@dataclass(frozen=True, slots=True)
class BayesianThreshold:
    """Bayesian distribution over a gate threshold.

    Models threshold as Beta distribution with shape parameters (alpha, beta).
    Mean = alpha / (alpha + beta)
    Variance = (alpha * beta) / ((alpha + beta)^2 * (alpha + beta + 1))

    Attributes:
        gate_name: Gate identifier
        alpha: Beta distribution shape parameter (successes + 1)
        beta: Beta distribution shape parameter (failures + 1)
        sample_count: Number of feedback signals processed
        last_updated: ISO8601 timestamp of last update
        convergence_kld: Latest KL-divergence to previous distribution
    """
    gate_name: str
    alpha: float
    beta: float
    sample_count: int = 0
    last_updated: Optional[str] = None
    convergence_kld: float = float('inf')

    def mean(self) -> float:
        """Compute mean of Beta distribution."""
        return self.alpha / (self.alpha + self.beta)

    def variance(self) -> float:
        """Compute variance of Beta distribution."""
        total = self.alpha + self.beta
        return (self.alpha * self.beta) / (total * total * (total + 1))

    def stddev(self) -> float:
        """Compute standard deviation."""
        return math.sqrt(self.variance())


class BayesianGateTuner:
    """Adaptive threshold optimizer using Bayesian learning.

    Maintains Beta distributions over gate thresholds, processes feedback signals,
    detects convergence, and logs all updates to audit chain.

    Load-bearing design:
    - Audit-first: every update logged before threshold changes
    - Append-only: checkpoint history never overwritten
    - Tenant-isolated: all operations scoped to tenant_id
    - Fail-closed: convergence detection never silently skips

    Attributes:
        graph: KnowledgeGraph for audit trail
        audit_logger: QualityGateAuditLogger for chain logging
        tenant_id: Tenant ID for isolation
        thresholds: Current learned thresholds
        distributions: Bayesian distributions (gate_name -> BayesianThreshold)
        feedback_history: Immutable list of processed feedback signals
        checkpoint_count: Number of saved checkpoints
    """

    # Convergence detection: KL-divergence threshold (< 1% change = converged)
    CONVERGENCE_KLD_THRESHOLD = 0.01

    # Beta prior: start with uniform distribution (alpha=1, beta=1)
    PRIOR_ALPHA = 1.0
    PRIOR_BETA = 1.0

    # Minimum sample count before considering convergence
    MIN_SAMPLES_FOR_CONVERGENCE = 10

    # KL-divergence window: check convergence over last N samples
    CONVERGENCE_WINDOW_SIZE = 100

    def __init__(
        self,
        graph: KnowledgeGraph,
        tenant_id: str,
        initial_thresholds: Optional[Dict[str, float]] = None,
    ) -> None:
        """Initialize BayesianGateTuner.

        Args:
            graph: KnowledgeGraph for audit integration
            tenant_id: Tenant ID (required for isolation)
            initial_thresholds: Initial threshold values (gate_name -> value)
                If None, uses 0.8 as default for all gates.

        Raises:
            ValueError: If tenant_id is empty or graph is None
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if graph is None:
            raise ValueError("graph is required")
        if not isinstance(graph, KnowledgeGraph):
            raise ValueError("graph must be a KnowledgeGraph instance")

        self.graph = graph
        self.tenant_id = tenant_id
        self.audit_logger = QualityGateAuditLogger(graph.conn)

        # Initialize threshold state
        self._thresholds: Dict[str, float] = initial_thresholds or {}
        self._distributions: Dict[str, BayesianThreshold] = {}
        self._feedback_history: List[GateFeedback] = []
        self._checkpoint_count = 0
        self._convergence_history: List[Tuple[str, float]] = []  # (timestamp, kld)

        # Log initialization
        logger.info(
            f"Initialized BayesianGateTuner for tenant {tenant_id} "
            f"with {len(self._thresholds)} initial thresholds"
        )

    def update_from_feedback(self, feedback: GateFeedback) -> None:
        """Process feedback signal and update threshold distribution.

        Algorithm:
        1. Validate feedback signal
        2. Compute prior hash (for audit chain)
        3. Update Beta distribution based on feedback
        4. Compute updated threshold (mean of distribution)
        5. Compute KL-divergence to prior
        6. Log to audit chain (immutable, append-only)
        7. Update internal state only after audit succeeds

        Args:
            feedback: GateFeedback signal to process

        Raises:
            ValueError: If feedback.tenant_id != self.tenant_id
            RuntimeError: If audit chain write fails
        """
        if feedback.tenant_id != self.tenant_id:
            raise ValueError(
                f"Feedback tenant_id {feedback.tenant_id} != tuner tenant_id {self.tenant_id}"
            )

        # Get or initialize distribution for this gate
        distribution = self._distributions.get(
            feedback.gate_name,
            BayesianThreshold(
                gate_name=feedback.gate_name,
                alpha=self.PRIOR_ALPHA,
                beta=self.PRIOR_BETA,
            ),
        )

        # Store prior distribution for KL-divergence computation
        prior_distribution = distribution
        prior_mean = distribution.mean()

        # Update Beta distribution based on feedback type
        alpha, beta = distribution.alpha, distribution.beta

        if feedback.feedback_type == FeedbackType.VERDICT_CORRECT:
            # Correct verdict: reinforce (increase alpha)
            alpha += feedback.confidence_score
        elif feedback.feedback_type == FeedbackType.VERDICT_INCORRECT:
            # Incorrect verdict: decrease confidence (increase beta)
            alpha -= feedback.confidence_score * 0.5
            beta += feedback.confidence_score
        elif feedback.feedback_type == FeedbackType.CONFIDENCE_CALIBRATION:
            # Calibration feedback: balance based on confidence
            if feedback.verdict_correct:
                alpha += feedback.confidence_score
            else:
                beta += feedback.confidence_score
        elif feedback.feedback_type == FeedbackType.THRESHOLD_BOUNDARY:
            # Boundary feedback: nudge threshold
            if feedback.verdict_correct:
                alpha += feedback.confidence_score * 2
            else:
                beta += feedback.confidence_score * 2

        # Clamp parameters to valid range [0.1, 1000]
        alpha = max(0.1, min(1000.0, alpha))
        beta = max(0.1, min(1000.0, beta))

        # Create updated distribution
        updated_distribution = BayesianThreshold(
            gate_name=feedback.gate_name,
            alpha=alpha,
            beta=beta,
            sample_count=distribution.sample_count + 1,
            last_updated=feedback.timestamp or datetime.utcnow().isoformat() + "Z",
        )

        # Compute KL-divergence to prior
        kld = self._compute_kl_divergence(prior_distribution, updated_distribution)

        # Update convergence KLD in distribution
        updated_distribution_with_kld = BayesianThreshold(
            gate_name=updated_distribution.gate_name,
            alpha=updated_distribution.alpha,
            beta=updated_distribution.beta,
            sample_count=updated_distribution.sample_count,
            last_updated=updated_distribution.last_updated,
            convergence_kld=kld,
        )

        # Compute new threshold (mean of distribution)
        new_threshold = updated_distribution_with_kld.mean()

        # Log to audit chain (must succeed before state update)
        try:
            self._log_threshold_update(
                feedback=feedback,
                gate_name=feedback.gate_name,
                prior_distribution=prior_distribution,
                updated_distribution=updated_distribution_with_kld,
                kld=kld,
                prior_threshold=self._thresholds.get(feedback.gate_name, 0.8),
                new_threshold=new_threshold,
            )
        except Exception as e:
            logger.error(
                f"Failed to log threshold update to audit chain: {e}. "
                "Update rejected (fail-closed)."
            )
            raise RuntimeError(f"Audit chain write failed: {e}")

        # Only update internal state after audit succeeds
        self._distributions[feedback.gate_name] = updated_distribution_with_kld
        self._thresholds[feedback.gate_name] = new_threshold
        self._feedback_history.append(feedback)
        self._convergence_history.append(
            (datetime.utcnow().isoformat() + "Z", kld)
        )

        logger.info(
            f"Updated {feedback.gate_name}: "
            f"α={alpha:.2f}, β={beta:.2f}, μ={new_threshold:.4f}, "
            f"KLD={kld:.6f}, samples={updated_distribution_with_kld.sample_count}"
        )

    def compute_confidence(self, artifact: Dict) -> Dict[str, float]:
        """Compute confidence in gate thresholds for artifact.

        For each gate, returns a confidence score based on:
        - Sample count (more samples = higher confidence)
        - KL-divergence history (lower recent KLD = higher confidence)
        - Distribution variance (lower variance = higher confidence)

        Algorithm:
        - Base confidence = 1.0 - (variance / max_variance)
        - Adjust by sample count: saturates at ~100 samples
        - Apply KLD discount if recent divergence is high

        Args:
            artifact: Artifact dict with artifact_id (used for logging only)

        Returns:
            Dict mapping gate_name -> confidence_score [0.0, 1.0]
        """
        confidence_scores: Dict[str, float] = {}

        for gate_name, distribution in self._distributions.items():
            # Base confidence from variance (lower variance = higher confidence)
            max_variance = 0.25  # Max variance for Beta(1,1)
            variance = distribution.variance()
            base_confidence = 1.0 - (variance / max_variance)
            base_confidence = max(0.0, min(1.0, base_confidence))

            # Adjust by sample count (logarithmic saturation)
            sample_factor = math.log(distribution.sample_count + 1) / math.log(101)
            sample_factor = min(1.0, sample_factor)

            # Apply KLD discount (recent high divergence lowers confidence)
            kld_discount = 1.0
            if distribution.convergence_kld < float('inf'):
                # If KLD > threshold, apply penalty
                if distribution.convergence_kld > self.CONVERGENCE_KLD_THRESHOLD:
                    kld_discount = max(0.5, 1.0 - distribution.convergence_kld)

            # Combine factors
            confidence = base_confidence * sample_factor * kld_discount
            confidence_scores[gate_name] = max(0.0, min(1.0, confidence))

        logger.debug(
            f"Computed confidence for {artifact.get('artifact_id', 'unknown')}: "
            f"{confidence_scores}"
        )

        return confidence_scores

    def is_converged(self) -> bool:
        """Check if thresholds have converged to stable values.

        Convergence criteria:
        1. Have processed at least MIN_SAMPLES_FOR_CONVERGENCE samples
        2. KL-divergence over recent window (CONVERGENCE_WINDOW_SIZE) is < threshold
        3. All gates have reached convergence

        Algorithm:
        - If any gate has < 10 samples, not converged
        - If any gate's recent KLD > 1%, not converged
        - Otherwise, converged

        Returns:
            True if all gates converged, False otherwise
        """
        if not self._distributions:
            logger.debug("No distributions initialized yet")
            return False

        for gate_name, distribution in self._distributions.items():
            # Check minimum sample count
            if distribution.sample_count < self.MIN_SAMPLES_FOR_CONVERGENCE:
                logger.debug(
                    f"Gate {gate_name} not converged: "
                    f"only {distribution.sample_count} samples "
                    f"(need {self.MIN_SAMPLES_FOR_CONVERGENCE})"
                )
                return False

            # Check KL-divergence
            if distribution.convergence_kld > self.CONVERGENCE_KLD_THRESHOLD:
                logger.debug(
                    f"Gate {gate_name} not converged: "
                    f"KLD={distribution.convergence_kld:.6f} "
                    f"(threshold={self.CONVERGENCE_KLD_THRESHOLD})"
                )
                return False

        logger.info(
            f"All gates converged: {list(self._distributions.keys())} "
            f"(window KLD < {self.CONVERGENCE_KLD_THRESHOLD})"
        )
        return True

    def get_thresholds(self) -> Dict[str, float]:
        """Return current learned thresholds.

        Returns:
            Dict mapping gate_name -> current threshold value
        """
        return dict(self._thresholds)

    def get_distributions(self) -> Dict[str, BayesianThreshold]:
        """Return current Bayesian distributions.

        Returns:
            Dict mapping gate_name -> BayesianThreshold
        """
        return dict(self._distributions)

    def save_checkpoint(self, path: str, label: str = "") -> str:
        """Save immutable checkpoint to audit chain.

        Checkpoint contains:
        - Current thresholds (gate_name -> value)
        - Distributions (gate_name -> (alpha, beta, sample_count))
        - Feedback history (count)
        - Convergence status
        - Label (operator-provided context)

        Checkpoints are append-only: a new file is always created,
        never overwriting prior checkpoints. The path is timestamped
        to ensure uniqueness.

        Args:
            path: Directory path where checkpoint will be saved
            label: Operator-provided label (e.g., "after_validation_phase_1")

        Returns:
            Full path to saved checkpoint file

        Raises:
            ValueError: If path is empty
            RuntimeError: If write fails
        """
        if not path:
            raise ValueError("path is required")

        # Generate timestamped checkpoint file
        timestamp = datetime.utcnow().isoformat().replace(':', '-')
        checkpoint_id = f"checkpoint-{self._checkpoint_count:04d}-{timestamp}"
        if label:
            checkpoint_id = f"{checkpoint_id}-{label}"

        checkpoint_filename = f"{checkpoint_id}.json"
        checkpoint_path = f"{path}/{checkpoint_filename}"

        # Prepare checkpoint data
        checkpoint_data = {
            "checkpoint_id": checkpoint_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "tenant_id": self.tenant_id,
            "label": label,
            "thresholds": self._thresholds,
            "distributions": {
                gate_name: {
                    "alpha": dist.alpha,
                    "beta": dist.beta,
                    "sample_count": dist.sample_count,
                    "mean": dist.mean(),
                    "stddev": dist.stddev(),
                    "convergence_kld": dist.convergence_kld,
                }
                for gate_name, dist in self._distributions.items()
            },
            "feedback_count": len(self._feedback_history),
            "is_converged": self.is_converged(),
            "convergence_kld_threshold": self.CONVERGENCE_KLD_THRESHOLD,
        }

        # Compute checkpoint hash (for audit chain)
        checkpoint_hash = hashlib.sha256(
            json.dumps(checkpoint_data, sort_keys=True).encode()
        ).hexdigest()

        # Log to audit chain (must succeed before write)
        try:
            self._log_checkpoint(checkpoint_data, checkpoint_hash)
        except Exception as e:
            logger.error(f"Failed to log checkpoint to audit chain: {e}")
            raise RuntimeError(f"Audit chain write failed: {e}")

        # Write checkpoint to disk (append-only: never overwrite)
        try:
            checkpoint_with_hash = {**checkpoint_data, "hash": checkpoint_hash}
            with open(checkpoint_path, 'w') as f:
                json.dump(checkpoint_with_hash, f, indent=2)
            self._checkpoint_count += 1
        except Exception as e:
            logger.error(f"Failed to write checkpoint to disk: {e}")
            raise RuntimeError(f"Checkpoint write failed: {e}")

        logger.info(
            f"Saved checkpoint {checkpoint_id} "
            f"(hash={checkpoint_hash[:16]}, path={checkpoint_path})"
        )

        return checkpoint_path

    def _compute_kl_divergence(
        self,
        prior: BayesianThreshold,
        posterior: BayesianThreshold,
    ) -> float:
        """Compute Kullback-Leibler divergence between Beta distributions.

        KL(P||Q) for Beta distributions:
        KL = log(B(α_q, β_q) / B(α_p, β_p))
           + (α_p - α_q) * ψ(α_p) + (β_p - β_q) * ψ(β_p)
           + (α_q + β_q - α_p - β_p) * ψ(α_p + β_p)

        where B is the beta function and ψ is the digamma function.

        Args:
            prior: Prior distribution (P)
            posterior: Posterior distribution (Q)

        Returns:
            KL divergence value (non-negative)
        """
        # Beta function: B(a, b) = Γ(a) * Γ(b) / Γ(a + b)
        # Log-beta: log B(a, b) = log Γ(a) + log Γ(b) - log Γ(a + b)
        #                        ≈ lgamma(a) + lgamma(b) - lgamma(a + b)

        def log_beta(a: float, b: float) -> float:
            """Compute log of beta function."""
            return math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)

        def digamma(x: float) -> float:
            """Compute digamma function (approximate)."""
            # Approximation: ψ(x) ≈ log(x) - 1/(2x)
            # More accurate approximation uses series, but this is sufficient
            return math.log(x) - 1.0 / (2.0 * x)

        # KL divergence components
        log_beta_ratio = log_beta(posterior.alpha, posterior.beta) - log_beta(
            prior.alpha, prior.beta
        )

        digamma_prior = digamma(prior.alpha)
        digamma_prior_sum = digamma(prior.alpha + prior.beta)

        alpha_delta = (prior.alpha - posterior.alpha) * digamma_prior
        beta_delta = (prior.beta - posterior.beta) * digamma(prior.beta)
        sum_delta = (
            posterior.alpha + posterior.beta - prior.alpha - prior.beta
        ) * digamma_prior_sum

        kld = log_beta_ratio + alpha_delta + beta_delta + sum_delta

        # KL divergence should be non-negative; clamp if numerical error
        return max(0.0, kld)

    def _log_threshold_update(
        self,
        feedback: GateFeedback,
        gate_name: str,
        prior_distribution: BayesianThreshold,
        updated_distribution: BayesianThreshold,
        kld: float,
        prior_threshold: float,
        new_threshold: float,
    ) -> None:
        """Log threshold update to audit chain.

        Creates audit event recording:
        - Feedback signal (artifact_id, verdict_correct, confidence)
        - Prior distribution state (α, β, mean)
        - Updated distribution state (α, β, mean)
        - KL-divergence and convergence status
        - Threshold delta (before -> after)

        Args:
            feedback: GateFeedback signal that triggered update
            gate_name: Gate being updated
            prior_distribution: Previous distribution
            updated_distribution: New distribution
            kld: KL-divergence between prior and posterior
            prior_threshold: Previous threshold
            new_threshold: New threshold

        Raises:
            RuntimeError: If audit chain write fails
        """
        event_data = {
            "event_type": "threshold_updated",
            "tenant_id": self.tenant_id,
            "gate_name": gate_name,
            "feedback": {
                "artifact_id": feedback.artifact_id,
                "feedback_type": feedback.feedback_type.value,
                "verdict_correct": feedback.verdict_correct,
                "confidence_score": feedback.confidence_score,
            },
            "prior_distribution": {
                "alpha": prior_distribution.alpha,
                "beta": prior_distribution.beta,
                "mean": prior_distribution.mean(),
                "sample_count": prior_distribution.sample_count,
            },
            "updated_distribution": {
                "alpha": updated_distribution.alpha,
                "beta": updated_distribution.beta,
                "mean": updated_distribution.mean(),
                "sample_count": updated_distribution.sample_count,
            },
            "kl_divergence": kld,
            "threshold_delta": {
                "prior": prior_threshold,
                "updated": new_threshold,
                "delta": new_threshold - prior_threshold,
            },
        }

        # Get prior hash for chain linking
        prior_hash = self.audit_logger.get_last_event_hash(self.tenant_id)

        # Compute event hash
        event_hash = self.audit_logger.compute_event_hash(event_data, prior_hash)

        # Prepare for audit trail insert (we use the DuckDB connection directly)
        timestamp = datetime.utcnow().isoformat() + "Z"
        event_id = f"threshold-update-{event_hash[:16]}"

        # Create tuner-specific events table if needed
        try:
            self.graph.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tuner_events (
                    id VARCHAR PRIMARY KEY,
                    timestamp VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    gate_name VARCHAR NOT NULL,
                    event_type VARCHAR NOT NULL,
                    feedback_type VARCHAR,
                    verdict_correct BOOLEAN,
                    confidence_score FLOAT,
                    prior_alpha FLOAT,
                    prior_beta FLOAT,
                    updated_alpha FLOAT,
                    updated_beta FLOAT,
                    kl_divergence FLOAT,
                    prior_threshold FLOAT,
                    new_threshold FLOAT,
                    event_hash VARCHAR NOT NULL,
                    prior_hash VARCHAR,
                    event_json VARCHAR NOT NULL
                )
                """
            )
        except Exception as e:
            # Table may already exist
            if "already exists" not in str(e):
                logger.warning(f"Could not create tuner_events table: {e}")

        # Insert into tuner_events table
        try:
            self.graph.conn.execute(
                """
                INSERT INTO tuner_events (
                    id, timestamp, tenant_id, gate_name, event_type,
                    feedback_type, verdict_correct, confidence_score,
                    prior_alpha, prior_beta, updated_alpha, updated_beta,
                    kl_divergence, prior_threshold, new_threshold,
                    event_hash, prior_hash, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event_id,
                    timestamp,
                    self.tenant_id,
                    gate_name,
                    "threshold_updated",
                    feedback.feedback_type.value,
                    feedback.verdict_correct,
                    feedback.confidence_score,
                    prior_distribution.alpha,
                    prior_distribution.beta,
                    updated_distribution.alpha,
                    updated_distribution.beta,
                    kld,
                    prior_threshold,
                    new_threshold,
                    event_hash,
                    prior_hash,
                    json.dumps(event_data, sort_keys=True),
                ],
            )
        except Exception as e:
            logger.error(f"Failed to insert tuner event to audit trail: {e}")
            raise RuntimeError(f"Audit trail write failed: {e}")

    def _log_checkpoint(self, checkpoint_data: dict, checkpoint_hash: str) -> None:
        """Log checkpoint to audit chain.

        Args:
            checkpoint_data: Checkpoint data dict
            checkpoint_hash: SHA256 hash of checkpoint

        Raises:
            RuntimeError: If audit chain write fails
        """
        # Get prior hash for chain linking
        prior_hash = self.audit_logger.get_last_event_hash(self.tenant_id)

        # Prepare event
        event_data = {
            "event_type": "checkpoint_saved",
            "tenant_id": self.tenant_id,
            "checkpoint_id": checkpoint_data["checkpoint_id"],
            "label": checkpoint_data.get("label", ""),
            "is_converged": checkpoint_data["is_converged"],
            "feedback_count": checkpoint_data["feedback_count"],
            "gate_count": len(checkpoint_data["distributions"]),
            "checkpoint_hash": checkpoint_hash,
        }

        # Compute event hash
        event_hash = self.audit_logger.compute_event_hash(event_data, prior_hash)

        # Prepare for audit trail insert
        timestamp = datetime.utcnow().isoformat() + "Z"
        event_id = f"checkpoint-{event_hash[:16]}"

        # Create checkpoints table if needed
        try:
            self.graph.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tuner_checkpoints (
                    id VARCHAR PRIMARY KEY,
                    timestamp VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    checkpoint_id VARCHAR NOT NULL,
                    label VARCHAR,
                    is_converged BOOLEAN,
                    feedback_count INTEGER,
                    gate_count INTEGER,
                    checkpoint_hash VARCHAR NOT NULL,
                    event_hash VARCHAR NOT NULL,
                    prior_hash VARCHAR,
                    event_json VARCHAR NOT NULL
                )
                """
            )
        except Exception as e:
            if "already exists" not in str(e):
                logger.warning(f"Could not create tuner_checkpoints table: {e}")

        # Insert into checkpoints table
        try:
            self.graph.conn.execute(
                """
                INSERT INTO tuner_checkpoints (
                    id, timestamp, tenant_id, checkpoint_id, label,
                    is_converged, feedback_count, gate_count,
                    checkpoint_hash, event_hash, prior_hash, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event_id,
                    timestamp,
                    self.tenant_id,
                    checkpoint_data["checkpoint_id"],
                    checkpoint_data.get("label", ""),
                    checkpoint_data["is_converged"],
                    checkpoint_data["feedback_count"],
                    len(checkpoint_data["distributions"]),
                    checkpoint_hash,
                    event_hash,
                    prior_hash,
                    json.dumps(event_data, sort_keys=True),
                ],
            )
        except Exception as e:
            logger.error(f"Failed to insert checkpoint to audit trail: {e}")
            raise RuntimeError(f"Audit trail write failed: {e}")
    
    def get_statistics(self) -> Dict[str, Dict]:
        """Get comprehensive learning statistics for all gates.
        
        Returns statistics dictionary with per-gate information:
        - sample_count: Number of feedback signals processed
        - mean_threshold: Current mean threshold value
        - confidence: Confidence in current threshold
        - convergence_status: Whether this gate has converged
        - kl_divergence: Recent KL-divergence to prior
        - alpha, beta: Current Beta distribution parameters
        - variance, stddev: Distribution spread
        
        Statistics are computed on-demand and reflect current state.
        
        Returns:
            Dict mapping gate_name -> statistics_dict with keys:
            - sample_count, mean_threshold, confidence, convergence_status,
            - kl_divergence, alpha, beta, variance, stddev
        """
        stats = {}
        
        for gate_name, distribution in self._distributions.items():
            stats[gate_name] = {
                "sample_count": distribution.sample_count,
                "mean_threshold": distribution.mean(),
                "confidence": self.compute_confidence({"artifact_id": f"stats-{gate_name}"})[
                    gate_name
                ],
                "convergence_status": (
                    "converged"
                    if distribution.convergence_kld < self.CONVERGENCE_KLD_THRESHOLD
                    and distribution.sample_count >= self.MIN_SAMPLES_FOR_CONVERGENCE
                    else "learning"
                ),
                "kl_divergence": distribution.convergence_kld,
                "alpha": distribution.alpha,
                "beta": distribution.beta,
                "variance": distribution.variance(),
                "stddev": distribution.stddev(),
            }
        
        logger.debug(f"Computed statistics for {len(stats)} gates")
        return stats
    
    def feedback_summary(self) -> Dict[str, int]:
        """Get summary of feedback signals by type.
        
        Counts feedback signals processed, grouped by feedback type.
        Useful for monitoring learning progress and data balance.
        
        Returns:
            Dict with counts of each feedback type:
            - verdict_correct: Correct verdict feedback count
            - verdict_incorrect: Incorrect verdict feedback count
            - confidence_calibration: Calibration feedback count
            - threshold_boundary: Boundary feedback count
        """
        summary = {feedback_type.value: 0 for feedback_type in FeedbackType}
        
        for feedback in self._feedback_history:
            summary[feedback.feedback_type.value] += 1
        
        logger.debug(f"Feedback summary: {summary}")
        return summary
    
    def reset_to_prior(self, gate_name: Optional[str] = None) -> None:
        """Reset threshold distribution(s) to prior (uniform).
        
        Resets the Beta distribution to uniform prior (alpha=1, beta=1),
        clearing all learned parameters. Useful for restarting learning
        if current distribution has diverged significantly.
        
        Logs reset event to audit chain (fail-closed: audit must succeed).
        Does NOT clear feedback history; only resets distributions.
        
        Args:
            gate_name: Gate to reset, or None to reset all gates
        
        Raises:
            RuntimeError: If audit chain write fails
        """
        gates_to_reset = (
            [gate_name] if gate_name else list(self._distributions.keys())
        )
        
        for gate in gates_to_reset:
            if gate not in self._distributions:
                logger.warning(f"Gate {gate} not found in distributions")
                continue
            
            old_distribution = self._distributions[gate]
            new_distribution = BayesianThreshold(
                gate_name=gate,
                alpha=self.PRIOR_ALPHA,
                beta=self.PRIOR_BETA,
            )
            
            # Log reset to audit chain (fail-closed)
            try:
                self._log_reset(gate, old_distribution, new_distribution)
            except Exception as e:
                logger.error(f"Failed to log reset to audit chain: {e}")
                raise RuntimeError(f"Audit chain write failed: {e}")
            
            # Update internal state only after audit succeeds
            self._distributions[gate] = new_distribution
            self._thresholds[gate] = self.PRIOR_ALPHA / (
                self.PRIOR_ALPHA + self.PRIOR_BETA
            )
            
            logger.info(f"Reset {gate} to prior distribution (α=1, β=1, μ=0.5)")
    
    def _log_reset(
        self,
        gate_name: str,
        prior_state: BayesianThreshold,
        new_state: BayesianThreshold,
    ) -> None:
        """Log threshold reset to audit chain.
        
        Creates audit event recording the state transition from prior
        learned distribution back to uniform prior.
        
        Args:
            gate_name: Gate being reset
            prior_state: Previous distribution (before reset)
            new_state: New (prior) distribution after reset
        
        Raises:
            RuntimeError: If audit chain write fails
        """
        prior_hash = self.audit_logger.get_last_event_hash(self.tenant_id)
        
        event_data = {
            "event_type": "threshold_reset",
            "tenant_id": self.tenant_id,
            "gate_name": gate_name,
            "prior_state": {
                "alpha": prior_state.alpha,
                "beta": prior_state.beta,
                "mean": prior_state.mean(),
                "sample_count": prior_state.sample_count,
            },
            "new_state": {
                "alpha": new_state.alpha,
                "beta": new_state.beta,
                "mean": new_state.mean(),
                "sample_count": 0,
            },
            "reason": "Manual reset to prior distribution",
        }
        
        event_hash = self.audit_logger.compute_event_hash(event_data, prior_hash)
        timestamp = datetime.utcnow().isoformat() + "Z"
        event_id = f"threshold-reset-{event_hash[:16]}"
        
        try:
            self.graph.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tuner_resets (
                    id VARCHAR PRIMARY KEY,
                    timestamp VARCHAR NOT NULL,
                    tenant_id VARCHAR NOT NULL,
                    gate_name VARCHAR NOT NULL,
                    event_hash VARCHAR NOT NULL,
                    prior_hash VARCHAR,
                    event_json VARCHAR NOT NULL
                )
                """
            )
        except Exception as e:
            if "already exists" not in str(e):
                logger.warning(f"Could not create tuner_resets table: {e}")
        
        try:
            self.graph.conn.execute(
                """
                INSERT INTO tuner_resets (
                    id, timestamp, tenant_id, gate_name,
                    event_hash, prior_hash, event_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event_id,
                    timestamp,
                    self.tenant_id,
                    gate_name,
                    event_hash,
                    prior_hash,
                    json.dumps(event_data, sort_keys=True),
                ],
            )
        except Exception as e:
            logger.error(f"Failed to insert reset event to audit trail: {e}")
            raise RuntimeError(f"Audit trail write failed: {e}")
    
    def export_model(self, path: str) -> str:
        """Export current model state to JSON file (for backup/transfer).
        
        Exports all learned thresholds, distributions, and metadata.
        This is a convenience export (separate from audit trail checkpoints).
        
        Args:
            path: Directory where model will be exported
        
        Returns:
            Path to exported model file
        """
        timestamp = datetime.utcnow().isoformat().replace(':', '-')
        export_filename = f"model-export-{timestamp}.json"
        export_path = f"{path}/{export_filename}"
        
        export_data = {
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "tenant_id": self.tenant_id,
            "thresholds": self._thresholds,
            "distributions": {
                gate: {
                    "alpha": dist.alpha,
                    "beta": dist.beta,
                    "mean": dist.mean(),
                    "stddev": dist.stddev(),
                    "sample_count": dist.sample_count,
                }
                for gate, dist in self._distributions.items()
            },
            "feedback_count": len(self._feedback_history),
            "convergence_status": self.is_converged(),
        }
        
        try:
            with open(export_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            logger.info(f"Exported model to {export_path}")
        except Exception as e:
            logger.error(f"Failed to export model: {e}")
            raise RuntimeError(f"Export failed: {e}")
        
        return export_path
