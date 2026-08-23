# Brain v0.2 Operator Training — Module 5: Hands-On Lab
## 60-Minute Guided Practice Scenarios

**Version:** 1.0 (2026-08-23)  
**Target Audience:** Operators completing their training, practicing real incident scenarios  
**Prerequisite:** Modules 1–4 (all prior modules)  
**Outcome:** Successfully diagnose and resolve 3 production-like scenarios independently

---

## Learning Objectives

By the end of this lab, you will:
1. Set up a staging environment
2. Simulate Scenario 1: Subsystem crash (15 min)
3. Simulate Scenario 2: Memory leak (20 min)
4. Simulate Scenario 3: Event queue overflow (15 min)
5. Verify incident resolution with metrics and logs

**Success Criteria:** Complete all 3 scenarios with ≥80% accuracy (correct diagnosis + recovery).

---

## Pre-Lab Setup (5 minutes)

### Environment Preparation

```bash
# 1. Clone staging environment
git clone https://github.com/corvinOS/corvinOS.git ~/staging-corvin
cd ~/staging-corvin

# 2. Install dependencies
poetry install

# 3. Start service in foreground (for easier debugging)
poetry run corvin-service --foreground &
# Keep this terminal open, open another for testing

# 4. Verify service health
curl http://localhost:8765/health
# Output: {"status": "ok", "subsystems": 13}

# 5. Monitor dashboard (third terminal)
watch -n 2 'corvin metrics query "corvin_subsystem_health_status"'
```

### Create Log Tail Terminal

```bash
# Keep this running to see events as they happen
tail -f ~/.corvin/tenants/_default/audit.jsonl | jq '.'
```

---

## Scenario 1: Subsystem Crash (15 minutes)

### Part A: Simulate the Crash (5 minutes)

```bash
# In a test terminal:
echo "Simulating cost_controller crash..."

# Inject a division-by-zero error
corvin test inject-error cost_controller --error-type division_by_zero

# This will cause cost_controller to crash when handling requests
```

### Part B: Observe the Alert (2 minutes)

```bash
# Watch error rate increase
watch -n 1 'corvin metrics query "rate(corvin_errors_total{subsystem=\"cost_controller\"}[5m])"'

# Expected: error rate jumps from 0% to >20%
# Alert "HighErrorRate" should fire within 5 minutes
```

### Part C: Diagnose (5 minutes)

**Your Task:** Answer these questions using only tools/metrics, not by looking at the code.

**Q1: Which subsystem is failing?**
```bash
# Check the metrics
corvin metrics query 'corvin_errors_total' --labels | grep subsystem
# Answer: cost_controller
```

**Q2: What's the error rate?**
```bash
corvin metrics query 'rate(corvin_errors_total{subsystem="cost_controller"}[5m])'
# Answer: Should be >20%
```

**Q3: Is it a crash or a logic error?**
```bash
# Check subsystem health
corvin status cost_controller
# If UNHEALTHY or OFFLINE: crash
# If HEALTHY: logic error in code
# Answer: [your diagnosis]
```

**Q4: When did it start?**
```bash
# Check error log
tail -100 ~/.corvin/tenants/_default/audit.jsonl | grep cost_controller | tail -5
# Look for first error timestamp
# Answer: [timestamp]
```

### Part D: Recover (3 minutes)

**Your Task:** Fix it using only the recovery procedures from Module 3/4.

```bash
# Execute recovery
corvin restart-subsystem cost_controller

# Verify recovery
watch -n 2 'corvin status cost_controller && echo "---" && corvin metrics query "rate(corvin_errors_total{subsystem=\"cost_controller\"}[5m])"'

# Expected: subsystem returns to HEALTHY, error rate drops to 0%
```

### Part E: Validate Resolution

```bash
# 1. Check subsystem is healthy
corvin status cost_controller
# Output: HEALTHY ✓

# 2. Check error rate recovered
corvin metrics query 'rate(corvin_errors_total{subsystem="cost_controller"}[5m])'
# Output: <0.1% ✓

# 3. Run smoke test
corvin test smoke
# Output: All tests passed ✓

# 4. Verify audit chain
corvin audit verify
# Output: Chain integrity: VALID ✓
```

### Scenario 1 Checklist

- [ ] Identified subsystem: cost_controller
- [ ] Identified root cause: division by zero / crash
- [ ] Executed restart procedure
- [ ] Verified metrics returned to normal
- [ ] Smoke test passed
- [ ] Audit chain still valid

**Scenario 1 Complete!** Move to Scenario 2.

---

## Scenario 2: Memory Leak (20 minutes)

