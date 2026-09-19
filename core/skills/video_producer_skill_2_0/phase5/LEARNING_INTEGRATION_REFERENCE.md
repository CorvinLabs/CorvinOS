# Learning Integration Reference — Blocker 6 Implementation Guide

**Quick Start Guide for Integrating Learning Feedback into Video Production Tier Selection**

---

## API Reference

### RenderOutcome Dataclass

```python
@dataclass
class RenderOutcome:
    """Result of a tier render operation"""
    success: bool                           # True if render completed
    tier: str                              # "TIER_1_QUICK", "TIER_2_RICH", "TIER_3_PREMIUM"
    animation_id: str                      # Animation identifier
    render_time_ms: int                    # Render time in milliseconds
    quality_score: float                   # Quality score (0.0 - 1.0)
    fallback_used: bool = False            # True if original tier failed and fallback was used
    error: Optional[str] = None            # Error message if failed
    confidence: float = field(default=0.5) # P(tier succeeds next time) - set by optimizer
```

### LearningOptimizer Methods

#### record_render_outcome(outcome: RenderOutcome) → RenderOutcome

Record a tier render outcome and update metrics.

```python
optimizer = LearningOptimizer()

outcome = RenderOutcome(
    success=True,
    tier="TIER_2_RICH",
    animation_id="lesson_001",
    render_time_ms=25000,
    quality_score=0.85,
    fallback_used=False
)

result = optimizer.record_render_outcome(outcome)
# result.confidence now set to calculated value
```

#### calculate_confidence(outcome: RenderOutcome) → float

Calculate confidence P(tier succeeds next time) based on success/failure.

```python
confidence = optimizer.calculate_confidence(outcome)
# Returns: 0.0 - 1.0
# Success + high quality: 0.8-1.0
# Failure + fallback: 0.1-0.2
```

#### optimizer_feedback_next_tier(animation_id: str) → str

Calculate recommended tier for next render based on historical performance.

```python
next_tier = optimizer.optimizer_feedback_next_tier("lesson_001")
# Returns: "TIER_1_QUICK", "TIER_2_RICH", or "TIER_3_PREMIUM"
# Based on: success_rate (70%) + render_time trade-off (30%)
```

#### get_tier_success_rate(tier_name: str) → float

Get success rate for a tier (0.0 - 1.0).

```python
success_rate = optimizer.get_tier_success_rate("TIER_2_RICH")
# 0.75 means 75% success rate (3 successes, 1 failure)
```

#### get_tier_avg_render_time(tier_name: str) → float

Get average render time for a tier in milliseconds.

```python
avg_time_ms = optimizer.get_tier_avg_render_time("TIER_1_QUICK")
# 5200.0 means average 5.2 second render time
```

---

## Integration Examples

### Example 1: Basic Tier Dispatcher Usage

```python
from tier_dispatcher import TierDispatcher, AnimationRequest, TierLevel
from learning_integration import LearningOptimizer

# Initialize optimizer
optimizer = LearningOptimizer()

# Create dispatcher with learning integration
dispatcher = TierDispatcher(
    tier1=quick_renderer,
    tier2=manim_animator,
    tier3=premium_queue,
    learning_optimizer=optimizer
)

# Dispatch request
request = AnimationRequest(
    animation_id="lesson_001",
    didactic_level="intermediate",
    duration_seconds=30,
    preferred_tier=TierLevel.TIER_2_RICH,
    narration_audio="lesson.mp3",
    voice_sync_mapping={"frame_to_event": {...}}
)

result = dispatcher.dispatch(request)
# Internally:
# 1. Queries optimizer for recommended tier (may differ from preferred_tier)
# 2. Tries recommended tier
# 3. Records RenderOutcome with confidence score
# 4. Falls back if needed, recording fallback outcomes
# 5. Applies voice-sync composition
```

### Example 2: Monitoring Tier Performance

```python
# After several renders...

stats = optimizer.get_statistics()
# Returns:
# {
#     "lesson_001": {
#         "avg_quality": 8.5,
#         "avg_engagement": 7.2,
#         "avg_render_time_ms": 24000,
#         "preferred_tier": "TIER_2_RICH",
#         "num_samples": 8
#     }
# }

# Per-tier success rates
tier1_success = optimizer.get_tier_success_rate("TIER_1_QUICK")       # 0.95
tier2_success = optimizer.get_tier_success_rate("TIER_2_RICH")       # 0.76
tier3_success = optimizer.get_tier_success_rate("TIER_3_PREMIUM")    # 0.60

# Tier performance
tier1_time = optimizer.get_tier_avg_render_time("TIER_1_QUICK")      # 5200.0 ms
tier2_time = optimizer.get_tier_avg_render_time("TIER_2_RICH")       # 26000.0 ms
tier3_time = optimizer.get_tier_avg_render_time("TIER_3_PREMIUM")    # 15000.0 ms
```

### Example 3: Confidence-Based Retry Strategy

```python
# Use confidence scores to decide whether to retry or accept failure

outcome = RenderOutcome(
    success=False,
    tier="TIER_3_PREMIUM",
    animation_id="lesson_002",
    render_time_ms=0,
    quality_score=0.0,
    fallback_used=True,
    error="Timeout after 60s"
)

result = optimizer.record_render_outcome(outcome)
confidence = result.confidence  # 0.2 (very low, fallback used)

if confidence < 0.3:
    # Very low confidence in this tier
    logger.warning(f"TIER_3 failing consistently, recommend TIER_2 or TIER_1")
    next_tier = optimizer.optimizer_feedback_next_tier("lesson_002")
    logger.info(f"Optimizer recommends {next_tier}")
elif confidence < 0.6:
    # Medium-low confidence
    logger.info(f"TIER_3 has moderate issues, consider alternative")
else:
    # Good confidence, transient failure likely
    logger.debug(f"Transient failure, TIER_3 mostly reliable")
```

