"""
timeline.py — Phase 6c: Storyboard Visualization with Skill + Credential Integration

FastAPI endpoints for orchestration timeline state management.
Provides real-time visualization of frame processing status, progress tracking,
executor state, skill confidence, and credential rotation events.

Integration Points:
  - ADR-0206: Phase 6 Orchestration Foundation
  - ADR-0532: Skills 2.0 (OS-Skills) — skill confidence display
  - ADR-0565: Credential Rotation — credential status + audit trail
  - ADR-0314: Learning Infrastructure — skill feedback + improvement trend
  - ADR-0232: Audit Chain — hash-chained orchestration events
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
from dataclasses import dataclass
import hashlib

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/timeline", tags=["orchestration-timeline"])

# Global task store (in-memory; replace with persistent storage in Phase 6d)
_TASK_STORE: Dict[str, 'TimelineTaskState'] = {}
_AUDIT_EVENTS: List[Dict[str, Any]] = []  # Hash-chained audit events

# ============================================================================
# Models
# ============================================================================

class SkillConfidenceModel(BaseModel):
    """Skill confidence metrics (ADR-0532: Skills 2.0)."""
    skillId: str = Field(..., description="Skill identifier (e.g., 'os.video_producer')")
    version: str = Field(..., description="Skill version")
    confidence: float = Field(..., ge=0, le=1, description="Confidence score (0-1)")
    feedbackCount: int = Field(default=0, description="Number of feedback events received")
    accuracyTrend: Optional[str] = Field(None, description="Trend: '↑' (improving), '↓' (declining), '→' (stable)")
    lastUpdated: datetime = Field(default_factory=datetime.utcnow)


class CredentialStatusModel(BaseModel):
    """Credential rotation status (ADR-0565: Secret Rotation)."""
    credentialId: str = Field(..., description="Credential identifier")
    credentialType: str = Field(..., description="Type: 'api_key', 'oauth_token', 'service_account'")
    rotationStatus: str = Field(..., description="Status: 'active', 'rotating', 'rotated', 'expired'")
    lastRotatedAt: Optional[datetime] = None
    nextRotationAt: Optional[datetime] = None
    daysUntilRotation: Optional[int] = None
    auditEventCount: int = Field(default=0, description="Number of audit events (accessed, rotated, etc.)")


class FrameStateModel(BaseModel):
    """Represents the state of a single frame in the orchestration (Phase 6c enhanced)."""
    frameId: str = Field(..., description="Unique frame identifier")
    workerType: str = Field(
        ...,
        description="Type of worker: 'tts', 'screenshot', 'ffmpeg', 'youtube'"
    )
    status: str = Field(
        ...,
        description="Frame status: 'pending', 'running', 'completed', 'error'"
    )
    progress: Optional[float] = Field(
        None,
        description="Processing progress (0-100) for running frames"
    )
    errorMessage: Optional[str] = Field(
        None,
        description="Error message if frame failed"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        None,
        description="Additional frame metadata"
    )
    # Phase 6c: Skill + Credential integration
    skillConfidence: Optional[SkillConfidenceModel] = Field(
        None,
        description="Skill confidence metrics for this worker (ADR-0532)"
    )
    credentialStatus: Optional[CredentialStatusModel] = Field(
        None,
        description="Credential rotation status used by this worker (ADR-0565)"
    )
    auditEventHash: Optional[str] = Field(
        None,
        description="Hash of audit events for this frame (ADR-0232)"
    )
    createdAt: datetime = Field(default_factory=datetime.utcnow)
    completedAt: Optional[datetime] = None


class LearningMetricsModel(BaseModel):
    """Learning metrics for the orchestration (ADR-0314: Learning Infrastructure)."""
    outcomeCount: int = Field(default=0, description="Number of outcome feedback events")
    averageConfidence: float = Field(default=0.5, ge=0, le=1, description="Average skill confidence")
    improvementTrend: Optional[str] = Field(
        None,
        description="Overall trend: '↑' (improving), '↓' (declining), '→' (stable)"
    )
    lastFeedbackAt: Optional[datetime] = None


class ExecutorStatusModel(BaseModel):
    """Represents the overall executor/orchestration status (Phase 6c enhanced)."""
    totalFrames: int = Field(..., description="Total frames to process")
    completedFrames: int = Field(..., description="Frames completed")
    failedFrames: int = Field(default=0, description="Frames that failed")
    currentFrameId: Optional[str] = Field(
        None,
        description="Currently processing frame ID"
    )
    isRunning: bool = Field(..., description="Whether orchestration is running")
    overallProgress: float = Field(..., description="Overall progress percentage")
    estimatedTimeRemaining: Optional[int] = Field(
        None,
        description="Estimated time remaining in milliseconds"
    )
    # Phase 6c: Learning metrics
    learningMetrics: Optional[LearningMetricsModel] = Field(
        None,
        description="Learning metrics for the orchestration (ADR-0314)"
    )
    startedAt: datetime = Field(default_factory=datetime.utcnow)
    updatedAt: datetime = Field(default_factory=datetime.utcnow)


class TimelineStateModel(BaseModel):
    """Complete timeline state for a task (Phase 6c)."""
    taskId: str = Field(..., description="Task identifier")
    frames: List[FrameStateModel] = Field(..., description="List of frame states")
    executorStatus: ExecutorStatusModel = Field(..., description="Executor status")
    auditEventsCount: int = Field(default=0, description="Total audit events for this task")
    lastAuditHash: Optional[str] = Field(
        None,
        description="Last audit event hash (for chain verification)"
    )


@dataclass
class TimelineTaskState:
    """Internal task state (in-memory storage, Phase 6c)."""
    taskId: str
    frames: Dict[str, FrameStateModel]
    executorStatus: ExecutorStatusModel
    auditEventHashes: List[str]  # Hash-chained audit events
    createdAt: datetime = None

    def __post_init__(self):
        if self.createdAt is None:
            self.createdAt = datetime.utcnow()


class FrameUpdateModel(BaseModel):
    """Model for updating a frame's state."""
    status: str = Field(..., description="New status")
    progress: Optional[float] = None
    errorMessage: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


