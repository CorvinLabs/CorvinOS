# Audit Trail: Immutable Event Logging

**The Audit Trail is CorvinOS's proof system.** Every Skill decision, every feedback signal, every system action is logged immutably in a cryptographically-chained event log. This enables:

- **GDPR Art. 30, 32:** Proof of what happened, when, and by which code
- **EU AI Act Art. 50:** Transparent record of AI decisions
- **Operator Proof:** "Show me what this system did" → full audit trail
- **Compliance Defense:** Auditor can verify no tampering

This guide explains how auditing works, how to query it, and what guarantees it provides.

---

## Why Audit Matters

### Regulatory Requirements

| Regulation | Requirement | CorvinOS Mechanism |
|---|---|---|
| **GDPR Art. 5** | Integrity + confidentiality of personal data | Hash-chained events, tenant isolation |
| **GDPR Art. 30** | Document processing (what, who, when) | Audit event with skill_id, timestamp, tenant_id |
| **GDPR Art. 32** | Audit trail of changes | Every Skill decision logged; immutable chain |
| **EU AI Act Art. 50** | AI-nature disclosure + opt-out | Audit shows disclosure decision, user consent |

### Operational Benefits

- **Debugging:** "Why was this request routed to Opus?" → Trace Skill decisions
- **Security:** "Was this data flow allowed?" → Audit shows decision + reason
- **Performance:** "How long did Skill X take?" → Latency in every audit event
- **Learning:** "Did this Skill improve?" → Track confidence scores over time

---

## Audit Event Schema

Every event in the audit trail is immutable and hash-chained:

```json
{
  "tenant_id": "_default",                           // Tenant isolation (GDPR Art. 5)
  "timestamp": "2026-09-02T14:30:45.123Z",          // ISO 8601, UTC
  "event_type": "skill_executed",                    // What happened?
  "event_id": "evt_abc123def456",                    // Unique ID
  "skill_id": "os.delegation_router",                // Which Skill?
  "skill_version": "2.0.1",                          // Version matters for trace
  "input": {                                         // What did the Skill receive?
    "request": "summarize this article",
    "complexity_estimate": 0.72
  },
  "output": {                                        // What did it return?
    "route_to": "opus",
    "confidence": 0.88,
    "reasoning": "complexity > threshold"
  },
  "latency_ms": 42,                                  // How fast?
  "lom": "core/skills/os_skills/router.py:L237",    // Line of Moral Responsibility
  "lom_hash": "sha256(...)",                         // Proves which code executed
  "hash": "sha256(...)",                             // This event's hash
  "prev_hash": "sha256(...)",                        // Links to previous event
  "user_id": "user_xyz",                             // (optional) Which user?
  "tags": ["routing", "complexity", "learning"]      // (optional) For filtering
}
```

### Required vs. Optional Fields

| Field | Type | Required | Purpose |
|---|---|---|---|
| **tenant_id** | string | ✅ YES | GDPR isolation; fail-closed if missing |
| **timestamp** | ISO 8601 | ✅ YES | When did it happen? |
| **event_type** | string | ✅ YES | skill_executed, skill_feedback, audit_chain_verified, etc. |
| **hash** | hex | ✅ YES | This event's SHA256 |
| **prev_hash** | hex | ✅ YES | Chain to previous event |
| **skill_id** | string | ⚠️ CONDITIONAL | Required if event_type is skill_* |
| **lom** | file:line | ⚠️ CONDITIONAL | Required if LoM binding enabled |
| **user_id** | string | ❌ OPTIONAL | User identifier (scrubbed if PII) |
| **tags** | string[] | ❌ OPTIONAL | For filtering / querying |

---

## Event Types

### Skill Events

```
skill_loaded
  - Fired when: Skill registered and loaded
  - Contains: skill_id, version, dependencies, boot_layer
  - Audit: Prove which Skill code is running

skill_executed
  - Fired when: Skill.execute() completes
  - Contains: input, output, latency, confidence
  - Audit: Prove what the Skill did and how long

skill_feedback
  - Fired when: User provides feedback
  - Contains: feedback_type (outcome/preference/confidence/metric), signal
  - Audit: Track learning loop

skill_config_updated
  - Fired when: Optimizer tunes parameters
  - Contains: param_name, old_value, new_value, confidence_delta
  - Audit: Prove the Skill improved; track tuning history
```

### Consent + Compliance Events

```
consent_granted
  - Fired when: User grants consent (start of session)
  - Contains: consent_type, scope, ttl
  - Audit: GDPR Art. 6, 7 — prove consent was given

consent_checked
  - Fired when: System checks user consent before action
  - Contains: consent_type, decision (allowed/denied)
  - Audit: Prove decision respected user choice

house_rule_denied
  - Fired when: House-Rules gate blocks an action
  - Contains: rule_id, reason, user_intent
  - Audit: Unambiguous denial; can't be overridden
```

