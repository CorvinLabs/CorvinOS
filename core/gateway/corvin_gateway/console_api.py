"""
CorvinOS Console REST API — Phase 1 Implementation

Endpoints:
  POST /v1/console/auth/login
  POST /v1/console/auth/logout
  GET  /v1/console/auth/whoami
  GET  /v1/console/dashboard
  GET  /v1/console/profile
  PUT  /v1/console/profile

Session Management:
  - Cookie: corvin_console_sid (HttpOnly, Secure, SameSite)
  - CSRF Token: X-CSRF-Token header (required for mutations)
  - Auth: Bearer token alternative (for CLI/API clients)

Data Isolation:
  - Per-session state (Redis-backed, in-memory for mock)
  - No cross-session data leakage
  - Timestamps UTC

Error Handling:
  - 400: Bad Request (validation)
  - 401: Unauthorized (no session)
  - 403: Forbidden (CSRF failed, permission denied)
  - 500: Server error (logged, safe message)
"""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr


# ── Token Metrics Configuration ──────────────────────────────────────────
# Claude Opus 4.1 pricing as of 2026-08-18
# Source: https://www.anthropic.com/pricing
COST_PER_1K_TOKENS = 0.0084  # USD


# ── Session & CSRF Management ────────────────────────────────────────────

_SESSIONS: dict[str, dict[str, Any]] = {}
_TEST_USER = {
    "email": "test@example.com",
    "password_hash": hashlib.sha256(b"password123").hexdigest(),  # test cred
    "tier": "owner",
    "tenant_id": "_default",
}


def _gen_csrf_token() -> str:
    """Generate a secure CSRF token (32 bytes hex)."""
    return secrets.token_hex(16)


def _gen_session_id() -> str:
    """Generate a session ID."""
    return secrets.token_hex(32)


def _verify_csrf(request: Request, token: Optional[str]) -> bool:
    """Verify CSRF token from request headers."""
    if not token:
        return False
    sid = request.cookies.get("corvin_console_sid")
    if not sid or sid not in _SESSIONS:
        return False
    stored_token = _SESSIONS[sid].get("csrf_token")
    return stored_token == token


def _get_session(request: Request) -> dict[str, Any] | None:
    """Get the current session from cookie."""
    sid = request.cookies.get("corvin_console_sid")
    if not sid:
        return None
    session = _SESSIONS.get(sid)
    if not session:
        return None
    # Check expiration (24 hours)
    if time.time() > session.get("expires_at", 0):
        del _SESSIONS[sid]
        return None
    return session


# ── Models ───────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    tier: str
    tenant_id: str
    fingerprint: str
    csrf_token: str
    expires_at: float


class WhoamiResponse(BaseModel):
    tier: str
    tenant_id: str
    fingerprint: str
    csrf_token: str
    expires_at: float


class DashboardResponse(BaseModel):
    engines_online: int
    channels: list[str]
    audit_events_today: int
    last_sync: str
    uptime_percent: float


class ProfileResponse(BaseModel):
    notification_sound: str
    theme: str
    language: str
    timezone: str


class ProfileUpdateRequest(BaseModel):
    notification_sound: Optional[str] = None
    theme: Optional[str] = None
    language: Optional[str] = None
    timezone: Optional[str] = None


# ── Router ───────────────────────────────────────────────────────────────

router = APIRouter(prefix="/v1/console", tags=["console"])


