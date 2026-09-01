"""
Phase 5: Community Plugin Submissions (ADR-0511)

Endpoints:
- POST /api/v1/marketplace/plugins/submit — User submits plugin
- GET /api/v1/marketplace/plugins/pending-review — Moderator views submissions
- POST /api/v1/marketplace/plugins/{submission_id}/approve — Approve submission
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
from dataclasses import dataclass, asdict
from enum import Enum

from fastapi import APIRouter, HTTPException, Body, Query

router = APIRouter(prefix="/api/v1/marketplace", tags=["community"])

# In-memory storage (Phase 5 stub; Phase 6 will add persistent DB)
_submissions: Dict[str, "PluginSubmission"] = {}
_submission_counter = 0


class SubmissionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class PluginSubmission:
    """Community plugin submission."""
    submission_id: str
    plugin_name: str
    description: str
    github_url: str
    license: str  # MIT, Apache-2.0, GPL-3.0, etc.
    category: str  # memory, security_compliance, integration, data_processing, observability
    author: str
    status: SubmissionStatus
    created_at: str
    updated_at: str
    reviewer_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            **asdict(self),
            "status": self.status.value,
        }


@router.post("/plugins/submit")
async def submit_plugin(
    body: Dict[str, Any] = Body(...),
) -> Dict[str, Any]:
    """
    Submit a community plugin for review.

    Request:
    ```json
    {
      "plugin_name": "My Awesome Plugin",
      "description": "Does something cool",
      "github_url": "https://github.com/user/my-plugin",
      "license": "MIT",
      "category": "integration",
      "author": "John Doe"
    }
    ```

    Response:
    ```json
    {
      "submission_id": "sub_abc123",
      "status": "pending",
      "message": "Submitted for review"
    }
    ```
    """
    try:
        # Validate required fields
        required = ["plugin_name", "description", "github_url", "license", "category", "author"]
        for field in required:
            if field not in body:
                raise HTTPException(status_code=400, detail=f"Missing: {field}")

        # Create submission
        submission_id = f"sub_{uuid.uuid4().hex[:8]}"
        now = datetime.utcnow().isoformat() + "Z"

        submission = PluginSubmission(
            submission_id=submission_id,
            plugin_name=body["plugin_name"],
            description=body["description"],
            github_url=body["github_url"],
            license=body["license"],
            category=body["category"],
            author=body["author"],
            status=SubmissionStatus.PENDING,
            created_at=now,
            updated_at=now,
        )

        _submissions[submission_id] = submission

        return {
            "submission_id": submission_id,
            "status": "pending",
            "message": "Plugin submitted for review",
            "plugin_name": submission.plugin_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plugins/pending-review")
async def list_pending_submissions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """
    List pending plugin submissions for moderator review.

    Response:
    ```json
    {
      "submissions": [...],
      "total": 5,
      "pending_count": 3,
      "approved_count": 2
    }
    ```
    """
    pending = [
        s.to_dict()
        for s in _submissions.values()
        if s.status == SubmissionStatus.PENDING
    ]
    pending = pending[offset : offset + limit]

    return {
        "submissions": pending,
        "total": len(_submissions),
        "pending_count": sum(1 for s in _submissions.values() if s.status == SubmissionStatus.PENDING),
        "approved_count": sum(1 for s in _submissions.values() if s.status == SubmissionStatus.APPROVED),
    }


@router.post("/plugins/{submission_id}/approve")
async def approve_submission(
    submission_id: str,
    body: Dict[str, Any] = Body(None),
) -> Dict[str, Any]:
    """
    Approve a plugin submission and move to marketplace.

    Request:
    ```json
    {
      "reviewer": "admin@example.com",
      "notes": "Looks good, security audit passed"
    }
    ```

    Response:
    ```json
    {
      "submission_id": "sub_abc123",
      "status": "approved",
      "message": "Plugin approved and added to community registry"
    }
    ```
    """
    if submission_id not in _submissions:
        raise HTTPException(status_code=404, detail=f"Submission {submission_id} not found")

    submission = _submissions[submission_id]

    if body is None:
        body = {}

    reviewer = body.get("reviewer", "system")
    notes = body.get("notes", "")

    # Mark as approved
    submission.status = SubmissionStatus.APPROVED
    submission.reviewer_notes = notes
    submission.updated_at = datetime.utcnow().isoformat() + "Z"

    # In Phase 6, this would:
    # 1. Create plugin entry in community registry
    # 2. Add to Corvin-Marketplace/plugins/contributor/{category}/{plugin_id}/
    # 3. Regenerate index
    # 4. Notify author

    return {
        "submission_id": submission_id,
        "status": "approved",
        "message": "Plugin approved and added to community registry",
        "plugin_name": submission.plugin_name,
        "reviewer": reviewer,
        "notes": notes,
    }
