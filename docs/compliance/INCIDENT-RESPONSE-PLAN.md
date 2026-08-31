# Incident Response Plan — EU AI Act Art. 73

**Basis:** EU AI Act 2026 Art. 73, L39 Incident Tracker.  
**Responsible:** Operator DPO + Corvin maintainer (for platform-level incidents).

---

## Serious Incident Definition (Corvin Context)

A "serious incident" in the Corvin context is a structural control failure:

| Category | Definition | Detection |
|---|---|---|
| `chain_integrity` | `audit.chain_gap_detected` CRITICAL event | Auto (IncidentAutoDetector) |
| `engine_policy_violation` | `data_flow.blocked` or `egress.blocked` where engine executed despite denial | Auto |
| `pii_in_audit_chain` | Regex scan detects unredacted email/phone in audit segment | `corvin-incident scan` (daily) |
| `secret_exposure` | `path_gate.denied` on vault path where write succeeded | Auto |
| `consent_bypass` | Message processed without prior `consent.granted` for that uid | `corvin-incident scan` (daily) |
| `disclosure_failure` | Session reached `bridge.message_received` without `disclosure.shown` | `corvin-incident scan` (daily) |

Only `severity: serious` incidents trigger the 15-day Art. 73 notification clock.
`severity: warning` and `severity: informational` incidents are tracked but
do not trigger notification obligations.

---

## Detection Mechanisms

### Automatic (CRITICAL audit events)
The `IncidentAutoDetector` runs on every CRITICAL audit emit. When
`audit.chain_gap_detected`, `data_flow.blocked`, `egress.blocked`, or
`path_gate.denied` fires at CRITICAL severity, an incident is opened automatically.

No manual action required for detection. Verify daily with:
```bash
corvin-incident list --status open
```

### Batch scan (daily recommended)
Consent-bypass and disclosure-failure require scanning the audit chain:
```bash
corvin-incident scan --since 30
```

The `corvin-incident-scan.timer` runs daily at 03:45 UTC. Install (systemd user session):

```bash
PLUGIN_ROOT=$(python3 -c "import operator.bridges.shared as s; print(s.__file__)" 2>/dev/null || echo "/opt/corvin-repo/operator")
PYTHON_BIN=$(which python3)
SYSTEMD_USER=~/.config/systemd/user

mkdir -p "$SYSTEMD_USER"
sed -e "s|__PYTHON_BIN__|$PYTHON_BIN|g" \
    -e "s|__PLUGIN_ROOT__|$PLUGIN_ROOT|g" \
    operator/voice/scripts/systemd/corvin-incident-scan.service > "$SYSTEMD_USER/corvin-incident-scan.service"
cp operator/voice/scripts/systemd/corvin-incident-scan.timer "$SYSTEMD_USER/corvin-incident-scan.timer"

systemctl --user daemon-reload
systemctl --user enable --now corvin-incident-scan.timer
```

---

## Response Flowchart

```
CRITICAL audit event fires
       ↓
IncidentAutoDetector opens incident record
       ↓
DPO reviews:  corvin-incident show <id>
       ↓
Is it a real failure? (not a test / probe)
  ├── NO  → corvin-incident close <id>
  └── YES → Contain: stop service if needed
              ↓
           Root cause analysis
              ↓
           Is severity "serious"? (structural control failure)
             ├── NO  → corvin-incident update <id> --status contained
             └── YES → 15-day Art. 73 clock starts at detected_at
                          ↓
                       Generate notification draft:
                       corvin-incident notify-draft <id> \
                         --authority BSI --operator-name "Acme GmbH" \
                         --output notification-<id>.md
                          ↓
                       DPO reviews + completes [OPERATOR: FILL IN] sections
                          ↓
                       Submit to supervisory authority (manual)
                          ↓
                       corvin-incident update <id> --status notified \
                         --notified-at <ISO-8601 timestamp>
                          ↓
                       Remediate + implement preventive controls
                          ↓
                       corvin-incident close <id>
```

---

## 15-Day Notification Timeline

| Day | Action |
|---|---|
| 0 | Incident detected; `incident.opened` in audit chain |
| 1 | DPO confirms "serious" classification |
| 2-5 | Containment + root cause analysis |
| 6-8 | Generate notification draft (`notify-draft`) |
| 9-12 | DPO + legal review; complete operator sections |
| 13-14 | Submit to supervisory authority |
| 15 | **Hard deadline** (Art. 73 §2) |

If the investigation is not complete by day 15, submit a preliminary notification
with known facts and note that the investigation is ongoing.

---

## Supervisory Authority Contacts (EU)

| Country | Authority | Relevant for |
|---|---|---|
| Germany | BSI (Bundesamt für Sicherheit in der Informationstechnik) | AI systems; data security |
| Germany | BfDI (Bundesbeauftragter für den Datenschutz) | GDPR-intersecting incidents |
| EU | ENISA (European Union Agency for Cybersecurity) | Cross-border / critical incidents |

---

## CLI Reference

```bash
# List open incidents
corvin-incident list --status open

# Show full record (including description)
corvin-incident show <incident_id>

# Update status
corvin-incident update <incident_id> --status contained

# Generate notification draft
corvin-incident notify-draft <incident_id> \
  --authority BSI \
  --operator-name "Acme GmbH" \
  --output notification-<incident_id>.md

# Mark as notified
corvin-incident update <incident_id> \
  --status notified \
  --notified-at "2026-08-15T14:00:00Z"

# Close
corvin-incident close <incident_id>

# Batch scan for latent issues
corvin-incident scan --since 30

# Export all incidents for audit package
corvin-incident export --output incidents.json
```

---

## Related Documents

- `docs/compliance/RISK-CLASSIFICATION.md` — what is and isn't a "serious" incident scope
- `docs/compliance/OPERATOR-OBLIGATIONS.md` — Art. 28-30 pre-deployment checklist
- `docs/compliance/DPIA-TEMPLATE.md` — Data Protection Impact Assessment
- `docs/claude-ref/` — L39 Incident Tracker architectural specification