### Part A: Simulate the Leak (3 minutes)

```bash
# Inject memory leak into learning_engine
echo "Simulating memory leak..."
corvin test inject-error learning_engine --error-type memory_leak --leak-rate 10MB/min
```

### Part B: Observe Symptoms (5 minutes)

```bash
# Watch memory grow over time
echo "Monitoring memory for 10 minutes..."
for i in {1..10}; do
  echo "T+$((i*60))s: $(corvin metrics query 'corvin_process_memory_bytes{subsystem=\"learning_engine\"}')"
  sleep 60
done

# Expected: memory grows linearly
# Time 0: 50 MB
# Time 1: 60 MB
# Time 2: 70 MB
# ...
# Time 10: 150 MB
```

### Part C: Diagnose (7 minutes)

**Your Task:** Answer these questions.

**Q1: Is this a real memory leak or temporary allocation?**
```bash
# Check if memory stays high or drops after GC
for i in {1..5}; do
  MEM=$(corvin metrics query 'corvin_process_memory_bytes{subsystem="learning_engine"}')
  echo "Sample $i: $MEM"
  sleep 10
done

# If consistently growing: real leak
# If sawtooth pattern: temporary, GC works
# Answer: [your diagnosis]
```

**Q2: Where is the memory going?**
```bash
# Check learning engine database size
du -h ~/.corvin/tenants/_default/learning_engine.db
# If >500MB: event store has too many events

sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "SELECT COUNT(*) FROM events;"
# If >100k: event store unbounded

# Answer: [your diagnosis]
```

**Q3: Which subsystem feature added this?**
```bash
# Check git log
git log --oneline core/orchestration/learning_engine.py | head -3
# Answer: [recent commit]
```

### Part D: Recovery (3 minutes)

**Your Task:** Stop the leak without losing data.

```bash
# Option A: Restart learning_engine
corvin restart-subsystem learning_engine

# Option B: If leak persists, check event store
sqlite3 ~/.corvin/tenants/_default/learning_engine.db \
  "DELETE FROM events WHERE age_days > 30;"

# Verify recovery
watch -n 5 'corvin metrics query "corvin_process_memory_bytes{subsystem=\"learning_engine\"}"'
# Should stabilize after 30 seconds

# Expected: memory drops to 50-100 MB and stays flat
```

### Part E: Validate Resolution

```bash
# 1. Memory is stable
corvin metrics query 'corvin_process_memory_bytes{subsystem="learning_engine"}'
# Should be <100 MB and not growing

# 2. Learning engine still works
corvin test learning_engine
# Output: All tests passed ✓

# 3. No new errors
corvin metrics query 'rate(corvin_errors_total{subsystem="learning_engine"}[5m])'
# Output: <0.5% ✓
```

### Scenario 2 Checklist

- [ ] Identified memory leak in learning_engine
- [ ] Confirmed it was growing, not GC sawtooth
- [ ] Checked event store size (root cause)
- [ ] Executed restart or database cleanup
- [ ] Verified memory stabilized
- [ ] Learning engine tests still pass

**Scenario 2 Complete!** Move to Scenario 3.

---

## Scenario 3: Event Queue Overflow (15 minutes)

### Part A: Simulate High Load (3 minutes)

```bash
# Inject high event publishing rate
echo "Simulating event storm..."
corvin test inject-load --event-rate 50events/sec --duration 10m

# This makes subsystems publish events faster than they can process
```

### Part B: Observe Queue Saturation (5 minutes)

```bash
# Watch context bus queue fill up
watch -n 1 'corvin metrics query "corvin_context_bus_queue_depth"'

# Expected progression:
# T+0s: 5/100 (normal)
# T+30s: 45/100 (building up)
# T+60s: 100/100 (FULL!)
# T+90s: events dropped

# When you see "100/100", you've detected the problem
```

### Part C: Diagnose (4 minutes)

**Your Task:** Identify the bottleneck.

**Q1: Which subsystem is slow?**
```bash
# Compare publishing vs processing rate
echo "Publishing rate:"
corvin metrics query 'rate(corvin_events_published_total[1m])'
echo "Processing rate:"
corvin metrics query 'rate(corvin_events_processed_total[1m])'

# If publish > process: bottleneck exists
# Look at which handler is slowest:
corvin metrics query 'corvin_event_handler_duration_ms' --labels | sort -k2 -nr | head -5

# Answer: [slowest subsystem]
```

**Q2: Why is it slow?**
```bash
# Check if it's blocked on I/O
corvin debug profile learning_engine --duration=30s
# Look for: disk I/O, database locks, network timeouts

# Answer: [root cause]
```

