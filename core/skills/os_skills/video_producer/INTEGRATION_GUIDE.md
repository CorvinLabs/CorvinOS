# Autonomous Video Processor — Integration Guide

**Status:** Production-ready for integration with maestro + workers  
**Date:** 2026-09-19  

---

## Integration Overview

The **Autonomous Video Processor** integrates seamlessly into the maestro orchestrator (ADR-0692) as a reusable Worker component.

```
os.video_producer (maestro)
  ├── Phase 1: Asset Analysis
  ├── Phase 2: Storyboard Generation
  ├── Phase 3: Parallel Workers
  │   ├── worker.asset_analyzer
  │   ├── AutonomousVideoProcessor ← NEW
  │   ├── BlenderHeadlessOrchestrator ← NEW
  │   ├── worker.voice_synthesizer
  │   └── worker.screenshot_capturer
  ├── Phase 4: Video Assembly
  ├── Phase 5: YouTube Export (async)
  ├── Phase 6: Learning Optimization
  └── Phase 7: Audit Finalization
```

---

## How to Use in Maestro

### 1. Import the Components

```python
from core.skills.os_skills.video_producer.autonomous_processor import (
    AutonomousVideoProcessor,
)
from core.skills.os_skills.video_producer.auto_format_converter import (
    AutoFormatConverter,
)
from core.skills.os_skills.video_producer.blender_orchestrator import (
    BlenderHeadlessOrchestrator,
    RenderConfig,
)
```

### 2. In the Maestro Orchestrator

```python
# In orchestrator_final_v6.py or maestro.py

async def orchestrate_phase_3_parallel_workers(self, storyboard):
    """Phase 3: Execute parallel workers with precondition checks."""
    
    # Precondition: storyboard must exist
    if not storyboard or not storyboard.get("scenes"):
        raise PreconditionNotMetError("Storyboard required for Phase 3")
    
    # Worker 1: Asset Analyzer (existing)
    analysis_result = await self.worker_asset_analyzer.execute(
        assets=self.assets,
    )
    
    # Worker 2: Autonomous Video Processor (NEW)
    processor = AutonomousVideoProcessor(
        tenant_id=self.tenant_id,
        output_dir=self.output_dir,
    )
    
    processor_result = await processor.process_video(
        input_file=self.input_video_file,
        output_file=f"{self.output_dir}/processed_video.mp4",
    )
    
    if not processor_result.success:
        self._log_audit("processor_failed", processor_result.validation.errors)
        raise ProcessingError("Video processing failed")
    
    # Worker 3: Blender Enhancement (NEW, optional)
    if self.enable_blender:
        blender = BlenderHeadlessOrchestrator(
            blend_file=self.blend_file,
            tenant_id=self.tenant_id,
        )
        
        render_result = await blender.render_enhancement(
            input_video=processor_result.output_file,
            config=RenderConfig(
                resolution="1920x1080",
                fps=30,
                frame_count=180,
                output_codec="h264",
                bitrate_kbps=8000,
            ),
        )
        
        if not render_result.success:
            logger.warning(f"Blender render failed: {render_result.errors}")
            # Fallback to processor output (non-fatal)
            final_video = processor_result.output_file
        else:
            final_video = render_result.output_file
    else:
        final_video = processor_result.output_file
    
    # Emit feedback events
    for event in processor_result.audit_events:
        self._log_audit(event["event_type"], event["data"])
    
    for event in processor_result.learning_events:
        await self.learning_event_sink.write(event)
    
    return {
        "video_file": final_video,
        "processor_result": processor_result,
        "analysis_result": analysis_result,
    }
```

### 3. In Worker Base Class

```python
# Extend worker_base.py to include autonomous processor support

class WorkerBase:
    """Base class for all Video Producer workers."""
    
    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.audit_events = []
        self.learning_events = []
    
    async def register_autonomous_processor(self):
        """Register autonomous processor for this worker."""
        self.processor = AutonomousVideoProcessor(
            tenant_id=self.tenant_id,
        )
        return self.processor
```

---

## Precondition Validation

### Define Preconditions

