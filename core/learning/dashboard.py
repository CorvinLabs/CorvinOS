"""Reporting Dashboard — observability UI (ADR-0321).

Provides REST API + WebSocket endpoints for observability:
- GET /api/learning/summary — system-wide metrics
- GET /api/learning/skills/{skill_name} — per-skill stats
- GET /api/learning/user/{user_id} — user-scoped metrics
- WS /api/learning/stream — real-time updates

Implements caching (5s TTL) to prevent EventStore hammering.
Every query execution is audit-logged (not view events).
Tenant isolation enforced on all queries.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable
from enum import Enum

from core.learning.learning_events import EventType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricSummary:
    """Summary statistics for a metric."""

    metric_type: str
    count: int
    mean: float
    min: float
    max: float
    stddev: float

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "metric_type": self.metric_type,
            "count": self.count,
            "mean": self.mean,
            "min": self.min,
            "max": self.max,
            "stddev": self.stddev,
        }


@dataclass(frozen=True)
class SkillPerformance:
    """Aggregated performance metrics for a skill."""

    skill_name: str
    accuracy: Optional[float] = None
    latency_ms: Optional[float] = None
    confidence: Optional[float] = None
    user_satisfaction: Optional[float] = None
    usage_count: int = 0
    last_updated: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "skill_name": self.skill_name,
            "accuracy": self.accuracy,
            "latency_ms": self.latency_ms,
            "confidence": self.confidence,
            "user_satisfaction": self.user_satisfaction,
            "usage_count": self.usage_count,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


@dataclass(frozen=True)
class DashboardMetrics:
    """Complete dashboard metrics snapshot."""

    timestamp: datetime
    accuracy_summary: Optional[MetricSummary] = None
    latency_summary: Optional[MetricSummary] = None
    confidence_summary: Optional[MetricSummary] = None
    satisfaction_summary: Optional[MetricSummary] = None
    skills: dict = field(default_factory=dict)
    total_events: int = 0

    def to_dict(self) -> dict:
        """Convert to dict for API response."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "accuracy_summary": self.accuracy_summary.to_dict() if self.accuracy_summary else None,
            "latency_summary": self.latency_summary.to_dict() if self.latency_summary else None,
            "confidence_summary": self.confidence_summary.to_dict() if self.confidence_summary else None,
            "satisfaction_summary": self.satisfaction_summary.to_dict() if self.satisfaction_summary else None,
            "skills": {name: perf.to_dict() for name, perf in self.skills.items()},
            "total_events": self.total_events,
        }


