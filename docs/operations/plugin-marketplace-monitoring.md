# Plugin Marketplace Monitoring & Alerting Setup

**Document:** Monitoring & Alerting Configuration  
**Date Created:** 2026-08-29  
**Status:** Ready for Deployment  
**Owner:** SRE/DevOps  
**Target:** Active monitoring during rollout + production baseline

---

## Overview

This document defines the monitoring strategy, alert thresholds, dashboards, and runbooks for the plugin marketplace (ADR-0249, ADR-0383, ADR-0385). Monitoring starts during Phase 1 (dark ship) and continues through all rollout phases.

---

## Monitoring Architecture

### Data Sources

1. **Audit Trail** (`~/.corvin/audit.jsonl`)
   - Hash-chained events
   - All plugin operations (upload, install, report, etc.)
   - Latency: real-time (synchronous writes)

2. **System Metrics** (OS-level)
   - Disk I/O, CPU, memory
   - Process uptime and restarts
   - File descriptor usage

3. **Application Logs**
   - Console stderr/stdout
   - systemd journal
   - Latency and error traces

4. **Registry State** (`~/.corvin/tenants/_default/plugins/registry.yaml`)
   - Plugin count and size
   - Health check status
   - Last mutation timestamp

### Collection Strategy

- **Audit Trail:** Parsed every 60 seconds (for Phase 1–3), every 10 seconds (for Phase 4+)
- **System Metrics:** Collected via `systemctl status` and `systemd.journal` (native)
- **Application Logs:** Streamed via `journalctl --follow`
- **Registry State:** Checked on-demand (low overhead, checked every 5 minutes)

---

## Key Metrics & Thresholds

### Metric Group 1: Audit Chain Integrity

| Metric | Definition | Threshold | Alert Level | Action |
|--------|-----------|-----------|-------------|--------|
| **Chain Valid** | Audit chain cryptographic verification passes | 100% | CRITICAL if broken | Stop all operations, investigate |
| **Entries Verified** | Number of consecutive valid hash links | N/A (tracked for forensics) | — | Log at every 1000-entry boundary |
| **Verification Latency** | Time to verify last 100 entries | <100ms | WARNING if >500ms | Check disk I/O, consider async verification |
| **Last Write Time** | Timestamp of most recent audit event | <5 minutes ago | CRITICAL if stale | Audit writer may be hung |

**Alert Configuration:**
```yaml
alerts:
  - name: audit_chain_broken
    condition: chain_valid == False
    severity: CRITICAL
    action: page_on_call
    message: "Audit chain corrupted — immediate investigation required"
    runbook: "docs/operations/plugin-marketplace-runbook.md#scenario-2-registry-corrupted-backup-also-corrupted"

  - name: audit_writes_stale
    condition: last_write_time > 5 minutes
    severity: CRITICAL
    action: page_on_call
    message: "No audit events in 5+ minutes — audit writer may be hung"
    runbook: "docs/operations/plugin-marketplace-runbook.md#issue-plugin-report-submissions-not-creating-audit-events"
```

### Metric Group 2: Plugin Upload & Installation

| Metric | Definition | Threshold | Alert Level | Action |
|--------|-----------|-----------|-------------|--------|
| **Upload Success Rate** | % of uploads that complete successfully | >95% | WARNING if <90% | Investigate upload failures |
| **Upload Latency (p95)** | 95th percentile upload time | <5s | WARNING if >10s | Check registry write performance |
| **Upload Latency (p99)** | 99th percentile upload time | <10s | CRITICAL if >30s | Immediate investigation |
| **Install Success Rate** | % of installs that complete successfully | >95% | WARNING if <90% | Investigate install failures |
| **Install Latency (p95)** | 95th percentile install time | <2s | WARNING if >5s | Check manifest parsing, disk I/O |
| **Manifest Validation Errors** | Count of invalid manifests rejected | 0–10/day | WARNING if >20/day | May indicate bad uploads or attacks |

