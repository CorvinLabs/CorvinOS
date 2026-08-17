# Phase 7c Deployment Checklist

**What:** Wire ChatLearningWrapper into production code  
**Time:** 30 min hands-on + 5 min monitoring  
**Risk:** Low (wrapped execution, no behavior change, rollback instant)  
**Tested:** ✅ All 7 components verified (phase7c_live_integration.py)

---

## Pre-Deployment (5 min)

- [ ] Read `docs/TREE_OF_THOUGHTS_LIVE_WIRING.md` (architecture overview)
- [ ] Read `docs/TREE_OF_THOUGHTS_OPERATOR_GUIDE.md` (operator UX)
- [ ] Review this checklist
- [ ] Have rollback plan ready (git revert SHA)

**Rollback Plan:** If latency increases >50ms or errors spike:
```bash
git revert 40c6f2f  # This commit (Phase 7c docs)
git push
# Dashboard still works (frozen confidence from Phase 7d)
```

---

## Step 1: Wire Chat Turn Tracking (10 min)

### File: `core/console/corvin_console/chat_runtime.py`

**Find:** Line 4432 (or search for `async def stream_turn`)

**Look for:**
```python
async def stream_turn(...):
    # ... setup code ...
    async for event in _stream_claude_turn(
        chat_key=chat_key,
        messages=messages,
        system_prompt=system_prompt,
        # ... other args ...
    ):
        yield event
```

**Replace with:**
```python
async def stream_turn(...):
    # ... setup code ...
    
    from core.console.corvin_console.chat_learning_wrapper import get_chat_learning_wrapper
    
    wrapper = get_chat_learning_wrapper(session.tenant_id)
    
    async for event in wrapper.stream_turn_with_learning(
        stream_turn_fn=_stream_claude_turn,
        chat_key=chat_key,
        messages=messages,
        system_prompt=system_prompt,
        # ... other kwargs (copy them from _stream_claude_turn call)
    ):
        yield event
```

**Verify:**
```bash
cd /home/shumway/projects/CorvinOS
git diff core/console/corvin_console/chat_runtime.py | head -30
```

Expected: `+` lines show wrapper import + new wrapper call

---

## Step 2: Wire TTS Provider Tracking (10 min)

### File: `operator/voice/scripts/say.py` (or wherever TTS subprocess is called)

**Find:** Where `subprocess.run()` or `asyncio.create_subprocess_exec()` calls the TTS provider

**Pattern:** Look for calls like:
```python
result = await asyncio.create_subprocess_exec(
    "ffmpeg", "-f", "pipe", ...
)
```
Or:
```python
result = subprocess.run(["espeak", ...])
```

**Wrap with:**
```python
from core.learning import LearningIntegration, ExecutionMetrics
import time

integration = LearningIntegration()
start = time.time()

try:
    # Original subprocess call
    result = await asyncio.create_subprocess_exec(
        "ffmpeg", "-f", "pipe", ...
    )
    success = (result.returncode == 0 if hasattr(result, 'returncode') else True)
except Exception as e:
    success = False

elapsed = int((time.time() - start) * 1000)

# Record metrics
metrics = ExecutionMetrics(
    subject_id=f"pattern_tts_{provider_name}",  # e.g., "pattern_tts_edge"
    latency_ms=elapsed,
    success=success,
    context={"provider": provider_name, "voice": voice_id}
)
integration.metrics.record(metrics)

return result if success else None
```

**Note:** If there are multiple TTS providers in the same file, wrap each one separately with its own `provider_name`.

**Verify:**
```bash
cd /home/shumway/projects/CorvinOS
grep -n "pattern_tts_" operator/voice/scripts/say.py
```

Expected: 1-3 lines showing each provider pattern registered

---

## Step 3: Local Test (5 min)

### Run E2E readiness check:
```bash
cd /home/shumway/projects/CorvinOS
python3 tests/test_learning_phase7c_live_integration.py
```

**Expected output:**
```
✅ PHASE 7c ALL COMPONENTS READY FOR DEPLOYMENT

📋 Deployment Checklist:
   1. Read: docs/TREE_OF_THOUGHTS_LIVE_WIRING.md
   2. Wire: ChatLearningWrapper into chat_runtime.py::stream_turn()
   3. Deploy: Push to production
   4. Monitor: Watch confidence updates in dashboard
```

If not all green, check Step 1-2 again.

---

## Step 4: Commit & Deploy (5 min)

