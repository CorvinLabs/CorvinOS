# DataHub Creator Phase 4 Troubleshooting Guide

## Common Issues & Solutions

### 1. Dashboard Returns 503 "Learning Subsystem Not Wired"

**Symptom:**
```
GET /api/v1/learning/skills
Response: 503 Service Unavailable
Body: {"detail": "learning subsystem not wired (core.learning unavailable)"}
```

**Root Cause:**
- Phases 1-3 not installed
- Import error in `core/learning/`

**Solution:**
```bash
# Check if core.learning exists
python3 -c "from core.learning import LearningIntegration; print('OK')"

# If ImportError: install Phases 1-3 first
cd /home/shumway/projects/CorvinOS
pip install -e . --extras=learning

# Restart console
systemctl --user restart corvin-webui.service
```

---

### 2. Audit Chain Verification Fails on Boot

**Symptom:**
```
ERROR: Audit chain broken at line 42: expected prev_hash=abc123, got xyz789
Application HALT (fail-closed)
```

**Root Cause:**
- Manual edit of `audit.jsonl`
- Filesystem corruption
- Process crash mid-write
- Concurrent writes to same file

**Solution:**

**Option A: Restore from Backup (Preferred)**
```bash
# If backup exists
cp ~/.corvin/tenants/_default/audit/datahub.jsonl.bak \
   ~/.corvin/tenants/_default/audit/datahub.jsonl

systemctl --user restart corvin-console.service
```

**Option B: Truncate and Rebuild**
```bash
# WARNING: This deletes audit history before break point
# Only use if backup unavailable

# Find the line number where chain breaks (from error message)
# Delete lines from that point forward
head -n 41 ~/.corvin/tenants/_default/audit/datahub.jsonl > /tmp/datahub_safe.jsonl

# Restore truncated version
mv /tmp/datahub_safe.jsonl ~/.corvin/tenants/_default/audit/datahub.jsonl

# Restart to verify
systemctl --user restart corvin-console.service
```

**Option C: Start Fresh (Last Resort)**
```bash
# This DELETES all audit history
rm ~/.corvin/tenants/_default/audit/datahub.jsonl

systemctl --user restart corvin-console.service

# System will create new chain on first event
```

**Prevention:**
- **Never edit `audit.jsonl` manually**
- Always use `AuditTrail.write_event()` API
- Enable daily backups:
  ```bash
  crontab -e
  # Add: 0 2 * * * cp ~/.corvin/tenants/_default/audit/datahub.jsonl{,.bak}
  ```

---

### 3. Dashboard Loads But Shows "No Data"

**Symptom:**
- Dashboard renders successfully
- KPI cards show 0 for all metrics
- Tables/charts empty

**Diagnosis:**
```bash
# Check if any events written
wc -l ~/.corvin/tenants/_default/audit/datahub.jsonl
# Output: 0 datahub.jsonl → no events yet

# Verify API endpoints return data
curl -s http://localhost:8765/api/v1/learning/skills | jq .
# Output: {"data": []} → no skills generated yet
```

**Solution:**

**If No Skills Generated:**
- This is normal if Phase 2 (Creator) hasn't been used yet
- Generate a test skill:
  ```bash
  corvin create-skill --name="test-skill" --type="simple"
  # Watch dashboard update in real-time
  ```

**If Skills Generated But Dashboard Still Empty:**
- Check network tab (F12 → Network):
  - Does `/api/v1/learning/skills` return 200?
  - Does response include `data` array?
- Hard-refresh browser (Ctrl+Shift+R)
- Check browser console for JavaScript errors

---

### 4. Prometheus Metrics Endpoint Returns 500

**Symptom:**
```bash
curl http://localhost:8765/api/v1/learning/metrics
500 Internal Server Error
```

**Root Cause:**
- Audit trail not accessible
- Permission issue on audit file

**Solution:**
```bash
# Check file permissions
ls -la ~/.corvin/tenants/_default/audit/datahub.jsonl
# Should show: -rw-rw-r-- (permissions 664 or 644)

# Fix permissions if needed
chmod 644 ~/.corvin/tenants/_default/audit/datahub.jsonl

# Verify readable
python3 -c "
from core.skills.os_skills.audit.trail import AuditTrail
from pathlib import Path
trail = AuditTrail('_default', Path.home() / '.corvin/tenants/_default/audit/datahub.jsonl')
print('Audit trail OK')
"
```

---

### 5. GDPR Export Includes PII (Redaction Not Working)

**Symptom:**
```python
export = reporter.export_for_compliance(redact=True)
# But email "user@example.com" still appears in export
```

**Root Cause:**
- PII pattern regex not matching your data format
- Old PII patterns need calibration

**Solution:**

