# HANDOFF: ADR-0222 Measurement Week Glue

**Date:** 2026-07-25  
**Session 1:** TDE-Visibility k=8 + ADR-0222 Analysis  
**Session 2:** ADR-0222 k=1–k=3  
**Status:** Ready for Measurement Week Execution (k=1 Skeleton + k=2 Stub + k=3 Tests COMPLETE)

---

## What's Done

### ✅ TDE Visibility (ADR-0214)
- k=1–7: Voice-summary fixes + metrics visualization (commits cd1fce7..5956239)
- k=8: Backend persistence (commit a6293f0)
  - `_append_turn()` extended with `tde_progress` kwarg
  - TDE execution constructs TdeProgress dict (run_id, steps, delegated, local, L34-gate)
  - persists to turns.jsonl automatically
- Metrics card + TDE Graph tab now survive page reload ✅

### ✅ Measurement Foundation (ADR-0222 F1–F4)
- F1: Real counterfactual (strong reference models via `CORVIN_TDE_REFERENCE_MODEL`)
- F2: Record executed model per step (multi-arm log live)
- F3: Standardized on `step.action` as canonical key
- F4: Cross-model exploration primitive (`CORVIN_TDE_EXPLORE_MODELS`)

### ✅ Whole-Task Baseline + Decision Gate (F5)
- `whole_task_tier_baseline()` implemented (tde/tde_engine.py)
- `decision_gate.py` pure logic (evaluate_band, evaluate_tde_verdict)
- `GateAssumptions` (quality_floor_loss, min_net_savings, min_margin_over_tier, min_samples_per_band)
- Honesty invariant enforced: verdicts only on measured data (data_source="measured")

---

## What's Missing: Measurement Week Glue

The **sampled real-traffic recorder** that runs {direct, F5-tier, TDE} in parallel and collects BandEvidence.

### Architecture (Phase 2 Data Collection)

```python
# When a TDE-eligible turn arrives:
if measurement_week_enabled:
    # 1. Run all 3 variants in parallel (or sequential with isolation)
    direct_result = await run_direct_turn(task, user_model)
    tier_result = await run_tier_baseline(task)  # F5 already built
    tde_result = await run_tde_turn(task)  # already exists
    
    # 2. Collect metrics from each
    direct_tokens = direct_result.usage.total_tokens
    direct_quality = judge(direct_result, reference=user_model_output)
    
    tier_tokens = tier_result.usage.total_tokens
    tier_loss = judge(tier_result, reference=direct_output)
    
    tde_tokens = tde_result.usage.total_tokens
    tde_loss = judge(tde_result, reference=direct_output)
    
    # 3. Write to measurement log
    measurement_log.append({
        "task_band": classify_band(task),  # "trivial" | "moderate" | "complex"
        "direct_tokens": direct_tokens,
        "tier_tokens": tier_tokens,
        "tier_loss": tier_loss,
        "tde_tokens": tde_tokens,
        "tde_loss": tde_loss,
        "data_source": "measured",  # <- this upgrades from "assumptions"
    })
    
    # 4. Gate consumes aggregated evidence
    if len(measurement_log) >= min_samples_per_band * num_bands:
        evidence = aggregate_measured_evidence(measurement_log)
        verdict = evaluate_tde_verdict(evidence)
        # verdicts now have data_source="measured" -> amplifier_survives can be TRUE
```

---

## Files to Build

### 1. `operator/orchestration/tde/tde_measurement.py` (NEW)
**Data structures:**
```python
@dataclass
class MeasurementSample:
    """One sampled trial of {direct, tier, TDE} on a task."""
    task_id: str
    task_band: str  # from task classification
    timestamp: float
    
    # Results from each variant
    direct_tokens: int
    direct_output: str
    
    tier_tokens: int
    tier_output: str
    tier_loss: float  # vs direct
    
    tde_tokens: int
    tde_output: str
    tde_loss: float  # vs direct
    
    quality_judge_model: str  # the judge that scored losses

@dataclass
class AggregatedBandEvidence:
    """Rolled-up stats for a band (avg tokens, losses, sample count)."""
    band: str
    samples: list[MeasurementSample]
    
    @property
    def direct_tokens(self) -> float:
        return sum(s.direct_tokens for s in self.samples) / len(self.samples)
    
    @property
    def tde_tokens(self) -> float:
        return sum(s.tde_tokens for s in self.samples) / len(self.samples)
    
    # ... etc (all aggregate to BandEvidence after transformation)

def aggregate_measured_evidence(samples: list[MeasurementSample]) -> list[BandEvidence]:
    """Rolls MeasurementSamples into the BandEvidence format decision_gate expects."""
    by_band = defaultdict(list)
    for s in samples:
        by_band[s.task_band].append(s)
    
    return [
        BandEvidence(
            band=band,
            direct_tokens=agg.direct_tokens,
            tde_tokens=agg.tde_tokens,
            tde_loss=agg.tde_loss,
            tier_tokens=agg.tier_tokens,
            tier_loss=agg.tier_loss,
            n_measured=len(agg.samples),
            data_source="measured",  # <- THE KEY: upgrades from assumptions
        )
        for band, samples in by_band.items()
        for agg in [AggregatedBandEvidence(band, samples)]
    ]
```

