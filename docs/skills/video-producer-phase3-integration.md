# Video Producer Phase 3: Orchestrated Worker Skills Integration

**Status:** Phase 3 Implementation Complete  
**ADR Reference:** ADR-0696 (Phase 2-3) + ADR-0692 (Video Producer Orchestration)  
**Last Updated:** 2026-09-13

## Overview

Phase 3 implements the three core Worker Skills that form the **Maestro Orchestration Layer**:

1. **Slide Renderer** (250 LOC) — PowerPoint → PNG sequence with timing and metadata
2. **Voice Synthesizer** (300 LOC) — Text → MP3 narration with duration measurement
3. **Video Assembler** (550 LOC) — FFmpeg-based composition + timing validation

These execute in **Phases 5–6** of the orchestrator after storyboard generation, feeding into Phase 7 (YouTube upload).

## Architecture: Three-Tier Worker Pattern

```
Orchestrator (Maestro)
    ├─ Phase 5: Parallel Workers
    │   ├─ voice_synthesizer.synthesize_narration()    → audio/*.mp3 + timings.json
    │   ├─ slide_renderer.render_slides()              → slides/*.png + metadata.json
    │   └─ screenshot_capturer.capture_scenes()        → screenshots/*.png
    └─ Phase 6: Video Assembly
        └─ video_assembler.assemble_video()            → output.mp4 + metadata.json
```

## Phase 5: Parallel Workers (Maestro Coordination)

### Execution Flow

```python
async def _execute_phase_5_parallel_workers(storyboard: Storyboard) -> dict:
    """Dispatch voice, slides, screenshots in parallel via asyncio.gather()."""
    tasks = [
        voice_synthesizer.synthesize_narration(storyboard),
        slide_renderer.render_slides(ppt_path, storyboard),
        screenshot_capturer.capture_scenes(storyboard),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    # Aggregate results → return {slides_dir, voice_dir, screenshots_dir}
```

### Preconditions (Phase 5 Gate)

- `storyboard.scenes` non-empty
- All scenes have `narration` text (ADR-0694 ensures this)
- PPT file path provided in storyboard metadata

### Outputs

| Output | Type | Contents | Usage |
|--------|------|----------|-------|
| `slides_dir` | Path | PNG sequence `s01.png, s02.png, ...` | Phase 6 input |
| `voice_dir` | Path | MP3 sequence `s01.mp3, s02.mp3, ...` | Phase 6 input |
| `timings.json` | JSON | `{scene_id: audio_duration_ms}` | Phase 6 timing validation |
| `screenshots_dir` | Path | Optional PNG overlays (if kind="screencast") | Phase 6 optional |

## Phase 6: Video Assembly (FFmpeg Orchestration)

### Five-Stage Pipeline

```python
async def assemble_video(
    storyboard, slides_dir, audio_dir, timings, output_name
) -> dict:
    """Compose video from slides + audio + optional screenshots."""
    # Stage 1: Asset Validation
    if not slides_dir.exists() or not audio_dir.exists():
        return {status: "blocked", error: "..."}
    
    # Stage 2: Timing Validation
    timing_issues = await _validate_timing(storyboard, timings)
    # Detects: audio > slide, gaps, overlaps
    
    # Stage 3: FFmpeg Filter Graph Construction
    filter_graph = FilterGraph(storyboard, slides_dir, audio_dir).build()
    # Generates concat filter + overlay filters (text, captions)
    
    # Stage 4: FFmpeg Execution
    await _run_ffmpeg(filter_graph, audio_files, output_path)
    # Invokes: ffmpeg -filter_complex "..." -c:v libx264 -c:a aac -b:v 7200k -y output.mp4
    
    # Stage 5: Output Metadata Collection
    duration = await _get_video_duration(output_path)  # via ffprobe
    file_size = output_path.stat().st_size
    
    return {
        status: "success",
        video_path: str(output_path),
        duration_seconds: duration,
        file_size_bytes: file_size,
        encoding_latency_ms: elapsed,
        timing_issues: [],
        metadata: {...},
    }
```

### FFmpeg Filter Graph Example

For a 2-scene video with narration overlay:

```
[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,
     pad=1920:1080:(ow-iw)/2:(oh-ih)/2[scaled_s01];
[1:a]aformat=sample_rates=48000[audio_s01];
[scaled_s01][audio_s01]concat=n=1:v=1:a=1[out_s01];
[2:v]scale=1920:1080:force_original_aspect_ratio=decrease,
     pad=1920:1080:(ow-iw)/2:(oh-ih)/2[scaled_s02];
[3:a]aformat=sample_rates=48000[audio_s02];
[scaled_s02][audio_s02]concat=n=1:v=1:a=1[out_s02];
[out_s01][out_s02]concat=n=2:v=1:a=1[final]
```

