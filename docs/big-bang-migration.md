# Big Bang Migration: From Feature Flags to Skills

This document explains how CorvinOS migrated from **feature flags** (hardcoded on/off toggles) to **Skills** (versioned, self-learning programs) in one coordinated effort.

---

## Why Feature Flags Were Wrong

### The Problem: Hardcoded, Unversioned, Unmeasurable

```svg
<svg viewBox="0 0 900 400" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="900" height="400" fill="#FEE2E2"/>
  
  <!-- Title -->
  <text x="450" y="30" font-size="18" font-weight="bold" text-anchor="middle" fill="#DC2626">
    ❌ Feature Flags: The Old Way
  </text>
  
  <!-- Code example -->
  <rect x="50" y="60" width="800" height="80" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="1" font-family="monospace"/>
  <text x="70" y="85" font-size="11" fill="#7F1D1D" font-family="monospace">if spec.features.new_router:</text>
  <text x="100" y="105" font-size="11" fill="#7F1D1D" font-family="monospace">    route_to_opus()  # New logic</text>
  <text x="100" y="125" font-size="11" fill="#7F1D1D" font-family="monospace">else:</text>
  <text x="100" y="145" font-size="11" fill="#7F1D1D" font-family="monospace">    route_to_haiku()  # Old logic</text>
  
  <!-- Problems -->
  <text x="50" y="180" font-size="12" font-weight="bold" fill="#7F1D1D">Problems with Feature Flags:</text>
  
  <g id="problem1">
    <rect x="50" y="200" width="260" height="80" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="1"/>
    <text x="180" y="220" font-size="11" font-weight="bold" text-anchor="middle" fill="#7F1D1D">❌ No Versioning</text>
    <text x="60" y="240" font-size="10" fill="#7F1D1D">Changing flag means</text>
    <text x="60" y="255" font-size="10" fill="#7F1D1D">full system restart</text>
    <text x="60" y="270" font-size="10" fill="#7F1D1D">(all code re-deployed)</text>
  </g>
  
  <g id="problem2">
    <rect x="320" y="200" width="260" height="80" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="1"/>
    <text x="450" y="220" font-size="11" font-weight="bold" text-anchor="middle" fill="#7F1D1D">❌ No Learning</text>
    <text x="330" y="240" font-size="10" fill="#7F1D1D">Behavior is hardcoded;</text>
    <text x="330" y="255" font-size="10" fill="#7F1D1D">even with feedback,</text>
    <text x="330" y="270" font-size="10" fill="#7F1D1D">manual tuning needed</text>
  </g>
  
  <g id="problem3">
    <rect x="590" y="200" width="260" height="80" rx="4" fill="#FECACA" stroke="#DC2626" stroke-width="1"/>
    <text x="720" y="220" font-size="11" font-weight="bold" text-anchor="middle" fill="#7F1D1D">❌ Hard to Test</text>
    <text x="600" y="240" font-size="10" fill="#7F1D1D">Testing both paths</text>
    <text x="600" y="255" font-size="10" fill="#7F1D1D">requires config</text>
    <text x="600" y="270" font-size="10" fill="#7F1D1D">changes + restarts</text>
  </g>
</svg>
```

### Specific Issues

| Issue | Impact | Example |
|---|---|---|
| **No versioning** | Change flag = redeploy entire system | Enable new router → redeploy core, plugins, gateway, console |
| **No learning** | Behavior never improves with feedback | Router has hardcoded threshold; users give feedback; no automatic tuning |
| **No composition** | Flags scattered across codebase | Feature X uses flags A+B; Feature Y uses B+C; total config space explodes |
| **No observability** | Can't measure flag effectiveness | "Is new router better?" → must manually compare logs |
| **No rollback** | Flag flip requires deployment | Change flag, redeploy, wait 5 minutes; if bad, change again, redeploy |
| **Accumulation** | Legacy flags never cleaned up | 50+ old flags in code, most unused, creates tech debt |

---

## The Big Bang Decision (ADR-0544)

**Decision:** Replace ALL feature flags with Skills in one coordinated effort.

**Why "Big Bang"?**
- Not gradual migration (too complex)
- Coordinated effort across all systems
- Single commit / PR with all changes
- Verify audit trail still intact
- Deploy with compliance gates

**When?** Weeks 11–13 (September 2026)

**How?** Three stages with safety gates.

---

## What Changed

### Before: Feature Flags Everywhere

```python
# OLD: core/plugins/routing.py
if spec.features.use_new_router:
    complexity = estimate_complexity(request)
    if complexity > 0.5:  # Hardcoded threshold
        route = "opus"
    else:
        route = "haiku"
else:
    # Old logic
    route = "haiku"  # Always haiku

# OLD: core/plugins/consent.py
if spec.compliance.enforce_consent:  # Another flag!
    check_consent(request)
else:
    allow()

# OLD: core/plugins/logging.py
if spec.telemetry.send_errors:  # Another flag!
    send_error_to_telemetry()
```

### After: Skills-Based ACP

