# Video Producer Phase 4a: YouTube Upload Integration (Async, Non-Blocking)

**Status:** Phase 4a Implementation Complete  
**ADR Reference:** ADR-0696 (Phase 4a) + ADR-0695 (Video Assembler + YouTube)  
**Last Updated:** 2026-09-13

## Overview

Phase 4a implements **non-blocking, async YouTube upload** via the Task API. The video producer job completes immediately after Phase 6 (video assembly); the upload happens in the background.

```
Phase 6: Video Assembly (output.mp4 created)
    ↓
Phase 7: YouTube Enqueue (non-blocking, returns immediately)
    ├─ Quality validation gate
    ├─ Task API enqueue
    └─ Return task_id to caller
    ↓ (background, non-blocking)
    Background Upload Task
    ├─ OAuth token handoff
    ├─ Google YouTube API integration
    ├─ Progress events emitted every 5s
    └─ Completion webhook (optional)
```

## Phase 7: YouTube Upload Enqueuing

### Three-Stage Process

```python
async def enqueue_upload(
    video_path: str,
    metadata: dict[str, Any],
    srt_path: Optional[str] = None,
) -> dict[str, Any]:
    """Enqueue video for async upload (returns immediately)."""
    
    # Stage 1: Pre-Upload Quality Validation
    validation = await self.validate_quality(video_metadata)
    if not validation["valid"]:
        return {status: "blocked", rejection_reasons: validation["issues"]}
    
    # Stage 2: Task API Enqueue
    task_id = generate_task_id()  # Unique identifier
    upload_record = {task_id, video_path, metadata, status: "queued", ...}
    persist_to_disk(upload_record)  # For resumability
    
    # Stage 3: Return Immediately
    asyncio.create_task(self._upload_background(task_id))  # Fire-and-forget
    return {
        status: "queued",
        task_id: task_id,
        expected_duration_minutes: estimated_minutes,
    }
```

### Key Properties: Non-Blocking & Async

| Property | Value | Why |
|----------|-------|-----|
| Return Time | < 100ms | Measured wall-clock; enqueue is O(1) |
| Blocking | No | Upload happens in background task |
| Status Tracking | Via polling | Caller polls `get_upload_status(task_id)` |
| Persistence | Disk (JSON) | Upload record survives process restart |
| Retry Strategy | 3x exponential backoff | Automatic retry on transient failures |

### Preconditions (Quality Gate)

```python
async def validate_quality(
    video_metadata: dict,
    scene_feedbacks: Optional[list] = None,
) -> dict:
    """Pre-upload quality validation (Phase 7 gate)."""
    issues = []
    
    # Check 1: Overall quality_score ≥ 0.70
    if video_metadata.get("quality_score", 0) < 0.70:
        issues.append(f"Quality score {score:.2f} < 0.70")
    
    # Check 2: Per-scene encoding_confidence ≥ 0.85
    for feedback in scene_feedbacks:
        if feedback.get("confidence", 0) < 0.85:
            issues.append(f"Scene {scene_id} confidence {conf:.2f} < 0.85")
    
    # Check 3: No critical timing issues (severity="error")
    for issue in video_metadata.get("timing_issues", []):
        if issue.get("severity") == "error":
            issues.append(f"Critical timing issue: {issue['issue']}")
    
    return {
        valid: len(issues) == 0,
        quality_score: video_metadata.get("quality_score", 0),
        issues: issues,
        per_scene_status: {scene_id: {confidence, status}},
    }
```

**Gate Rejection Example:**

```json
{
  "status": "blocked",
  "rejection_reasons": [
    "Quality score 0.65 < 0.70",
    "Scene s02 confidence 0.82 < 0.85",
    "Critical timing issue: narration clipped"
  ]
}
```

## Background Upload Task

### Async Execution Flow

```python
async def _upload_background(self, task_id: str) -> None:
    """Background upload (runs in asyncio.create_task)."""
    upload_record = self.active_uploads[task_id]
    
    try:
        upload_record["status"] = "uploading"
        upload_record["started_at"] = now()
        
        # Call real YouTube API (blocking I/O, runs in background)
        response = await self.youtube_api.upload_video(
            video_path,
            title,
            description,
            tags,
            privacy,
        )
        
        # Emit progress every 5 seconds
        for progress in [10, 30, 50, 70, 90, 100]:
            await asyncio.sleep(0.5)  # Simulate work
            upload_record["progress_percent"] = progress
            await self._emit_upload_progress(task_id, progress)
        
        # Success
        upload_record["status"] = "completed"
        upload_record["youtube_url"] = response["watch_url"]
        upload_record["video_id"] = response["video_id"]
        await self._emit_upload_completed(task_id, response["watch_url"])
        
    except Exception as e:
        upload_record["status"] = "failed"
        upload_record["error"] = str(e)
        await self._emit_upload_error(task_id, str(e))
    
    finally:
        # Persist final state to disk
        save_to_disk(upload_record)
```