### Audit Events

```
audit_chain_verified
  - Fired when: Boot tripwire verifies chain integrity
  - Contains: chain_height, last_hash, verification_result
  - Audit: Prove the entire audit chain is valid

audit_key_rotated
  - Fired when: Audit signing key rotated (security)
  - Contains: old_key_id, new_key_id, rotation_time
  - Audit: Track key lifecycle
```

### Learning Events

```
learning_event_received
  - Fired when: Feedback event processed
  - Contains: skill_id, feedback_type, signal
  - Audit: Track learning loop

optimizer_config_updated
  - Fired when: Optimizer tunes Skill parameters
  - Contains: skill_id, param_delta, confidence_before/after
  - Audit: Prove optimization happened
```

---

## Hash-Chaining: Immutable Proof

```svg
<svg viewBox="0 0 900 550" xmlns="http://www.w3.org/2000/svg">
  <!-- Background -->
  <rect width="900" height="550" fill="#F9FAFB"/>
  
  <!-- Title -->
  <text x="450" y="30" font-size="20" font-weight="bold" text-anchor="middle" fill="#1F2937">
    Hash-Chain: Immutable Proof of Integrity
  </text>
  
  <!-- Event boxes -->
  <g id="event1">
    <rect x="50" y="70" width="200" height="120" rx="4" fill="#DBEAFE" stroke="#3B82F6" stroke-width="2"/>
    <text x="150" y="90" font-size="10" font-weight="bold" text-anchor="middle" fill="#1E40AF">Event #1</text>
    <text x="60" y="110" font-size="9" fill="#0C2340">event_type: skill_executed</text>
    <text x="60" y="125" font-size="9" fill="#0C2340">skill_id: os.router</text>
    <text x="60" y="140" font-size="9" fill="#0C2340">timestamp: 2026-09-02T14:30</text>
    <text x="60" y="155" font-size="9" fill="#0C2340" font-family="monospace">hash: a7f3c...</text>
    <text x="60" y="170" font-size="9" fill="#0C2340" font-family="monospace">prev_hash: 0000...</text>
  </g>
  
  <!-- Arrow 1 -->
  <path d="M 250 130 L 310 130" stroke="#6B7280" stroke-width="2" fill="none" marker-end="url(#arrowhead)"/>
  <text x="280" y="120" font-size="9" fill="#6B7280">links via</text>
  <text x="280" y="135" font-size="9" fill="#6B7280">prev_hash</text>
  
  <!-- Event boxes -->
  <g id="event2">
    <rect x="310" y="70" width="200" height="120" rx="4" fill="#DCFCE7" stroke="#10B981" stroke-width="2"/>
    <text x="410" y="90" font-size="10" font-weight="bold" text-anchor="middle" fill="#065F46">Event #2</text>
    <text x="320" y="110" font-size="9" fill="#022C1A">event_type: skill_feedback</text>
    <text x="320" y="125" font-size="9" fill="#022C1A">feedback: correct=true</text>
    <text x="320" y="140" font-size="9" fill="#022C1A">timestamp: 2026-09-02T14:31</text>
    <text x="320" y="155" font-size="9" fill="#022C1A" font-family="monospace">hash: b4e8d...</text>
    <text x="320" y="170" font-size="9" fill="#022C1A" font-family="monospace">prev_hash: a7f3c...</text>
  </g>
  
  <!-- Arrow 2 -->
  <path d="M 510 130 L 570 130" stroke="#6B7280" stroke-width="2" fill="none" marker-end="url(#arrowhead)"/>
  <text x="540" y="120" font-size="9" fill="#6B7280">links via</text>
  <text x="540" y="135" font-size="9" fill="#6B7280">prev_hash</text>
  
  <!-- Event boxes -->
  <g id="event3">
    <rect x="570" y="70" width="200" height="120" rx="4" fill="#FEF3C7" stroke="#F59E0B" stroke-width="2"/>
    <text x="670" y="90" font-size="10" font-weight="bold" text-anchor="middle" fill="#92400E">Event #3</text>
    <text x="580" y="110" font-size="9" fill="#5B4B08">event_type: skill_config_updated</text>
    <text x="580" y="125" font-size="9" fill="#5B4B08">threshold: 0.50 → 0.65</text>
    <text x="580" y="140" font-size="9" fill="#5B4B08">timestamp: 2026-09-02T14:32</text>
    <text x="580" y="155" font-size="9" fill="#5B4B08" font-family="monospace">hash: c2f1b...</text>
    <text x="580" y="170" font-size="9" fill="#5B4B08" font-family="monospace">prev_hash: b4e8d...</text>
  </g>
  
  <!-- Explanation section -->
  <rect x="50" y="230" width="800" height="280" rx="4" fill="#F3F4F6" stroke="#D1D5DB" stroke-width="1"/>
  
  <text x="70" y="255" font-size="12" font-weight="bold" fill="#1F2937">How Hash-Chaining Works</text>
  
  <text x="70" y="280" font-size="11" fill="#374151">1️⃣  Each event is hashed using SHA256</text>
  <text x="90" y="298" font-size="10" fill="#6B7280">hash(event_type, skill_id, timestamp, input, output, ...)</text>
  <text x="90" y="313" font-size="10" fill="#6B7280">Example: SHA256("skill_executed|os.router|2026-09-02T14:30|...") = a7f3c...</text>
  
  <text x="70" y="338" font-size="11" fill="#374151">2️⃣  Each new event includes the previous event's hash (prev_hash)</text>
  <text x="90" y="356" font-size="10" fill="#6B7280">Event #2 contains: prev_hash = a7f3c (Event #1's hash)</text>
  <text x="90" y="371" font-size="10" fill="#6B7280">Event #3 contains: prev_hash = b4e8d (Event #2's hash)</text>
  
  <text x="70" y="396" font-size="11" fill="#374151">3️⃣  If anyone tampers with Event #1, its hash changes</text>
  <text x="90" y="414" font-size="10" fill="#6B7280">But Event #2 still references the OLD hash → chain breaks</text>
  <text x="90" y="429" font-size="10" fill="#6B7280">Tampering is detected immediately</text>
  
  <text x="70" y="454" font-size="11" fill="#374151">4️⃣  To tamper with multiple events and hide the change...</text>
  <text x="90" y="472" font-size="10" fill="#6B7280">Attacker would need to re-hash ALL subsequent events</text>
  <text x="90" y="487" font-size="10" fill="#6B7280">But signatures (RFC 3161) and timestamps prove nothing changed</text>
  
  <!-- Verification result -->
  <rect x="50" y="510" width="800" height="30" rx="4" fill="#DCFCE7" stroke="#10B981" stroke-width="2"/>
  <text x="450" y="532" font-size="11" font-weight="bold" text-anchor="middle" fill="#065F46">
    ✅ Chain Integrity Verified: 142857 events, all hashes linked, 0 tampering detected
  </text>
  
  <!-- Arrow marker -->
  <defs>
    <marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <polygon points="0 0, 10 3, 0 6" fill="#6B7280"/>
    </marker>
  </defs>
</svg>
```

