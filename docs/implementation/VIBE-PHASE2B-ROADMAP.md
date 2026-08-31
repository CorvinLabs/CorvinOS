# VIBE Phase 2b — Real Integration Roadmap

**Status:** Ready to start (Phase 2a complete with 3 commits + 2 ADRs)  
**Estimated Duration:** 2-3 weeks  
**Priority:** HIGH (enables voice I/O + learning in production)

---

## What Phase 2b Adds

Phase 2a built the **skeleton** (BtwAdvisor, VoiceCoordinator, TaskManager).  
Phase 2b adds the **real integrations** that make them production-ready.

### Deliverables

| # | Task | Effort | Depends On | ADR |
|---|---|---|---|---|
| 2b-1 | Real STT integration (Whisper) | 3 days | VoiceCoordinator (✅ Phase 2a) | — |
| 2b-2 | Real TTS audio streaming | 4 days | VoiceCoordinator (✅ Phase 2a) | — |
| 2b-3 | Hub event publishing wiring | 2 days | BtwAdvisor, VoiceCoordinator, TaskManager (✅ Phase 2a) | ADR-0508 |
| 2b-4 | VibeDashboard UI (Proposal 4) | 5 days | Console API wiring | ADR-0509 |
| 2b-5 | Feature flag loading (tenant.corvin.yaml) | 2 days | App config refactor | — |
| 2b-6 | Confidence scoring refinement | 3 days | Real STT metrics | — |
| **Total** | **Phase 2b** | **~19 days** | **All Phase 2a complete** | **0508-0509** |

---

## Detailed Tasks

### 2b-1: Real STT Integration

**Current State:** Mock STT in WebSocket (voice_stream_routes.py:123)

**Implementation:**
```python
# core/gateway/routes/voice_stream_routes.py
# Replace mock STT with real Whisper

from openai import OpenAI  # or: from ollama import Ollama

async def transcribe_audio_chunk(audio_bytes: bytes) -> Dict[str, Any]:
    """Real STT via OpenAI Whisper (or local Ollama fallback)."""
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    result = client.audio.transcriptions.create(
        model="whisper-1",
        file=("audio.wav", audio_bytes),
        language="en",
    )
    
    return {
        "text": result.text,
        "confidence": 0.92,  # TODO: extract from result metadata
    }
```

**Testing:**
- Unit test: mock OpenAI API
- Integration test: real audio file → transcription
- E2E: record voice → transcribe → verify text matches

**Timeline:** 3 days

---

### 2b-2: Real TTS Audio Streaming

**Current State:** Mock TTS (voice_stream_routes.py:send_response_audio stubs)

**Implementation:**
```python
# core/gateway/routes/voice_stream_routes.py
# Add TTS streaming when Brain sends response_ready event

from piper import piper_tts  # or: use OpenAI TTS

async def stream_tts_response(text: str, voice: str = "nova") -> AsyncIterator[bytes]:
    """Stream TTS audio chunks to WebSocket client."""
    # TODO: use piper or OpenAI TTS API
    # yield audio_chunk every 500ms
    pass
```

**Wiring:**
- VoiceCoordinator.handle_request("start_tts") → call stream_tts_response
- WebSocket loop: await generator, send each chunk via send_response_audio()
- Brain publishes "response_ready" event → VoiceCoordinator queues TTS

**Testing:**
- Unit: mock TTS API
- E2E: response_ready → audio chunks sent → client receives

**Timeline:** 4 days

---

### 2b-3: Hub Event Publishing Wiring

**Current State:** all subsystems have `publish_event()` (todo marked)

**Implementation:**

1. **Wire BtwAdvisor → Hub:**
   ```python
   # core/gateway/routes/btw_routes.py (line 103-110, marked TODO K1-003)
   
   from core.orchestration.hub import get_hub
   
   hub = get_hub(task_id)
   await hub.publish_event("guidance_received", {
       "actor": actor,
       "task_id": task_id,
       "instruction": instruction_text,
       ...
   })
   ```

2. **Wire VoiceCoordinator → Hub:**
   ```python
   # core/orchestration/subsystems/voice_coordinator.py (publish_event method)
   
   async def publish_event(self, event_name: str, event_data: Dict):
       """Publish to Hub (Brain listens)."""
       if self.hub:
           await self.hub.publish_event(event_name, event_data)
   ```

3. **Wire TaskManager → Hub:**
   ```python
   # core/orchestration/subsystems/task_manager.py
   
   # Subscribe to Brain's loss_signal events
   # Publish pattern_learned to Hub
   ```

**Testing:**
- Mock Hub: verify publish_event() called with correct payload
- Real Hub: end-to-end event flow

**Timeline:** 2 days

**ADR:** ADR-0508 (Hub integration architecture)

---

### 2b-4: VibeDashboard UI (Proposal 4)

**Current State:** Deferred to Phase 2b

**Implementation:** (React components)
```tsx
// core/console/corvin_console/web-next/src/pages/vibe-engineering/VibeDashboard.tsx

const VibeDashboard = ({ task }) => (
  <div className="vibe-dashboard">
    <ProgressBar current={task.current_file} total={task.total_files} />
    <StrategyCard strategy={task.strategy} confidence={task.strategy_confidence} />
    <CostCard spent={task.cost_spent} budget={task.budget} />
    <ErrorsCard errors={task.errors} />
    <BtwInput onSubmit={handleBtw} voiceEnabled={true} />
    <VoicePanel onSpeak={startVoiceInput} />
  </div>
)
```

