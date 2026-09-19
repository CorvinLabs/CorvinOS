"""
Phase 5.1.3: Marketplace SLO Monitoring Middleware (ADR-0892 amendment)

SLO Gates:
- Error rate: <0.1% (fail-dark: 0.09%)
- P99 latency: <500ms
- Circuit breaker: engages on breach, recovery after 60s of healthy metrics

Integration: FastAPI middleware on marketplace routes + RealtimeHealthMonitor.
Alerts: Log + Slack webhook (optional) on threshold breach.
"""

import time
import json
import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, Any
from collections import deque
import threading

logger = logging.getLogger(__name__)


@dataclass
class MarketplaceMetric:
    """One request's latency + status."""
    timestamp: float
    latency_ms: float
    status_code: int
    path: str
    
    @property
    def is_error(self) -> bool:
        return self.status_code >= 400


class MarketplaceSLOMonitor:
    """Real-time SLO tracking for marketplace endpoints."""
    
    def __init__(
        self,
        window_size_sec: int = 30,
        p99_threshold_ms: float = 500.0,
        error_rate_threshold_pct: float = 0.1,
        recovery_delay_sec: int = 60,
    ):
        self.window_size_sec = window_size_sec
        self.p99_threshold_ms = p99_threshold_ms
        self.error_rate_threshold_pct = error_rate_threshold_pct
        self.recovery_delay_sec = recovery_delay_sec
        
        # Metrics buffer (rolling window)
        self.metrics: deque = deque()
        self.lock = threading.Lock()
        
        # Circuit breaker state
        self.circuit_breaker_until: Optional[float] = None
        self.last_health_check: float = time.time()
        
    def record_request(self, latency_ms: float, status_code: int, path: str) -> None:
        """Record one marketplace request."""
        metric = MarketplaceMetric(
            timestamp=time.time(),
            latency_ms=latency_ms,
            status_code=status_code,
            path=path,
        )
        with self.lock:
            self.metrics.append(metric)
            # Prune old metrics (outside window)
            cutoff = time.time() - self.window_size_sec
            while self.metrics and self.metrics[0].timestamp < cutoff:
                self.metrics.popleft()
    
    def is_circuit_breaker_open(self) -> bool:
        """True if circuit breaker is currently engaged."""
        if self.circuit_breaker_until is None:
            return False
        now = time.time()
        if now < self.circuit_breaker_until:
            return True
        # Breaker closed; attempt to reset
        self.circuit_breaker_until = None
        return False
    
    def check_slos(self) -> Dict[str, Any]:
        """Evaluate SLOs and return current status."""
        with self.lock:
            metrics_list = list(self.metrics)
        
        if not metrics_list:
            return {
                "status": "UNKNOWN",
                "p99_latency_ms": 0,
                "error_rate_pct": 0.0,
                "sample_count": 0,
                "breached": False,
            }
        
        # Calculate metrics
        latencies = sorted([m.latency_ms for m in metrics_list])
        p99_latency = latencies[max(0, int(len(latencies) * 0.99) - 1)]
        
        errors = sum(1 for m in metrics_list if m.is_error)
        error_rate = (errors / len(metrics_list)) * 100.0
        
        # Check thresholds
        latency_breached = p99_latency > self.p99_threshold_ms
        error_breached = error_rate > self.error_rate_threshold_pct
        breached = latency_breached or error_breached
        
        status = "RED" if breached else "GREEN"
        
        result = {
            "status": status,
            "p99_latency_ms": round(p99_latency, 1),
            "error_rate_pct": round(error_rate, 2),
            "sample_count": len(metrics_list),
            "breached": breached,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        
        # Engage circuit breaker if breached
        if breached and self.circuit_breaker_until is None:
            self.circuit_breaker_until = time.time() + self.recovery_delay_sec
            alert_slo_breach(result, self.recovery_delay_sec)
        
        return result


def alert_slo_breach(slo_status: Dict[str, Any], recovery_delay_sec: int) -> None:
    """Send alert on SLO breach (log + optional Slack)."""
    message = (
        f"🚨 MARKETPLACE SLO BREACH\n"
        f"Status: {slo_status['status']}\n"
        f"P99 Latency: {slo_status['p99_latency_ms']}ms (threshold: 500ms)\n"
        f"Error Rate: {slo_status['error_rate_pct']}% (threshold: 0.1%)\n"
        f"Samples: {slo_status['sample_count']}\n"
        f"Circuit Breaker engaged for {recovery_delay_sec}s"
    )
    logger.warning(message)
    
    # Optional: Slack webhook
    import os
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL_MARKETPLACE_SLO")
    if slack_webhook:
        try:
            import httpx
            httpx.post(
                slack_webhook,
                json={"text": message},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Failed to send Slack alert: {e}")


# ── FastAPI Middleware ────────────────────────────────────────────────────

_monitor = MarketplaceSLOMonitor()


def get_slo_monitor() -> MarketplaceSLOMonitor:
    """Singleton SLO monitor instance."""
    return _monitor


async def marketplace_slo_middleware(request, call_next):
    """
    Middleware to track latency + errors for /api/v1/marketplace/* paths.
    
    Install in FastAPI app:
    ```python
    from starlette.middleware.base import BaseHTTPMiddleware
    
    class MarketplaceSLOMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            return await marketplace_slo_middleware(request, call_next)
    
    app.add_middleware(MarketplaceSLOMiddleware)
    ```
    """
    # Check if this is a marketplace request
    if not request.url.path.startswith("/v1/console/api/v1/marketplace"):
        return await call_next(request)
    
    # Check circuit breaker
    monitor = get_slo_monitor()
    if monitor.is_circuit_breaker_open():
        from starlette.responses import JSONResponse
        return JSONResponse(
            {
                "error": "Service temporarily unavailable (SLO recovery)",
                "retry_after_sec": 30,
            },
            status_code=503,
        )
    
    # Measure request
    start = time.time()
    try:
        response = await call_next(request)
        latency_ms = (time.time() - start) * 1000
        
        # Record metric
        monitor.record_request(
            latency_ms=latency_ms,
            status_code=response.status_code,
            path=request.url.path,
        )
        
        # Log latency
        if latency_ms > 300:
            logger.warning(
                f"🟡 Marketplace latency high: {latency_ms:.1f}ms "
                f"({request.method} {request.url.path})"
            )
        
        return response
    except Exception as e:
        latency_ms = (time.time() - start) * 1000
        monitor.record_request(
            latency_ms=latency_ms,
            status_code=500,
            path=request.url.path,
        )
        raise


# ── Console Endpoint: SLO Status ──────────────────────────────────────────

def register_slo_status_endpoint(router):
    """Register /api/v1/marketplace/slo-status endpoint."""
    from fastapi import APIRouter
    from typing import Dict, Any
    
    slo_router = APIRouter(prefix="/api/v1/marketplace", tags=["marketplace-slo"])
    
    @slo_router.get("/slo-status")
    async def get_slo_status() -> Dict[str, Any]:
        """
        Get current marketplace SLO status.
        
        Returns:
        {
          "status": "GREEN" | "RED",
          "p99_latency_ms": float,
          "error_rate_pct": float,
          "sample_count": int,
          "breached": bool,
          "circuit_breaker_open": bool,
          "timestamp": "2026-09-20T..."
        }
        """
        monitor = get_slo_monitor()
        status = monitor.check_slos()
        status["circuit_breaker_open"] = monitor.is_circuit_breaker_open()
        return status
    
    return slo_router


if __name__ == "__main__":
    # Demo: create monitor, record some requests
    monitor = MarketplaceSLOMonitor()
    
    # Simulate requests
    import random
    for i in range(50):
        latency = random.gauss(150, 50)  # Normal distribution, mean 150ms, std 50ms
        status = 200 if random.random() > 0.001 else 500  # 0.1% error
        monitor.record_request(latency, status, "/api/v1/marketplace/plugins")
    
    status = monitor.check_slos()
    print(f"✅ SLO Status: {json.dumps(status, indent=2)}")
