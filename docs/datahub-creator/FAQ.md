# DataHub Creator Phase 4 FAQ

## General Questions

### Q: What is DataHub Creator?
**A:** DataHub Creator is a unified system for creating and managing Skills and Tools in CorvinOS. It consists of 4 phases:
1. **Phase 1 (DataHub):** Ingests data from multiple sources (Memory, RAG, MCP, Files)
2. **Phase 2 (Creator 2.0):** Generates skills through a 12-phase model with integrated loss tracking
3. **Phase 3 (Daemon):** Learns from skill usage and automatically optimizes weights
4. **Phase 4 (Dashboard + Compliance):** Provides observability and GDPR compliance

### Q: Is Phase 4 required to use Phases 1-3?
**A:** No. Phases 1-3 function independently. Phase 4 adds:
- Real-time dashboard visibility
- Audit trail for compliance
- Prometheus metrics for monitoring

You can use Phases 1-3 without Phase 4, but lose observability.

### Q: What's the performance impact of Phase 4?
**A:** Minimal:
- **Audit writes:** <50ms per event (disk write)
- **Dashboard queries:** <500ms for 100 events
- **Memory overhead:** ~10MB per 1000 audit events

### Q: Can Phase 4 be disabled?
**A:** Not recommended. Audit trails are fail-closed (part of system integrity). You can:
- Reduce polling frequency on dashboard (5s → 30s)
- Archive old audit events (retention policy)
- But you cannot disable audit writing itself

---

## Dashboard Questions

### Q: Why does the dashboard show "No Data"?
**A:** Several reasons:
1. **No skills generated yet** → Use Phase 2 to generate a skill
2. **Audit trail empty** → Check `~/.corvin/tenants/_default/audit/datahub.jsonl` exists
3. **API endpoint down** → Check `/api/v1/learning/skills` returns 200
4. **Browser cache stale** → Hard-refresh (Ctrl+Shift+R)

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for detailed diagnosis.

### Q: How often does the dashboard update?
**A:** Every 5 seconds. This is configurable in the frontend code (`LearningDashboard.tsx`).

### Q: Can I export dashboard data?
**A:** Partially. You can:
- **Prometheus metrics:** `curl http://localhost:8765/api/v1/learning/metrics > metrics.txt`
- **Compliance export:** `GET /api/v1/learning/audit?format=jsonl` (PII redacted)
- **Raw audit trail:** Direct access to `~/.corvin/tenants/_default/audit/datahub.jsonl`

### Q: What do the KPI cards mean?
**A:**
- **Skills Generated:** Total unique skills created by Phase 2
- **Avg Improvement:** Average loss reduction per skill (Phase 2 metric)
- **Feedback Count:** Total user/system feedback signals received (Phase 3)
- **Convergence:** Daemon learning confidence (0-100%), higher = more stable

### Q: Why is convergence stuck at 30%?
**A:** Possible reasons:
1. **Insufficient feedback:** Need 100+ samples to converge. Keep using skills.
2. **Poor data quality:** Feedback is one-sided (95% positive). Review bias alerts.
3. **High variance:** Data is noisy. More diverse skill usage helps.

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) section 8 for solutions.

### Q: Can I manually set convergence to 100%?
**A:** No. Convergence is read-only, computed from feedback data. It represents the daemon's confidence in learned weights.

---

## Audit Trail Questions

### Q: What exactly is stored in the audit trail?
**A:** Every significant event:
- Skill generation (loss before/after, phase count)
- Weight updates (which source, old/new values)
- Feedback received (signal type, impact on loss)

Each event is immutable and hash-chained. See [RUNBOOK.md](RUNBOOK.md) for event schema.

### Q: Where is the audit trail stored?
**A:** `~/.corvin/tenants/_default/audit/datahub.jsonl`

Format: JSONL (one JSON object per line, immutable).

### Q: How long are audit events kept?
**A:** 90 days (GDPR Art. 5 Storage Limitation).

Older events are automatically deleted via `reporter.enforce_retention()`. You can customize:
```python
reporter = ComplianceReporter(trail, retention_days=365)  # Keep 1 year
```

### Q: Can I delete specific audit events?
**A:** No. Events are immutable. If you need to mark an event as erroneous:
1. Document the issue
2. Write a correction event (audit trail shows both)
3. Compliance auditor can see the full context

