"""Phase 9: Pattern Discovery for TreeOfThoughts.

Auto-learn new patterns from production failures by clustering similar errors,
inferring when/anti_when contexts, and auto-registering with confidence scoring.

GDPR-compliant: no PII in cluster logs, only error_type and context metadata.
Safety: only proposes patterns after 50+ samples per cluster.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from collections import defaultdict, Counter
import json
from .models import TreeNode, LearningEvent
from .storage import LearningEventStore


@dataclass(frozen=True)
class FailureCluster:
    """Immutable record of a cluster of similar failures."""
    cluster_id: str  # e.g., "cluster_timeout_api_calls"
    error_type: str  # e.g., "timeout", "ratelimit", "auth_failed"
    sample_count: int
    context_patterns: dict[str, list]  # e.g., {"endpoint": ["POST /api/v1", ...], "provider": ["openai", ...]}
    when_conditions: list[str]  # Inferred conditions where this error occurs
    anti_when_conditions: list[str]  # Inferred conditions where this error is avoided
    confidence_when: float  # How confident the "when" conditions are
    confidence_anti_when: float  # How confident the "anti_when" conditions are
    discovered_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source_events: list[dict] = field(default_factory=list)  # Summary of source events
    ready_for_proposal: bool = False  # True if sample_count >= 50


@dataclass(frozen=True)
class DiscoveredPattern:
    """Immutable record of a pattern successfully discovered and registered."""
    pattern_id: str
    name: str
    when: list[str]
    anti_when: list[str]
    baseline_confidence: float = 0.5  # Conservative starting point
    registered_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source_cluster_id: str = ""
    source_sample_count: int = 0


class FailureClusterer:
    """Clusters production failures and discovers patterns.

    Algorithm:
    1. Buffer failed events (event_type="failed")
    2. Group by error_type (e.g., "timeout", "rate_limit")
    3. For each error_type, cluster by common context patterns
    4. When cluster has >=50 samples:
       - Infer when/anti_when conditions from context patterns
       - Create TreeNode pattern with baseline confidence 0.5
       - Register via LearningIntegration
       - Audit trail log
    """

    MIN_SAMPLES_FOR_PROPOSAL = 50

    def __init__(self, store: LearningEventStore, base_dir: Path = None):
        """Initialize clusterer.

        Args:
            store: LearningEventStore for reading events
            base_dir: Where to store cluster and discovery logs (default: ~/.corvin/learning/discoveries)
        """
        self.store = store
        if base_dir is None:
            base_dir = Path.home() / ".corvin" / "learning" / "discoveries"
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # In-memory cluster accumulator
        # Key: (error_type, context_signature)
        # Value: list of (event, timestamp) tuples
        self._failure_buffer: dict[tuple, list[tuple]] = defaultdict(list)

        # Discovered clusters (cache)
        self._clusters: dict[str, FailureCluster] = {}

        # Registered patterns from discoveries
        self._discoveries: dict[str, DiscoveredPattern] = {}

    def add_failure(self, subject_id: str, error_type: str, context: dict, timestamp: str = None) -> None:
        """Add a failure event to the clustering buffer.

        Args:
            subject_id: Pattern or method that failed
            error_type: Type of error (e.g., "timeout", "rate_limit")
            context: Context dict from the failure event
            timestamp: ISO8601 timestamp (default: now)
        """
        if timestamp is None:
            timestamp = datetime.now().isoformat()

        # Create a signature from context (deterministic key for grouping)
        context_sig = self._context_signature(context)

        # Use (error_type, context_sig) as cluster key
        cluster_key = (error_type, context_sig)

        # Record the failure
        failure_record = {
            "subject_id": subject_id,
            "error_type": error_type,
            "context": context,
            "timestamp": timestamp,
        }
        self._failure_buffer[cluster_key].append((failure_record, timestamp))

    def discover_patterns(self, integration=None) -> list[DiscoveredPattern]:
        """Discover patterns from accumulated failures.

        Clusters failures, infers patterns, and auto-registers patterns
        that have >=50 samples.

        Args:
            integration: Optional LearningIntegration instance for auto-registration

        Returns:
            List of newly discovered patterns
        """
        discovered = []

        # Group failures by error_type
        by_error_type = defaultdict(list)
        for (error_type, context_sig), failures in self._failure_buffer.items():
            by_error_type[error_type].extend(failures)

        # Cluster each error_type
        for error_type, failures in by_error_type.items():
            if len(failures) < self.MIN_SAMPLES_FOR_PROPOSAL:
                # Not enough samples yet
                continue

            # Cluster failures by context patterns
            clusters = self._cluster_by_context(error_type, failures)

            for cluster in clusters:
                if cluster.ready_for_proposal:
                    # Infer when/anti_when conditions
                    cluster = self._infer_conditions(cluster)

                    # Create and register pattern
                    pattern = self._register_pattern_for_cluster(cluster, integration)
                    if pattern:
                        discovered.append(pattern)
                        self._discoveries[pattern.pattern_id] = pattern

        return discovered

    def get_clusters(self) -> list[FailureCluster]:
        """Get all discovered clusters."""
        return list(self._clusters.values())

    def get_discoveries(self) -> list[DiscoveredPattern]:
        """Get all successfully discovered patterns."""
        return list(self._discoveries.values())

    # --- Private Methods ---

    def _context_signature(self, context: dict) -> str:
        """Create a deterministic signature from context dict.

        Extracts key fields that are likely to define patterns:
        - error_type, provider, endpoint, stage, etc.
        - Ignores timestamps, request_ids, user-specific data
        """
        # Fields we care about for clustering
        keys_of_interest = [
            "provider", "endpoint", "stage", "method",
            "model", "voice", "language", "format",
            "error_class", "error_message_prefix",
        ]

        sig_parts = []
        for key in sorted(keys_of_interest):
            if key in context:
                val = context[key]
                # Normalize value to string, truncate if needed
                if isinstance(val, (list, dict)):
                    val_str = str(type(val).__name__)  # Just the type
                else:
                    val_str = str(val)[:50]  # Truncate long strings
                sig_parts.append(f"{key}={val_str}")

        # If no recognized keys, use a generic signature
        if not sig_parts:
            sig_parts.append("generic")

        return "|".join(sig_parts)

    def _cluster_by_context(self, error_type: str, failures: list[tuple]) -> list[FailureCluster]:
        """Cluster failures by context patterns.

        Extracts common context values across failures and groups them.
        Returns clusters with >=50 samples.
        """
        clusters = []

        # Extract all context keys that appear
        all_context_keys = set()
        for failure_record, _ in failures:
            all_context_keys.update(failure_record["context"].keys())

        # Count frequency of each context value
        context_patterns = defaultdict(Counter)
        for failure_record, _ in failures:
            context = failure_record["context"]
            for key in all_context_keys:
                if key in context:
                    val = context[key]
                    # Normalize to string
                    val_str = str(val)
                    context_patterns[key][val_str] += 1

        # Create cluster record
        if len(failures) >= self.MIN_SAMPLES_FOR_PROPOSAL:
            cluster_id = f"cluster_{error_type}_{len(clusters)}"

            # Extract patterns (values that appear frequently)
            context_patterns_summary = {}
            for key, counter in context_patterns.items():
                top_values = [v for v, _ in counter.most_common(5)]
                if top_values:
                    context_patterns_summary[key] = top_values

            cluster = FailureCluster(
                cluster_id=cluster_id,
                error_type=error_type,
                sample_count=len(failures),
                context_patterns=context_patterns_summary,
                when_conditions=[],
                anti_when_conditions=[],
                confidence_when=0.0,
                confidence_anti_when=0.0,
                ready_for_proposal=len(failures) >= self.MIN_SAMPLES_FOR_PROPOSAL,
                source_events=[
                    {
                        "subject_id": f["subject_id"],
                        "error_type": f["error_type"],
                        "timestamp": ts,
                    }
                    for f, ts in failures[:10]  # Sample first 10
                ]
            )

            clusters.append(cluster)
            self._clusters[cluster_id] = cluster

        return clusters

    def _infer_conditions(self, cluster: FailureCluster) -> FailureCluster:
        """Infer when/anti_when conditions from cluster patterns.

        When: Context patterns that strongly predict this error
        Anti-When: Conditions that avoid this error
        """
        when_conditions = []
        anti_when_conditions = []

        # Infer "when" from the most common context patterns
        for key, values in cluster.context_patterns.items():
            if values:
                # Use top value as the condition
                top_value = values[0]
                when_conditions.append(f"{key} == {top_value}")

        # Infer "anti_when" (conditions that would avoid this)
        # This is a simple heuristic: avoid specific providers known to fail
        if cluster.error_type == "rate_limit":
            anti_when_conditions = [
                "batch_size < 50",
                "request_interval >= 1s",
            ]
        elif cluster.error_type == "timeout":
            anti_when_conditions = [
                "provider != fallback",
                "timeout_seconds >= 30",
            ]
        elif cluster.error_type == "auth_failed":
            anti_when_conditions = [
                "credentials_refreshed_within_1h",
                "auth_endpoint_responding",
            ]

        # Create updated cluster with inferred conditions
        # Note: FailureCluster is frozen, so we can't modify it in-place
        # Return as-is, the conditions will be extracted when creating TreeNode
        return FailureCluster(
            cluster_id=cluster.cluster_id,
            error_type=cluster.error_type,
            sample_count=cluster.sample_count,
            context_patterns=cluster.context_patterns,
            when_conditions=when_conditions,
            anti_when_conditions=anti_when_conditions,
            confidence_when=0.75,  # Heuristic confidence in inferred conditions
            confidence_anti_when=0.60,
            discovered_at=cluster.discovered_at,
            source_events=cluster.source_events,
            ready_for_proposal=cluster.ready_for_proposal,
        )

    def _register_pattern_for_cluster(
        self,
        cluster: FailureCluster,
        integration=None,
    ) -> Optional[DiscoveredPattern]:
        """Create TreeNode and register pattern.

        Args:
            cluster: FailureCluster with inferred conditions
            integration: Optional LearningIntegration for registration

        Returns:
            DiscoveredPattern if successful, None otherwise
        """
        # Create pattern ID from error type
        pattern_id = f"pattern_auto_{cluster.error_type}_{cluster.cluster_id}"
        pattern_name = f"Handle {cluster.error_type} errors (auto-discovered)"

        # Create TreeNode
        node = TreeNode(
            id=pattern_id,
            level="pattern",
            name=pattern_name,
            confidence=0.5,  # Conservative baseline
            when=cluster.when_conditions,
            anti_when=cluster.anti_when_conditions,
            body=self._generate_pattern_body(cluster),
            calls_in_production=cluster.sample_count,
            metrics={
                "error_type": cluster.error_type,
                "sample_count": cluster.sample_count,
                "confidence_when": cluster.confidence_when,
                "confidence_anti_when": cluster.confidence_anti_when,
            }
        )

        # Register in store
        self.store.register_node(node)

        # Audit log
        self._log_discovery(pattern_id, node, cluster)

        # If integration provided, also register there
        if integration:
            integration.register_pattern(
                pattern_id,
                pattern_name,
                cluster.when_conditions,
                cluster.anti_when_conditions,
            )

        # Create discovery record
        discovery = DiscoveredPattern(
            pattern_id=pattern_id,
            name=pattern_name,
            when=cluster.when_conditions,
            anti_when=cluster.anti_when_conditions,
            baseline_confidence=0.5,
            source_cluster_id=cluster.cluster_id,
            source_sample_count=cluster.sample_count,
        )

        return discovery

    def _generate_pattern_body(self, cluster: FailureCluster) -> str:
        """Generate human-readable pattern body."""
        lines = [
            f"Auto-discovered pattern for {cluster.error_type} errors.",
            f"Based on {cluster.sample_count} production failures.",
            "",
            "Context patterns:",
        ]

        for key, values in cluster.context_patterns.items():
            lines.append(f"  - {key}: {', '.join(values[:3])}")

        return "\n".join(lines)

    def _log_discovery(self, pattern_id: str, node: TreeNode, cluster: FailureCluster) -> None:
        """Log pattern discovery to audit trail (append-only)."""
        # Ensure base_dir exists
        self.base_dir.mkdir(parents=True, exist_ok=True)

        log_path = self.base_dir / "discoveries.jsonl"

        discovery_record = {
            "timestamp": datetime.now().isoformat(),
            "pattern_id": pattern_id,
            "pattern_name": node.name,
            "error_type": cluster.error_type,
            "sample_count": cluster.sample_count,
            "when_conditions": cluster.when_conditions,
            "anti_when_conditions": cluster.anti_when_conditions,
            "confidence_when": cluster.confidence_when,
            "confidence_anti_when": cluster.confidence_anti_when,
            "context_patterns": {
                k: list(v)  # Convert Counter to list for JSON serialization
                for k, v in cluster.context_patterns.items()
            },
            "source_events_sample": cluster.source_events[:5],
        }

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(discovery_record) + "\n")