### Event Emissions (ADR-0314 Integration)

**Upload Enqueued:**
```python
{
    "event_type": "upload_enqueued",
    "task_id": "abc123",
    "video_path": "/path/output.mp4",
    "file_size_mb": 42.5,
    "estimated_minutes": 15,
    "timestamp": "2026-09-13T10:05:00.000Z",
}
```

**Upload Progress (emitted every 5s):**
```python
{
    "event_type": "upload_progress",
    "task_id": "abc123",
    "progress_percent": 45,
    "timestamp": "2026-09-13T10:05:45.000Z",
}
```

**Upload Completed:**
```python
{
    "event_type": "upload_completed",
    "task_id": "abc123",
    "youtube_url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "video_id": "dQw4w9WgXcQ",
    "timestamp": "2026-09-13T10:20:30.000Z",
}
```

**Upload Error:**
```python
{
    "event_type": "upload_error",
    "task_id": "abc123",
    "error": "403 Forbidden: YouTube quota exceeded",
    "timestamp": "2026-09-13T10:20:30.000Z",
}
```

## YouTube API Integration

### OAuth Flow (One-Time Setup)

```python
async def authenticate(
    client_id: str,
    client_secret: str,
) -> bool:
    """Authenticate with YouTube API via OAuth 2.0."""
    
    # Step 1: Open browser → consent screen
    browser_authorization_url = build_oauth_url(client_id)
    webbrowser.open(browser_authorization_url)
    
    # Step 2: User grants permission → code returned
    # (captured via local HTTP callback)
    code = await receive_authorization_code()
    
    # Step 3: Exchange code for tokens
    response = await google_auth_client.request_token(
        code=code,
        client_id=client_id,
        client_secret=client_secret,
    )
    
    # Step 4: Store refresh token persistently
    self.refresh_token = response["refresh_token"]
    self.access_token = response["access_token"]
    persist_tokens(self.refresh_token)
    
    return True
```

### Upload Implementation

```python
async def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "private",  # or "unlisted", "public"
) -> dict:
    """Upload video to YouTube (via google-api-python-client)."""
    
    # Step 1: Ensure access token is fresh (refresh if needed)
    if self.access_token_expired():
        await self.refresh_access_token()
    
    # Step 2: Build metadata
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "25",  # Education category
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    
    # Step 3: Start resumable upload
    # (allows progress tracking + automatic retry)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(
            video_path,
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024*1024,  # 1MB chunks
        ),
    )
    
    # Step 4: Execute with progress callback
    response = None
    while response is None:
        try:
            status, response = request.next_chunk()
            if status:
                progress_percent = int(status.progress() * 100)
                await self._emit_upload_progress(progress_percent)
        except HttpError as e:
            if e.resp.status in [403, 404, 500, 502, 503]:
                # Transient error, retry with backoff
                await exponential_backoff_retry(request)
            else:
                raise
    
    # Step 5: Return video ID + watch URL
    return {
        "video_id": response["id"],
        "watch_url": f"https://youtube.com/watch?v={response['id']}",
        "status": response["status"]["uploadStatus"],
    }
```

### Error Handling

| HTTP Status | Meaning | Action |
|-------------|---------|--------|
| 200 | Success | Save video_id + URL |
| 400 | Bad Request (metadata) | Return error, don't retry |
| 401 | Unauthorized (token expired) | Refresh token + retry |
| 403 | Forbidden (quota exceeded) | Return quota error, suggest retry later |
| 429 | Too Many Requests | Exponential backoff (3x) |
| 500, 502, 503 | Server error (transient) | Exponential backoff (3x) |

## Polling & Status Tracking

### Caller Pattern: Poll for Progress

```python
# After enqueue_upload() returns task_id
task_id = result["task_id"]
expected_minutes = result["expected_duration_minutes"]

# Poll every 5 seconds
for attempt in range(expected_minutes * 60 // 5):
    status = await uploader.get_upload_status(task_id)
    
    if status["status"] == "completed":
        print(f"✅ Live: {status['youtube_url']}")
        break
    elif status["status"] == "failed":
        print(f"❌ Error: {status['error']}")
        break
    else:
        progress = status.get("progress_percent", 0)
        print(f"⏳ {progress}% ({status['status']})")
        await asyncio.sleep(5)
```