### Q: What if the audit chain breaks?
**A:** System halts (fail-closed). This prevents silently corrupted audit trails.

Options:
1. **Restore from backup:** `cp audit.jsonl.bak audit.jsonl`
2. **Truncate and rebuild:** Remove broken events, restart
3. **Start fresh:** Delete and create new chain (loses history)

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) section 2.

---

## Compliance Questions

### Q: How does DataHub Creator handle GDPR?
**A:** Multiple mechanisms:
1. **PII Redaction:** Emails, phone numbers, SSNs are redacted in exports
2. **User ID Masking:** Consistent SHA256 hash (deterministic, anonymous)
3. **Retention Policy:** Events deleted after 90 days
4. **Tenant Isolation:** All queries filtered by tenant_id
5. **Audit Trail:** Complete record of all decisions for transparency

### Q: What's the difference between "redact" and "mask"?
**A:**
- **Redact:** Replace with placeholder → `user@example.com` becomes `[REDACTED_email]`
- **Mask:** Replace with consistent hash → `user_123` becomes `a7f8b2c4d9e1f6a3`

Redaction is for plaintext PII (emails, phone). Masking is for IDs (keeps comparability).

### Q: Can I export compliance reports?
**A:** Yes:
```python
export = reporter.export_for_compliance(
    since=datetime.now() - timedelta(days=30),  # Last 30 days
    redact=True  # PII redacted
)
# Export is JSONL-formatted, ready for auditor
```

### Q: Does Phase 4 log user conversations?
**A:** No. Only logged:
- Skill generation events (metadata, loss, phase count)
- Feedback signals (positive/negative, not text)
- Weight updates (which source, values)

Conversation content is **never** logged to audit trail.

### Q: How do I prove compliance to an auditor?
**A:** Provide:
1. **Compliance export:** Redacted audit trail (JSONL)
2. **Policy documentation:** Retention policy, consent gates
3. **Metrics:** Prometheus metrics showing learning progress
4. **Bias report:** `reporter.detect_bias()` output

### Q: Can users request deletion of their data (GDPR Art. 17)?
**A:** Partially:
- User IDs are masked (no direct identity link)
- After 90 days, events auto-deleted
- For specific deletion requests, operator must:
  1. Identify events by masked user ID
  2. Manually review and delete (breaks audit chain?)
  3. Document decision

This is a gap in Phase 4 — GDPR Art. 17 handling needs operator manual intervention.

---

## Monitoring Questions

### Q: What Prometheus metrics should I alert on?
**A:** Critical alerts:
```yaml
alert: AuditChainBroken
expr: datahub_audit_chain_verified == 0
severity: critical
```

High-priority alerts:
```yaml
alert: DaemonStalled
expr: increase(datahub_feedback_signals_total[1h]) == 0
severity: high

alert: LearningNotConverging
expr: datahub_daemon_convergence_status < 0.5
severity: high
```

See [RUNBOOK.md](RUNBOOK.md) section "Critical Alerts".

### Q: How do I integrate with Grafana?
**A:** Add Prometheus datasource, then:
1. Create dashboard
2. Add panels:
   - `rate(datahub_skill_generation_count[5m])`
   - `rate(datahub_feedback_signals_total[5m])`
   - `datahub_daemon_convergence_status`
   - `datahub_audit_chain_height`

### Q: Can I export metrics to another system?
**A:** Yes. `/api/v1/learning/metrics` returns Prometheus text format. You can:
- Scrape directly into Prometheus
- Parse text and push to CloudWatch/DataDog
- Store in InfluxDB via Telegraf

---

## Technical Questions

### Q: What happens if the audit file is too large?
**A:** Performance degrades:
- **Dashboard load:** >2s (query slower)
- **Chain verification:** >500ms on boot
- **Compliance export:** >2s

Solutions:
1. Reduce retention days: `reporter.enforce_retention(days=30)`
2. Implement pagination in dashboard frontend
3. Archive old files: `mv audit.jsonl audit_2026-08.jsonl.gz`

### Q: Can multiple processes write to the audit trail?
**A:** Only one tenant per file. Multiple tenants have separate files:
- Tenant `_default`: `~/.corvin/tenants/_default/audit/datahub.jsonl`
- Tenant `acme_corp`: `~/.corvin/tenants/acme_corp/audit/datahub.jsonl`