# ============================================================================
# Audit Chain Helper Functions (ADR-0232)
# ============================================================================

def _hash_audit_event(event_data: str, prev_hash: Optional[str] = None) -> str:
    """Generate hash-chained audit event (fail-closed).

    Args:
        event_data: Event data to hash
        prev_hash: Previous event hash for chaining

    Returns:
        SHA256 hash of event (chained to previous if provided)
    """
    combined = f"{prev_hash or ''}|{event_data}" if prev_hash else event_data
    return hashlib.sha256(combined.encode()).hexdigest()


def _emit_audit_event(event_type: str, data: Dict[str, Any], task_id: str):
    """Emit audit event to the chain (ADR-0232: Hash-chained audit).

    Args:
        event_type: Type of event (e.g., 'frame_status_changed', 'executor_paused')
        data: Event payload
        task_id: Task identifier for audit trail
    """
    prev_hash = _AUDIT_EVENTS[-1]['hash'] if _AUDIT_EVENTS else None
    event_hash = _hash_audit_event(f"{event_type}:{task_id}:{str(data)}", prev_hash)

    audit_event = {
        'timestamp': datetime.utcnow().isoformat(),
        'event_type': event_type,
        'task_id': task_id,
        'data': data,
        'hash': event_hash,
        'prev_hash': prev_hash,
    }
    _AUDIT_EVENTS.append(audit_event)
    logger.info(f"Audit event emitted: {event_type} (hash: {event_hash[:16]}...)")


def _get_or_create_task(task_id: str) -> TimelineTaskState:
    """Get or create a task state in the store.

    Args:
        task_id: Task identifier

    Returns:
        TimelineTaskState object
    """
    if task_id not in _TASK_STORE:
        _TASK_STORE[task_id] = TimelineTaskState(
            taskId=task_id,
            frames={},
            executorStatus=ExecutorStatusModel(
                totalFrames=0,
                completedFrames=0,
                isRunning=False,
                overallProgress=0.0,
            ),
            auditEventHashes=[],
        )
        _emit_audit_event('task_created', {'taskId': task_id}, task_id)
    return _TASK_STORE[task_id]


# ============================================================================
# Endpoints
# ============================================================================

@router.get("/state/{task_id}", response_model=TimelineStateModel)
async def get_timeline_state(
    task_id: str,
) -> TimelineStateModel:
    """
    Get the current timeline state for a task (Phase 6c).

    Returns the complete orchestration state including:
    - All frames with skill confidence + credential status (ADR-0532, ADR-0565)
    - Executor status with learning metrics (ADR-0314)
    - Audit chain information (ADR-0232)

    Args:
        task_id: Task identifier

    Returns:
        TimelineStateModel with real orchestration state
    """
    try:
        logger.info(f"Fetching timeline state for task: {task_id}")

        # Get or create task state from store
        task_state = _get_or_create_task(task_id)

        # Convert frames dict to list
        frames_list = list(task_state.frames.values())

        # Calculate last audit hash
        last_audit_hash = task_state.auditEventHashes[-1] if task_state.auditEventHashes else None

        # Build response
        return TimelineStateModel(
            taskId=task_id,
            frames=frames_list,
            executorStatus=task_state.executorStatus,
            auditEventsCount=len(task_state.auditEventHashes),
            lastAuditHash=last_audit_hash,
        )
    except Exception as e:
        logger.error(f"Error fetching timeline state for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/frame/{task_id}")
