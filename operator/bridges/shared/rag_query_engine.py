"""RAG Query Engine — Core query execution and response handling.

Handles:
- Query execution against a single RAG provider
- Response transformation per provider schema
- Error handling with circuit breaker
- Timeout management
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore

logger = logging.getLogger(__name__)


# ── Enums ────────────────────────────────────────────────────

class QueryStatus(Enum):
    """Query execution status."""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    AUTH_ERROR = "auth_error"
    MALFORMED_RESPONSE = "malformed_response"
    PROVIDER_ERROR = "provider_error"
    UNKNOWN_ERROR = "unknown_error"


class CircuitState(Enum):
    """Circuit breaker state."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


# ── Data Models ────────────────────────────────────────────

@dataclass
class RAGQuery:
    """User's retrieval request."""
    query: str
    limit: int = 5
    metadata_filters: Optional[dict] = None
    preferred_providers: Optional[list[str]] = None
    timeout_ms: int = 5000

    def __post_init__(self):
        """Validate query."""
        if not self.query or not self.query.strip():
            raise ValueError("Query cannot be empty")
        if self.limit < 1 or self.limit > 100:
            raise ValueError("Limit must be between 1 and 100")


@dataclass
class RAGResultItem:
    """Single result from a provider."""
    content: str
    score: float  # 0.0-1.0
    metadata: dict = field(default_factory=dict)
    source_url: Optional[str] = None

    def __post_init__(self):
        """Validate result."""
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"Score must be 0.0-1.0, got {self.score}")


@dataclass
class RAGProviderResult:
    """Results from a single provider."""
    provider_id: str
    status: QueryStatus
    items: list[RAGResultItem] = field(default_factory=list)
    latency_ms: int = 0
    error_message: Optional[str] = None


# ── Circuit Breaker ────────────────────────────────────────

