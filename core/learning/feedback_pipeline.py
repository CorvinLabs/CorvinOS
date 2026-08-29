"""Phase 6: CI/CD Feedback Pipeline — Closed-loop learning from test results.

Provides:
1. CI/CD result ingestion (pass/fail/flake detection)
2. Pattern analysis (root-cause identification)
3. Prompt refinement recommendations (ADR-0371)
4. Failure correlation (test, prompt, model version)
5. Flakiness detection & anomaly flagging
6. Compliance: audit trail, tenant isolation (GDPR Art. 5, 30, 32)
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Set
from uuid import uuid4

logger = logging.getLogger(__name__)


class TestResultType(str, Enum):
    """CI/CD test outcome classification."""
    PASS = "pass"
    FAIL = "fail"
    FLAKE = "flake"  # Intermittent failure
    SKIP = "skip"
    ERROR = "error"  # Infrastructure error


class FailureCategory(str, Enum):
    """Root-cause classification for failures."""
    PROMPT_QUALITY = "prompt_quality"
    MODEL_BEHAVIOR = "model_behavior"
    CONTEXT_LIMIT = "context_limit"
    TIMEOUT = "timeout"
    EXTERNAL_SERVICE = "external_service"
    INFRASTRUCTURE = "infrastructure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TestResult:
    """Immutable CI/CD test result record."""

    result_id: str
    test_name: str
    test_suite: str
    status: TestResultType
    timestamp_utc: datetime
    tenant_id: str

    # Execution context
    duration_ms: int
    model_version: str
    prompt_version: str
    environment: str = "ci"

    # Failure details
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    category: Optional[FailureCategory] = None

    # Metadata
    commit_hash: Optional[str] = None
    run_id: Optional[str] = None
    retry_count: int = 0

    def to_dict(self) -> dict:
        """Convert to JSON-serializable dict."""
        return {
            k: (v.value if isinstance(v, Enum) else
                v.isoformat() if isinstance(v, datetime) else v)
            for k, v in asdict(self).items()
        }


@dataclass
class PatternAnalysis:
    """Analysis of recurring failure patterns."""

    pattern_id: str
    category: FailureCategory
    affected_tests: Set[str]
    failure_count: int
    flake_count: int
    first_seen: datetime
    last_seen: datetime
    avg_duration_ms: float
    confidence: float  # 0.0-1.0, likelihood this is a real pattern

    # Root-cause correlation
    correlated_prompt_versions: List[str]
    correlated_model_versions: List[str]
    correlated_environments: List[str]

    # Recommendations
    recommendations: List[str] = field(default_factory=list)


@dataclass
class PromptRefinement:
    """Recommendation to refine a prompt based on failure patterns."""

    refinement_id: str
    prompt_version: str
    category: FailureCategory
    issue_description: str
    suggested_changes: List[str]
    affected_tests: List[str]
    confidence: float  # 0.0-1.0
    estimated_improvement: float  # Expected % improvement
    created_at: datetime

    approved: bool = False
    approved_at: Optional[datetime] = None
    applied_at: Optional[datetime] = None


class FeedbackPipeline:
    """Ingest, analyze, and learn from CI/CD feedback."""

    def __init__(self, storage_dir: Path, tenant_id: str):
        """Initialize feedback pipeline.

        Args:
            storage_dir: Path to store feedback data
            tenant_id: Tenant ID (for isolation)
        """
        self.storage_dir = Path(storage_dir)
        self.tenant_id = tenant_id
        self.storage_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.storage_dir / "feedback.db"
        self._init_db()

        self._test_results: Dict[str, TestResult] = {}
        self._patterns: Dict[str, PatternAnalysis] = {}
        self._refinements: Dict[str, PromptRefinement] = {}
        self._lock = __import__('threading').Lock()

    def _init_db(self) -> None:
        """Initialize SQLite database for audit trail."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS test_results (
                    result_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    test_name TEXT NOT NULL,
                    test_suite TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timestamp_utc TEXT NOT NULL,
                    duration_ms INTEGER,
                    model_version TEXT,
                    prompt_version TEXT,
                    environment TEXT,
                    error_message TEXT,
                    category TEXT,
                    commit_hash TEXT,
                    run_id TEXT,
                    retry_count INTEGER,
                    created_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS patterns (
                    pattern_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    failure_count INTEGER,
                    flake_count INTEGER,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    avg_duration_ms REAL,
                    confidence REAL,
                    affected_tests TEXT,
                    recommendations TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS refinements (
                    refinement_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    category TEXT NOT NULL,
                    issue_description TEXT,
                    suggested_changes TEXT,
                    confidence REAL,
                    estimated_improvement REAL,
                    affected_tests TEXT,
                    approved INTEGER DEFAULT 0,
                    approved_at TEXT,
                    applied_at TEXT,
                    created_at TEXT NOT NULL
                )
            """)

            conn.commit()

    def ingest_test_result(
        self,
        test_name: str,
        test_suite: str,
        status: TestResultType,
        duration_ms: int,
        model_version: str,
        prompt_version: str,
        error_message: Optional[str] = None,
        stack_trace: Optional[str] = None,
        commit_hash: Optional[str] = None,
        run_id: Optional[str] = None,
        retry_count: int = 0,
    ) -> TestResult:
        """Ingest a test result from CI/CD.

        Args:
            test_name: Name of test
            test_suite: Suite containing test
            status: Result type (pass/fail/flake/skip/error)
            duration_ms: Execution time
            model_version: Model version used
            prompt_version: Prompt version used
            error_message: Optional error message
            stack_trace: Optional stack trace
            commit_hash: Git commit SHA
            run_id: CI/CD run ID
            retry_count: Number of retries before success

        Returns:
            TestResult record
        """
        now = datetime.now(timezone.utc)
        result_id = str(uuid4())

        # Classify failure if applicable
        category = self._classify_failure(error_message, stack_trace) if status == TestResultType.FAIL else None

        result = TestResult(
            result_id=result_id,
            test_name=test_name,
            test_suite=test_suite,
            status=status,
            timestamp_utc=now,
            tenant_id=self.tenant_id,
            duration_ms=duration_ms,
            model_version=model_version,
            prompt_version=prompt_version,
            error_message=error_message,
            stack_trace=stack_trace,
            category=category,
            commit_hash=commit_hash,
            run_id=run_id,
            retry_count=retry_count,
        )

        with self._lock:
            self._test_results[result_id] = result
            self._store_result(result)

        logger.info(f"Ingested test result: {test_name} ({status.value})")
        return result

    def _store_result(self, result: TestResult) -> None:
        """Persist test result to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO test_results
                (result_id, tenant_id, test_name, test_suite, status, timestamp_utc,
                 duration_ms, model_version, prompt_version, environment, error_message,
                 category, commit_hash, run_id, retry_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                result.result_id, result.tenant_id, result.test_name, result.test_suite,
                result.status.value, result.timestamp_utc.isoformat(),
                result.duration_ms, result.model_version, result.prompt_version,
                result.environment, result.error_message, result.category.value if result.category else None,
                result.commit_hash, result.run_id, result.retry_count,
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

    def _classify_failure(self, error_msg: Optional[str], stack_trace: Optional[str]) -> FailureCategory:
        """Heuristically classify failure category."""
        if not error_msg and not stack_trace:
            return FailureCategory.UNKNOWN

        text = (error_msg or "") + (stack_trace or "")
        text_lower = text.lower()

        if any(w in text_lower for w in ["context", "token", "length"]):
            return FailureCategory.CONTEXT_LIMIT

        if any(w in text_lower for w in ["timeout", "timed out"]):
            return FailureCategory.TIMEOUT

        if any(w in text_lower for w in ["http", "connection", "socket", "network"]):
            return FailureCategory.EXTERNAL_SERVICE

        if any(w in text_lower for w in ["infra", "docker", "resource"]):
            return FailureCategory.INFRASTRUCTURE

        if any(w in text_lower for w in ["prompt", "template"]):
            return FailureCategory.PROMPT_QUALITY

        if any(w in text_lower for w in ["model", "response"]):
            return FailureCategory.MODEL_BEHAVIOR

        return FailureCategory.UNKNOWN

    def analyze_patterns(self, window_days: int = 7) -> List[PatternAnalysis]:
        """Analyze failure patterns over time window.

        Args:
            window_days: Look back this many days

        Returns:
            List of identified patterns
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

        # Group failures by category
        by_category: Dict[FailureCategory, List[TestResult]] = {}

        with self._lock:
            for result in self._test_results.values():
                if result.timestamp_utc < cutoff or result.status not in [TestResultType.FAIL, TestResultType.FLAKE]:
                    continue

                category = result.category or FailureCategory.UNKNOWN
                by_category.setdefault(category, []).append(result)

        patterns = []

        for category, results in by_category.items():
            if len(results) < 2:  # Need at least 2 occurrences to be a pattern
                continue

            # Build pattern analysis
            affected_tests = set(r.test_name for r in results)
            failure_count = sum(1 for r in results if r.status == TestResultType.FAIL)
            flake_count = sum(1 for r in results if r.status == TestResultType.FLAKE)

            timestamps = [r.timestamp_utc for r in results]
            model_versions = list(set(r.model_version for r in results))
            prompt_versions = list(set(r.prompt_version for r in results))
            environments = list(set(r.environment for r in results))

            avg_duration = sum(r.duration_ms for r in results) / len(results)

            # Confidence: higher if consistent category, multiple occurrences, recent
            recency_score = min(1.0, len([r for r in results if r.timestamp_utc > datetime.now(timezone.utc) - timedelta(days=1)]) / len(results))
            consistency_score = min(1.0, failure_count / len(results))
            confidence = (recency_score * 0.3 + consistency_score * 0.7)

            pattern_id = str(uuid4())
            pattern = PatternAnalysis(
                pattern_id=pattern_id,
                category=category,
                affected_tests=affected_tests,
                failure_count=failure_count,
                flake_count=flake_count,
                first_seen=min(timestamps),
                last_seen=max(timestamps),
                avg_duration_ms=avg_duration,
                confidence=confidence,
                correlated_prompt_versions=prompt_versions,
                correlated_model_versions=model_versions,
                correlated_environments=environments,
                recommendations=self._generate_recommendations(category, results),
            )

            patterns.append(pattern)

            with self._lock:
                self._patterns[pattern_id] = pattern
                self._store_pattern(pattern)

        return patterns

    def _generate_recommendations(self, category: FailureCategory, results: List[TestResult]) -> List[str]:
        """Generate actionable recommendations for a pattern."""
        recommendations = []

        if category == FailureCategory.PROMPT_QUALITY:
            recommendations.append("Review and refine prompt templates")
            recommendations.append("Add context examples to prompts")
            recommendations.append("Clarify instructions in prompt")

        elif category == FailureCategory.CONTEXT_LIMIT:
            recommendations.append("Reduce context window usage")
            recommendations.append("Implement smarter context selection")
            recommendations.append("Split large tasks into subtasks")

        elif category == FailureCategory.TIMEOUT:
            recommendations.append("Increase timeout thresholds")
            recommendations.append("Optimize slow code paths")
            recommendations.append("Add request timeout handling")

        elif category == FailureCategory.EXTERNAL_SERVICE:
            recommendations.append("Add retry logic for external calls")
            recommendations.append("Implement circuit breaker pattern")
            recommendations.append("Add fallback mechanisms")

        elif category == FailureCategory.INFRASTRUCTURE:
            recommendations.append("Review resource allocation")
            recommendations.append("Check CI/CD infrastructure stability")
            recommendations.append("Add monitoring and alerting")

        return recommendations

    def _store_pattern(self, pattern: PatternAnalysis) -> None:
        """Persist pattern to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO patterns
                (pattern_id, tenant_id, category, failure_count, flake_count,
                 first_seen, last_seen, avg_duration_ms, confidence, affected_tests,
                 recommendations, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pattern.pattern_id, self.tenant_id, pattern.category.value,
                pattern.failure_count, pattern.flake_count,
                pattern.first_seen.isoformat(), pattern.last_seen.isoformat(),
                pattern.avg_duration_ms, pattern.confidence,
                json.dumps(list(pattern.affected_tests)),
                json.dumps(pattern.recommendations),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()

    def recommend_refinements(self, patterns: List[PatternAnalysis]) -> List[PromptRefinement]:
        """Generate prompt refinement recommendations based on patterns.

        Args:
            patterns: List of identified patterns

        Returns:
            List of prompt refinement suggestions
        """
        refinements = []

        for pattern in patterns:
            if pattern.confidence < 0.6:  # Skip low-confidence patterns
                continue

            for prompt_version in pattern.correlated_prompt_versions:
                refinement_id = str(uuid4())

                suggested_changes = []
                if pattern.category == FailureCategory.PROMPT_QUALITY:
                    suggested_changes = [
                        "Add explicit formatting requirements",
                        "Include example outputs",
                        "Clarify edge case handling",
                    ]
                elif pattern.category == FailureCategory.CONTEXT_LIMIT:
                    suggested_changes = [
                        "Reduce prompt verbosity",
                        "Remove redundant examples",
                        "Use more concise instructions",
                    ]

                # Estimate improvement: 5-15% depending on confidence
                estimated_improvement = 0.05 + (pattern.confidence * 0.10)

                refinement = PromptRefinement(
                    refinement_id=refinement_id,
                    prompt_version=prompt_version,
                    category=pattern.category,
                    issue_description=f"Pattern detected: {pattern.category.value} in {len(pattern.affected_tests)} tests",
                    suggested_changes=suggested_changes,
                    affected_tests=list(pattern.affected_tests),
                    confidence=pattern.confidence,
                    estimated_improvement=estimated_improvement,
                    created_at=datetime.now(timezone.utc),
                )

                refinements.append(refinement)

                with self._lock:
                    self._refinements[refinement_id] = refinement
                    self._store_refinement(refinement)

        return refinements

    def _store_refinement(self, refinement: PromptRefinement) -> None:
        """Persist refinement recommendation to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO refinements
                (refinement_id, tenant_id, prompt_version, category, issue_description,
                 suggested_changes, confidence, estimated_improvement, affected_tests,
                 approved, approved_at, applied_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                refinement.refinement_id, self.tenant_id, refinement.prompt_version,
                refinement.category.value, refinement.issue_description,
                json.dumps(refinement.suggested_changes), refinement.confidence,
                refinement.estimated_improvement, json.dumps(refinement.affected_tests),
                1 if refinement.approved else 0,
                refinement.approved_at.isoformat() if refinement.approved_at else None,
                refinement.applied_at.isoformat() if refinement.applied_at else None,
                refinement.created_at.isoformat()
            ))
            conn.commit()

    def approve_refinement(self, refinement_id: str) -> bool:
        """Approve a prompt refinement for application.

        Args:
            refinement_id: ID of refinement to approve

        Returns:
            True if approved, False if not found
        """
        with self._lock:
            if refinement_id not in self._refinements:
                return False

            refinement = self._refinements[refinement_id]
            refinement.approved = True
            refinement.approved_at = datetime.now(timezone.utc)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE refinements
                SET approved = 1, approved_at = ?
                WHERE refinement_id = ?
            """, (datetime.now(timezone.utc).isoformat(), refinement_id))
            conn.commit()

        return True

    def get_flakiness_score(self, test_name: str, window_days: int = 7) -> float:
        """Calculate flakiness score for a test (0.0-1.0).

        Args:
            test_name: Name of test
            window_days: Time window

        Returns:
            Flakiness score (higher = more flaky)
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

        results = [
            r for r in self._test_results.values()
            if r.test_name == test_name and r.timestamp_utc > cutoff
        ]

        if not results:
            return 0.0

        flakes = sum(1 for r in results if r.status == TestResultType.FLAKE)
        return min(1.0, flakes / len(results))

    def get_stats(self, window_days: int = 7) -> dict:
        """Get overall statistics.

        Args:
            window_days: Time window

        Returns:
            Statistics dictionary
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)

        with self._lock:
            recent = [r for r in self._test_results.values() if r.timestamp_utc > cutoff]

        total = len(recent)
        if total == 0:
            return {
                "total_tests": 0,
                "pass_rate": 0.0,
                "fail_rate": 0.0,
                "flake_rate": 0.0,
                "avg_duration_ms": 0,
            }

        passed = sum(1 for r in recent if r.status == TestResultType.PASS)
        failed = sum(1 for r in recent if r.status == TestResultType.FAIL)
        flaked = sum(1 for r in recent if r.status == TestResultType.FLAKE)

        avg_duration = sum(r.duration_ms for r in recent) / total

        return {
            "total_tests": total,
            "pass_rate": passed / total,
            "fail_rate": failed / total,
            "flake_rate": flaked / total,
            "avg_duration_ms": avg_duration,
            "num_patterns": len(self._patterns),
        }