**Components:**
- ProgressBar: 0/50 files complete
- StrategyCard: current strategy + confidence
- CostCard: spent vs budget
- ErrorsCard: error list + root causes
- BtwInput: text input for /btw commands
- VoicePanel: voice mic + status

**Testing:**
- Console E2E: navigate to dashboard, see real task state
- WebSocket mock: voice input simulation

**Timeline:** 5 days

**ADR:** ADR-0509 (VibeDashboard UX + API contract)

---

### 2b-5: Feature Flag Loading

**Current State:** Hardcoded in btw_routes.py:26

**Implementation:**
```python
# Load from tenant.corvin.yaml

from core.tenant.tenant_config import load_tenant_config

config = load_tenant_config(tenant_id)
features_enabled = config.get("spec", {}).get("features", {})

btw_steering_enabled = features_enabled.get("btw_steering_enabled", False)
voice_streaming_enabled = features_enabled.get("voice_streaming_enabled", False)
task_learning_enabled = features_enabled.get("task_learning_enabled", False)
```

**Config Schema:**
```yaml
# tenant.corvin.yaml

spec:
  features:
    btw_steering_enabled: false       # dark ship default
    voice_streaming_enabled: false
    task_learning_enabled: false
    vibe_dashboard_enabled: false
```

**Timeline:** 2 days

---

### 2b-6: Confidence Scoring Refinement

**Current State:** Hardcoded 0.7 threshold in all subsystems

**Implementation:**
```python
# core/learning/confidence_scoring.py (NEW)

class ConfidenceScorer:
    """Confidence estimation from real STT metrics."""
    
    async def score_transcription(self, 
        stt_result: Dict,
        audio_quality: float,  # signal/noise ratio
        speaker_match: float   # speaker fingerprint match
    ) -> float:
        """Combined confidence from multiple signals."""
        base_confidence = stt_result.get("confidence", 0.8)
        adjusted = base_confidence * audio_quality * speaker_match
        return min(1.0, adjusted)
```

**Metrics to Collect:**
- STT confidence from Whisper API
- Audio quality (SNR)
- Speaker fingerprint match
- User satisfaction feedback (did Brain understand correctly?)

**Timeline:** 3 days

---

## ADRs for Phase 2b

### ADR-0508: Hub Integration Architecture
- How BtwAdvisor/VoiceCoordinator/TaskManager publish to Hub
- Event payload contracts
- Fallback behavior if Hub unavailable
- Audit trail integration

### ADR-0509: VibeDashboard UX + API
- Component hierarchy
- Real-time update mechanism (WebSocket or polling)
- Error display + root cause visualization
- /btw input UX + voice mic placement

---

## Dependencies & Blockers

**Must Complete Before 2b-1:**
- ✅ Phase 2a (BtwAdvisor, VoiceCoordinator, TaskManager) — DONE

**Must Have for 2b-3:**
- Hub event publishing API (may already exist; verify with Brain team)
- Task ID routing to correct Hub instance

**Must Have for 2b-4:**
- Console web-next build system (should exist; verify)
- Real task state API endpoint

---

## Success Criteria

| Criterion | How to Verify |
|---|---|
| STT latency < 100ms per chunk | Measure in E2E test |
| TTS starts < 500ms after response ready | Measure timestamp delta |
| /btw affects next strategy | Trace LoopEngineer.next_strategy() decision |
| Voice works end-to-end | Record voice → transcribe → hear response |
| TaskManager learns | Complete task → check JSONL file → recommend on next task |
| VibeDashboard updates real-time | Animate progress bar while task runs |
| Feature flags work | Toggle in tenant.corvin.yaml → feature on/off |

---

## Known Risks

| Risk | Mitigation |
|---|---|
| Whisper API rate limits | Cache results, implement backoff, fallback to local Ollama |
| TTS latency high | Use local piper instead of cloud API; stream chunks asap |
| Hub not ready | Wait for Hub team; Phase 2b can proceed with mock Hub until then |
| WebSocket timeout during TTS | Keep-alive pings; buffer audio on client |
| TOCTOU race in JSONL writes | AsyncIO lock in TaskPatternStore (already implemented) |

---

## Next Steps (After This Session)

1. **Review & Accept ADRs 0506-0507** (Phase 2a design)
2. **Assign Phase 2b ownership** (engineer or team)
3. **Create Sprint for Phase 2b:**
   - 2b-1: STT (3d)
   - 2b-2: TTS (4d)
   - 2b-3: Hub wiring (2d)
   - 2b-4: Dashboard (5d)
   - 2b-5: Config (2d)
   - 2b-6: Confidence (3d)
4. **Week 5 Decision Gate:** measure adoption, quality, safety
5. **Roll to production (Phase 3)** or iterate

---

**Handoff:** Phase 2a complete with 3 commits + 2 ADRs. Phase 2b roadmap ready. Token budget exhausted; recommend fresh session for Phase 2b implementation.