@dataclass
class CircuitBreaker:
    """Simple circuit breaker pattern for provider health."""
    failure_threshold: int = 5
    timeout_seconds: int = 30

    failures: int = 0
    last_failure_time: Optional[float] = None
    state: CircuitState = CircuitState.CLOSED

    def record_success(self) -> None:
        """Record successful call."""
        self.failures = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record failed call."""
        self.failures += 1
        self.last_failure_time = time.time()

        if self.failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                f"Circuit breaker OPEN after {self.failures} failures"
            )

    def can_execute(self) -> bool:
        """Check if execution allowed."""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Check if timeout has passed. Guard with `is not None` (not a bare
            # truthiness test): a last_failure_time of 0 (epoch) is a valid
            # "long ago" timestamp and must still allow recovery, not be treated
            # as "never failed" — otherwise the breaker can latch OPEN forever.
            if self.last_failure_time is not None:
                elapsed = time.time() - self.last_failure_time
                if elapsed >= self.timeout_seconds:
                    self.state = CircuitState.HALF_OPEN
                    logger.info("Circuit breaker HALF_OPEN, attempting recovery")
                    return True
            return False

        # HALF_OPEN: try one request
        return True

    def reset(self) -> None:
        """Reset circuit breaker."""
        self.failures = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED


# ── Query Engine ────────────────────────────────────────────

class RAGQueryEngine:
    """Execute queries against a single RAG provider."""

    def __init__(self, provider_id: str, manifest: dict, auth_tokens: dict):
        """Initialize query engine for a provider."""
        self.provider_id = provider_id
        self.manifest = manifest
        self.auth_tokens = auth_tokens
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=manifest.get("spec", {})
            .get("resilience", {})
            .get("circuit_breaker", {})
            .get("failure_threshold", 5),
            timeout_seconds=manifest.get("spec", {})
            .get("resilience", {})
            .get("circuit_breaker", {})
            .get("timeout_seconds", 30),
        )
        self.http_client = httpx.AsyncClient() if httpx else None

    async def execute(self, query: RAGQuery) -> RAGProviderResult:
        """Execute query against provider."""
        start_time = time.time()

        # Check circuit breaker
        if not self.circuit_breaker.can_execute():
            return RAGProviderResult(
                provider_id=self.provider_id,
                status=QueryStatus.PROVIDER_ERROR,
                error_message="Circuit breaker OPEN",
                latency_ms=int((time.time() - start_time) * 1000),
            )

        try:
            # Transform query
            provider_query = self._transform_query(query)

            # Build request
            endpoint = self.manifest["spec"]["retrieval"]["endpoint"]
            method = self.manifest["spec"]["retrieval"].get("method", "POST")
            headers = self._build_headers()
            timeout = self.manifest["spec"]["retrieval"].get("timeout_ms", 5000) / 1000

            # Execute
            if not self.http_client:
                raise RuntimeError("httpx not installed")

            if method == "GET":
                response = await self.http_client.get(
                    endpoint,
                    params=provider_query,
                    headers=headers,
                    timeout=timeout,
                )
            else:
                response = await self.http_client.post(
                    endpoint,
                    json=provider_query,
                    headers=headers,
                    timeout=timeout,
                )

            response.raise_for_status()

            # Transform response
            items = self._transform_response(response.json())

            # Record success
            latency_ms = int((time.time() - start_time) * 1000)
            self.circuit_breaker.record_success()

            return RAGProviderResult(
                provider_id=self.provider_id,
                status=QueryStatus.SUCCESS,
                items=items,
                latency_ms=latency_ms,
            )

        except asyncio.TimeoutError:
            latency_ms = int((time.time() - start_time) * 1000)
            self.circuit_breaker.record_failure()
            return RAGProviderResult(
                provider_id=self.provider_id,
                status=QueryStatus.TIMEOUT,
                error_message=f"Query timeout after {latency_ms}ms",
                latency_ms=latency_ms,
            )

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                status = QueryStatus.AUTH_ERROR
            else:
                status = QueryStatus.PROVIDER_ERROR
            latency_ms = int((time.time() - start_time) * 1000)
            self.circuit_breaker.record_failure()
            return RAGProviderResult(
                provider_id=self.provider_id,
                status=status,
                error_message=f"HTTP {e.response.status_code}: {e}",
                latency_ms=latency_ms,
            )

        except (json.JSONDecodeError, ValueError) as e:
            latency_ms = int((time.time() - start_time) * 1000)
            self.circuit_breaker.record_failure()
            return RAGProviderResult(
                provider_id=self.provider_id,
                status=QueryStatus.MALFORMED_RESPONSE,
                error_message=f"Response parsing error: {e}",
                latency_ms=latency_ms,
            )

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)
            self.circuit_breaker.record_failure()
            logger.error(f"Query error in {self.provider_id}: {e}")
            return RAGProviderResult(
                provider_id=self.provider_id,
                status=QueryStatus.UNKNOWN_ERROR,
                error_message=str(e),
                latency_ms=latency_ms,
            )

    def _transform_query(self, query: RAGQuery) -> dict:
        """Transform unified query to provider-specific format."""
        provider_id = self.provider_id

        # Default: pass through
        return {
            "query": query.query,
            "limit": query.limit,
        }

    def _transform_response(self, raw_response: dict) -> list[RAGResultItem]:
        """Transform provider response to unified format."""
        # Default: expect { results: [{content, score, metadata}, ...] }
        results = []
        for item in raw_response.get("results", []):
            results.append(
                RAGResultItem(
                    content=item.get("content", ""),
                    score=min(1.0, max(0.0, float(item.get("score", 0.5)))),
                    metadata=item.get("metadata", {}),
                    source_url=item.get("source_url"),
                )
            )
        return results

    def _build_headers(self) -> dict:
        """Build HTTP headers with auth."""
        headers = {"Accept": "application/json"}

        auth = self.manifest["spec"]["retrieval"]["auth"]
        auth_type = auth.get("type", "none")
        token_env_var = auth.get("token_env_var")

        if auth_type == "none":
            return headers

        token = self.auth_tokens.get(token_env_var)
        if not token:
            logger.warning(f"Auth token not found: {token_env_var}")
            return headers

        if auth_type == "bearer-token":
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api-key":
            headers["X-API-Key"] = token

        return headers

    async def close(self) -> None:
        """Close HTTP client."""
        if self.http_client:
            await self.http_client.aclose()
