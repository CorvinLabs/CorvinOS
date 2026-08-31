# Audit chain — tamper-evident record keeping

> The hash-chained audit log is the foundation of Corvin compliance. This document
> explains how it works, why it's tamper-evident, what it records, and how to verify it.

---

## Mental model

The audit chain is not a compliance layer on top of Corvin. It **is** Corvin's
operational record. Every regulatory mechanism (disclosure, consent, classification, incident)
emits into it as part of doing its job. The chain and the compliance are the same thing.

```
User message arrives
    → L19 emits disclosure.shown          → chain entry #N+1
    → L16 consent validated               → chain entry #N+2
    → Engine spawned, response generated
    → L34 data_flow.approved              → chain entry #N+3
    → Response delivered
                                          ↓
                            audit.jsonl:  … ← #N ← #N+1 ← #N+2 ← #N+3 ← …
```

The chain grows continuously. An auditor — human or automated — can verify the entire
history with one command.

---

## Storage

```
<corvin_home>/global/forge/audit.jsonl               ← single-operator
<corvin_home>/tenants/<tid>/global/forge/audit.jsonl  ← per-tenant
```

- Append-only JSONL: one JSON object per line
- File permissions: enforced by path-gate (Write to `audit.jsonl` is blocked)
- Concurrent writes: in-process `threading.Lock` + filesystem `fcntl.flock(LOCK_EX)`

---

## Per-record structure

```jsonc
{
  "ts":           1778204500.2,        // unix epoch (float)
  "event_type":   "consent.granted",   // canonical event type
  "severity":     "INFO",              // INFO | WARNING | ERROR | CRITICAL
  "run_id":       "",                  // run UUID when tied to a forge run; else ""
  "tool":         "consent",           // emitter identity
  "details":      { … },               // event-specific allow-listed fields
  "prev_hash":    "9a3c7b1f…",        // hash of previous record (16 hex chars)
  "hash":         "7b1fa83c…",         // sha256(prev_hash ‖ canonical_json)[:16]
}
```

---

## Hash computation

```
hash = sha256(prev_hash ‖ canonical_json(record))[:16]
```

Where:
- `prev_hash` is the `hash` field of the immediately preceding record
- `canonical_json(record)` is `json.dumps(record, sort_keys=True, separators=(",", ":"))` — deterministic
- First record: `prev_hash = "0" * 16`
- The hash is truncated to 16 hex characters for readability; full SHA-256 prevents collisions

**Why this works for tamper detection:**

Modifying any field of any record changes that record's canonical JSON → changes its hash →
breaks the `prev_hash` link of the next record → detectable at that offset by `verify_chain()`.

Even whitespace changes are detected. Even reordering fields (canonical form normalizes them).

---

## Concurrent write safety

Two processes write to the same chain: the bridge adapter and the forge MCP server.

Protocol:

```python
with threading.Lock():               # in-process serialization
    with fcntl.flock(LOCK_EX):       # cross-process serialization
        prev = read_last_hash(path)  # re-read AFTER taking flock
        record["prev_hash"] = prev
        record["hash"] = compute_hash(prev, record)
        append(path, record)
```

The key detail: `prev_hash` is re-read **after** acquiring the flock. A concurrent writer
that commits between our read and our lock doesn't break the chain — we read its output.

---

## Event severity catalog

Every event type is registered in `forge/security_events.py::_VOICE_EVENT_SEVERITY`.
This dict is the single source of truth. Selecting events by category:

### Compliance gate events (always CRITICAL or WARNING)

| Event | Severity | Module |
|---|---|---|
| `path_gate.denied` | CRITICAL | L10 |
| `path_gate.self_test_failed` | CRITICAL | L10 |
| `audit.chain_gap_detected` | CRITICAL | L16 |
| `data_flow.blocked` | CRITICAL | L34 |
| `egress.blocked` | CRITICAL | L35 |
| `incident.opened` | CRITICAL (if serious) | L39 |
| `consent.toctou_drop` | WARNING | L16 |
| `consent.store_corrupted` | CRITICAL | L16 |
| `consent.gate_unavailable_drop` | WARNING | L16 |
| `erasure.user_model_deleted` | INFO | L36/L28.2 |

### Lifecycle events (INFO)

| Event | Module |
|---|---|
| `disclosure.shown` | L19 |
| `disclosure.joined` | L19 |
| `consent.granted` | L16 |
| `consent.revoked` | L16 |
| `consent.observer_dropped` | L16 |
| `data_flow.approved` | L34 |
| `egress.approved` | L35 |
| `incident.status_changed` | L39 |
| `incident.closed` | L39 |
| `operator.declaration_verified` | Operator Declaration |
| `compliance.manifest_check` | Compliance Manifest |
| `auth.elevation_lockout_started` | L16 |
| `auth.elevation_lockout` | L16 |

---

## Audit chain verification

### Manual

```bash
voice-audit verify                     # default chain
voice-audit verify --tenant acme       # specific tenant chain
voice-audit verify --since-days 7      # only last 7 days
```

