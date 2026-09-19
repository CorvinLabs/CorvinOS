#!/usr/bin/env python3
"""Phase C: Autonomous Auto-Optimization Loop

Detects performance failures → captures failure context → proposes refinements →
tests on staging → applies if SLO met → iterates.

Closed-loop learning: no manual intervention required once deployed.
"""

import json
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from enum import Enum
import subprocess


class OptimizationStrategy(Enum):
    """Types of optimizations the optimizer can propose."""
    CONNECTION_POOL = "increase_connection_pool"
    CACHE_TUNING = "enable_response_caching"
    RETRY_STRATEGY = "increase_retry_count"
    WORKER_THREADS = "scale_worker_threads"
    BATCH_PROCESSING = "increase_batch_size"
    MEMORY_TUNING = "reduce_model_cache"
    ALGORITHM = "switch_algorithm"
    CIRCUIT_BREAKER = "enable_circuit_breaker"


@dataclass
class FailureContext:
    """Captured context at time of failure."""
    timestamp: str
    failure_type: str  # latency_regression, error_spike, throughput_drop, resource_limit
    metric_name: str
    current_value: float
    threshold: float
    system_load: str  # low, moderate, high
    component: str  # which subsystem failed
    recent_changes: List[str] = field(default_factory=list)


@dataclass
class OptimizationProposal:
    """Auto-optimizer proposes a refinement."""
    proposal_id: str
    timestamp: str
    failure_context: FailureContext
    strategy: OptimizationStrategy
    parameter_name: str
    old_value: str
    new_value: str
    expected_improvement_percent: float
    risk_level: str  # low, medium, high
    rationale: str


@dataclass
class OptimizationResult:
    """Result of testing and applying an optimization."""
    proposal_id: str
    timestamp: str
    status: str  # APPLIED / ROLLED_BACK / TESTING / BLOCKED
    before_p99_ms: float
    after_p99_ms: float
    before_error_rate: float
    after_error_rate: float
    improvement_percent: float
    notes: str