**Alert Configuration:**
```yaml
alerts:
  - name: upload_success_rate_low
    condition: upload_success_rate < 0.90
    severity: WARNING
    action: page_on_call
    duration: 5_minutes  # Wait 5 min to filter transient spikes
    message: "Upload success rate dropped to {{ value }}% — investigating"

  - name: upload_latency_p99_high
    condition: upload_latency_p99 > 30_seconds
    severity: CRITICAL
    action: page_on_call
    message: "Upload latency p99 = {{ value }}s — performance degradation"

  - name: manifest_validation_errors_high
    condition: manifest_validation_errors_24h > 20
    severity: WARNING
    action: log_and_notify
    message: "{{ value }} manifest validation errors in past 24h"
    runbook: "docs/operations/plugin-marketplace-runbook.md#issue-upload-endpoint-accepts-file-but-rejects-it-as-untrusted"
```

### Metric Group 3: Plugin Reports

| Metric | Definition | Threshold | Alert Level | Action |
|--------|-----------|-----------|-------------|--------|
| **Report Submission Success Rate** | % of report submissions that succeed | >95% | WARNING if <90% | Investigate report handling |
| **Report Latency (p95)** | 95th percentile report submission time | <500ms | WARNING if >1s | Check audit writer latency |
| **Report Latency (p99)** | 99th percentile report submission time | <1s | CRITICAL if >5s | Immediate investigation |
| **Spam Reports** | Reports from same user >5/hour | <5 | WARNING if detected | May indicate attack, consider rate-limiting |
| **Report Duplication** | Exact duplicates within 1 minute | 0 | WARNING if >1 | May indicate client retry issue |

**Alert Configuration:**
```yaml
alerts:
  - name: report_success_rate_low
    condition: report_success_rate < 0.90
    severity: WARNING
    action: page_on_call
    duration: 5_minutes
    message: "Report success rate = {{ value }}%"

  - name: report_latency_p99_high
    condition: report_latency_p99 > 5_seconds
    severity: CRITICAL
    action: page_on_call
    message: "Report latency p99 = {{ value }}s"

  - name: spam_reports_detected
    condition: max_reports_per_user_1h > 5
    severity: WARNING
    action: log_and_notify
    message: "User submitted {{ value }} reports in 1 hour — possible abuse"
```

### Metric Group 4: Plugin Registry Health

| Metric | Definition | Threshold | Alert Level | Action |
|--------|-----------|-----------|-------------|--------|
| **Registry Load Time** | Time to deserialize registry.yaml | <100ms | WARNING if >200ms | Check file size, disk I/O |
| **Registry File Size** | Size of registry.yaml on disk | <10MB | WARNING if >50MB | May need cleanup (ADR-0244) |
| **Plugin Count** | Number of installed plugins | <1000 | WARNING if >2000 | Cleanup may be needed |
| **Registry Backup Age** | Time since last successful backup | <1 hour | WARNING if >24h | Backup system may be stuck |
| **Backup File Exists** | registry.yaml.bak present and recent | Yes + <1h | CRITICAL if missing | No rollback capability |

**Alert Configuration:**
```yaml
alerts:
  - name: registry_load_time_high
    condition: registry_load_time_ms > 200
    severity: WARNING
    action: log_and_monitor
    message: "Registry load time = {{ value }}ms"

  - name: registry_file_size_high
    condition: registry_file_size_mb > 50
    severity: WARNING
    action: log_and_notify
    message: "Registry file size = {{ value }}MB — cleanup may help"

  - name: backup_missing_or_stale
    condition: backup_file_missing OR backup_age_hours > 24
    severity: CRITICAL
    action: page_on_call
    message: "Registry backup missing or stale — no rollback capability"
```

### Metric Group 5: Disk Space & I/O

| Metric | Definition | Threshold | Alert Level | Action |
|--------|-----------|-----------|-------------|--------|
| **Free Space** | Disk free in ~/.corvin/ partition | >5GB | CRITICAL if <1GB | Cleanup needed |
| **Free Space %** | Free space as % of total | >20% | WARNING if <10% | Approaching limit |
| **I/O Wait Time** | CPU time waiting for disk I/O | <5% | WARNING if >10% | Possible disk bottleneck |
| **Temp Files** | Count of stale .registry-*.tmp files | 0 | WARNING if >1 | Failed cleanup may indicate issue |