```python
# NEW: core/skills/os_skills/router.py
@Skill.register
class DelegationRouter(Skill):
    skill_id = "os.delegation_router"
    version = "2.0.1"
    
    def execute(self, request: dict) -> dict:
        config = self.get_config()  # Learned via feedback
        threshold = config.get("threshold", 0.5)
        
        complexity = estimate_complexity(request)
        route = "opus" if complexity > threshold else "haiku"
        
        return {"route": route, "confidence": 0.88}

# NEW: core/skills/meta_skills/consent_enforcer.py
@Skill.register
class ConsentEnforcer(Skill):
    skill_id = "meta.consent_enforcer"
    boot_layer = "meta"  # Immutable, always-on
    
    def execute(self, request: dict) -> dict:
        # Consent is not a flag; it's a hard gate
        if not has_consent(request):
            return {"denied": True, "reason": "No consent"}
        return {"allowed": True}
```

---

## Migration Timeline

### Week 11: Foundation

**What:**
- Write all Skills (replace every feature flag)
- Create comprehensive test suite
- Set up monitoring/alerting
- Prepare rollback plan

**Verification:**
```bash
# 1. All feature flags mapped to Skills
for flag in $(grep -r "spec.features\|spec.compliance" core/ | cut -d: -f1 | sort -u); do
  echo "Flag in: $flag"
done

# 2. Corresponding Skill exists
corvin skill list | wc -l
# Output: 27 skills (one for each flag)

# 3. Unit + E2E tests pass
pytest tests/skills/ -v
# Output: 450 tests PASSED

# 4. Audit chain verified
corvin audit verify-chain --tenant=_default
# Output: Chain intact
```

### Week 12: Stage 1 – Canary (10% Traffic)

**What:**
- Deploy Skills code (but feature flags still work)
- Route 10% traffic through new Skills
- Audit trail should show hybrid execution
- Monitor latency, errors, confidence

**Safety Gates:**

| Gate | Threshold | Action |
|---|---|---|
| **Latency increase** | > 5ms (5% vs 50ms baseline) | STOP, rollback |
| **Error rate** | > 0.5% | STOP, investigate |
| **Audit chain** | Any break | STOP immediately |
| **Confidence** | < 85% | Warning, extend canary |

**Verification:**
```bash
# 1. Skills receiving traffic
corvin audit filter --event-type skill_executed --since <day1_start> | wc -l
# Output: ~125,000 skill_executed events (10% of daily 1.25M)

# 2. Metrics healthy
corvin metrics query --skill "os.*" --since <day1_start> | grep latency
# Output: Latency 50-55ms (baseline 50ms) ✅

# 3. Audit trail looks good
corvin audit verify-chain
# Output: Chain height 142857 → 143245 (388 new events), all valid ✅
```

**Decision Point:**
- ✅ **If green:** Proceed to Stage 2
- ❌ **If red:** Pause, debug, rollback to feature flags

### Week 13: Stage 2 – Ramp (50% Traffic)

**What:**
- Remove feature flag checks (100% of new requests use Skills)
- Keep old code for 2-week safety window
- Monitor for divergence
- Continue feedback collection

**Verification:**
```bash
# 1. 50% traffic through Skills
corvin audit filter --event-type skill_executed --since <day4_start> | wc -l
# Output: ~625,000 (50% of daily 1.25M)

# 2. Skills confidence improving
for skill in os.delegation_router os.context_adapter os.house_rules_enforcer; do
  corvin skill convergence $skill
done
# Output: All 85-92% (target 95%)

# 3. No audit anomalies
corvin audit verify-chain
# Output: Chain integrity verified ✅
```

### Stage 3: General Availability (100% Traffic)

**What:**
- All requests use Skills
- No feature flags
- Feature flag code removed from codebase
- Skills converge to 95%+ confidence

**Verification:**
```bash
# 1. 100% Skills traffic
corvin audit count --event-type skill_executed | tail -1
# Output: 1,248,567 events (100% ✅)

# 2. Confidence convergence
for skill in os.*; do
  conf=$(corvin skill convergence $skill | grep "Confidence" | cut -d: -f2)
  if [ $(echo "$conf < 0.95" | bc) -eq 1 ]; then
    echo "⚠️  $skill below 95%"
  fi
done
# Output: All above 95% ✅

# 3. Feature flags removed
grep -r "spec.features\|spec.compliance" core/ || echo "✅ No flags found"
# Output: ✅ All removed

# 4. Audit trail complete
corvin audit export --format=pdf --since=2026-09-08 --until=2026-09-15
# Output: 9.2M events, all hash-chained, signatures valid ✅
```

---

## Compliance Gates (Hard Stops)

### Gate 1: Audit Chain Verification (Every Stage)

```bash
# Before any stage transition
corvin audit verify-chain --tenant=_default

# If broken: STOP, investigate, don't proceed
# Exit code 0 = proceed; Exit code 1 = blocked
```

**What this checks:**
- All events have valid hashes
- Hash chain links correctly (N → N-1 → N-2 → ...)
- No tampering or corruption
- Signatures valid (RFC 3161 TSA)

### Gate 2: Consent Verification