Exit code 0 = chain intact. Exit code 1 = gap detected (also emits `audit.chain_gap_detected`).

### Automated (daily systemd timer)

The `corvin-audit-verify.timer` runs daily at 04:30 UTC via `corvin-audit-verify.service`.
On failure:
1. Exit code 1 propagates to systemd (service is marked failed)
2. `audit.chain_gap_detected` (CRITICAL) emitted out-of-band (`hash_chain=False`)
3. Bridge notification forwarded to operator

### At boot

`bridge.sh doctor` calls `voice-audit verify` as a CRITICAL check. A broken chain = unhealthy
container.

---

## The one exception: `hash_chain=False`

The daily verify timer itself emits `audit.chain_gap_detected` when it finds a break.
This event is written with `hash_chain=False` — it does not link to the broken chain,
because the chain is broken and linking would be recursive nonsense.

This is the only authorized use of `hash_chain=False`. Everything else is `hash_chain=True`
(the default).

---

## Audit segments, encryption, and retention (Layer 37)

### Segment lifecycle

```
┌──────────────────────────────────────────────────────┐
│  audit.jsonl   ← live, append-only                   │
│  (grows until max_size_mb or max_age_days triggered)  │
└──────────────────┬───────────────────────────────────┘
                   │  rotate_segment()
                   ▼
┌──────────────────────────────────────────────────────┐
│  audit.2026-05-26T030000.jsonl                        │
│  (rotated; audit.rotation_link written to new live)   │
│  Fail-closed: if rotation_link write fails,           │
│  rotate_and_seal() raises RuntimeError; the           │
│  rotation is aborted.                                 │
└──────────────────┬───────────────────────────────────┘
                   │  seal_segment()
                   ▼
┌──────────────────────────────────────────────────────┐
│  audit.2026-05-26T030000.jsonl.age                    │
│  (AES-256-GCM sealed; plaintext securely overwritten) │
│  chmod 0444                                           │
└──────────────────────────────────────────────────────┘
```

### Chain continuity across rotation

The `audit.rotation_link` event is the first entry in the new live `audit.jsonl`.
Its `prev_hash` equals the `hash` of the last entry in the rotated segment.

This means the chain is continuous across segment boundaries. A full historical verify
requires unseal → verify each segment → confirm hash continuity at boundaries.

### Encryption configuration

```yaml
spec:
  audit:
    encryption_at_rest:
      enabled: true
      recipient: "age1xyz…"    # age recipient public key
      sealer_cmd: age          # "age" | "gpg" | custom binary
      tsa_enabled: false       # RFC 3161 timestamp authority (optional)
    retention_years: 7
    rotation:
      max_size_mb: 100
      max_age_days: 30
```

### Retention enforcement

`enforce_retention()` deletes sealed segments older than `retention_years`.
Before deletion: `audit.segment_retired` (INFO) emitted into the live chain.

### Operator unseal

```bash
voice-audit unseal <segment>
```

This emits `audit.unseal_requested` (WARNING) into the live chain **before** decrypting.
The request itself is audited. The decrypted segment is written to a temp path (mode 0600).

---

## PII protection in the audit chain

The audit chain is designed so it can be exported to a regulator or security auditor
without exposing personal data. Every event type's `details` field is defined by an
explicit allow-list:

| Information type | What's in chain | What's NOT in chain |
|---|---|---|
| User identity | `uid_hash` (sha256[:8]) — including all `consent.*` events | Raw uid, email, name |
| Consent events | mode, ttl_s, channel | Conversation content |
| Voice transcription | provider, lang, audio_s, char_count | Transcript text |
| Data flow | classification, engine_id, reason | Task text, prompt |
| Incidents | incident_id, category, trigger_chain_hash | Description text |
| Operator declaration | declaration_version, dpia_date, profile | declared_by, permitted_use |
| Forge runs | run_id, tool name, sha256 prefix | Tool source code, output |

This means `voice-audit verify` (and any chain export) is GDPR-safe by design.

---

## For a forensic audit

To reconstruct the compliance history for a date range:

1. **Identify segments** covering the range:
   ```bash
   ls <tenant>/global/forge/audit.*.jsonl.age
   ```

2. **Unseal each segment** (emits audit trail of the unseal):
   ```bash
   voice-audit unseal audit.2026-03-01T030000.jsonl.age
   ```

3. **Verify segment chain**:
   ```bash
   voice-audit verify --file /tmp/audit.2026-03-01T030000.jsonl
   ```

4. **Filter for compliance events**:
   ```bash
   jq 'select(.event_type | startswith("disclosure","consent","data_flow","egress","incident"))' \
     /tmp/audit.2026-03-01T030000.jsonl
   ```

5. **Verify chain continuity across segments** (last hash of N == first prev_hash of N+1):
   ```bash
   jq -s '[-1] | .hash' segment_N.jsonl
   jq -s '[0] | .prev_hash' segment_N_plus_1.jsonl
   ```

The entire history is verifiable offline without any running Corvin instance.
