"""Per-tenant rate-limit gate for the Gateway.

ADR-0007 Phase 7.2. Token-bucket gate driven by
``tenant.corvin.yaml::spec.budget.max_runs_per_day`` (Phase 3.1).
A tenant with no `budget` block is unlimited; the gate is a no-op.

Algorithm
---------

Classic Linux-shaped token bucket:

* Each tenant has a budget of N tokens per 24h, refilled
  continuously at N/86400 tokens/s.
* A successful POST consumes 1 token.
* A POST that would push tokens negative returns 429 + audit
  ``gateway.rate_limited``.

The state is process-local; multi-process workers (Phase 7+
follow-up) would share state via SQLite or Redis. The current
shape is correct for the Phase 2 traffic envelope.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402

from . import tenant_config as _tenant_config


# ── Event registration ──────────────────────────────────────────────


_RL_EVENTS = {
    "gateway.rate_limited":     "WARNING",
    "gateway.rate_limit_reset": "INFO",
}
for _evt, _sev in _RL_EVENTS.items():
    _security_events.EVENT_SEVERITY.setdefault(_evt, _sev)


_DAY_S = 86400.0


# ── Bucket state ────────────────────────────────────────────────────


@dataclass
class _Bucket:
    tokens:        float
    last_refill:   float
    rate_per_s:    float
    capacity:      float

    def consume(self, *, now: float, cost: float = 1.0) -> bool:
        """Refill, then attempt to deduct `cost`. Returns True iff
        the bucket had enough tokens."""
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_s)
        self.last_refill = now
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False


# ── Limiter ─────────────────────────────────────────────────────────


class RateLimiter:
    """Per-tenant token-bucket gate.

    Stateless across tenants: state is keyed by tenant_id; an unknown
    tenant gets a fresh bucket on first request.

    Construction is cheap; the FastAPI lifespan instantiates one
    process-wide limiter and stashes it on ``app.state.rate_limiter``.
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, tenant_id: str) -> tuple[bool, _Bucket | None]:
        """Try to consume one request token. Returns
        ``(allowed, bucket_or_None)``. The bucket is None when the
        tenant has no rate-limit configured (unlimited)."""
        validate_tenant_id(tenant_id)
        try:
            cfg = _tenant_config.load_or_default(tenant_id)
        except _tenant_config.TenantConfigMalformed:
            # Fail-OPEN on malformed config: the Phase 3.2 engine-
            # policy gate inside the dispatcher catches and fails
            # closed with the right diagnostic. The rate-limit gate
            # is not the place to surface tenant-config errors.
            return (True, None)
        cap = cfg.spec.budget.max_runs_per_day
        if cap is None or cap <= 0:
            return (True, None)  # unlimited
        rate = float(cap) / _DAY_S
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(tenant_id)
            if bucket is None:
                bucket = _Bucket(
                    tokens=float(cap),
                    last_refill=now,
                    rate_per_s=rate,
                    capacity=float(cap),
                )
                self._buckets[tenant_id] = bucket
            else:
                # Refresh capacity / rate in case the operator
                # edited the tenant config since last check.
                bucket.capacity = float(cap)
                bucket.rate_per_s = rate
            allowed = bucket.consume(now=now)
            return (allowed, bucket if not allowed else None)

    def reset(self, tenant_id: str) -> None:
        """Operator escape hatch: blow away the bucket so the next
        request starts with full capacity. Used by the CLI when a
        tenant gets accidentally blocked."""
        validate_tenant_id(tenant_id)
        with self._lock:
            if tenant_id in self._buckets:
                del self._buckets[tenant_id]
        _audit_rl(
            "gateway.rate_limit_reset",
            tenant_id=tenant_id, details={},
        )


# ── Audit helper ────────────────────────────────────────────────────


def _audit_rl(
    event_type: str,
    *,
    tenant_id: str,
    details: dict[str, Any] | None = None,
    severity: str | None = None,
) -> None:
    try:
        chain = (
            _forge_paths.tenant_global_dir(tenant_id)
            / "forge" / "audit.jsonl"
        )
        _security_events.write_event(
            chain, event_type,
            severity=severity, details=dict(details or {}),
            hash_chain=True,
        )
    except Exception:
        pass


def audit_rate_limited(
    tenant_id: str, *, capacity: float, tokens_remaining: float,
) -> None:
    """Emit the gateway.rate_limited event. The HTTP handler calls
    this from the 429 branch."""
    _audit_rl(
        "gateway.rate_limited",
        tenant_id=tenant_id,
        details={
            "capacity":         capacity,
            "tokens_remaining": round(tokens_remaining, 3),
            "retry_after_s":    max(1, int((1.0 - tokens_remaining) / max(
                capacity / _DAY_S, 1e-9,
            ))),
        },
    )