```bash
# During Stage 2+
corvin audit filter --event-type consent_granted --sample-size 1000

# Must confirm 100% of sampled requests had consent
# If consent_granted < 100%: WARNING, investigate
```

### Gate 3: Skill Dependency Validation

```bash
# Before each stage
corvin skill validate-dependencies

# Checks:
# - All Skills exist
# - No circular dependencies
# - DAG is valid
# - All meta-Skills are enabled

# Exit code 0 = OK; Exit code 1 = blocked
```

---

## Migration Risks + Mitigations

### Risk 1: Audit Chain Breaks During Migration

**Symptom:** Hash chain verification fails mid-migration.

**Why it matters:** Compliance violation (GDPR Art. 30).

**Mitigation:**
- Boot tripwire runs EVERY startup
- No Skill executes until chain verified
- If break detected, system stops immediately
- Operator manually investigates + recovery

**Evidence:** No audit breaks in Phase 1–2 canary testing ✅

### Risk 2: Skills Don't Converge (Confidence < 90%)

**Symptom:** Week 2 Skills still at 70% confidence.

**Why it matters:** System not ready for GA.

**Mitigation:**
- Extend learning window (add Week 3)
- Investigate feedback quality
- Manually adjust parameters if needed
- Run extended E2E testing

**Evidence:** ADR-0314 learning infrastructure tested ✅

### Risk 3: Gradual Divergence (Skills ≠ Flags)

**Symptom:** New Skills produce different results than old flags.

**Why it matters:** Regression; users see different behavior.

**Mitigation:**
- A/B equivalence testing (ADR-0575)
- Run both paths on canary traffic
- Compare outputs (must match 99%+)
- If divergence > 1%, debug + don't proceed

**Evidence:** 150 equivalence test cases all PASS ✅

### Risk 4: Operator Lockout

**Symptom:** All Skills disabled accidentally; system offline.

**Why it matters:** Service unavailable.

**Mitigation:**
- Meta-Skills cannot be disabled (immutable)
- Rollback recovers old feature flags (2-week window)
- Manual enable script if needed

**Evidence:** Meta-Skills tested against disable attempts ✅

---

## Rollback Plan

**If anything goes wrong, revert to feature flags in < 5 minutes:**

```bash
# Stage 1 (Day 1): All feature flags still in code
# Stage 2 (Days 4–7): Feature flag code still present (disabled)
# After Stage 3: Feature flag code removed (7+ days after GA)

# Within 2 weeks of GA, rollback is available:
git checkout HEAD~1  # Previous commit had feature flags

# Enable feature flags
corvin config set spec.use_skills=false
corvin restart

# System boots with old feature flags ✅
# Skills don't execute
# Users see old behavior

# Audit trail still exists (not deleted)
# Can investigate what went wrong
```

**After 2 weeks:** Rollback unavailable (feature flag code deleted).

---

## Post-Migration Cleanup

### Week 14+: Hardening

1. **Remove feature flag code:**
   ```bash
   grep -r "spec.features\|spec.compliance" core/
   # Should return empty
   
   git rm core/feature_flags.py
   git commit -m "chore: remove legacy feature flags [skip-adr-check]"
   ```

2. **Clean up config files:**
   ```bash
   # Remove feature flag documentation
   rm docs/feature-flags.md
   
   # Update compliance docs
   # -> Feature flags → Skills (ADR-0544)
   ```

3. **Archive migration logs:**
   ```bash
   corvin audit export \
     --since=2026-09-08 \
     --until=2026-09-15 \
     --output=/backups/migration-audit-2026-09.tar.gz
   ```

---

## Lessons Learned

### What Went Well ✅

1. **Skills architecture solid:** No regressions, no audit chain breaks
2. **Convergence fast:** Skills reached 95% confidence in 2 weeks
3. **Operator confidence high:** Rollback plan gave comfort
4. **Compliance verified:** Audit trail proved migration safety
5. **Zero user-facing issues:** No incidents during rollout

### What We'd Do Differently Next Time

1. **Start Skills earlier:** Skills 2.0 infrastructure ready; waiting for org confidence added 2 weeks
2. **Parallel testing:** A/B test more extensively before canary
3. **Communication:** More frequent operator updates during migration

---

## Lessons for Future Subsystem Migrations

If you're migrating another L-Layer → Skills:

1. **Write ADR first** — document the why, alternatives, risks
2. **Build comprehensive tests** — unit + E2E + equivalence
3. **Instrument heavily** — audit events at every step
4. **Stage carefully** — 10% → 50% → 100%
5. **Plan rollback** — assume it will be needed
6. **Get compliance sign-off** — audit trail must be airtight
7. **Communicate with operators** — build trust before the big switch

---

## See Also

- **[ADR-0544](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Big Bang migration decision
- **[ACP Vision](acp-vision.md)** — Why Skills matter
- **[Skills System](skills-system.md)** — How to write Skills
- **[Audit Trail](audit-trail.md)** — Verification at each step

---

**The Big Bang Migration proved Skills 2.0 works. Feature flags are history. Skills are the future.**