### Part D: Recovery (2 minutes)

**Your Task:** Reduce queue depth back to normal.

```bash
# Option A: Disable slow subscriber temporarily
corvin config set features.learning_engine_subscriber_enabled=false
corvin config reload

# Option B: Or restart context bus
corvin restart-subsystem context_bus

# Watch queue depth drop
watch -n 1 'corvin metrics query "corvin_context_bus_queue_depth"'
# Expected: 100/100 → 50/100 → 0/100 within 30 seconds
```

### Part E: Validate Resolution

```bash
# 1. Queue is not full
corvin metrics query 'corvin_context_bus_queue_depth'
# Output: <50/100 ✓

# 2. Processing rate > publishing rate
corvin metrics query 'rate(corvin_events_published_total[1m])'  # should be low
corvin metrics query 'rate(corvin_events_processed_total[1m])'  # should match

# 3. No new errors
corvin metrics query 'rate(corvin_errors_total[5m])'
# Output: <1% ✓

# 4. Smoke test
corvin test smoke
# Output: All passed ✓
```

### Scenario 3 Checklist

- [ ] Identified event queue overflow
- [ ] Found bottleneck subsystem (learning_engine)
- [ ] Understood root cause (I/O bound or slow handler)
- [ ] Executed mitigation (disable subscriber or restart bus)
- [ ] Verified queue depth returned to normal
- [ ] Error rate dropped
- [ ] All subsystems healthy

**Scenario 3 Complete!** All lab scenarios done.

---

## Lab Summary & Grading

### Grading Rubric (Maximum 100 points)

#### Scenario 1: Subsystem Crash (30 points)
- [ ] Correctly identified affected subsystem (10 pts)
  - Score: 10 if cost_controller, 5 if other subsystem, 0 if incorrect
- [ ] Diagnosed root cause correctly (10 pts)
  - Score: 10 if crash, 5 if logic error, 0 if incorrect
- [ ] Executed recovery procedure correctly (10 pts)
  - Score: 10 if restart, 5 if partial, 0 if no recovery

#### Scenario 2: Memory Leak (35 points)
- [ ] Identified memory leak (10 pts)
  - Score: 10 if linear growth confirmed, 5 if suspected, 0 if missed
- [ ] Found root cause in event store (10 pts)
  - Score: 10 if event count checked, 5 if inferred, 0 if missed
- [ ] Executed recovery (10 pts)
  - Score: 10 if restart + cleanup, 5 if partial, 0 if none
- [ ] Verified stability (5 pts)
  - Score: 5 if memory confirmed stable, 0 if not verified

#### Scenario 3: Event Queue Overflow (35 points)
- [ ] Detected queue saturation (10 pts)
  - Score: 10 if queue at 100/100, 5 if partial, 0 if missed
- [ ] Identified slow subsystem (10 pts)
  - Score: 10 if learning_engine found, 5 if other, 0 if incorrect
- [ ] Executed mitigation (10 pts)
  - Score: 10 if disabled subscriber or restarted bus, 5 if partial, 0 if none
- [ ] Verified recovery (5 pts)
  - Score: 5 if queue depth < 50/100, 0 if not verified

### Passing Score: ≥ 80 points

**Your Score: _______ / 100**

**Result:**
- **80–100:** ✓ PASS — Ready for on-call rotation
- **60–79:** ⚠️ CONDITIONAL PASS — Review weak areas, retake scenario
- **<60:** ✗ FAIL — Schedule retraining, try again next week

---

## Lab Cleanup

```bash
# 1. Stop the service
systemctl stop corvin-service

# 2. Clear test data
rm -rf ~/.corvin/tenants/_default/*
# (Or restore from backup if needed)

# 3. Restart clean
systemctl start corvin-service

# 4. Verify clean state
corvin health check
```

---

## Common Lab Mistakes to Avoid

| Mistake | Impact | Fix |
|---------|--------|-----|
| Restarting entire service instead of subsystem | Loses active tasks | Use `corvin restart-subsystem <name>` |
| Ignoring audit chain verification | May hide corruption | Always run `corvin audit verify` |
| Assuming diagnosis without checking metrics | Wrong recovery path | Always query Prometheus first |
| Not monitoring recovery progress | Miss re-occurrence | Use `watch` commands to tail metrics |
| Clearing audit trail | Compliance violation | Never delete audit.jsonl, only events.db |

---

**Next Module:** [Competency Validation Module](MODULE-6-COMPETENCY-VALIDATION.md) (30 min)  
**Time Spent:** 60 minutes  
**Status:** Lab complete, ready for final assessment ✅