**Alert Configuration:**
```yaml
alerts:
  - name: disk_free_critical
    condition: disk_free_gb < 1
    severity: CRITICAL
    action: page_on_call
    message: "Only {{ value }}GB disk free — immediate cleanup needed"

  - name: disk_io_wait_high
    condition: io_wait_percent > 10
    severity: WARNING
    action: page_on_call
    message: "I/O wait time = {{ value }}% — disk bottleneck"

  - name: temp_files_stale
    condition: stale_temp_files_count > 0
    severity: WARNING
    action: cleanup_and_alert
    message: "Found {{ value }} stale temp files — attempting cleanup"
```

### Metric Group 6: Trust & Security

| Metric | Definition | Threshold | Alert Level | Action |
|--------|-----------|-----------|-------------|--------|
| **Trust Anchor Valid** | Trust anchor file exists and is readable | Yes | CRITICAL if No | Cannot verify signatures |
| **Invalid Signatures** | Plugins rejected due to bad signature | <5/day | WARNING if >10/day | Possible key compromise or attacks |
| **Community Plugin Installs** | Installs of community origin plugins | N/A | INFO only | Track adoption; use for metrics |
| **Builtin Plugins Active** | Count of active builtin plugins | N/A | INFO only | Track for rollout progress |
| **Vetted Plugins Active** | Count of active vetted plugins (if signed) | N/A | INFO only | Track for rollout progress |

**Alert Configuration:**
```yaml
alerts:
  - name: trust_anchor_missing
    condition: trust_anchor_valid == False
    severity: CRITICAL
    action: page_on_call
    message: "Trust anchor file missing or unreadable"
    runbook: "docs/operations/plugin-trust-anchor-procedures.md"

  - name: signature_verification_failures_high
    condition: invalid_signatures_24h > 10
    severity: WARNING
    action: page_on_call
    message: "{{ value }} signature verification failures in past 24h"
    runbook: "docs/operations/plugin-marketplace-runbook.md#issue-upload-endpoint-accepts-file-but-rejects-it-as-untrusted"
```

### Metric Group 7: Process Health

| Metric | Definition | Threshold | Alert Level | Action |
|--------|-----------|-----------|-------------|--------|
| **Console Uptime** | Time since console last restarted | N/A | INFO only | Track restart frequency |
| **Console Restart Count** | Number of restarts in past 24h | 0 | WARNING if >3 | May indicate crashes |
| **Memory Usage** | Console process memory | <500MB | WARNING if >1GB | Possible memory leak |
| **File Descriptors** | Open FDs in console process | <1024 | WARNING if >500 | May indicate leak |

**Alert Configuration:**
```yaml
alerts:
  - name: console_restart_frequency
    condition: restart_count_24h > 3
    severity: WARNING
    action: page_on_call
    message: "Console restarted {{ value }} times in past 24h"

  - name: memory_usage_high
    condition: memory_usage_mb > 1000
    severity: WARNING
    action: page_on_call
    message: "Console memory = {{ value }}MB — possible leak"
```

---

## Alert Severity Levels

| Level | Definition | On-Call Paged? | Escalation | Response Time |
|-------|-----------|----------------|------------|---------------|
| **CRITICAL** | Service down or data integrity at risk | YES | Page immediately | <5 minutes |
| **WARNING** | Degradation detected, may progress to critical | If trend detected | Check after 5 min | <30 minutes |
| **INFO** | Operational metric, no action needed | NO | Log only | N/A |

---

## Dashboard Setup

### Dashboard 1: Plugin Marketplace Health (Real-Time)

**Refresh:** Every 30 seconds  
**Components:**

```
┌─────────────────────────────────────────────────────────────┐
│ PLUGIN MARKETPLACE HEALTH (Last 1h)                         │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  Audit Chain Status:     ✓ VALID (10,234 entries)           │
│  Last Write:             2 seconds ago                       │
│                                                               │
│  ┌─ Upload Performance ─────────────────────────────────┐   │
│  │ Success Rate: 97.3%  │ Uploads (1h): 148            │   │
│  │ Latency p95: 2.1s    │ Avg Size: 2.4 MB             │   │
│  │ Latency p99: 4.3s    │ Failures: 4                  │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─ Report Activity ────────────────────────────────────┐   │
│  │ Submissions (1h): 34 │ Success Rate: 99.1%           │   │
│  │ Latency p95: 245ms   │ Errors: <1                    │   │
│  │ Latency p99: 512ms   │ Spam Reports: 0               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─ Registry Health ────────────────────────────────────┐   │
│  │ Installed Plugins: 47  │ Load Time: 34ms             │   │
│  │ File Size: 3.2 MB      │ Backups: Up-to-date         │   │
│  │ Free Space: 8.7 GB     │ Temp Files: 0               │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
└─────────────────────────────────────────────────────────────┘
```