### 2. `operator/orchestration/tde/tde_measurement.py` (continued)
**Sampler hook interface:**
```python
class MeasurementRecorder:
    """Singleton that records {direct, tier, TDE} samples during measurement week."""
    
    def __init__(self, measurement_log_path: str):
        self.log_path = measurement_log_path
        self.enabled = os.getenv("TDE_MEASUREMENT_ENABLED") == "1"
        self.samples: list[MeasurementSample] = []
    
    async def record_sample(self, sample: MeasurementSample) -> None:
        """Append to in-memory buffer and persist to log."""
        self.samples.append(sample)
        # Write to turns.jsonl-like format or separate measurement.jsonl
        with open(self.log_path, "a") as f:
            f.write(json.dumps(asdict(sample), default=str) + "\n")
    
    def get_aggregated_evidence(self) -> list[BandEvidence]:
        """Return current measured evidence for the gate."""
        return aggregate_measured_evidence(self.samples)
```

### 3. `chat_runtime.py` integration point
**In `_stream_tde_turn()`, wrap execution:**
```python
# Pseudocode; actual implementation needs async wiring
if measurement_recorder.enabled and should_measure_this_band(task):
    # Run all 3 in parallel or with fork/wait
    direct_result = await run_direct_turn_for_comparison(...)
    tier_result = whole_task_tier_baseline(...)  # F5 already built
    tde_result = ... # existing TDE execution
    
    sample = MeasurementSample(
        task_id=run_id,
        task_band=analysis.classification.task_type,
        direct_tokens=direct_result.usage.total_tokens,
        direct_output=direct_result.text,
        tier_tokens=tier_result.usage.total_tokens,
        tier_output=tier_result.text,
        tier_loss=judge(tier_result, direct_result),  # F1 judge
        tde_tokens=result.get("usage", {}).get("total_tokens", 0),
        tde_output=final,
        tde_loss=judge(final, direct_result),
        quality_judge_model=os.getenv("CORVIN_TDE_JUDGE_MODEL", "haiku"),
    )
    await measurement_recorder.record_sample(sample)
```

### 4. `decision_gate.py` (minimal change)
The decision gate already accepts `BandEvidence` with `data_source="measured"`.  
Just ensure `evaluate_tde_verdict()` is called with measured evidence once available:
```python
# In chat_runtime.py or routing decision:
if measurement_recorder.enabled:
    measured_evidence = measurement_recorder.get_aggregated_evidence()
    if len(measured_evidence) >= 1:  # threshold to flip from assumptions
        verdict = evaluate_tde_verdict(measured_evidence)
        if verdict["amplifier_survives"]:
            # TDE is empirically winning; safe to route
            ...
        else:
            # Premise falsified; revert to direct / tier-only
            ...
```

---

## Key Design Points

1. **Feature-flagged** — controlled by `TDE_MEASUREMENT_ENABLED=1` env var
   - Measurement week runs in isolation (not interfering with production routing)
   - Data flows to measurement.jsonl, not the main audit chain

2. **Honesty invariant**
   - Every `BandEvidence` from measured data has `data_source="measured"`
   - Gate only sets `amplifier_survives=True` on measured wins
   - Assumption-sourced wins remain `predicted_winning_bands` (informational)

3. **Parallel or sequential?**
   - Parallel: {direct, tier, TDE} run at the same time → captures real contention
   - Sequential (easier): run each in isolation → cleaner comparison, no contention noise
   - **Recommendation:** start sequential for clean baseline, add contention study later

4. **Task band classification**
   - Use `analysis.classification.task_type` (already measured in InitialAnalysis)
   - Bands: "trivial" | "moderate" | "complex" (or richer classification)
   - Gate needs min 30 samples per band before decisive verdict

5. **Quality judge**
   - Use F1-upgraded judge (`CORVIN_TDE_JUDGE_MODEL` env, strong model like Opus)
   - Judge compares tier/TDE outputs against direct output (reference)
   - Loss = fractional semantic difference (0.0 = identical, 1.0 = unrelated)

---

## Test Surface (for next session's k=1–k=3)

### k=1: Skeleton + Unit Tests
- `test_tde_measurement.py` — MeasurementSample, AggregatedBandEvidence, aggregate_measured_evidence
- Mock samples → verify BandEvidence construction
- Verify data_source="measured" propagates correctly

### k=2: Decision Gate Tests (already mostly there)
- `test_tde_decision_gate.py` — verify evaluate_tde_verdict accepts measured evidence
- Run synthetic_evidence_from_assumptions (predictions)
- Run measured evidence (verdicts)
- Assert amplifier_survives flips correctly

### k=3: Integration + Feature Flag
- Mock chat_runtime with measurement_recorder
- Verify sample recording works
- Verify feature flag gating

---

## Success Criteria

Once complete (and measurement week runs):
- ✅ Real {direct, tier, TDE} samples collected
- ✅ BandEvidence aggregated with `data_source="measured"`
- ✅ decision_gate.evaluate_tde_verdict accepts measured data
- ✅ amplifier_survives flips to TRUE iff TDE empirically wins on measured
- ✅ ADR-0222 Phase 2 fulfilled: honest baseline + decision gate

---

## Notes

- **No changes to existing TDE execution logic** — sampler is additive, feature-flagged
- **Reuse F5 baseline already built** — don't recompute
- **Judge is F1-upgraded** — set CORVIN_TDE_JUDGE_MODEL env during measurement week
- **Minimal chatroom impact** — sampler runs out-of-band or with sampling (e.g., 5% of turns)
- **Persistence:** turns.jsonl stays clean; measurement.jsonl is separate log

---

**Ready for next session.** Grab the skeleton in k=1, unit tests in k=2, integration in k=3.