class MetricsAggregator:
    """Aggregate metrics for observability dashboard."""

    def __init__(self, tenant_id: str):
        """Initialize aggregator.

        Args:
            tenant_id: Tenant ID (for isolation)
        """
        self.tenant_id = tenant_id

    def aggregate_metrics(
        self,
        metrics: list[dict],
        metric_type: Optional[str] = None,
    ) -> Optional[MetricSummary]:
        """Aggregate metrics to summary statistics.

        Args:
            metrics: List of metric dicts from EventStore
            metric_type: Type of metric to aggregate (for label)

        Returns:
            MetricSummary or None if no metrics
        """
        if not metrics:
            return None

        values = [float(m.get("value", 0)) for m in metrics]
        if not values:
            return None

        mean = sum(values) / len(values)
        min_val = min(values)
        max_val = max(values)

        # Calculate stddev
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        stddev = variance ** 0.5

        return MetricSummary(
            metric_type=metric_type or "unknown",
            count=len(metrics),
            mean=round(mean, 4),
            min=round(min_val, 4),
            max=round(max_val, 4),
            stddev=round(stddev, 4),
        )

    def aggregate_skill_performance(
        self,
        accuracy_metrics: list[dict],
        latency_metrics: list[dict],
        confidence_metrics: list[dict],
        satisfaction_metrics: list[dict],
        skill_name: str,
    ) -> SkillPerformance:
        """Aggregate performance metrics for a skill.

        Args:
            accuracy_metrics: List of accuracy metric dicts
            latency_metrics: List of latency metric dicts
            confidence_metrics: List of confidence metric dicts
            satisfaction_metrics: List of satisfaction metric dicts
            skill_name: Name of the skill

        Returns:
            SkillPerformance aggregated stats
        """
        accuracy = None
        if accuracy_metrics:
            accuracy_values = [float(m.get("value", 0)) for m in accuracy_metrics]
            accuracy = round(sum(accuracy_values) / len(accuracy_values), 4)

        latency = None
        if latency_metrics:
            latency_values = [float(m.get("value", 0)) for m in latency_metrics]
            latency = round(sum(latency_values) / len(latency_values), 2)

        confidence = None
        if confidence_metrics:
            confidence_values = [float(m.get("value", 0)) for m in confidence_metrics]
            confidence = round(sum(confidence_values) / len(confidence_values), 4)

        satisfaction = None
        if satisfaction_metrics:
            satisfaction_values = [float(m.get("value", 0)) for m in satisfaction_metrics]
            satisfaction = round(sum(satisfaction_values) / len(satisfaction_values), 2)

        # Usage count is total of all metric types
        usage_count = (
            len(accuracy_metrics)
            + len(latency_metrics)
            + len(confidence_metrics)
            + len(satisfaction_metrics)
        )

        return SkillPerformance(
            skill_name=skill_name,
            accuracy=accuracy,
            latency_ms=latency,
            confidence=confidence,
            user_satisfaction=satisfaction,
            usage_count=usage_count,
            last_updated=datetime.utcnow(),
        )

    def build_dashboard(
        self,
        all_metrics: list[dict],
        skills_by_name: dict = None,
    ) -> DashboardMetrics:
        """Build complete dashboard from metrics.

        Args:
            all_metrics: All metric records
            skills_by_name: Pre-aggregated skill metrics {skill_name: {metric_type: [metrics]}}

        Returns:
            DashboardMetrics dashboard snapshot
        """
        if not all_metrics:
            return DashboardMetrics(timestamp=datetime.utcnow(), total_events=0)

        # Separate by metric type
        accuracy = [m for m in all_metrics if m.get("metric_type") == "accuracy"]
        latency = [m for m in all_metrics if m.get("metric_type") == "latency"]
        confidence = [m for m in all_metrics if m.get("metric_type") == "confidence"]
        satisfaction = [m for m in all_metrics if m.get("metric_type") == "satisfaction"]

        # Aggregate each type
        acc_summary = self.aggregate_metrics(accuracy, "accuracy")
        lat_summary = self.aggregate_metrics(latency, "latency")
        conf_summary = self.aggregate_metrics(confidence, "confidence")
        sat_summary = self.aggregate_metrics(satisfaction, "satisfaction")

        # Build skill performance
        skills_dict = {}
        if skills_by_name:
            for skill_name, metrics_by_type in skills_by_name.items():
                perf = self.aggregate_skill_performance(
                    metrics_by_type.get("accuracy", []),
                    metrics_by_type.get("latency", []),
                    metrics_by_type.get("confidence", []),
                    metrics_by_type.get("satisfaction", []),
                    skill_name,
                )
                skills_dict[skill_name] = perf

        return DashboardMetrics(
            timestamp=datetime.utcnow(),
            accuracy_summary=acc_summary,
            latency_summary=lat_summary,
            confidence_summary=conf_summary,
            satisfaction_summary=sat_summary,
            skills=skills_dict,
            total_events=len(all_metrics),
        )


# Cache key types
class CacheKeyType(Enum):
    """Dashboard cache key types (for audit)."""
    SUMMARY = "dashboard_summary"
    SKILL_STATS = "skill_stats"
    USER_STATS = "user_stats"


@dataclass(frozen=True)
class CacheEntry:
    """In-memory cache entry with TTL."""
    data: dict
    timestamp: datetime
    ttl_seconds: int = 5

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        age = (datetime.utcnow() - self.timestamp).total_seconds()
        return age > self.ttl_seconds