**Implementation:**

```bash
#!/usr/bin/env bash

watch -n 30 'python3 << EOF
import json
from pathlib import Path
from datetime import datetime, timedelta
import time

audit_path = Path.home() / ".corvin/audit.jsonl"
registry_path = Path.home() / ".corvin/tenants/_default/plugins/registry.yaml"

# Parse audit trail
lookback_1h = datetime.now() - timedelta(hours=1)
uploads = installs = reports = errors = 0
upload_times = []
report_times = []

for line in audit_path.read_text().strip().split("\n"):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
    if ts < lookback_1h:
        continue
    
    event_type = event.get("type", "")
    if event_type == "plugin.uploaded":
        uploads += 1
        upload_times.append(event.get("duration_ms", 0))
    elif event_type == "plugin.reported":
        reports += 1
        report_times.append(event.get("duration_ms", 0))
    elif "error" in event_type:
        errors += 1

# Calculate statistics
upload_times.sort()
report_times.sort()

upload_success_rate = (uploads - errors) / max(uploads, 1) * 100 if uploads > 0 else 0
report_success_rate = (reports - errors) / max(reports, 1) * 100 if reports > 0 else 0

upload_p95 = upload_times[int(len(upload_times) * 0.95)] if len(upload_times) > 0 else 0
upload_p99 = upload_times[int(len(upload_times) * 0.99)] if len(upload_times) > 0 else 0
report_p95 = report_times[int(len(report_times) * 0.95)] if len(report_times) > 0 else 0
report_p99 = report_times[int(len(report_times) * 0.99)] if len(report_times) > 0 else 0

# Get audit chain status
from corvin_compliance.audit import verify_audit_chain
result = verify_audit_chain(audit_path, max_lookback=1000)

print("\033[2J\033[H")  # Clear screen
print("PLUGIN MARKETPLACE HEALTH (Last 1h)")
print("=" * 60)
print(f"Audit Chain: {'✓ VALID' if result.valid else '✗ BROKEN'} ({result.entries_verified} entries)")
print()
print(f"Uploads: {uploads} | Success: {upload_success_rate:.1f}% | p95: {upload_p95:.0f}ms | p99: {upload_p99:.0f}ms")
print(f"Reports: {reports} | Success: {report_success_rate:.1f}% | p95: {report_p95:.0f}ms | p99: {report_p99:.0f}ms")
print(f"Errors: {errors}")
EOF'
```

### Dashboard 2: Audit Trail Verification (Hourly)

**Refresh:** Every hour  
**Components:**

```
┌─────────────────────────────────────────────────────┐
│ AUDIT TRAIL STATUS (Hourly Check)                  │
├─────────────────────────────────────────────────────┤
│                                                      │
│ Chain Valid:        ✓ YES                           │
│ Entries Verified:   10,234 / 10,234 (100%)         │
│ Last Entry:         2026-08-29 14:52:33 UTC        │
│ Chain Verification: 34ms                            │
│                                                      │
│ Event Types (past 1h):                              │
│   plugin.uploaded              12                   │
│   plugin.installed             8                    │
│   plugin.reported              34                   │
│   plugin.registry_updated      3                    │
│   error.*                      0                    │
│                                                      │
│ Oldest Entry:       2026-08-01 09:22:15 UTC        │
│ Newest Entry:       2026-08-29 14:52:33 UTC        │
│ Total Entries:      10,234                         │
│ File Size:          2.1 MB                         │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### Dashboard 3: Plugin Governance Metrics (Daily)

**Refresh:** Once per day (or on-demand)  
**Components:**

```
┌──────────────────────────────────────────────────────┐
│ PLUGIN GOVERNANCE (Daily Summary)                    │
├──────────────────────────────────────────────────────┤
│                                                       │
│ Plugins by Origin:                                   │
│   Builtin:      8  (all active)                     │
│   Vetted:       3  (all active, signatures valid)   │
│   Community:    36 (32 active, 4 disabled)          │
│                                                       │
│ Upload Sources:                                      │
│   Web UI:       89 uploads (92 % of total)         │
│   CLI:          8 uploads (8%)                     │
│                                                       │
│ Community Feedback:                                  │
│   Reports:      45 (24h)                            │
│   Most Reported: plugin-xyz (3 reports)             │
│   Average Rating: 4.2/5.0                           │
│                                                       │
│ Security Incidents:                                  │
│   Signature Failures: 0                             │
│   Manifest Errors: 1 (malformed JSON)               │
│   Trust Anchor Status: ✓ Valid                      │
│                                                       │
└──────────────────────────────────────────────────────┘
```

---

## Alerting Channels

### Primary: Email to On-Call

```yaml
# config/alerting/email-on-call.yaml
recipients:
  - on-call@corvinlabs.com