```python
from skills.skill_base import Precondition

@skill.register(
    preconditions=[
        Precondition(
            path="input_video",
            must_exist=True,
            description="Input video file required",
        ),
    ]
)
async def autonomous_process(input_video: str):
    processor = AutonomousVideoProcessor()
    result = await processor.process_video(input_video)
    return result
```

### Validate in Orchestrator

```python
async def validate_preconditions(self, worker_name: str, inputs: Dict):
    """Validate worker preconditions before execution."""
    worker = self.workers[worker_name]
    
    for precondition in worker.preconditions:
        if precondition.path == "input_video":
            if precondition.must_exist:
                if not Path(inputs["input_video"]).exists():
                    raise PreconditionNotMetError(
                        f"Precondition failed: {precondition.description}"
                    )
    
    return True
```

---

## Feedback Integration (ADR-0314)

### Emit Feedback from Maestro

```python
async def emit_feedback_from_processor(self, result):
    """Emit feedback from processor result."""
    
    # Collect learning events
    for event in result.learning_events:
        await self.learning_sink.write({
            "event_type": event["event_type"],
            "tenant_id": self.tenant_id,
            "skill_id": "video-producer:autonomous-processor",
            "data": event["data"],
        })
    
    # Emit per-scene feedback
    for scene in self.storyboard.get("scenes", []):
        await self.learning_sink.write({
            "event_type": "scene_rendered",
            "scene_id": scene["id"],
            "success": result.success,
            "quality_score": 0.85,  # From quality validator
        })
```

### Learning Loop

```python
async def optimize_from_feedback(self):
    """Optimizer reads feedback and tunes processor config."""
    
    # Read feedback signals
    feedback = await self.learning_sink.read_recent(
        event_type="video_processed",
        hours=24,
    )
    
    # Calculate effectiveness metrics
    success_rate = sum(1 for f in feedback if f["data"]["success"]) / len(feedback)
    
    # Tune processor parameters
    if success_rate < 0.9:
        # Increase tolerance or bitrate
        processor_config["bitrate_multiplier"] = 1.1
    elif success_rate > 0.95:
        # Optimize for speed
        processor_config["preset"] = "fast"
    
    # Save optimized config
    await self.config_store.save("processor_config", processor_config)
```

---

## Audit Trail Integration (ADR-0232)

### Hash-Chained Events

All processor events are automatically hash-chained:

```python
processor = AutonomousVideoProcessor(tenant_id="marketing")

result = await processor.process_video(input_file)

# Events in result.audit_events:
# {
#   "event_type": "phase_1_format_detection_complete",
#   "tenant_id": "marketing",
#   "timestamp": "2026-09-19T...",
#   "hash": "sha256(...)",
#   "prev_hash": "sha256(...)"  # ← Chained to previous event
# }
```

### Audit Trail Verification

```python
async def verify_audit_chain(self, processor_result):
    """Verify audit chain integrity."""
    
    prev_hash = None
    for event in processor_result.audit_events:
        if prev_hash:
            assert event["prev_hash"] == prev_hash, "Chain broken!"
        prev_hash = event["hash"]
    
    logger.info(f"✅ Audit chain verified ({len(processor_result.audit_events)} events)")
```

---

## Error Handling & Resilience

### Fail-Closed Design

```python
async def process_video_safely(input_file: str):
    processor = AutonomousVideoProcessor()
    
    try:
        result = await processor.process_video(input_file)
        
        if not result.success:
            # Log failure, don't fall back silently
            logger.error(f"Processing failed: {result.validation.errors}")
            raise ProcessingError(str(result.validation.errors))
        
        return result
    
    except ProcessingError as e:
        # Emit alert, notify operator
        await alert_operator(f"Video processing failed: {e}")
        raise
```

### Graceful Degradation (optional)

```python
async def process_with_fallback(input_file: str):
    processor = AutonomousVideoProcessor()
    
    # Try enhanced processing first
    result = await processor.process_video(
        input_file,
        config={"enable_blender": True},
    )
    
    if result.success:
        return result
    
    # Fallback: try simple processing
    logger.warning("Enhanced processing failed, trying simple")
    result = await processor.process_video(
        input_file,
        config={"enable_blender": False},
    )
    
    if not result.success:
        raise ProcessingError("Both processing attempts failed")
    
    return result
```

---

## Monitoring & Observability

