# Video Producer LLM Synthesis — Implementation Plan

**Status:** Ready for Phase 1  
**Timeline:** 6 weeks  
**Total LOC:** 2,200  

---

## Phase 1: Spec Generation & Validation (Weeks 1–2)

### Deliverables (1,100 LOC)

| Item | LOC | Files |
|---|---|---|
| Video Spec Schema (Pydantic) | 150 | `llm_synthesis/spec_schema.py` |
| Spec Validator (fail-closed) | 120 | `llm_synthesis/spec_validator.py` |
| LLM Prompt Template | 80 | `llm_synthesis/prompts.py` |
| Spec Generator | 180 | `llm_synthesis/spec_generator.py` |
| Spec Cache | 120 | `llm_synthesis/spec_cache.py` |
| Unit Tests | 300 | `tests/test_llm_synthesis_phase1.py` |
| E2E Test | 150 | `tests/e2e/test_llm_spec_generation.py` |

### Commits (3)

```
feat(video-producer): add LLM spec schema + validation
feat(video-producer): add LLM spec generator
test(video-producer): E2E test LLM spec generation
```

### Gate Criteria

- [ ] `spec_schema.py` validates valid/invalid specs
- [ ] `spec_validator.py` fail-closed (rejects 100% invalid)
- [ ] LLM spec generation: 3/3 test cases pass
- [ ] Cache hit rate >80%
- [ ] E2E: brief → spec_json → validation → SUCCESS

---

## Phase 2: Tool Execution (Weeks 3–4)

### Deliverables (1,650 LOC)

| Item | LOC | Files |
|---|---|---|
| Frame Renderer (extend PIL) | 200 | `renderers/frame_renderer.py` |
| SVG Renderer | 150 | `renderers/svg_renderer.py` |
| Audio Renderer (TTS) | 180 | `renderers/audio_renderer.py` |
| Composition (FFmpeg) | 150 | `renderers/composition.py` |
| Execution Orchestrator | 200 | `executor.py` |
| Timeout & Fallback | 120 | `fallback_chain.py` |
| Unit Tests | 350 | `tests/test_renderers_phase2.py` |
| E2E Test | 200 | `tests/e2e/test_full_pipeline_phase2.py` |

### Commits (4)

```
feat(video-producer): extend renderers for LLM specs
feat(video-producer): add execution orchestrator + fallback chain
test(video-producer): E2E test full pipeline (spec → MP4)
```

### Gate Criteria

- [ ] Spec → 1800 PNG frames (60s @ 30fps)
- [ ] SVG diagrams render to PNG
- [ ] Audio: text → WAV (duration check)
- [ ] FFmpeg: merge video + audio → MP4
- [ ] Fallback chain: Blender timeout → Slide fallback
- [ ] E2E: spec_json → MP4 → SUCCESS

---

## Phase 3: API & Polish (Weeks 5–6)

### Deliverables (1,030 LOC)

| Item | LOC | Files |
|---|---|---|
| HTTP Endpoint | 200 | `routes/llm_synthesis.py` |
| Quality Scoring | 150 | `learning/quality_feedback.py` |
| Extension Points | 80 | `extension_points.py` |
| Console UI (optional) | 150 | `console_routes.py` |
| Documentation | 200 | `docs/` |
| Integration Tests | 250 | `tests/test_api_phase3.py` |

### Commits (4)

```
feat(video-producer): add HTTP endpoint for LLM synthesis
feat(video-producer): add quality scoring + feedback
feat(video-producer): add marketplace extension points
docs(video-producer): LLM synthesis guide + examples
```

### Gate Criteria

- [ ] `POST /v1/video/llm-generate` → MP4
- [ ] Quality scoring stored in plugin audit
- [ ] Extension points callable
- [ ] 50+ integration tests green
- [ ] Load test: 10 concurrent requests OK

---

## Adversarial Review Findings (All Mitigated)

### 1. Audio-Video Desync **CRITICAL**

**Risk:** OpenAI TTS duration unpredictable.  
**Mitigation:** TTS duration predictor + ±2s tolerance in validator.  
**Implementation:** `estimate_tts_duration_ms()` in spec_schema.py

### 2. LLM Hallucination **CRITICAL**

**Risk:** Invalid Blender scenes, nonexistent files.  
**Mitigation:** Semantic validation + enumerated choices in LLM prompt.  
**Implementation:** `_validate_blender_specs()` in spec_validator.py

### 3. Timeout Cascade **MAJOR**

**Risk:** Race condition in parallel tool execution.  
**Mitigation:** Explicit sync barrier with `asyncio.gather()`.  
**Implementation:** `executor.py` (Phase 2)

### 4. Cache Invalidation **MAJOR**

**Risk:** Stale specs served without indication.  
**Mitigation:** TTL + version metadata in cache.  
**Implementation:** `spec_cache.py` (Phase 1)

### 5. Audit Completeness **MAJOR**

**Risk:** Missing audit events for fallback decisions.  
**Mitigation:** 12 audit event types (all decision points).  
**Implementation:** `audit.py` (Phase 2)

### 6. Plugin Isolation **MINOR**

**Risk:** Plugin override fails → breaks pipeline.  
**Mitigation:** Try-catch + fallback to default.  
**Implementation:** `executor.py` (Phase 2)

---

## Success Metrics

| Metric | Target |
|---|---|
| Phase 1 E2E Test | Brief → Spec → Validation → PASS |
| Phase 2 E2E Test | Spec → 1800 frames + Audio + MP4 → SUCCESS |
| Phase 3 E2E Test | POST request → MP4 download → SUCCESS |
| Code Coverage | >85% |
| Timeout SLA | All tasks <600s per 60-sec video |
| Quality Score | >0.85 (semantic validation + user feedback) |

---

## Go/No-Go Gates

**Phase 1 → Phase 2:** All validation tests pass + E2E spec gen OK  
**Phase 2 → Phase 3:** All renderers working + fallback chain tested  
**Phase 3 → Production:** Load test passed + docs complete  