severity_map:
  CRITICAL: immediate_page
  WARNING: next_check (5 min)
  INFO: daily_digest
```

### Secondary: Slack #corvinOS-ops

```yaml
# config/alerting/slack-ops.yaml
channel: "#corvinOS-ops"
severity_format:
  CRITICAL: "@channel 🚨 {message}"
  WARNING: "{message}"
  INFO: "ℹ️ {message}"
```

### Tertiary: Incident Tracking

```yaml
# config/alerting/incident-tracking.yaml
CRITICAL:
  - auto_create_incident: true
  - notify: incident-commanders@corvinlabs.com
  - notify: security-team@corvinlabs.com (if trust/security related)
```

---

## Query Reference

### Audit Trail Analysis (Common Queries)

**Query 1: Upload success rate in past 1 hour**

```bash
#!/usr/bin/env bash

python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta

audit_path = Path.home() / '.corvin/audit.jsonl'
lookback = datetime.now() - timedelta(hours=1)

uploads = errors = 0
for line in audit_path.read_text().strip().split('\n'):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if ts < lookback:
        continue
    
    if event['type'] == 'plugin.uploaded':
        uploads += 1
    elif event['type'] == 'plugin.upload_failed':
        errors += 1

success_rate = ((uploads - errors) / max(uploads, 1) * 100) if uploads > 0 else 100
print(f'Uploads: {uploads} | Errors: {errors} | Success rate: {success_rate:.1f}%')
"
```

**Query 2: Report latency statistics**

```bash
#!/usr/bin/env bash

python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta
import statistics

audit_path = Path.home() / '.corvin/audit.jsonl'
lookback = datetime.now() - timedelta(hours=1)

latencies = []
for line in audit_path.read_text().strip().split('\n'):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if ts < lookback:
        continue
    
    if event['type'] == 'plugin.reported' and 'duration_ms' in event:
        latencies.append(event['duration_ms'])

if latencies:
    latencies.sort()
    p50 = latencies[int(len(latencies) * 0.50)]
    p95 = latencies[int(len(latencies) * 0.95)]
    p99 = latencies[int(len(latencies) * 0.99)]
    mean = statistics.mean(latencies)
    print(f'Report Latency (past 1h): mean={mean:.0f}ms | p50={p50}ms | p95={p95}ms | p99={p99}ms')
else:
    print('No report events in past 1 hour')
"
```

**Query 3: Audit chain status check**

```bash
#!/usr/bin/env bash

python3 -c "
from corvin_compliance.audit import verify_audit_chain
from pathlib import Path

audit_path = Path.home() / '.corvin/audit.jsonl'
result = verify_audit_chain(audit_path)

print(f'Audit Chain: {\"✓ VALID\" if result.valid else \"✗ BROKEN\"}')
print(f'Entries Verified: {result.entries_verified}')
if not result.valid:
    print(f'Error: {result.error}')
    print(f'Failed at Entry: {result.failed_at}')
"
```

---

## Troubleshooting Common Alerts

### "Audit Chain Broken" (CRITICAL)

**Root Cause:** Hash chain verification failed or audit file corrupted  
**Investigation:**

```bash
python3 -c "
from corvin_compliance.audit import verify_audit_chain
from pathlib import Path

audit_path = Path.home() / '.corvin/audit.jsonl'
result = verify_audit_chain(audit_path)

print(f'Chain valid: {result.valid}')
print(f'Failed at entry: {result.failed_at}')
print(f'Error: {result.error}')