### Timing Validation Rules

**Gate Check:** Before Phase 6 execution

| Condition | Action | Example |
|-----------|--------|---------|
| `audio_duration > slide_duration - 200ms gap` | ⚠️ WARNING | 5s audio in 5s slide → pass (within 200ms) |
| `audio_duration > slide_duration + 200ms` | ⚠️ WARNING | 6s audio in 5s slide → flag, may clip |
| `audio_duration == 0` | ❌ SKIP | No audio generated → skip scene |
| Timing issue count > 3 | ⚠️ Quality flag | Sets confidence = 0.65 (uncertain) |

**Outcome:** `result["timing_issues"]` list of `{scene_id, issue, severity, action}`

### Preconditions (Phase 6 Gate)

- Phase 5 completed successfully: `slides_metadata["status"] == "success"`
- `slides_dir` populated with all expected PNG files
- `audio_dir` populated with all expected MP3 files
- Storyboard locked (read-only, not modified during execution)

### Outputs

| Field | Type | Meaning |
|-------|------|---------|
| `video_path` | str | Full path to `output.mp4` |
| `duration_seconds` | float | Measured via `ffprobe` (precise) |
| `file_size_bytes` | int | Physical MP4 size on disk |
| `encoding_latency_ms` | int | Wall-clock time for FFmpeg execution |
| `timing_issues` | list | Per-scene timing problems (if any) |
| `quality_score` | float | Computed: 1.0 - (timing_issues_count / scenes_count) |

**Example Output:**

```json
{
  "status": "success",
  "video_path": "/home/user/.corvin/video-producer/videos/job_abc123/output.mp4",
  "duration_seconds": 35.2,
  "file_size_bytes": 42000000,
  "encoding_latency_ms": 45230,
  "timing_issues": [
    {
      "scene_id": "s02",
      "issue": "Audio 5200ms > slide 5000ms (exceeds 200ms grace)",
      "severity": "warning",
      "action": "audio clipped or slide extended"
    }
  ],
  "metadata": {
    "scene_count": 3,
    "started_at": "2026-09-13T10:00:00.000Z",
    "completed_at": "2026-09-13T10:01:23.456Z",
    "codec": "h.264",
    "bitrate_kbps": 7200,
    "preset": "medium"
  }
}
```

## Integration with Learning Loop (ADR-0314)

Each worker emits **Quality Feedback Events** for optimization:

### Event Emissions

**Slide Renderer:**
```python
event = {
    "event_type": "slide_rendered",
    "scene_id": "s01",
    "render_latency_ms": 245,
    "confidence": 0.95,  # High confidence if no errors
    "timestamp": "2026-09-13T10:00:15.234Z",
}
await event_emitter.emit("slide_rendered", event)
```

**Video Assembler:**
```python
event = {
    "event_type": "video_assembled",
    "video_path": "/path/output.mp4",
    "encoding_latency_ms": 45230,
    "timing_issues_count": 1,
    "confidence": 0.85,  # Lower if timing issues detected
    "timestamp": "2026-09-13T10:01:23.456Z",
}
```

### Learning Loop Integration

The Learning Loop (ADR-0314) reads these events and:

1. **Confidence Tracking:** Learns when rendering/encoding is reliable
2. **Timing Optimization:** Tunes slide duration expectations based on narration patterns
3. **Quality Tuning:** Adjusts FFmpeg presets (slow/medium/fast) based on latency feedback

Example: If users consistently approve videos with 1 timing issue but reject videos with 3+, the optimizer learns to increase slide duration preemptively.

## Error Handling & Fallbacks

### Slide Renderer Fallbacks

1. **Primary:** `python-pptx` library for native PPT parsing
2. **Secondary:** `libreoffice --headless --convert-to png` (subprocess)
3. **Fallback:** Generate stub PNG (testing/offline)

### Video Assembler Fallbacks

1. **Primary:** `ffmpeg` binary (full video encoding)
2. **Fallback:** `ffprobe` for duration (if `ffmpeg` unavailable)
3. **Fallback:** Estimate duration from file size (1MB ≈ 1s at 7200k bitrate)

### Precondition Gate Failures