### Verification

Verify the chain is intact:

```bash
corvin audit verify-chain --tenant=_default
# Output:
# Chain height: 142857 events
# Last event: 2026-09-02T22:45:30.123Z
# Verification: ✅ PASS
# - All hashes linked correctly
# - No gaps or orphaned events
# - 0 tampering attempts detected
# - Signatures valid (RFC 3161 TSA)
```

---

## Tenant Isolation (GDPR Art. 5)

**Absolute rule:** No Skill decision or audit event can cross tenant boundaries.

```python
# Example: Query audit events
result = audit_query(
    tenant_id="_default",
    event_type="skill_executed",
    skill_id="os.delegation_router"
)

# ✅ CORRECT: Explicit tenant_id, no cross-tenant leakage

# ❌ WRONG: Querying without tenant_id → DENIED
result = audit_query(event_type="skill_executed")
# Error: tenant_id is required; fail-closed
```

### Audit Trail Structure

```
~/.corvin/audit/
├── tenants/
│   ├── _default/
│   │   ├── 2026/
│   │   │   ├── 09/  (September)
│   │   │   │   ├── 01.jsonl  (partition by date)
│   │   │   │   ├── 02.jsonl
│   │   │   │   └── ...
│   │   │   └── 08/
│   │   └── verification/
│   │       └── chain_verification_2026_09_02.json
│   └── tenant_xyz/
│       ├── 2026/
│       └── verification/
└── metadata.json
```

Each tenant has its own audit partition. No mixing.

---

## LoM Binding: Proving Code Identity

**LoM** = "Line of Moral Responsibility" — the exact line of code that made a decision.

```python
# core/skills/os_skills/router.py, line 237
if estimated_complexity > config["threshold"]:
    route = "opus"
else:
    route = "haiku"

# ⚠️ This decision (line 237) will appear in audit as:
# "lom": "core/skills/os_skills/router.py:L237"
```

### LoM Binding: Cryptographic Proof

When a Skill loads, its code is hashed. Every LoM reference includes that hash:

```json
{
  "event_type": "skill_executed",
  "skill_id": "os.delegation_router",
  "skill_version": "2.0.1",
  "lom": "core/skills/os_skills/router.py:L237",
  "lom_hash": "sha256(...)",  // Hash of entire router.py file at v2.0.1
  "hash": "sha256(...)"
}
```

**What this proves:**
- ✅ The decision was made by line 237 of router.py
- ✅ The code at that line is version 2.0.1 (proven by lom_hash)
- ✅ No one can claim a decision came from different code