# Show the problematic entry
if result.failed_at is not None:
    import json
    for i, line in enumerate(audit_path.read_text().split('\n')):
        if i == result.failed_at and line:
            event = json.loads(line)
            print(f'\nFailed entry: {json.dumps(event, indent=2)}')
            break
"
```

**Recovery:** See plugin-marketplace-runbook.md § Scenario 2

### "Upload Success Rate Low" (WARNING)

**Root Cause:** Upload failures in past 5 minutes  
**Investigation:**

```bash
python3 -c "
import json
from pathlib import Path
from datetime import datetime, timedelta

audit_path = Path.home() / '.corvin/audit.jsonl'
lookback = datetime.now() - timedelta(minutes=5)

failures = []
for line in audit_path.read_text().strip().split('\n'):
    if not line:
        continue
    event = json.loads(line)
    ts = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00'))
    if ts < lookback:
        continue
    
    if event['type'] == 'plugin.upload_failed':
        failures.append(event)

print(f'Upload failures in past 5 min: {len(failures)}')
for f in failures[-5:]:  # Show last 5
    print(f'  {f.get(\"timestamp\")}: {f.get(\"error_code\")} — {f.get(\"error_msg\")}')
"
```

**Recovery:** Check registry write permissions, disk space, or audit writer latency

---

## Metrics Export (Optional: Prometheus)

If Prometheus scraping is configured:

```python
# core/plugins/corvin_plugins/metrics.py

from prometheus_client import Counter, Histogram, Gauge

# Counters (monotonically increasing)
plugin_uploads_total = Counter(
    'plugin_uploads_total',
    'Total plugin uploads',
    ['origin', 'status']  # status: success | failed
)

plugin_installs_total = Counter(
    'plugin_installs_total',
    'Total plugin installations',
    ['status']  # status: success | failed
)

plugin_reports_total = Counter(
    'plugin_reports_total',
    'Total plugin reports',
    ['reason']  # reason: malicious | non-functional | spam | other
)

# Histograms (latency)
plugin_upload_duration_seconds = Histogram(
    'plugin_upload_duration_seconds',
    'Plugin upload duration',
    buckets=[0.1, 0.5, 1, 2, 5, 10, 30]
)

plugin_install_duration_seconds = Histogram(
    'plugin_install_duration_seconds',
    'Plugin installation duration',
    buckets=[0.1, 0.5, 1, 2, 5]
)

plugin_report_duration_seconds = Histogram(
    'plugin_report_duration_seconds',
    'Plugin report submission duration',
    buckets=[0.01, 0.05, 0.1, 0.2, 0.5, 1]
)

registry_load_duration_seconds = Histogram(
    'plugin_registry_load_duration_seconds',
    'Plugin registry load time',
    buckets=[0.01, 0.05, 0.1, 0.2, 0.5]
)

# Gauges (current state)
plugin_count = Gauge(
    'plugin_count',
    'Number of installed plugins',
    ['origin']  # origin: builtin | vetted | community
)

registry_file_size_bytes = Gauge(
    'plugin_registry_file_size_bytes',
    'Size of registry.yaml file'
)

disk_free_bytes = Gauge(
    'plugin_disk_free_bytes',
    'Free disk space in ~/.corvin/'
)

audit_chain_valid = Gauge(
    'audit_chain_valid',
    'Audit chain verification status (1=valid, 0=invalid)'
)
```

---

## On-Call Runbook Reference

### When to Page On-Call

| Scenario | Action | Contact |
|----------|--------|---------|
| Audit chain broken | Page immediately | On-call SRE + Maintainer |
| >5% error rate for 5+ min | Page immediately | On-call SRE |
| Disk free <1GB | Page immediately | On-call SRE |
| >3 console restarts in 24h | Page during business hours | Eng lead |
| Trust anchor missing | Page immediately | Maintainer |

### Escalation Path

1. **On-Call SRE** receives alert
2. **Run diagnosis** (queries above)
3. **If can fix:** apply fix and document
4. **If cannot fix:** escalate to Engineering Lead
5. **Critical security issues:** notify Security Team immediately

---

**Version:** 1.0  
**Last Updated:** 2026-08-29  
**Status:** Ready for Deployment  
**Next Review:** After Phase 1 completion (Sep 02)