### Track Processing Metrics

```python
async def track_processing_metrics(result):
    """Track metrics for observability dashboard."""
    
    metrics = {
        "processor_success_rate": 1 if result.success else 0,
        "format_detection_time_ms": compute_phase_time(
            result.process_stages["format_detection"]
        ),
        "processing_time_ms": compute_phase_time(
            result.process_stages["processing"]
        ),
        "validation_time_ms": compute_phase_time(
            result.process_stages["validation"]
        ),
        "total_time_sec": result.total_duration_sec,
        "output_codec": result.validation.video_codec,
        "output_bitrate_kbps": result.validation.bitrate_kbps,
        "output_file_size_mb": result.validation.file_size_bytes / 1e6,
    }
    
    await metrics_sink.write("video_processor", metrics)
```

### Dashboard Queries

```sql
-- Average processing time by pipeline type
SELECT
  pipeline_type,
  AVG(processing_time_ms) as avg_time_ms,
  COUNT(*) as total_videos
FROM video_processor_metrics
WHERE timestamp > NOW() - INTERVAL 24 HOUR
GROUP BY pipeline_type;

-- Success rate by resolution
SELECT
  output_resolution,
  SUM(processor_success_rate) / COUNT(*) as success_rate
FROM video_processor_metrics
WHERE timestamp > NOW() - INTERVAL 7 DAY
GROUP BY output_resolution;
```

---

## Testing Integration

### E2E Test Example

```python
@pytest.mark.asyncio
async def test_maestro_orchestrate_with_autonomous_processor():
    """Test maestro orchestrator with autonomous processor."""
    
    maestro = MaestroOrchestrator(
        tenant_id="test_tenant",
        assets_path="/test/assets",
        output_dir="/test/output",
        enable_blender=True,
    )
    
    # Create test storyboard
    storyboard = {
        "scenes": [
            {"id": "s01", "type": "intro"},
            {"id": "s02", "type": "demo"},
        ]
    }
    
    # Orchestrate (calls AutonomousVideoProcessor internally)
    result = await maestro.orchestrate(storyboard)
    
    # Verify
    assert result.success
    assert result.output_file.endswith(".mp4")
    assert len(result.audit_events) > 0
    assert len(result.learning_events) > 0
```

---

## Configuration Schema

```yaml
# In tenant.corvin.yaml

video_producer:
  autonomous_processor:
    enabled: true
    output_dir: /mnt/videos/output
    fail_closed: true  # Reject invalid input
    
  blender:
    enabled: true
    timeout_sec: 600  # 10 min default
    max_resolution: "3840x2160"  # 4K max
    
  format_converter:
    preferred_codec: h264  # H.264 or vp9
    bitrate_cap_kbps: 20000  # 20 Mbps max
    
  learning:
    enabled: true
    feedback_sink: "event_store"
    optimizer_enabled: true
    
  audit:
    enabled: true
    hash_chain_verify: true
    retention_days: 90
```

---

## Support & Troubleshooting

**Q: How do I enable Blender rendering?**

A: Set `enable_blender: true` in config or pass `RenderConfig` to `render_enhancement()`:

```python
blender = BlenderHeadlessOrchestrator(blend_file="/path/to/scene.blend")
result = await blender.render_enhancement(
    input_video="/tmp/input.mp4",
    config=RenderConfig(
        resolution="1920x1080",
        fps=30,
        frame_count=90,
        output_codec="h264",
        bitrate_kbps=5000,
    ),
)
```

**Q: What if format detection fails?**

A: The processor returns `result.success = False` with `result.validation.errors` detailing the issue. No fallback; operator must fix input.

**Q: Can I customize bitrate calculation?**

A: Yes, subclass `AutoFormatConverter` and override `_calculate_optimal_bitrate()`:

```python
class CustomConverter(AutoFormatConverter):
    def _calculate_optimal_bitrate(self, resolution, fps):
        # Custom formula
        return 8000  # Fixed 8 Mbps
```

**Q: How are learning events used?**

A: The optimizer reads feedback and tunes processor config per-tenant. See `learning_optimizer.py` for details.

---

**Version:** 1.0  
**Status:** ✅ Production-ready  
**Last Updated:** 2026-09-19