Concurrent writes to same file: not tested. Use file locking if needed.

### Q: How does hashing work in the audit trail?
**A:** SHA256 hash chain:
```
Event 1: hash = SHA256({event_type, timestamp, ...})
Event 2: prev_hash = Event 1.hash, hash = SHA256({..., prev_hash})
Event 3: prev_hash = Event 2.hash, hash = SHA256({..., prev_hash})
```

If Event 2 is tampered, Event 3's hash becomes invalid (chain breaks).

### Q: What's the "Line of Moral Responsibility" (LoM)?
**A:** TODO (ADR-0537) — planned for Phase 5. LoM binds each audit event to the code that made the decision, enabling audit-to-source traceability.

### Q: How do I integrate Phase 4 with my own learning system?
**A:** Use the AuditTrail API:
```python
from core.skills.os_skills.audit.trail import AuditTrail
from core.skills.os_skills.audit.reporter import ComplianceReporter

trail = AuditTrail(tenant_id="my_tenant", chain_path="/path/to/audit.jsonl")

# Write custom event
trail.write_event("my_event_type", skill_id="s1", payload={...})

# Query later
events = trail.query_events(event_type="my_event_type", limit=100)

# Export compliance report
reporter = ComplianceReporter(trail)
export = reporter.export_for_compliance(redact=True)
```

---

## Architecture Questions

### Q: How do Phases 1-4 relate?
**A:**
```
Phase 1 (DataHub)   — Input: Data sources
    ↓
Phase 2 (Creator)   — Process: Generate skills
    ↓
Phase 3 (Daemon)    — Learn: Optimize from usage
    ↓
Phase 4 (Dashboard) — Observe: Monitor + Compliance
```

Each phase is independent but can feed into the next.

### Q: Can I use Phase 2 without Phase 1?
**A:** Yes. Phase 1 provides data sources, but Phase 2 can create skills with user-provided context.

### Q: Can I use Phase 3 without Phase 2?
**A:** No. Phase 3 learns from skills generated by Phase 2. Without Phase 2, there's nothing to learn from.

### Q: Can I use Phase 4 without Phases 1-3?
**A:** Phase 4's audit trail is generic (supports any events). But without Phases 1-3, there's no meaningful data to audit.

---

## Support & Escalation

### Q: Where do I report bugs?
**A:** Open an issue in the CorvinOS GitHub repo with:
- Error message (full stack trace)
- Reproduction steps
- System info (`python --version`, CorvinOS version)
- Relevant logs (`journalctl --user -u corvin-console.service`)

### Q: Is there a roadmap for Phase 5+?
**A:** Yes. Planned enhancements:
- **Phase 5:** Line of Moral Responsibility (code attribution in audit trail)
- **Phase 6:** Advanced bias detection (fairness metrics)
- **Phase 7:** Multi-tenant learning (cross-tenant patterns)
- **Phase 8:** Operator feedback loops (e.g., "this weight is wrong")

### Q: How can I contribute?
**A:** See [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

---

## Known Limitations

### 1. Audit Trail Not Encrypted at Rest
**Status:** TODO (Phase 5)
**Workaround:** Use filesystem encryption (LUKS, FileVault)

### 2. No Rollback API
**Status:** TODO (Phase 5)
**Workaround:** Operator manually reviews audit trail and reverts (documented decision)

### 3. Bias Detection Based on Feedback Only
**Status:** By design (Phase 4)
**Context:** Future phases will add feature importance analysis

### 4. Dashboard Not Real-Time
**Status:** Polled every 5s (Phase 4)
**Workaround:** Reduce interval to 1s if needed (performance tradeoff)

### 5. No GDPR Art. 17 (Erasure) Automation
**Status:** TODO (Phase 5)
**Workaround:** Manual operator intervention + audit trail documentation

---

## Additional Resources

- **RUNBOOK.md:** Deployment, monitoring, troubleshooting
- **TROUBLESHOOTING.md:** Common issues and fixes
- **ADR-0661:** Architecture Decision (Phases 1-4 design)
- **ADR-0314:** Learning Infrastructure (event schema)
- **ADR-0232:** Boot Tripwire (audit fail-closed)

---

Last updated: 2026-09-11