**Option A: Check Regex Coverage**
```python
from core.skills.os_skills.audit.reporter import ComplianceReporter

text = "Contact: john.doe@example.com"
redacted = reporter.redact_pii(text)
print(redacted)
# Expected: "Contact: [REDACTED_email]"
# If not working: regex needs update

# Add pattern to PII_PATTERNS in reporter.py
# Example:
PII_PATTERNS['custom'] = r'your_regex_here'
```

**Option B: Verify Fields Being Redacted**
```python
# Check which payload fields contain PII
export = reporter.export_for_compliance(redact=False)
for line in export.split('\n'):
    event = json.loads(line)
    if 'email' in str(event['payload']).lower():
        print(f"Found email in: {event}")
```

**Option C: Conservative Approach**
```python
# If unsure about regex, manually review before export
export = reporter.export_for_compliance(redact=False)
# Review output manually
# Remove sensitive fields
# Redact manually if regex coverage insufficient
```

---

### 6. Bias Detection Flags False Positives

**Symptom:**
```python
alerts = reporter.detect_bias()
# Returns: ["skill_simple_test: 100% positive feedback (N=1)"]
# But skill is new, only 1 test execution
```

**Root Cause:**
- Low sample count (N<5) triggers bias alert
- Test data skews detection

**Solution:**

**Option A: Ignore Single-Sample Alerts**
```python
# Modify detect_bias() to skip N<5
for skill_id, signals in feedback_by_skill.items():
    if len(signals) < 5:  # Threshold
        continue  # Skip this skill
    # ... check for skew
```

**Option B: Separate Test vs. Production**
```python
# Tag test events differently
trail.write_event(
    "feedback_received",
    skill_id="test_skill",
    payload={
        "signal": "positive",
        "is_test": True  # Flag test events
    }
)

# Bias detection excludes test events
for event in events:
    if event.payload.get("is_test"):
        continue  # Skip test data
```

**Option C: Review Manually**
```python
# Get full breakdown, don't auto-flag
feedback_by_skill = {}
for event in events:
    if event.event_type == "feedback_received":
        skill = event.skill_id
        if skill not in feedback_by_skill:
            feedback_by_skill[skill] = []
        feedback_by_skill[skill].append(event.payload.get("signal"))

for skill, signals in sorted(feedback_by_skill.items(), key=lambda x: -len(x[1])):
    counts = Counter(signals)
    print(f"{skill}: {len(signals)} samples, {counts}")
    # Review and decide
```

---

### 7. Compliance Export File Huge (>1GB)

**Symptom:**
```bash
ls -lh compliance_report.jsonl
-rw-rw-r-- 1.2G compliance_report.jsonl
```

**Root Cause:**
- Audit trail has millions of events
- Export includes all events (limit not applied)

**Solution:**

**Option A: Filter by Date**
```python
# Only export last 7 days
since = datetime.now(timezone.utc) - timedelta(days=7)
export = reporter.export_for_compliance(since=since, redact=True)
```

**Option B: Sample Export**
```python
# Export every Nth event (e.g., every 10th)
all_events = trail.query_events(limit=999999)
sampled = [e for i, e in enumerate(all_events) if i % 10 == 0]
# ... serialize sampled events
```

**Option C: Streaming Export**
```python
# Write to file in chunks instead of building whole string
with open("compliance_report.jsonl", "w") as f:
    for event in trail.query_events(limit=999999):
        redacted = reporter.redact_event(event)
        f.write(json.dumps(asdict(redacted)) + '\n')
    # File is smaller as written incrementally
```

---

### 8. Learning Daemon Not Converging (Status Stuck at 30%)

**Symptom:**
- Dashboard shows `convergence_status: 0.3` (30%)
- After 500 feedback samples, still 0.3
- No progress for days

**Root Cause:**
- Insufficient feedback volume
- Feedback quality issues
- Poorly calibrated convergence thresholds

**Solution:**

**Option A: Check Feedback Quality**
```python
# Analyze feedback distribution
alerts = reporter.detect_bias()
print("Bias alerts:", alerts)
# If many skewed feedback: data quality issue

# Check convergence of individual sources
for source in ["memory:tier1", "memory:tier2", "rag"]:
    source_events = [e for e in events if e.payload.get("source_id") == source]
    print(f"{source}: {len(source_events)} events")
    if len(source_events) < 50:
        print(f"  → Insufficient data, increase usage")
```

**Option B: Increase Feedback Volume**
```bash
# Ensure Phase 3 daemon is running
ps aux | grep daemon
# If not running:
systemctl --user start corvin-daemon.service

# Increase skill usage (more skills = more feedback)
# Example: run batch skill generation test
for i in {1..20}; do
    corvin create-skill --name="test_$i" --type="random"
done
```