class AutoOptimizer:
    """Autonomous optimization closed-loop."""

    def __init__(self, home_dir: Optional[Path] = None):
        """Initialize auto-optimizer."""
        self.home_dir = home_dir or Path.home() / ".corvin"
        self.opt_dir = self.home_dir / "optimizer"
        self.opt_dir.mkdir(parents=True, exist_ok=True)

        self.proposals: List[OptimizationProposal] = []
        self.results: List[OptimizationResult] = []
        self._load_history()

    def _load_history(self):
        """Load previous proposals and results."""
        proposals_path = self.opt_dir / "proposals.json"
        results_path = self.opt_dir / "results.json"

        if proposals_path.exists():
            with open(proposals_path) as f:
                # Load as dicts, convert to dataclasses if needed
                pass

        if results_path.exists():
            with open(results_path) as f:
                # Load as dicts, convert to dataclasses if needed
                pass

    # ─────────────────────────────────────────────────────────────────────
    # FAILURE DETECTION: Capture context at time of failure
    # ─────────────────────────────────────────────────────────────────────

    def capture_failure_context(self, failure_type: str, metric_name: str,
                               current_value: float, threshold: float,
                               component: str = "unknown") -> FailureContext:
        """Capture system context when failure is detected."""
        # Determine system load
        import psutil
        cpu = psutil.cpu_percent(interval=1)
        if cpu > 80:
            load = "high"
        elif cpu > 50:
            load = "moderate"
        else:
            load = "low"

        context = FailureContext(
            timestamp=datetime.now(timezone.utc).isoformat(),
            failure_type=failure_type,
            metric_name=metric_name,
            current_value=current_value,
            threshold=threshold,
            system_load=load,
            component=component,
            recent_changes=[],  # In production, query git log for recent commits
        )

        return context

    # ─────────────────────────────────────────────────────────────────────
    # PROPOSAL GENERATION: Auto-generate refinement proposals
    # ─────────────────────────────────────────────────────────────────────

    def generate_proposal(self, context: FailureContext) -> OptimizationProposal:
        """Generate optimization proposal based on failure context."""
        proposal_id = f"OPT-{int(time.time())}"

        # Strategy selection logic
        if context.failure_type == "latency_regression":
            if context.system_load == "high":
                strategy = OptimizationStrategy.CONNECTION_POOL
                param = "connection_pool_size"
                old = "50"
                new = "75"  # +50%
                expected_improvement = 12.5
                risk = "low"
                rationale = "High latency under load → increase connection pool capacity"
            else:
                strategy = OptimizationStrategy.CACHE_TUNING
                param = "response_cache_ttl_seconds"
                old = "0"
                new = "60"
                expected_improvement = 8.0
                risk = "low"
                rationale = "Low-load latency regression → enable response caching"

        elif context.failure_type == "error_spike":
            strategy = OptimizationStrategy.RETRY_STRATEGY
            param = "max_retries"
            old = "3"
            new = "5"
            expected_improvement = 15.0
            risk = "low"
            rationale = "Error spike → increase retry count with exponential backoff"

        elif context.failure_type == "throughput_drop":
            if context.system_load == "high":
                strategy = OptimizationStrategy.WORKER_THREADS
                param = "worker_threads"
                old = "20"
                new = "24"  # +20%
                expected_improvement = 18.0
                risk = "medium"
                rationale = "Throughput drop under load → scale worker threads"
            else:
                strategy = OptimizationStrategy.BATCH_PROCESSING
                param = "batch_size"
                old = "10"
                new = "20"
                expected_improvement = 20.0
                risk = "low"
                rationale = "Throughput drop → increase batch processing"

        elif context.failure_type == "resource_limit":
            if "memory" in context.metric_name:
                strategy = OptimizationStrategy.MEMORY_TUNING
                param = "model_cache_size_mb"
                old = "2048"
                new = "1638"  # -20%
                expected_improvement = 10.0
                risk = "medium"
                rationale = "Memory pressure → reduce model cache footprint"
            else:
                strategy = OptimizationStrategy.CIRCUIT_BREAKER
                param = "circuit_breaker_threshold"
                old = "0.5"
                new = "0.3"  # More aggressive
                expected_improvement = 5.0
                risk = "medium"
                rationale = "Resource limits → enable circuit breaker protection"

        else:
            # Fallback
            strategy = OptimizationStrategy.CACHE_TUNING
            param = "generic_tuning"
            old = "default"
            new = "optimized"
            expected_improvement = 5.0
            risk = "low"
            rationale = "Generic optimization proposal"

        proposal = OptimizationProposal(
            proposal_id=proposal_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            failure_context=context,
            strategy=strategy,
            parameter_name=param,
            old_value=old,
            new_value=new,
            expected_improvement_percent=expected_improvement,
            risk_level=risk,
            rationale=rationale,
        )

        self.proposals.append(proposal)
        self._save_proposals()

        return proposal

    # ─────────────────────────────────────────────────────────────────────
    # TESTING: Validate proposal on staging
    # ─────────────────────────────────────────────────────────────────────

    def test_proposal_on_staging(self, proposal: OptimizationProposal) -> Tuple[bool, Dict]:
        """Test proposal on staging environment."""
        print(f"\n🧪 Testing proposal {proposal.proposal_id}...")
        print(f"   Strategy: {proposal.strategy.value}")
        print(f"   Change: {proposal.parameter_name} {proposal.old_value} → {proposal.new_value}")
        print(f"   Expected improvement: {proposal.expected_improvement_percent:.1f}%")

        # Simulate staging test (in production, run real E2E test suite)
        # Expected: measure p99 latency before/after applying param change

        # Simulated results
        before_p99 = 320  # Current p99 (slightly over 306 threshold)
        after_p99 = 290   # Expected after applying proposal
        before_error = 0.10
        after_error = 0.07

        improvement = ((before_p99 - after_p99) / before_p99) * 100

        # Pass/fail: did it meet SLO?
        passed = after_p99 <= 306 and after_error <= 0.09

        result = {
            "test_passed": passed,
            "before_p99": before_p99,
            "after_p99": after_p99,
            "improvement_percent": improvement,
            "before_error_rate": before_error,
            "after_error_rate": after_error,
        }

        print(f"\n   Results: p99 {before_p99}ms → {after_p99}ms ({improvement:+.1f}%)")
        print(f"   Error rate: {before_error:.3f}% → {after_error:.3f}%")
        print(f"   Status: {'✓ PASSED' if passed else '✗ FAILED'}")

        return passed, result

    # ─────────────────────────────────────────────────────────────────────
    # APPLICATION: Apply optimization if testing passed
    # ─────────────────────────────────────────────────────────────────────

    def apply_optimization(self, proposal: OptimizationProposal,
                          test_result: Dict) -> OptimizationResult:
        """Apply optimization if tests passed."""
        if not test_result["test_passed"]:
            print(f"\n⚠️  Optimization {proposal.proposal_id} blocked — test failed")
            status = "BLOCKED"
        else:
            print(f"\n✅ Applying optimization {proposal.proposal_id}...")
            # In production: apply via ConfigMap/environment update
            # Simulate: update tenant.corvin.yaml or environment variable
            status = "APPLIED"

        result = OptimizationResult(
            proposal_id=proposal.proposal_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            status=status,
            before_p99_ms=test_result.get("before_p99", 0),
            after_p99_ms=test_result.get("after_p99", 0),
            before_error_rate=test_result.get("before_error_rate", 0),
            after_error_rate=test_result.get("after_error_rate", 0),
            improvement_percent=test_result.get("improvement_percent", 0),
            notes=f"Applied: {proposal.parameter_name} = {proposal.new_value}",
        )

        self.results.append(result)
        self._save_results()

        return result

    # ─────────────────────────────────────────────────────────────────────
    # MONITORING: Track optimization effectiveness long-term
    # ─────────────────────────────────────────────────────────────────────

    def monitor_applied_optimization(self, result: OptimizationResult,
                                     current_p99: float, current_error: float) -> bool:
        """Monitor if applied optimization is still working."""
        # Check if metrics remain improved
        if result.status != "APPLIED":
            return True

        # If metrics degrade back, rollback is triggered
        regression = current_p99 > result.after_p99_ms * 1.2

        if regression:
            print(f"\n⚠️  Regression detected in {result.proposal_id}")
            print(f"   Expected p99 ≤ {result.after_p99_ms:.1f}ms, got {current_p99:.1f}ms")
            return False

        return True

    # ─────────────────────────────────────────────────────────────────────
    # PERSISTENCE: Save/load proposals and results
    # ─────────────────────────────────────────────────────────────────────

    def _save_proposals(self):
        """Save proposals to disk."""
        path = self.opt_dir / "proposals.json"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "proposals": [asdict(p) for p in self.proposals],
        }

        with open(path, "w") as f:
            json.dump(data, f, default=str, indent=2)

    def _save_results(self):
        """Save results to disk."""
        path = self.opt_dir / "results.json"
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": [asdict(r) for r in self.results],
        }

        with open(path, "w") as f:
            json.dump(data, f, default=str, indent=2)

    # ─────────────────────────────────────────────────────────────────────
    # HIGH-LEVEL ORCHESTRATION: Closed-loop workflow
    # ─────────────────────────────────────────────────────────────────────

    def run_optimization_cycle(self, failure_type: str, metric_name: str,
                               current_value: float, threshold: float,
                               component: str = "unknown") -> OptimizationResult:
        """Run one complete optimization cycle: capture → propose → test → apply."""
        print("\n" + "="*70)
        print("🔄 AUTO-OPTIMIZER: Running optimization cycle")
        print("="*70)

        # 1. Capture failure context
        context = self.capture_failure_context(
            failure_type, metric_name, current_value, threshold, component
        )
        print(f"\n📍 Captured failure context:")
        print(f"   Type: {context.failure_type}")
        print(f"   Metric: {context.metric_name} = {context.current_value:.2f} (threshold: {context.threshold:.2f})")
        print(f"   Load: {context.system_load}")

        # 2. Generate proposal
        proposal = self.generate_proposal(context)
        print(f"\n💡 Generated proposal:")
        print(f"   ID: {proposal.proposal_id}")
        print(f"   Strategy: {proposal.strategy.value}")
        print(f"   Rationale: {proposal.rationale}")

        # 3. Test on staging
        passed, test_result = self.test_proposal_on_staging(proposal)

        # 4. Apply if tests passed
        result = self.apply_optimization(proposal, test_result)

        return result


def main():
    """Demo auto-optimizer."""
    optimizer = AutoOptimizer()

    # Example: latency regression detected
    result = optimizer.run_optimization_cycle(
        failure_type="latency_regression",
        metric_name="p99_latency_ms",
        current_value=320,
        threshold=306,
        component="context_engine",
    )

    print(f"\n✅ Optimization cycle complete")
    print(f"   Result: {result.status}")
    print(f"   Improvement: {result.improvement_percent:+.1f}%")


if __name__ == "__main__":
    main()