| Gate | Failure | Outcome |
|------|---------|---------|
| Phase 5 workers | Any worker status != "success" | Phase 6 skipped, return "partial" |
| Slides missing | `slides_dir` not found | Return `{status: "blocked", error: "..."}` |
| Audio missing | `audio_dir` not found | Return `{status: "blocked", error: "..."}` |
| FFmpeg missing | No `/usr/bin/ffmpeg` | Write stub MP4 for testing |
| Timing validation | 5+ timing issues | Flag `confidence = 0.5`, allow upload (warn) |

## Audit Trail Integration

All worker operations are audited (ADR-0232/0233):

```python
# Example audit event (emitted by video_assembler)
{
    "tenant_id": "_default",
    "timestamp": "2026-09-13T10:01:23.456Z",
    "event_type": "video_assembled",
    "video_path": "/path/output.mp4",
    "duration_seconds": 35.2,
    "encoding_latency_ms": 45230,
    "timing_issues_count": 1,
    "confidence": 0.85,
    "hash": "sha256(previous_event_hash + this_event_data)",
    "prev_hash": "sha256(...)",
}
```

The hash chain ensures:
- **Immutability:** Audit trail cannot be altered post-hoc
- **Traceability:** Every video production is fully auditable
- **GDPR Compliance (Art. 32):** Data integrity proof

## Testing Strategy

### Phase 3 Test Coverage

**Critical E2E Tests (5 must-pass):**

1. `test_slide_renderer_e2e()` — PPT → PNG sequence + metadata
2. `test_video_assembler_timing_validation()` — Timing issues detected
3. `test_video_assembler_storyboard_lock()` — Exception on mid-execution modification
4. `test_phase_3_parallel_workers()` — Voice + slides + screenshots in parallel
5. `test_orchestrator_phase_6_output_completeness()` — All required fields present

**Coverage by Component:**
- Slide Renderer: 8 tests (init, preconditions, latency measurement, file creation)
- Video Assembler: 10 tests (timing validation, FFmpeg execution, metadata collection)
- Orchestrator: 7 tests (phase wiring, gate enforcement, error handling)

**Gate Requirements:**
- ✅ 0 regressions in Phase 1–2 tests
- ✅ All critical tests PASS
- ✅ E2E proof: PPT → PNG → MP4 (end-to-end real execution)

## Performance Targets (Non-Binding Guidance)

| Operation | Baseline | Target | Notes |
|-----------|----------|--------|-------|
| Slide render (1920×1080 @ 150 DPI) | 400ms | < 500ms | Per-slide, parallel |
| Audio synthesis (10s narration) | 2s | < 3s | Via TTS engine |
| FFmpeg encoding (5min video, 7200k) | 90s | 60-120s | Depends on CPU cores |
| Full orchestration (5-scene video) | 120s | < 180s | All phases, tolerance window |

## Reference Commands

### Manual FFmpeg Composition

```bash
# Simple 2-scene composition
ffmpeg \
  -i slides/s01.png -i audio/s01.mp3 \
  -i slides/s02.png -i audio/s02.mp3 \
  -filter_complex \
    "[0:v]scale=1920:1080,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v0];
     [1:a]aformat=sample_rates=48000[a0];
     [v0][a0]concat=n=1:v=1:a=1[v01];
     [2:v]scale=1920:1080,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v1];
     [3:a]aformat=sample_rates=48000[a1];
     [v1][a1]concat=n=1:v=1:a=1[v12];
     [v01][v12]concat=n=2:v=1:a=1[v]" \
  -map "[v]" -c:v libx264 -preset medium -b:v 7200k -y output.mp4
```

### Get Video Duration (ffprobe)

```bash
ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 \
  output.mp4
# Output: 35.234567
```

## Known Limitations & Future Work

### Limitations (Phase 3)

1. **Filter Graph Complexity:** Current implementation handles basic concat; advanced compositions (crossfades, effects) require richer filter graph
2. **Subtitle Timing:** Assumes SRT timecode alignment with audio; manual adjustment may be needed
3. **Platform Specificity:** PPT rendering varies (LibreOffice UNO, Playwright, native APIs) by platform
4. **Memory Usage:** Large PPT files (100+ slides) may exceed available RAM; should implement streaming

### Future Phases (4b+)

- **Phase 4b:** Motion graphics + transitions (via effects compositing)
- **Phase 5:** Thumbnail generation + preview creation
- **Phase 6:** Adaptive bitrate selection based on quality feedback