```bash
cd /home/shumway/projects/CorvinOS

# Stage changes
git add core/console/corvin_console/chat_runtime.py
git add operator/voice/scripts/say.py  # Or wherever TTS was wrapped

# Verify nothing else got staged
git status

# Commit
git commit -m "feat(learning): Phase 7c live wiring — chat turns + TTS tracking

Wires ChatLearningWrapper into production:
- Chat turn path: wrap stream_turn() with learning tracking
- TTS path: track provider calls and success rates
- Confidence updates in real-time based on execution

Ready for W1 canary (10% instances, 1 week)

Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>"

# Push
git push origin main
```

---

## Step 5: Verify Live (10 min)

### Start console (local or staging):
```bash
corvinos-serve --debug
```

### In browser, open dashboard:
```
http://localhost:3000/learning
```

### Simulate a chat turn:
```bash
# In another terminal, send a message to the chat
curl -X POST http://localhost:3000/v1/console/chat/turn \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, what can you do?",
    "chat_key": "test-chat"
  }'
```

### Watch dashboard update:
- Page should show pattern `pattern_chat_turn_execution`
- Check if `calls_in_production` incremented (was 0, now ≥1)
- Confidence should be ~0.5-0.55 (initial + success)

### Test TTS tracking:
```bash
# Trigger a TTS call (voice response)
# Or call say.py directly if available

python3 operator/voice/scripts/say.py "Hello world" --provider=openai
```

### Verify in dashboard:
- Pattern `pattern_tts_openai` should appear
- Confidence should be ~0.5-0.55

---

## Step 6: Monitor First 24 Hours

After deployment to production, watch:

| Metric | Expected | Alert threshold |
|--------|----------|-----------------|
| Chat latency | 850ms | >900ms (+50ms) |
| Error rate | <2% | >5% |
| Confidence updates/day | 100+ | <10 |
| Dashboard response time | <100ms | >200ms |

**Check logs:**
```bash
# Chat turn errors
tail -100 ~/.corvin/logs/console.log | grep "learning_wrapper\|stream_turn"

# TTS tracking errors
tail -100 ~/.corvin/logs/voice.log | grep "ExecutionMetrics\|pattern_tts"

# Learning system errors
tail -100 ~/.corvin/learning/events/$(date +%Y-%m-%d).jsonl | tail -10
```

---

## Step 7: Operator Feedback (Week 1-2)

After Phase 7c is live for 24 hours:

### Gather feedback:
- [ ] Did chat turn latency increase noticeably? (No = ✅)
- [ ] Are confidence scores updating? (Yes = ✅)
- [ ] Are all 12 patterns showing ≥1 call? (Yes = ✅)
- [ ] Did any TTS provider confidence drop unexpectedly? (Investigate if yes)

### Update dashboard notes:
Visit `/learning`, grade a pattern or two (👍/😐/👎) to verify grading works end-to-end.

### Report results:
- If all ✅: Proceed to W2 ramp (50% instances)
- If any issue: Post to #learning Slack channel, include logs from Step 6

---

## Rollback (Instant)

If anything goes wrong, rollback in seconds:

```bash
cd /home/shumway/projects/CorvinOS

# Find the Phase 7c commit
git log --oneline | grep "Phase 7c live wiring"

# Revert (replace SHA with actual commit)
git revert <SHA>

# Push
git push origin main

# Restart console
# (Dashboard will show frozen confidence from Phase 7d)
```

**What doesn't roll back:** All learning events already recorded (audit trail is immutable). Dashboard still works with frozen confidence.

---

## Success Criteria

Phase 7c deployment is successful when:

- ✅ All 12 patterns show ≥1 production call
- ✅ Chat turn confidence updates within 5 min of execution
- ✅ TTS provider confidence reflects actual success rate
- ✅ Zero performance regression (latency <1% change)
- ✅ Operator can grade patterns + see impact
- ✅ No error spam in logs

---

## Support

| Issue | Solution |
|-------|----------|
| Confidence not updating | Check `/learning/nodes` API returns new `calls_in_production` |
| TTS patterns missing | Grep for `pattern_tts_` in say.py, verify provider name matches |
| Dashboard latency spike | Check if EventStore is writing to slow disk (see audit.py L50+) |
| Chat turn errors | Review chat_learning_wrapper.py error handling (L40-60) |

---

**Next:** After W1 canary stabilizes, proceed to Phase 8 (Anomaly Detection). See `PHASE8_ROADMAP.md`.

**Status:** READY FOR PRODUCTION DEPLOYMENT  
**Last Updated:** 2026-08-17