@router.post("/auth/login", response_model=LoginResponse, status_code=200)
async def login(request: LoginRequest, response: Response) -> LoginResponse:
    """Authenticate with email + password, return session cookie + CSRF token."""
    # Validate credentials (demo: only test@example.com/password123)
    if request.email != _TEST_USER["email"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    password_hash = hashlib.sha256(request.password.encode()).hexdigest()
    if password_hash != _TEST_USER["password_hash"]:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Create session
    sid = _gen_session_id()
    csrf_token = _gen_csrf_token()
    expires_at = time.time() + 86400  # 24 hours
    fingerprint = hashlib.sha256(f"{request.email}:{sid}".encode()).hexdigest()[:16]

    _SESSIONS[sid] = {
        "email": request.email,
        "tier": _TEST_USER["tier"],
        "tenant_id": _TEST_USER["tenant_id"],
        "csrf_token": csrf_token,
        "expires_at": expires_at,
        "created_at": time.time(),
    }

    # Set session cookie
    response.set_cookie(
        key="corvin_console_sid",
        value=sid,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=86400,
    )

    return LoginResponse(
        tier=_TEST_USER["tier"],
        tenant_id=_TEST_USER["tenant_id"],
        fingerprint=fingerprint,
        csrf_token=csrf_token,
        expires_at=expires_at,
    )


@router.post("/auth/logout", status_code=200)
async def logout(request: Request) -> dict[str, bool]:
    """Invalidate session and clear session cookie."""
    sid = request.cookies.get("corvin_console_sid")
    if sid and sid in _SESSIONS:
        del _SESSIONS[sid]
    return {"success": True}


@router.get("/auth/whoami", response_model=WhoamiResponse)
async def whoami(request: Request) -> WhoamiResponse:
    """Get current user session info."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    csrf_token = _gen_csrf_token()  # Refresh CSRF token on each call
    session["csrf_token"] = csrf_token

    fingerprint = hashlib.sha256(
        f"{session['email']}:{request.cookies.get('corvin_console_sid')}".encode()
    ).hexdigest()[:16]

    return WhoamiResponse(
        tier=session["tier"],
        tenant_id=session["tenant_id"],
        fingerprint=fingerprint,
        csrf_token=csrf_token,
        expires_at=session["expires_at"],
    )


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(request: Request) -> DashboardResponse:
    """Get dashboard metrics (requires session)."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    return DashboardResponse(
        engines_online=2,
        channels=["discord", "slack", "telegram"],
        audit_events_today=142,
        last_sync=datetime.now(timezone.utc).isoformat(),
        uptime_percent=99.8,
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile(request: Request) -> ProfileResponse:
    """Get user profile settings."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    # Return default profile
    return ProfileResponse(
        notification_sound="bell",
        theme="light",
        language="en",
        timezone="UTC",
    )


@router.put("/profile", response_model=ProfileResponse, status_code=200)
async def update_profile(
    request: Request, payload: ProfileUpdateRequest
) -> ProfileResponse:
    """Update user profile (requires CSRF token)."""
    session = _get_session(request)
    if not session:
        raise HTTPException(status_code=401, detail="no session")

    # Verify CSRF token
    csrf_token = request.headers.get("X-CSRF-Token")
    if not _verify_csrf(request, csrf_token):
        raise HTTPException(status_code=403, detail="CSRF token invalid")

    # In real impl, would save to DB
    # For now, return updated values
    return ProfileResponse(
        notification_sound=payload.notification_sound or "bell",
        theme=payload.theme or "light",
        language=payload.language or "en",
        timezone=payload.timezone or "UTC",
    )


class LearningNode(BaseModel):
    """Learning node/discovery pattern."""
    pattern_id: str
    pattern_name: str
    error_type: str
    confidence: float
    when_conditions: list[str]
    anti_when_conditions: list[str]
    sample_count: int
    timestamp: str


class LearningNodesResponse(BaseModel):
    """Learning nodes API response."""
    nodes: list[LearningNode]
    total_count: int


class MetricsSummary(BaseModel):
    """Token metrics summary."""
    turn_count: int
    total_tokens: int
    baseline_tokens: int
    savings_tokens: int
    savings_percent: float
    avg_tokens_per_turn: float
    estimated_baseline_cost: float
    estimated_actual_cost: float
    estimated_savings: float
    by_task_type: dict[str, Any]


class MetricsResponse(BaseModel):
    """Metrics API response."""
    session_id: str
    summary: MetricsSummary


@router.get("/metrics/stats", response_model=MetricsResponse)
async def get_metrics_stats(session: SessionRecord = Depends(require_session)) -> MetricsResponse:
    """Get overall token metrics statistics."""
    try:
        db_path = Path.home() / ".corvin" / "token_metrics.db"
        if not db_path.exists():
            return MetricsResponse(
                session_id="none",
                summary=MetricsSummary(
                    turn_count=0,
                    total_tokens=0,
                    baseline_tokens=0,
                    savings_tokens=0,
                    savings_percent=0.0,
                    avg_tokens_per_turn=0.0,
                    estimated_baseline_cost=0.0,
                    estimated_actual_cost=0.0,
                    estimated_savings=0.0,
                    by_task_type={},
                ),
            )

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) as turn_count,
                SUM(total_tokens) as total_tokens,
                SUM(baseline_tokens) as baseline_tokens,
                SUM(baseline_tokens - total_tokens) as savings_tokens,
                AVG(savings_percent) as avg_savings_percent,
                task_type
            FROM token_metrics
            GROUP BY task_type
        """)
        by_task = cursor.fetchall()

        cursor.execute("""
            SELECT
                COUNT(*) as turn_count,
                SUM(total_tokens) as total_tokens,
                SUM(baseline_tokens) as baseline_tokens,
                SUM(baseline_tokens - total_tokens) as savings_tokens,
                AVG(savings_percent) as avg_savings_percent
            FROM token_metrics
        """)
        row = cursor.fetchone()
        conn.close()

        if not row or not row[0]:
            return MetricsResponse(
                session_id="none",
                summary=MetricsSummary(
                    turn_count=0,
                    total_tokens=0,
                    baseline_tokens=0,
                    savings_tokens=0,
                    savings_percent=0.0,
                    avg_tokens_per_turn=0.0,
                    estimated_baseline_cost=0.0,
                    estimated_actual_cost=0.0,
                    estimated_savings=0.0,
                    by_task_type={},
                ),
            )

        turn_count, total_tokens, baseline_tokens, savings_tokens, avg_savings = row
        turn_count = turn_count or 0
        total_tokens = total_tokens or 0
        baseline_tokens = baseline_tokens or 0
        savings_tokens = savings_tokens or 0
        avg_savings = avg_savings or 0.0

        cost_per_1k = COST_PER_1K_TOKENS
        baseline_cost = (baseline_tokens / 1000 * cost_per_1k) if baseline_tokens else 0.0
        actual_cost = (total_tokens / 1000 * cost_per_1k) if total_tokens else 0.0
        savings_cost = baseline_cost - actual_cost

        by_task_type = {}
        for task_type, count, tot, base, sav, pct in by_task:
            by_task_type[task_type] = {
                "turns": count,
                "total_tokens": tot or 0,
                "baseline_tokens": base or 0,
                "savings_tokens": sav or 0,
                "savings_percent": round(pct or 0.0, 1),
            }

        return MetricsResponse(
            session_id="current",
            summary=MetricsSummary(
                turn_count=turn_count,
                total_tokens=total_tokens,
                baseline_tokens=baseline_tokens,
                savings_tokens=savings_tokens,
                savings_percent=round(avg_savings, 1),
                avg_tokens_per_turn=round(total_tokens / turn_count, 1) if turn_count > 0 else 0.0,
                estimated_baseline_cost=round(baseline_cost, 2),
                estimated_actual_cost=round(actual_cost, 2),
                estimated_savings=round(savings_cost, 2),
                by_task_type=by_task_type,
            ),
        )
    except Exception as e:
        print(f"Error fetching metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch metrics")


