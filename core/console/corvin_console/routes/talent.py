"""Talent Score API — User Learning Analytics Dashboard

Mock endpoints for "Your Talent" dashboard displaying:
- Real-time talent score
- Component breakdown (accuracy, learning rate, variety, efficiency)
- Historical trend data
- Task type performance analysis
- Correlation analysis
- Learning insights and achievements
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import auth as session_auth
from ..deps import require_session

router = APIRouter(prefix="/talent", tags=["console-talent"])


@router.get("/score")
async def get_talent_score(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Get current talent score (mock data) — requires valid session."""

    return {
        "talent_score": 7.8,
        "trend": 0.5,
        "components": {
            "accuracy": 0.82,
            "learning_rate": 0.75,
            "variety": 0.88,
            "efficiency": 0.79,
        },
        "ranking": [
            {
                "id": "context_engineering",
                "rank": 1,
                "medal": "🥇",
                "status": "Excellent",
                "accuracy": 0.95,
                "feedback_pct": 94.5,
            },
            {
                "id": "plugin_development",
                "rank": 2,
                "medal": "🥈",
                "status": "Very Good",
                "accuracy": 0.88,
                "feedback_pct": 87.2,
            },
            {
                "id": "bug_fixing",
                "rank": 3,
                "medal": "🥉",
                "status": "Good",
                "accuracy": 0.82,
                "feedback_pct": 81.0,
            },
            {
                "id": "documentation",
                "rank": 4,
                "medal": "⭐",
                "status": "Developing",
                "accuracy": 0.76,
                "feedback_pct": 72.3,
            },
            {
                "id": "testing",
                "rank": 5,
                "medal": "✨",
                "status": "Developing",
                "accuracy": 0.71,
                "feedback_pct": 68.9,
            },
        ],
        "events": [
            {
                "timestamp": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                "type": "achievement",
                "title": "Master Debugger",
                "description": "Fixed 10+ bugs with 95%+ accuracy",
                "badge": "🐛",
            },
            {
                "timestamp": (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat(),
                "type": "milestone",
                "title": "100 Tasks Completed",
                "description": "Completed 100 tasks this month",
                "badge": "🎯",
            },
            {
                "timestamp": (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(),
                "type": "improvement",
                "title": "Score Improvement",
                "description": "Talent score improved 0.5 points this week",
                "badge": "📈",
            },
        ],
    }


@router.get("/history")
async def get_talent_history(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    """Get talent score history (mock data) — requires valid session."""
    # Validate days parameter to prevent DoS
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")

    daily_data = []
    for i in range(days):
        date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        base_score = 7.0 + (i * 0.1)
        daily_data.append({
            "date": date,
            "score": round(base_score + (i * 0.05), 2),
            "accuracy": round(0.75 + (i * 0.02), 2),
            "learning_rate": round(0.70 + (i * 0.015), 2),
            "variety": round(0.85 + (i * 0.01), 2),
            "efficiency": round(0.72 + (i * 0.025), 2),
            "record_count": 15 + (i * 2),
        })

    return {"daily": list(reversed(daily_data))}


@router.get("/task-types")
async def get_talent_task_types(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    """Get task type performance (mock data) — requires valid session."""
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")

    return {
        "task_types": [
            {
                "type": "Bug Fixes",
                "count": 24,
                "accuracy": 0.92,
                "feedback_percentage": 89.5,
                "efficiency": 0.85,
            },
            {
                "type": "Feature Development",
                "count": 18,
                "accuracy": 0.87,
                "feedback_percentage": 84.2,
                "efficiency": 0.78,
            },
            {
                "type": "Code Review",
                "count": 32,
                "accuracy": 0.91,
                "feedback_percentage": 88.7,
                "efficiency": 0.82,
            },
            {
                "type": "Documentation",
                "count": 12,
                "accuracy": 0.75,
                "feedback_percentage": 71.3,
                "efficiency": 0.68,
            },
            {
                "type": "Architecture Design",
                "count": 8,
                "accuracy": 0.89,
                "feedback_percentage": 86.4,
                "efficiency": 0.80,
            },
            {
                "type": "Testing",
                "count": 22,
                "accuracy": 0.80,
                "feedback_percentage": 77.8,
                "efficiency": 0.75,
            },
        ]
    }


@router.get("/correlation")
async def get_talent_correlation(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    """Get accuracy vs efficiency correlation (mock data) — requires valid session."""
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")

    points = []
    for _ in range(40):
        points.append({
            "accuracy": round(random.uniform(0.5, 1.0), 2),
            "efficiency": round(random.uniform(0.5, 1.0), 2),
        })

    return {"correlation": {"points": points}}


@router.get("/insights")
async def get_talent_insights(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    """Get learning insights (mock data) — requires valid session."""
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")

    return {
        "dimensions": [
            {
                "dimension": "Accuracy",
                "icon": "🎯",
                "current": 82.0,
                "change": 5.2,
                "status": "up",
                "narrative": "Accuracy improved significantly this week",
                "analysis": "More careful problem analysis and testing",
            },
            {
                "dimension": "Learning Rate",
                "icon": "📚",
                "current": 75.0,
                "change": 3.1,
                "status": "up",
                "narrative": "Learning speed is increasing",
                "analysis": "Applying patterns from recent tasks faster",
            },
            {
                "dimension": "Variety",
                "icon": "🎨",
                "current": 88.0,
                "change": 1.5,
                "status": "up",
                "narrative": "Diverse task completion",
                "analysis": "Tackling different problem types effectively",
            },
            {
                "dimension": "Efficiency",
                "icon": "⚡",
                "current": 79.0,
                "change": 2.8,
                "status": "up",
                "narrative": "Better time management",
                "analysis": "Solving tasks faster while maintaining quality",
            },
        ],
        "narratives": [
            {
                "icon": "💡",
                "title": "Pattern Recognition Boost",
                "description": "You're identifying common code patterns 40% faster",
            },
            {
                "icon": "🔧",
                "title": "Tool Mastery",
                "description": "Your debugging toolkit is more effective",
            },
            {
                "icon": "🚀",
                "title": "Performance Optimization",
                "description": "Solutions are more optimized for runtime efficiency",
            },
        ],
        "badges": [
            {
                "badge": "🏆",
                "title": "Bug Buster",
                "context": "Fixed 20+ bugs",
                "level": "gold",
            },
            {
                "badge": "📖",
                "title": "Documentation Master",
                "context": "Wrote 50+ doc updates",
                "level": "silver",
            },
            {
                "badge": "⚙️",
                "title": "Architecture Expert",
                "context": "Designed 5+ systems",
                "level": "bronze",
            },
        ],
    }


@router.get("/story")
async def get_talent_story(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    days: int = 7,
) -> dict[str, Any]:
    """Get learning journey narrative (mock data) — requires valid session."""
    if not 1 <= days <= 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")

    return {
        "story": {
            "summary": "Your learning journey shows consistent improvement across all dimensions. "
                      "You've mastered bug fixing and code review, with emerging strengths in architecture design.",
            "score_start": 6.8,
            "score_end": 7.8,
            "score_change": 1.0,
            "trend": "accelerating",
            "milestone": "Reached Expert level in Bug Fixing",
        }
    }