### Example 4: Training Loop for Convergence

```python
# Continuous improvement loop

import time

for epoch in range(100):
    # Sample animations
    animations = get_next_batch_of_animations()
    
    for anim in animations:
        # Dispatch using optimizer
        request = AnimationRequest(
            animation_id=anim.id,
            didactic_level=anim.level,
            duration_seconds=anim.duration,
            preferred_tier=TierLevel.TIER_2_RICH
        )
        
        result = dispatcher.dispatch(request)
        
        # (Operator would provide feedback here in real scenario)
    
    # Check convergence
    if epoch % 10 == 0:
        stats = optimizer.get_statistics()
        confidences = [
            optimizer.get_tier_success_rate(f"TIER_{i}")
            for i in [1, 2, 3]
        ]
        
        logger.info(f"Epoch {epoch}: tier confidences = {confidences}")
        
        # Early stopping if converged
        if max(confidences) > 0.95:
            logger.info("Converged! Excellent tier selection accuracy.")
            break

logger.info("Training complete. Optimizer ready for production.")
```

---

## Confidence Scoring Details

### Success Case (success=True)

```
base_confidence = 0.8
quality_bonus = quality_score * 0.2        (0-0.2 range)
time_factor = depends on tier:
  - TIER_1_QUICK: 1.0   (fast is expected)
  - TIER_2_RICH: 0.9 (if <30s) or 0.7 (if >30s)
  - TIER_3_PREMIUM: 0.8 (async, less predictable)

final_confidence = min(1.0, (base + bonus) * time_factor)
Range: 0.8 - 1.0
```

### Failure Case (success=False, fallback_used=False)

```
base_confidence = 0.3
recovery_factor = _tier_recovery_rate(tier)
  - High success rate tier (>80%): 0.7
  - Moderate success rate (50-80%): 0.4
  - Low success rate (<50%): 0.1

final_confidence = base + (recovery_factor * 0.3)
Range: 0.3 - 0.6
```

### Failure with Fallback (success=False, fallback_used=True)

```
final_confidence = 0.2  (very low, original tier clearly failed)
Range: 0.1 - 0.2
```

---

## Audit Trail Integration

All render outcomes are logged as immutable events:

```json
{
  "event_type": "skill_executed",
  "skill_id": "video_producer.tier_dispatcher",
  "timestamp": "2026-09-20T14:35:22.123Z",
  "tenant_id": "_default",
  "input": {
    "tier": "TIER_2_RICH",
    "animation_id": "lesson_001",
    "preferred_tier": "TIER_2_RICH"
  },
  "output": {
    "success": true,
    "render_time_ms": 25000,
    "quality_score": 0.85,
    "fallback_used": false
  },
  "confidence": 0.92,
  "lom": "tier_dispatcher.py:L142",
  "prev_hash": "sha256(...)",
  "chain_hash": "sha256(...)"
}
```

Events are:
- Immutable (frozen dataclass)
- Hash-chained (for audit trail verification)
- Tenant-scoped (GDPR Art. 32)
- Timestamped (ISO 8601 UTC)

---

## Troubleshooting

### Issue: All tiers have low success rates

**Cause:** Animations may be too complex or system resources insufficient.

**Solution:**
1. Check `get_statistics()` for quality scores
2. Verify render_time_ms values aren't exceeding tier limits
3. Consider increasing tier time budgets
4. Review error messages in fallback outcomes

### Issue: Optimizer keeps recommending the same tier

**Cause:** Dominant tier has very high success rate (>90%).

**Solution:**
1. Check success rate: `get_tier_success_rate("TIER_X")`
2. Review time trade-offs: `get_tier_avg_render_time()`
3. If acceptable, this is correct behavior (exploit best tier)
4. If wanting exploration, add noise to optimizer score

### Issue: Confidence scores not changing

**Cause:** Not recording render outcomes.

**Solution:**
1. Verify `record_render_outcome()` is called after each render
2. Check TierDispatcher integration:
   ```python
   dispatcher = TierDispatcher(..., learning_optimizer=optimizer)
   ```
3. Monitor logs for "Render outcome recorded"

---

## Best Practices

1. **Always record outcomes**, even failures
   - Failures are valuable learning signals
   
2. **Use confidence scores for decisions**
   - Confidence > 0.8: Safe to use tier
   - Confidence 0.5-0.8: Monitor, prepare fallback
   - Confidence < 0.5: Avoid if possible
   
3. **Monitor convergence**
   - Check `get_tier_success_rate()` periodically
   - Expect convergence after 50-100 renders per tier
   
4. **Validate quality scores**
   - Ensure quality_score reflects actual output quality
   - Range should be 0.0-1.0 (normalized)
   
5. **Track recovery rates**
   - Use `_tier_recovery_rate()` for retry strategies
   - High recovery rate = transient failures OK to retry

---

**Documentation Version:** 1.0  
**Last Updated:** 2026-09-20  
**Status:** PRODUCTION READY ✅