@router.get("/metrics/session/{session_id}", response_model=MetricsResponse)
async def get_session_metrics(
    session_id: str,
    session: SessionRecord = Depends(require_session),
) -> MetricsResponse:
    """Get token metrics for a specific session."""
    try:
        db_path = Path.home() / ".corvin" / "token_metrics.db"
        if not db_path.exists():
            raise HTTPException(status_code=404, detail="Metrics database not found")

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                COUNT(*) as turn_count,
                SUM(total_tokens) as total_tokens,
                SUM(baseline_tokens) as baseline_tokens,
                SUM(baseline_tokens - total_tokens) as savings_tokens,
                AVG(savings_percent) as avg_savings_percent
            FROM token_metrics
            WHERE session_id = ?
        """, (session_id,))
        row = cursor.fetchone()
        conn.close()

        if not row or not row[0]:
            raise HTTPException(status_code=404, detail="No metrics found for session")

        turn_count, total_tokens, baseline_tokens, savings_tokens, avg_savings = row
        turn_count = turn_count or 0
        total_tokens = total_tokens or 0
        baseline_tokens = baseline_tokens or 0
        savings_tokens = savings_tokens or 0
        avg_savings = avg_savings or 0.0

        cost_per_1k = COST_PER_1K_TOKENS
        baseline_cost = (baseline_tokens / 1000 * cost_per_1k) if baseline_tokens else 0.0
        actual_cost = (total_tokens / 1000 * cost_per_1k) if total_tokens else 0.0
        savings_cost = baseline_cost - actual_cost

        return MetricsResponse(
            session_id=session_id,
            summary=MetricsSummary(
                turn_count=turn_count,
                total_tokens=total_tokens,
                baseline_tokens=baseline_tokens,
                savings_tokens=savings_tokens,
                savings_percent=round(avg_savings, 1),
                avg_tokens_per_turn=round(total_tokens / turn_count, 1) if turn_count > 0 else 0.0,
                estimated_baseline_cost=round(baseline_cost, 2),
                estimated_actual_cost=round(actual_cost, 2),
                estimated_savings=round(savings_cost, 2),
                by_task_type={},
            ),
        )
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        print(f"Error fetching session metrics: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch metrics")


@router.get("/learning/nodes", response_model=LearningNodesResponse)
async def get_learning_nodes(session: SessionRecord = Depends(require_session)) -> LearningNodesResponse:
    """Get learning nodes and discovered patterns from TreeOfThoughts."""
    try:
        discoveries_path = Path.home() / ".corvin" / "learning" / "discoveries" / "discoveries.jsonl"

        if not discoveries_path.exists():
            return LearningNodesResponse(nodes=[], total_count=0)

        nodes: list[LearningNode] = []
        unique_patterns: dict[str, LearningNode] = {}

        with open(discoveries_path, 'r') as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    pattern_id = data.get('pattern_id', '')

                    if pattern_id not in unique_patterns:
                        node = LearningNode(
                            pattern_id=pattern_id,
                            pattern_name=data.get('pattern_name', 'Unknown'),
                            error_type=data.get('error_type', 'unknown'),
                            confidence=(data.get('confidence_when', 0.5) + data.get('confidence_anti_when', 0.5)) / 2,
                            when_conditions=data.get('when_conditions', []),
                            anti_when_conditions=data.get('anti_when_conditions', []),
                            sample_count=data.get('sample_count', 0),
                            timestamp=data.get('timestamp', datetime.now(timezone.utc).isoformat()),
                        )
                        unique_patterns[pattern_id] = node
                except json.JSONDecodeError:
                    continue

        nodes = list(unique_patterns.values())

        return LearningNodesResponse(
            nodes=nodes,
            total_count=len(nodes),
        )
    except Exception as e:
        print(f"Error fetching learning nodes: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch learning nodes")


@router.get("/health")
async def health_check() -> dict[str, str]:
    """Health check endpoint (no auth required)."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Standalone dev server (for testing) ──────────────────────────────────


if __name__ == "__main__":
    import uvicorn

    from fastapi import FastAPI

    app = FastAPI(
        title="CorvinOS Console API (Mock)",
        version="0.1.0",
        description="Mock Console API for local E2E testing",
    )

    app.include_router(router)

    print("🚀 Starting Console API on http://localhost:8765")
    print("   Test user: test@example.com / password123")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")