**Verification:**
```bash
corvin audit verify-lom --event <event_id>
# Output: LoM proven valid for event_id_xyz
# - Code file: core/skills/os_skills/router.py
# - Line: 237
# - Version: 2.0.1
# - Binding hash: matches known version
```

---

## Boot Tripwire: Startup Verification

On system boot, before any Skill runs:

```
┌─ Boot ─────────────────────────────┐
│                                    │
│ 1. Initialize audit backend        │ ← Connect to audit store
│ 2. Fetch last 10 events            │ ← Get recent history
│ 3. Verify hash chain (N→N-1→...)   │ ← Check integrity
│ 4. If mismatch: EXIT 1             │ ← FAIL-CLOSED
│ 5. If audit unreachable: EXIT 1    │ ← FAIL-CLOSED
│ 6. Continue to load Skills         │ ← Only after verified
│                                    │
└────────────────────────────────────┘
```

If boot tripwire fails, the system **will not start**. This ensures every Skill decision is auditable from Day 1.

---

## Querying the Audit Trail

### Get All Events for a Task

```bash
corvin audit show-task <task_id>
# Output: JSON array of all events related to this task
```

### Filter by Skill

```bash
corvin audit filter --skill os.delegation_router --limit 100
# Output: Last 100 events from this Skill
```

### Filter by Event Type

```bash
corvin audit filter --event-type skill_feedback --since 2026-09-01
# Output: All feedback events since Sept 1
```

### Trace Skill Composition Chain

```bash
corvin audit trace-skill os.delegation_router --task <task_id>
# Output: Full call chain
# - os.delegation_router called
#   └─ classify_content called
#   └─ estimate_complexity called
# (show latency, input, output for each)
```

### Export for Compliance

```bash
corvin audit export \
  --tenant=_default \
  --format=pdf \
  --since=2026-09-01 \
  --until=2026-09-30 \
  --events=skill_executed,consent_granted,house_rule_denied
# Output: compliance-report-2026-09.pdf (auditor-ready with signatures)
```

---

## Compliance Workflow

### GDPR Data Subject Access Request

**User asks:** "Show me everything you did with my data."

**Process:**
```bash
# 1. Filter events by user_id
corvin audit filter --user-id <user_id> --tenant <tenant_id>

# 2. Extract relevant events (consent, data_flow, skill_decisions)
corvin audit export --user-id <user_id> --format=json

# 3. Remove non-PII (system-internal events)
# 4. Provide to user in portable format (JSON, PDF)
```

### EU AI Act: Transparency Report

**Operator asks:** "Prove this system disclosed AI-nature to users."

**Process:**
```bash
# 1. Show disclosure card events
corvin audit filter --event-type disclosure_shown --since 2026-08-01

# 2. Show opt-out events
corvin audit filter --event-type user_opted_out --since 2026-08-01

# 3. Generate report with audit signature
corvin audit export --events=disclosure_shown,user_opted_out --format=pdf
```

---

## Failure Modes

### Scenario: Audit Backend Unreachable

**What happens:**
1. Skill tries to log event
2. Audit backend doesn't respond (timeout: 5s)
3. Event queued to disk: `~/.corvin/audit/queue/`
4. Skill continues (non-blocking)
5. When backend recovers, queue is flushed

**Guarantee:** No Skill decision is lost; all events eventually reach audit trail.

### Scenario: Hash Chain Broken

**What happens:**
1. Boot tripwire detects mismatch
2. Audit chain file is corrupted/tampered
3. System STOPS immediately (exit 1)
4. Operator manually investigates + runs recovery

**Guarantee:** Tampered audit is detected instantly; system fails safely.

---

## Audit Best Practices

1. **Verify chain daily:**
   ```bash
   corvin audit verify-chain --tenant=_default
   ```

2. **Monitor size** (audit grows ~500KB/day):
   ```bash
   du -sh ~/.corvin/audit/
   ```

3. **Archive old events** (>90 days, see ADR-0319):
   ```bash
   corvin audit archive --before=2026-06-01 --output=/backups/audit-archive.tar.gz
   ```

4. **Rotate signing key** (annually):
   ```bash
   corvin audit rotate-key --new-key-id=key_2027_001
   ```

---

## See Also

- **[Skills System](skills-system.md)** — How Skill decisions happen
- **[Learning Loop](learning-loop.md)** — How feedback is logged
- **[Deployment Guide](deployment-guide.md)** — Using audit to verify safe rollouts
- **[ADR-0232, 0233](https://github.com/CorvinLabs/Corvin-ADR/decisions/)** — Boot tripwire, audit chain integrity

---

**The audit trail is CorvinOS's proof system. Every decision is logged, immutable, and verifiable. Every operator can see exactly what the system did and defend its behavior.**