**Option C: Tune Convergence Thresholds**
```python
# If natural convergence rate too slow, adjust thresholds
# In WeightLearner (Phase 3):
# convergence_threshold = 0.85 → 0.7 (easier to converge)
# learning_rate = 0.01 → 0.02 (learn faster)

# Trade-off: faster convergence = less stable
# Be cautious with tuning
```

---

### 9. Dashboard Unresponsive (Hangs When Scrolling)

**Symptom:**
- Dashboard loads fine
- Scrolling causes 2-3 second freezes
- Charts lag when zooming

**Root Cause:**
- React re-renders too often (5s poll interval)
- Large dataset (1000+ events) in state
- Inefficient chart library performance

**Solution:**

**Option A: Reduce Polling Frequency**
```typescript
// In LearningDashboard.tsx, change from 5s to 30s
const interval = setInterval(fetchData, 30000); // Was 5000
```

**Option B: Limit Query Results**
```typescript
// In fetchData(), reduce limits
const skillsRes = await fetch('/api/v1/learning/skills?limit=20'); // Was 50
const weightsRes = await fetch('/api/v1/learning/weights?limit=50'); // Was 100
```

**Option C: Use Recharts Optimizations**
```typescript
// Add isAnimationActive={false} to charts for perf
<LineChart data={weights} isAnimationActive={false}>
  ...
</LineChart>
```

**Option D: Archive Old Data**
```python
# Clean up events older than 30 days
reporter.enforce_retention(days=30)
# Fewer events = faster queries + rendering
```

---

### 10. User IDs Not Masked in Compliance Export

**Symptom:**
```bash
export = reporter.export_for_compliance(redact=True)
# But user_id still shows: {"user_id": "user_123"}
# Expected: {"user_id": "a7f8b2c4d9e1f6a3"}
```

**Root Cause:**
- Payload field name mismatch (`user` vs `user_id` vs `owner`)
- Salt not configured consistently

**Solution:**

**Option A: Verify Field Name**
```python
# Check actual field names in events
for event in trail.query_events(limit=10):
    print(event.payload)
    # Look for: user_id, user, owner, uid, etc.
```

**Option B: Update Masking Logic**
```python
# In reporter.py, redact_event(), add missing field names
if key.lower() in ('user_id', 'user', 'owner', 'uid', 'user_name'):
    redacted_payload[key] = self.mask_user_id(value)
```

**Option C: Verify Salt Consistency**
```python
# If salt changes between runs, masks differ
reporter1 = ComplianceReporter(trail, user_id_salt="salt_v1")
reporter2 = ComplianceReporter(trail, user_id_salt="salt_v2")

# same user_id produces different hashes!
print(reporter1.mask_user_id("user_123"))
# Output: a7f8b2c4d9e1f6a3

print(reporter2.mask_user_id("user_123"))
# Output: x9y8z7w6v5u4t3s2  (different!)

# Fix: store salt in config, use consistently
```

---

## Debug Checklist

Before escalating, verify:

- [ ] Console restarted after deployment (`systemctl --user restart corvin-webui.service`)
- [ ] Audit trail file exists and is readable (`ls -la ~/.corvin/tenants/_default/audit/datahub.jsonl`)
- [ ] Audit chain verifies without errors
- [ ] Browser cache cleared (Ctrl+Shift+R)
- [ ] Network tab shows successful API calls (200 OK)
- [ ] Prometheus metrics endpoint returns data
- [ ] No errors in console logs (`journalctl --user -u corvin-console.service -n 50`)
- [ ] Tenant ID correct (`echo $CORVIN_TENANT_ID`)

---

## Logging & Debug Output

Enable debug logging:

```bash
# Set debug level
export CORVIN_LOG_LEVEL=DEBUG

# Restart
systemctl --user restart corvin-console.service

# Tail logs
journalctl --user -u corvin-console.service -f
```

---

## Performance Profiling

Profile dashboard load:

```javascript
// In browser console (F12)
console.time("dashboard-load");
fetch("/api/v1/learning/skills?limit=50")
  .then(r => r.json())
  .then(data => {
    console.timeEnd("dashboard-load");
    console.log(`Received ${data.length} skills`);
  });
```

Expected: <500ms network latency + <500ms rendering = <1s total.

---

## When to Escalate

**CRITICAL (Escalate Immediately):**
- Audit chain broken (fail-closed)
- Dashboard completely inaccessible (503)
- Data loss suspected

**HIGH (Within 1 Hour):**
- Compliance export broken (GDPR risk)
- PII leaking (redaction not working)
- Learning convergence stalled >2 days

**MEDIUM (Within 1 Day):**
- Dashboard slow (>2s load)
- Bias detection false positives
- Prometheus metrics inaccurate

**LOW (Backlog):**
- Dashboard UI improvements
- Documentation updates