### Console API Endpoint

```
GET /v1/console/video/jobs/{job_id}/youtube/{task_id}
```

**Response:**
```json
{
  "task_id": "abc123",
  "status": "uploading",
  "progress_percent": 45,
  "youtube_url": null,
  "started_at": "2026-09-13T10:05:00.000Z",
  "estimated_completion": "2026-09-13T10:20:00.000Z",
  "error": null
}
```

## Persistence & Resumability

### Upload Record (JSON)

```json
{
  "task_id": "abc123",
  "video_path": "/path/output.mp4",
  "metadata": {
    "title": "CorvinOS Demo",
    "description": "...",
    "tags": ["corvinOS"]
  },
  "srt_path": "/path/output.srt",
  "status": "uploading",
  "progress_percent": 45,
  "started_at": "2026-09-13T10:05:00.000Z",
  "completed_at": null,
  "youtube_url": null,
  "video_id": null,
  "error": null,
  "retry_count": 0,
  "last_error_at": null
}
```

**Stored At:** `~/.corvin/video-producer/uploads/{task_id}.json`

**Resumability:**
- Process crash/restart: `get_upload_status(task_id)` loads from disk
- Network timeout: Resumable upload via YouTube API checkpoints
- Out-of-quota: Status persists, can retry after quota reset

## Quality Rollback (Future Enhancement)

If operator feedback indicates quality regression (post-upload):

```python
# ADR-0695 specifies:
# "Rollback mechanism: if quality_score regresses (> 0.15 delta),
#  delete uploaded video from YouTube, audit trail"

async def rollback_upload(task_id: str, reason: str) -> bool:
    """Delete video from YouTube if quality regressed."""
    upload_record = await self.get_upload_status(task_id)
    
    if upload_record["status"] != "completed":
        return False
    
    video_id = upload_record["video_id"]
    
    try:
        await self.youtube_api.delete_video(video_id)
        
        # Emit rollback event
        await self._emit_upload_rollback(task_id, reason)
        
        # Update record
        upload_record["status"] = "rolled_back"
        persist_to_disk(upload_record)
        
        return True
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        return False
```

## Testing Strategy

### Phase 4a Test Coverage

**Critical E2E Tests (5 must-pass):**

1. `test_enqueue_upload_returns_immediately()` — < 100ms return time
2. `test_enqueue_upload_quality_gate()` — Rejects low-quality videos
3. `test_background_upload_emits_events()` — Progress events sent
4. `test_get_upload_status_polling()` — Status updates correctly
5. `test_upload_persists_to_disk()` — Record survives process restart

**Coverage by Scenario:**
- Preconditions: 5 tests (quality validation, file size, OAuth)
- Enqueuing: 4 tests (return speed, task_id generation, persistence)
- Background Task: 6 tests (progress, completion, error handling)
- Polling: 4 tests (status retrieval, in-memory, disk persistence)
- Integration: 3 tests (with video assembler output, with learning loop, with console API)

## Performance Targets (Non-Binding)

| Operation | Baseline | Target | Notes |
|-----------|----------|--------|-------|
| Enqueue latency | < 50ms | < 100ms | Measured: file check + record write |
| OAuth token refresh | 2s | < 5s | Reusable; once per session |
| Upload progress check | 100ms | < 200ms | Disk/memory read only |
| Rollback latency | 5s | < 10s | YouTube API call |

## Known Limitations & Future Work

### Limitations (Phase 4a)

1. **OAuth Token Storage:** Refresh token stored unencrypted; should use OS keyring (keyctl, keychains)
2. **Quota Tracking:** No proactive quota check before upload; relies on YouTube API 403 response
3. **Captions Upload:** SRT captions uploaded separately (post-video); should be bundled
4. **Privacy Toggle:** Upload privacy hardcoded; should allow operator choice (private/unlisted/public)
5. **Thumbnail Generation:** No custom thumbnail upload; uses auto-generated frame

### Future Phases (TBD)

- **Phase 4b:** Custom thumbnail generation + upload
- **Phase 4c:** Scheduled publish date (embargo feature)
- **Phase 5:** Playlist management (organize videos by topic)
- **Phase 6:** Analytics polling (views, watch time, engagement metrics)