class DashboardCache:
    """Simple TTL-based cache for dashboard metrics (prevent EventStore hammering).

    Caches for 5 seconds; thread-safe.
    """
    def __init__(self, ttl_seconds: int = 5):
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[dict]:
        """Get cached value if not expired."""
        with self._lock:
            entry = self._cache.get(key)
            if entry and not entry.is_expired():
                return entry.data
            return None

    def set(self, key: str, data: dict) -> None:
        """Set cache entry."""
        with self._lock:
            self._cache[key] = CacheEntry(
                data=data,
                timestamp=datetime.utcnow(),
                ttl_seconds=self.ttl_seconds,
            )

    def invalidate(self, key: str) -> None:
        """Invalidate a cache entry."""
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear entire cache."""
        with self._lock:
            self._cache.clear()


#: Hard cap on live WebSocket subscribers per tenant dashboard.
#: ``POST /api/learning/subscribe`` allocated one permanent record per call and
#: nothing ever removed it — 20 000 calls grew RSS by 6.7 MB that was never
#: reclaimed, and ``prune_stale_subscribers`` had zero callers anywhere in the
#: tree (round-4 console review, F5). Registration now prunes first and REFUSES
#: at the cap; the route maps the refusal to 503.
MAX_SUBSCRIBERS_PER_TENANT = 256

#: Inactivity after which a subscriber is pruned on the next registration.
SUBSCRIBER_STALE_SECONDS = 300


class SubscriberLimitExceeded(RuntimeError):
    """Refused: the tenant already holds ``MAX_SUBSCRIBERS_PER_TENANT`` live subscribers."""


class WebSocketSubscriber:
    """Manages a WebSocket subscriber for real-time dashboard updates."""

    def __init__(self, subscriber_id: str, tenant_id: str, user_id: Optional[str] = None):
        self.subscriber_id = subscriber_id
        self.tenant_id = tenant_id
        self.user_id = user_id  # If set, only user-scoped metrics
        self.created_at = datetime.utcnow()
        self.last_activity = datetime.utcnow()

    def is_stale(self, timeout_seconds: int = 300) -> bool:
        """Check if subscriber has been inactive > timeout."""
        age = (datetime.utcnow() - self.last_activity).total_seconds()
        return age > timeout_seconds

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.utcnow()


class LearningDashboard:
    """Main dashboard class for learning observability.

    Provides:
    - Aggregated metrics snapshots (cached)
    - Per-skill performance tracking
    - Per-user satisfaction/engagement
    - Real-time WebSocket updates
    - Audit logging for all queries
    - Tenant isolation (GDPR Art. 32)
    """

    def __init__(
        self,
        tenant_id: str,
        event_store,  # EventStore instance
        audit_backend,  # For logging queries
        cache_ttl_seconds: int = 5,
    ):
        """Initialize dashboard.

        Args:
            tenant_id: Tenant ID for isolation
            event_store: EventStore instance for metric queries
            audit_backend: Audit backend for query logging
            cache_ttl_seconds: Cache TTL (default 5s)
        """
        self.tenant_id = tenant_id
        self.event_store = event_store
        self.audit_backend = audit_backend
        self.cache = DashboardCache(ttl_seconds=cache_ttl_seconds)
        self.aggregator = MetricsAggregator(tenant_id=tenant_id)

        # WebSocket subscribers (key: subscriber_id)
        self._subscribers: dict[str, WebSocketSubscriber] = {}
        self._subscribers_lock = threading.RLock()

        # Query counter (for audit)
        self._query_count = 0
        self._query_count_lock = threading.RLock()

    def _audit_query(self, query_type: str, filters: Optional[dict] = None) -> None:
        """Log query execution to audit backend.

        Logs query execution (not view events) to prevent audit bloat.
        """
        if not self.audit_backend:
            return

        with self._query_count_lock:
            self._query_count += 1
            query_id = str(uuid.uuid4())

        try:
            event_dict = {
                "event_id": query_id,
                "event_type": "dashboard_query_executed",
                "query_type": query_type,
                "filters": filters or {},
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "lom": "core.learning.dashboard.LearningDashboard._audit_query",
            }

            self.audit_backend.write_audit_event(event_dict)
        except Exception as e:
            logger.warning(f"Failed to audit dashboard query: {e}")

    def get_summary_stats(self) -> DashboardMetrics:
        """Get system-wide summary metrics (cached).

        Returns:
            DashboardMetrics snapshot
        """
        cache_key = f"{self.tenant_id}:summary"

        # Check cache. The cached value is the SERIALISED form (``to_dict``),
        # so ``timestamp`` comes back as an ISO string and the nested summaries
        # as dicts. Round-4 review: this used to splat it straight back into
        # ``DashboardMetrics(**cached)``, which produced an object whose
        # ``timestamp`` was a ``str``; the very next ``to_dict()`` then raised
        # ``'str' object has no attribute 'isoformat'`` and the route answered
        # HTTP 500 for every request inside the 5-second TTL. Nothing called
        # /summary twice in a row in a test, so it was never seen.
        cached = self.cache.get(cache_key)
        if cached:
            logger.debug(f"Dashboard summary cache hit for {self.tenant_id}")
            return self._metrics_from_cache(cached)

        # Query metrics from event store
        self._audit_query("summary", filters={})

        # Metrics beyond the event count come from ADR-0320 MetricsCollector,
        # which is not wired yet: those summaries are ``None`` (= not recorded),
        # never a fabricated zero.
        metrics = DashboardMetrics(
            timestamp=datetime.utcnow(),
            total_events=self.event_store.count_events(self.tenant_id),
        )

        # Cache result
        self.cache.set(cache_key, metrics.to_dict())
        return metrics

    @staticmethod
    def _metrics_from_cache(cached: dict) -> "DashboardMetrics":
        """Rebuild a :class:`DashboardMetrics` from its serialised form."""
        raw_ts = cached.get("timestamp")
        if isinstance(raw_ts, datetime):
            timestamp = raw_ts
        else:
            try:
                timestamp = datetime.fromisoformat(str(raw_ts).rstrip("Z"))
            except (TypeError, ValueError):
                timestamp = datetime.utcnow()

        def _summary(value):
            if value is None or isinstance(value, MetricSummary):
                return value
            if isinstance(value, dict):
                allowed = {f for f in MetricSummary.__dataclass_fields__}
                return MetricSummary(**{k: v for k, v in value.items() if k in allowed})
            return None

        skills = {}
        for name, perf in (cached.get("skills") or {}).items():
            if isinstance(perf, SkillPerformance):
                skills[name] = perf
                continue
            if not isinstance(perf, dict):
                continue
            last = perf.get("last_updated")
            try:
                parsed_last = datetime.fromisoformat(str(last).rstrip("Z")) if last else None
            except (TypeError, ValueError):
                parsed_last = None
            skills[name] = SkillPerformance(
                skill_name=perf.get("skill_name", name),
                accuracy=perf.get("accuracy"),
                latency_ms=perf.get("latency_ms"),
                confidence=perf.get("confidence"),
                user_satisfaction=perf.get("user_satisfaction"),
                usage_count=perf.get("usage_count", 0),
                last_updated=parsed_last,
            )

        return DashboardMetrics(
            timestamp=timestamp,
            accuracy_summary=_summary(cached.get("accuracy_summary")),
            latency_summary=_summary(cached.get("latency_summary")),
            confidence_summary=_summary(cached.get("confidence_summary")),
            satisfaction_summary=_summary(cached.get("satisfaction_summary")),
            skills=skills,
            total_events=cached.get("total_events", 0),
        )

    def get_skill_stats(self, skill_name: str) -> SkillPerformance:
        """Get performance metrics for a specific skill (cached).

        Args:
            skill_name: Name of the skill

        Returns:
            SkillPerformance aggregated stats
        """
        cache_key = f"{self.tenant_id}:skill:{skill_name}"

        # Check cache
        cached = self.cache.get(cache_key)
        if cached:
            logger.debug(f"Dashboard skill cache hit for {skill_name}")
            perf_dict = cached
            return SkillPerformance(
                skill_name=perf_dict["skill_name"],
                accuracy=perf_dict.get("accuracy"),
                latency_ms=perf_dict.get("latency_ms"),
                confidence=perf_dict.get("confidence"),
                user_satisfaction=perf_dict.get("user_satisfaction"),
                usage_count=perf_dict.get("usage_count", 0),
                last_updated=datetime.fromisoformat(perf_dict["last_updated"])
                if perf_dict.get("last_updated")
                else None,
            )

        # Audit query
        self._audit_query("skill_stats", filters={"skill_name": skill_name})

        perf = self._aggregate_skill_stats(skill_name)

        # Cache result
        self.cache.set(cache_key, perf.to_dict())
        return perf

    #: How many of a skill's most recent events feed one stats query.
    SKILL_STATS_WINDOW = 1000

    @staticmethod
    def _mean(values: list) -> Optional[float]:
        return (sum(values) / len(values)) if values else None

    def _aggregate_skill_stats(self, skill_name: str) -> SkillPerformance:
        """Per-skill metrics computed from the tenant's REAL learning events.

        Round-4 review, F11: this was ``# Query metrics (placeholder)`` returning
        ``None``/0 for every field, while the route advertised "Accuracy (% of
        successful executions) / Latency (avg response time, ms) / Confidence /
        User satisfaction / Usage count". A dimension with no recorded events
        still returns ``None`` — that is the honest answer and the caller must
        render it as "not recorded", never as zero.
        """
        if self.event_store is None:
            return SkillPerformance(skill_name=skill_name)

        try:
            events = self.event_store.query_events(
                self.tenant_id, skill_id=skill_name, limit=self.SKILL_STATS_WINDOW
            )
        except Exception as exc:  # noqa: BLE001 — a read failure is not a zero
            logger.warning("skill stats unreadable for %s: %s", skill_name, type(exc).__name__)
            return SkillPerformance(skill_name=skill_name)

        outcomes: list[bool] = []
        latencies: list[float] = []
        confidences: list[float] = []
        ratings: list[float] = []
        executions = 0
        last_ts: Optional[str] = None

        for event in events:
            signal = getattr(event, "signal", None) or {}
            etype = getattr(getattr(event, "event_type", None), "value", None)
            ts = getattr(event, "timestamp", None)
            if isinstance(ts, str) and (last_ts is None or ts > last_ts):
                last_ts = ts

            if etype == EventType.SKILL_EXECUTED.value:
                executions += 1
            if etype == EventType.OUTCOME.value and isinstance(signal.get("success"), bool):
                outcomes.append(bool(signal["success"]))

            for key in ("latency_ms", "duration_ms"):
                value = signal.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    latencies.append(float(value))
                    break

            if etype == EventType.CONFIDENCE.value:
                for key in ("confidence", "score", "value"):
                    value = signal.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        confidences.append(float(value))
                        break

            if etype == EventType.FEEDBACK.value:
                rating = signal.get("quality_rating")
                if isinstance(rating, (int, float)) and not isinstance(rating, bool):
                    ratings.append(float(rating))

        parsed_ts: Optional[datetime] = None
        if last_ts:
            try:
                parsed_ts = datetime.fromisoformat(last_ts.rstrip("Z"))
            except ValueError:
                parsed_ts = None

        return SkillPerformance(
            skill_name=skill_name,
            accuracy=(sum(1 for ok in outcomes if ok) / len(outcomes)) if outcomes else None,
            latency_ms=self._mean(latencies),
            confidence=self._mean(confidences),
            user_satisfaction=self._mean(ratings),
            usage_count=executions or len(events),
            last_updated=parsed_ts,
        )

    def get_user_stats(self, user_id: str) -> dict:
        """Get user-scoped metrics (cached).

        Args:
            user_id: User ID

        Returns:
            Dict of user metrics (satisfaction, engagement, etc.)
        """
        cache_key = f"{self.tenant_id}:user:{user_id}"

        # Check cache
        cached = self.cache.get(cache_key)
        if cached:
            logger.debug(f"Dashboard user cache hit for {user_id}")
            return cached

        # Audit query
        self._audit_query("user_stats", filters={"user_id": user_id})

        # Round-4 review, F11: there is NO per-user dimension in the ADR-0314
        # event schema — ``LearningEvent`` is (tenant_id, skill_id, signal) and
        # a user id is deliberately never persisted (GDPR Art. 5, content-free
        # records). This method therefore cannot compute per-user satisfaction
        # or engagement from anything, and returning all-``None`` under a
        # docstring promising them is the defect. It now says so explicitly, so
        # a caller cannot mistake "not recorded" for "zero".
        user_stats = {
            "user_id": user_id,
            "available": False,
            "reason": (
                "learning events carry no user dimension (GDPR Art. 5: records are "
                "content-free and tenant-scoped); per-user metrics are not recorded"
            ),
            "satisfaction_avg": None,
            "engagement_score": None,
            "query_count": None,
            "last_query": None,
        }

        # Cache result
        self.cache.set(cache_key, user_stats)
        return user_stats

    def subscribe_for_updates(self, user_id: Optional[str] = None) -> str:
        """Register a WebSocket subscriber for real-time updates.

        Args:
            user_id: If set, only user-scoped metrics; if None, system-wide

        Returns:
            Subscriber ID for future updates/unsubscribe
        """
        # Wire the pruner to the only path that grows the store. A background
        # sweeper would be a second lifecycle to own; registration is where the
        # cost is incurred, so it is where the cleanup belongs.
        self.prune_stale_subscribers(timeout_seconds=SUBSCRIBER_STALE_SECONDS)

        subscriber_id = str(uuid.uuid4())
        subscriber = WebSocketSubscriber(
            subscriber_id=subscriber_id,
            tenant_id=self.tenant_id,
            user_id=user_id,
        )

        with self._subscribers_lock:
            if len(self._subscribers) >= MAX_SUBSCRIBERS_PER_TENANT:
                raise SubscriberLimitExceeded(
                    f"subscriber limit reached ({MAX_SUBSCRIBERS_PER_TENANT}) for "
                    f"tenant {self.tenant_id!r} — unsubscribe before registering again"
                )
            self._subscribers[subscriber_id] = subscriber

        logger.debug(f"Registered WebSocket subscriber {subscriber_id} (user_id={user_id})")
        return subscriber_id

    def unsubscribe(self, subscriber_id: str) -> bool:
        """Unregister a WebSocket subscriber.

        Args:
            subscriber_id: Subscriber ID from subscribe_for_updates()

        Returns:
            True if subscriber was found and removed
        """
        with self._subscribers_lock:
            removed = subscriber_id in self._subscribers
            self._subscribers.pop(subscriber_id, None)

        if removed:
            logger.debug(f"Unregistered WebSocket subscriber {subscriber_id}")
        return removed

    def touch_subscriber(self, subscriber_id: str) -> bool:
        """Update subscriber activity timestamp.

        Args:
            subscriber_id: Subscriber ID

        Returns:
            True if subscriber found
        """
        with self._subscribers_lock:
            sub = self._subscribers.get(subscriber_id)
            if sub:
                sub.touch()
                return True
        return False

    def prune_stale_subscribers(self, timeout_seconds: int = SUBSCRIBER_STALE_SECONDS) -> int:
        """Remove inactive subscribers.

        Called from :meth:`subscribe_for_updates` (every registration) and by
        the WebSocket handler through :meth:`touch_subscriber`. It used to have
        NO caller at all, which is what made the subscriber store unbounded.

        Args:
            timeout_seconds: Inactivity threshold (default 300s = 5min)

        Returns:
            Number of subscribers pruned
        """
        pruned = 0
        with self._subscribers_lock:
            stale_ids = [
                sub_id
                for sub_id, sub in self._subscribers.items()
                if sub.is_stale(timeout_seconds=timeout_seconds)
            ]
            for sub_id in stale_ids:
                del self._subscribers[sub_id]
                pruned += 1

        if pruned:
            logger.debug(f"Pruned {pruned} stale WebSocket subscribers")
        return pruned

    def get_subscriber_count(self) -> int:
        """Get current subscriber count."""
        with self._subscribers_lock:
            return len(self._subscribers)

    def broadcast_update(
        self,
        message_type: str,
        data: dict,
        user_id_filter: Optional[str] = None,
        callback: Optional[Callable[[str, str], None]] = None,
    ) -> int:
        """Broadcast real-time update to subscribers.

        Args:
            message_type: Type of update (e.g. "metrics_updated", "skill_alert")
            data: Update payload
            user_id_filter: If set, only notify subscribers for this user
            callback: Function to send message to each subscriber (called with subscriber_id, json_message)

        Returns:
            Number of subscribers notified
        """
        message = {
            "type": message_type,
            "data": data,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        message_json = json.dumps(message)
        notified = 0

        with self._subscribers_lock:
            for sub_id, sub in self._subscribers.items():
                # Filter by user_id if specified
                if user_id_filter and sub.user_id != user_id_filter:
                    continue

                if callback:
                    try:
                        callback(sub_id, message_json)
                        notified += 1
                    except Exception as e:
                        logger.warning(f"Failed to send update to subscriber {sub_id}: {e}")

        return notified