async def add_frame(
    task_id: str,
    frame: FrameStateModel,
) -> Dict[str, Any]:
    """
    Add or update a frame in the orchestration timeline.

    Creates a new frame if it doesn't exist, updates existing frame if it does.
    """
    try:
        logger.info(
            f"Adding/updating frame {frame.frameId} for task {task_id}: "
            f"status={frame.status}"
        )

        # TODO: Implement frame storage in orchestrator/task store
        # Validate frame data
        if frame.status not in ["pending", "running", "completed", "error"]:
            raise ValueError(f"Invalid status: {frame.status}")

        if frame.progress is not None and not (0 <= frame.progress <= 100):
            raise ValueError(f"Progress must be between 0-100, got {frame.progress}")

        # TODO: Store frame in persistent storage

        return {
            "taskId": task_id,
            "frameId": frame.frameId,
            "status": "stored",
            "timestamp": datetime.utcnow().isoformat(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error adding frame for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/frame/{task_id}/{frame_id}", response_model=Dict[str, Any])
async def update_frame(
    task_id: str,
    frame_id: str,
    update: FrameUpdateModel,
) -> Dict[str, Any]:
    """
    Update an existing frame's state (Phase 6c with audit trail).

    Allows updating status, progress, error message, and metadata.
    All updates are logged to the audit chain (ADR-0232).

    Args:
        task_id: Task identifier
        frame_id: Frame identifier
        update: Frame update data

    Returns:
        Update confirmation with audit hash
    """
    try:
        logger.info(
            f"Updating frame {frame_id} for task {task_id}: "
            f"status={update.status}"
        )

        # Validate update data
        if update.status not in ["pending", "running", "completed", "error"]:
            raise ValueError(f"Invalid status: {update.status}")

        if update.progress is not None and not (0 <= update.progress <= 100):
            raise ValueError(f"Progress must be between 0-100")

        # Get task state
        task_state = _get_or_create_task(task_id)

        # Update or create frame
        if frame_id not in task_state.frames:
            # Create new frame with default values
            task_state.frames[frame_id] = FrameStateModel(
                frameId=frame_id,
                workerType="unknown",
                status=update.status,
                progress=update.progress,
                errorMessage=update.errorMessage,
                metadata=update.metadata,
            )
        else:
            # Update existing frame
            frame = task_state.frames[frame_id]
            frame.status = update.status
            if update.progress is not None:
                frame.progress = update.progress
            if update.errorMessage is not None:
                frame.errorMessage = update.errorMessage
            if update.metadata is not None:
                frame.metadata = {**(frame.metadata or {}), **update.metadata}
            if update.status == "completed":
                frame.completedAt = datetime.utcnow()

        # Emit audit event (ADR-0232)
        _emit_audit_event(
            'frame_status_changed',
            {
                'frameId': frame_id,
                'status': update.status,
                'progress': update.progress,
            },
            task_id,
        )

        # Update executor progress
        completed_count = sum(1 for f in task_state.frames.values() if f.status == "completed")
        failed_count = sum(1 for f in task_state.frames.values() if f.status == "error")
        total = len(task_state.frames)

        task_state.executorStatus.completedFrames = completed_count
        task_state.executorStatus.failedFrames = failed_count
        task_state.executorStatus.overallProgress = (completed_count / total * 100) if total > 0 else 0
        task_state.executorStatus.updatedAt = datetime.utcnow()

        return {
            "taskId": task_id,
            "frameId": frame_id,
            "status": "updated",
            "timestamp": datetime.utcnow().isoformat(),
            "auditHash": _AUDIT_EVENTS[-1]['hash'] if _AUDIT_EVENTS else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating frame {frame_id} for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/executor-status/{task_id}", response_model=ExecutorStatusModel)
async def get_executor_status(
    task_id: str,
) -> ExecutorStatusModel:
    """
    Get the current executor status for a task.

    Returns aggregated information about the overall orchestration progress.
    """
    try:
        logger.info(f"Fetching executor status for task: {task_id}")

        # TODO: Implement executor status lookup
        return ExecutorStatusModel(
            totalFrames=0,
            completedFrames=0,
            isRunning=False,
            overallProgress=0.0,
        )
    except Exception as e:
        logger.error(f"Error fetching executor status for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/executor/{task_id}/pause", response_model=Dict[str, Any])
async def pause_executor(task_id: str) -> Dict[str, Any]:
    """Pause the executor for a task (Phase 6c with audit trail).

    Args:
        task_id: Task identifier

    Returns:
        Pause confirmation with audit hash
    """
    try:
        logger.info(f"Pausing executor for task: {task_id}")

        task_state = _get_or_create_task(task_id)
        task_state.executorStatus.isRunning = False
        task_state.executorStatus.updatedAt = datetime.utcnow()

        # Emit audit event
        _emit_audit_event('executor_paused', {'taskId': task_id}, task_id)

        return {
            "taskId": task_id,
            "action": "pause",
            "timestamp": datetime.utcnow().isoformat(),
            "auditHash": _AUDIT_EVENTS[-1]['hash'] if _AUDIT_EVENTS else None,
        }
    except Exception as e:
        logger.error(f"Error pausing executor for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/executor/{task_id}/resume", response_model=Dict[str, Any])
async def resume_executor(task_id: str) -> Dict[str, Any]:
    """Resume the executor for a task (Phase 6c with audit trail).

    Args:
        task_id: Task identifier

    Returns:
        Resume confirmation with audit hash
    """
    try:
        logger.info(f"Resuming executor for task: {task_id}")

        task_state = _get_or_create_task(task_id)
        task_state.executorStatus.isRunning = True
        task_state.executorStatus.updatedAt = datetime.utcnow()

        # Emit audit event
        _emit_audit_event('executor_resumed', {'taskId': task_id}, task_id)

        return {
            "taskId": task_id,
            "action": "resume",
            "timestamp": datetime.utcnow().isoformat(),
            "auditHash": _AUDIT_EVENTS[-1]['hash'] if _AUDIT_EVENTS else None,
        }
    except Exception as e:
        logger.error(f"Error resuming executor for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/executor/{task_id}/retry-frame/{frame_id}", response_model=Dict[str, Any])
async def retry_frame(
    task_id: str,
    frame_id: str,
) -> Dict[str, Any]:
    """Retry a failed frame (Phase 6c with audit trail).

    Args:
        task_id: Task identifier
        frame_id: Frame identifier to retry

    Returns:
        Retry confirmation with audit hash
    """
    try:
        logger.info(f"Retrying frame {frame_id} for task {task_id}")

        task_state = _get_or_create_task(task_id)

        if frame_id not in task_state.frames:
            raise ValueError(f"Frame {frame_id} not found")

        frame = task_state.frames[frame_id]
        if frame.status != "error":
            raise ValueError(f"Frame {frame_id} is not in error state")

        # Reset frame to pending
        frame.status = "pending"
        frame.progress = 0
        frame.errorMessage = None
        frame.completedAt = None

        # Emit audit event
        _emit_audit_event(
            'frame_retry_initiated',
            {'frameId': frame_id},
            task_id,
        )

        return {
            "taskId": task_id,
            "frameId": frame_id,
            "action": "retry",
            "timestamp": datetime.utcnow().isoformat(),
            "auditHash": _AUDIT_EVENTS[-1]['hash'] if _AUDIT_EVENTS else None,
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrying frame {frame_id} for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/frames/{task_id}", response_model=List[FrameStateModel])
async def list_frames(
    task_id: str,
    status: Optional[str] = Query(None, description="Filter by status"),
) -> List[FrameStateModel]:
    """
    List all frames for a task, optionally filtered by status (Phase 6c).

    Args:
        task_id: Task identifier
        status: Optional status filter (pending, running, completed, error)

    Returns:
        List of FrameStateModel objects
    """
    try:
        logger.info(f"Listing frames for task {task_id}, filter={status}")

        task_state = _get_or_create_task(task_id)
        frames = list(task_state.frames.values())

        if status:
            frames = [f for f in frames if f.status == status]

        return frames
    except Exception as e:
        logger.error(f"Error listing frames for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit-trail/{task_id}", response_model=Dict[str, Any])
async def get_audit_trail(
    task_id: str,
) -> Dict[str, Any]:
    """
    Get the complete audit trail for a task (ADR-0232: Audit Chain).

    Returns hash-chained audit events for compliance and verification.

    Args:
        task_id: Task identifier

    Returns:
        Audit trail with events and chain verification status
    """
    try:
        logger.info(f"Fetching audit trail for task: {task_id}")

        # Filter events by task_id
        task_events = [e for e in _AUDIT_EVENTS if e['task_id'] == task_id]

        # Verify chain integrity (simplified: all hashes must be present)
        chain_valid = True
        if len(task_events) > 1:
            for i in range(1, len(task_events)):
                if task_events[i]['prev_hash'] != task_events[i-1]['hash']:
                    chain_valid = False
                    break

        return {
            "taskId": task_id,
            "eventCount": len(task_events),
            "events": task_events,
            "chainValid": chain_valid,
            "lastEventHash": task_events[-1]['hash'] if task_events else None,
        }
    except Exception as e:
        logger.error(f"Error fetching audit trail for {task_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
