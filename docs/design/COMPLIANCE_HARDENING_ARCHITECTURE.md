# ADR-0XXX: Compliance Hardening — Non-Negotiable Security Architecture
## EU AI Act 2026 + GDPR Structural Guarantees (Cannot Be Disabled)

**Date:** 2026-07-26
**Status:** Critical architecture constraint. Long-form study; **canonical decision:
ADR-0232** (`Corvin-ADR` repo), inherited by **ADR-0233** for the plugin surface.
**Related to:** ADR-0231 (Compartmentalization), `docs/claude-ref/compliance-baseline.md`
**Stakeholders:** Legal, Security, Ops, Engineering

> This document is adopted as written. ADR-0233 applies its mandatory-vs-extensible
> split to plugins: an installed backend may only *add* a sink or a rule, and
> `core/compliance/tripwire.py` fails the boot closed if the mandatory core audit
> writer is unreachable or its chain does not verify.

---

## Core Principle: Fail-Closed, Not Fail-Open

**CorvinOS must NOT have a "compliance off" mode.**

Every regulatory requirement is **baked into the platform structure**, not a toggle:
- ✅ Bot disclosure card (users always told "you're talking to AI")
- ✅ Audit trail hash-chain (operations always logged, never modifiable)
- ✅ Per-user consent gates (deny-by-default, explicit opt-in required)
- ✅ Path-gate (filesystem operations gated, fail-closed)
- ✅ Flow guard (data classification enforced, fail-closed)
- ✅ House-rules gate (acceptable-use enforced, fail-closed)
- ✅ Erasure orchestrator (GDPR Art. 17 automation)

**No feature flag. No env var kill-switch. No "compliance mode off" setting.**

---

## Distinction: Mandatory vs. Extensible

### Mandatory (NEVER Pluginified, NEVER Optional)

These are **core load-bearing constraints** baked into the platform:

| Layer | Mechanism | Regulation | Why Non-Negotiable |
|-------|-----------|-----------|-------------------|
| **L1** | HTTP/Request Validation | EU AI Act Art. 5 | Prevents malicious input before it enters system |
| **L10** | Path-Gate (FS-write protection) | GDPR Art. 32 | Prevents unauthorized data writes (fail-closed) |
| **L16** | Audit Trail (hash-chain base) | GDPR Art. 30, 32 | Immutable record of operations |
| **L18-21** | Consent Gate (deny-by-default) | GDPR Art. 6, 7 | Users must opt-in; can't process without consent |
| **L34** | Flow Guard (data classification) | GDPR Art. 32 | Prevents PII → untrusted destinations |
| **L44** | House Rules (acceptable-use gate) | EU AI Act Art. 5, 50 | Blocks malicious/harmful use before execution |
| **L36** | Erasure Orchestrator | GDPR Art. 17 | Automated "right to be forgotten" |
| **L37** | Audit Encryption (at-rest) | GDPR Art. 32 | Encrypts audit trail, RFC 3161 timestamping |

**Failure mode: Any of these broken → platform SHUTS DOWN (fail-closed).**

### Extensible (CAN Be Enhanced, But Core Unchanged)

These are **implementation details** that can be extended:

| Component | What's Fixed | What's Extensible |
|-----------|-------------|------------------|
| **L16 Audit Backend** | Hash-chain algorithm (SHA-256), immutability contract | New backends (file/DB/external), retention policies |
| **L18 Consent Gate** | Deny-by-default logic, TTL enforcement | Consent UI (email, SMS, voice), consent storage backend |
| **L34 Flow Guard** | Classifier (PII detection), fail-closed default | Data destination whitelist, classification algorithms |
| **L44 House Rules** | Gate logic (block/allow), confidence threshold | Rule definitions, exception handling |
| **L36 Erasure** | Automation (find+delete), audit logging | Erasure strategies, notification backends |

**Example: Add new PII classifier**
```python
# Extensible: Add new data type classification
class HistoricalPIIClassifier(Classifier):
    """Detect historical PII (maiden names, birth cities)."""
    def classify(self, text: str) -> Classification:
        # New logic
        return Classification(pii_score=0.95, pii_types=["historical_pii"])

# Then register with Flow Guard
flow_guard.register_classifier(HistoricalPIIClassifier())
```

**Result:** House Rules gate still blocks PII; you just added a new type.

---

## Architecture: Mandatory Core + Extensible Layers

```
┌────────────────────────────────────────────────────────┐
│         MANDATORY COMPLIANCE CORE (Immutable)          │
├────────────────────────────────────────────────────────┤
│                                                        │
│  ┌──────────────────────────────────────────────────┐ │
│  │ L1 Input Validation (fail-closed)                 │ │
│  │ L10 Path-Gate (filesystem writes blocked)         │ │
│  │ L16 Audit Trail (hash-chain, immutable)           │ │
│  │ L18 Consent Gate (deny-by-default)                │ │
│  │ L34 Flow Guard (data classification, fail-closed) │ │
│  │ L44 House Rules (acceptable-use gate)             │ │
│  │ L36 Erasure Orchestrator (GDPR Art. 17)           │ │
│  │ L37 Audit Encryption (at-rest)                    │ │
│  │                                                    │ │
│  │ >>> Can NOT be disabled, toggled, or bypassed     │ │
│  │ >>> Failure → platform SHUTS DOWN                 │ │
│  └──────────────────────────────────────────────────┘ │
│                                                        │
└────────────────────────────────────────────────────────┘
           ↑ (Extensibility Boundary)
           │
           ├─ Audit backends (file, DB, syslog)
           ├─ Consent backends (email, SMS, voice)
           ├─ Data classifiers (PII, PHI, financial)
           ├─ House rules definitions
           └─ Erasure strategies
           
           (All extensions still fail-closed if broken)
```

---

## Mandatory Mechanism: L16 Audit Trail (Cannot Be Disabled)

### Core Guarantee (Hardcoded)

```python
# core/audit/audit_core.py — THIS CODE CANNOT BE REMOVED

class MandatoryAuditTrail:
    """Immutable audit trail. Can never be disabled."""
    
    def __init__(self, audit_path: Path):
        self.audit_path = audit_path
        self._hash_chain = "0" * 64  # Initial hash
    
    def log_event(self, event_type: str, details: dict) -> None:
        """MUST be called. Cannot fail silently."""
        
        # 1. Hash-chain: compute hash of previous + this event
        event_json = json.dumps(details, sort_keys=True)
        this_hash = hashlib.sha256(
            (self._hash_chain + event_json).encode()
        ).hexdigest()
        
        # 2. Write immutably
        event_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "event_type": event_type,
            "details": details,
            "hash_chain": this_hash,
        }
        
        # Write + fsync (force to disk immediately)
        with open(self.audit_path, 'a') as f:
            f.write(json.dumps(event_record) + "\n")
            os.fsync(f.fileno())  # Force write to disk
        
        # 3. Update local hash
        self._hash_chain = this_hash
    
    def verify_chain(self) -> bool:
        """Verify audit trail integrity (no tampering)."""
        previous_hash = "0" * 64
        
        with open(self.audit_path, 'r') as f:
            for line in f:
                record = json.loads(line)
                event_json = json.dumps(record["details"], sort_keys=True)
                expected_hash = hashlib.sha256(
                    (previous_hash + event_json).encode()
                ).hexdigest()
                
                if record["hash_chain"] != expected_hash:
                    return False  # Chain corrupted!
                
                previous_hash = record["hash_chain"]
        
        return True
```

**Key properties:**
- ✅ No feature flag (hardcoded in core)
- ✅ No env var kill-switch (code doesn't check env vars)
- ✅ No plugin-based (not pluginifiable)
- ✅ Fail-closed: If audit write fails → exception → platform crashes (doesn't silently lose events)

### Extension: Custom Audit Backends (But Core Guaranteed)

```python
# NEW: Extensible audit backend plugins (but core requirements always met)

class AuditBackendPlugin(Plugin):
    """Custom audit storage (file, DB, cloud) but core hash-chain MUST work."""
    
    async def write_event(self, event_record: dict) -> None:
        """Write audit event. MUST NOT lose data."""
        # Custom implementation (file, DB, Postgres, etc.)
        # But must preserve hash_chain field
    
    async def verify_chain(self) -> bool:
        """Verify immutability. MUST validate hash-chain."""
        # Custom verification (file integrity, DB constraints, etc.)
        # Core hash-chain check non-negotiable
```

**Example: Add Postgres audit backend**
```python
class PostgresAuditBackend(AuditBackendPlugin):
    """Store audit trail in Postgres."""
    
    async def write_event(self, event_record: dict) -> None:
        # Preserve hash_chain (core requirement)
        # Add to Postgres with SERIAL constraint (immutable)
        await db.execute("""
            INSERT INTO audit_log 
            (timestamp, event_type, hash_chain, details)
            VALUES (?, ?, ?, ?)
        """, (event_record["timestamp"], event_record["event_type"],
              event_record["hash_chain"], json.dumps(event_record)))
    
    async def verify_chain(self) -> bool:
        # Verify hash-chain (core requirement)
        # Plus: verify DB constraints, backups, etc.
        ...
```

**But the plugin CANNOT:**
- ✅ Remove hash-chain verification
- ✅ Allow modification of logged events
- ✅ Skip logging certain events
- ✅ Disable the audit trail

---

## Mandatory Mechanism: L44 House Rules (Cannot Be Disabled)

### Core Gate (Hardcoded, Fail-Closed)

```python
# core/house_rules/gate_core.py — THIS CODE CANNOT BE REMOVED

class MandatoryHouseRulesGate:
    """Acceptable-use gate. Can never be disabled."""
    
    def __init__(self, confidence_threshold: float = 0.90):
        self.threshold = confidence_threshold
    
    async def evaluate(self, user_input: str) -> bool:
        """MUST be called before LLM execution."""
        
        # Check against banned categories
        # (offensive, illegal, malicious, etc.)
        score = await self._classify(user_input)
        
        if score["confidence"] < self.threshold:
            # Uncertain → DENY (fail-closed)
            raise HouseRulesBlockedError(
                f"Request blocked: {score['reason']}"
            )
        
        if score["is_banned"]:
            # Clearly banned → DENY
            raise HouseRulesBlockedError(
                f"Request violates house rules: {score['reason']}"
            )
        
        return True  # Allowed
    
    async def _classify(self, text: str) -> dict:
        """Classify text. MUST NOT allow bypass."""
        # Call LLM classifier (or ML model, or rules engine)
        # MUST have HIGH confidence before allowing
        ...
```

**Key properties:**
- ✅ No toggle (always runs)
- ✅ No env var bypass (code doesn't check for one)
- ✅ Fail-closed (default deny, must explicitly allow)
- ✅ High confidence required (0.90+)

### Extension: Custom House Rules Definitions (But Core Gate Always Active)

```python
# NEW: Extensible rule definitions (but core gate is mandatory)

class HouseRulesPlugin(Plugin):
    """Define custom house rules (ethical guidelines, org policies)."""
    
    async def classify_request(self, user_input: str) -> dict:
        """Classify request. Must return: is_banned, confidence, reason."""
        # Custom rules engine
        # But classification MUST be fed back to mandatory gate
```

**Example: Add org-specific policies**
```python
class ComplianceHouseRules(HouseRulesPlugin):
    """Org-specific guidelines (financial regs, privacy, etc.)."""
    
    async def classify_request(self, text: str) -> dict:
        # New rules: block requests about proprietary data
        # New rules: block GDPR-sensitive topics
        # New rules: enforce data residency
        
        return {
            "is_banned": True/False,
            "confidence": 0.95,
            "reason": "...",
            "policy_violated": "GDPR_data_residency_EU_only"
        }
```

**But the plugin CANNOT:**
- ✅ Set confidence below 0.90
- ✅ Skip the mandatory gate
- ✅ Silently allow banned requests
- ✅ Disable the gate

---

## Enforcement: Tripwire Assertions

**Every mandatory mechanism has a "tripwire" that crashes the platform if bypassed.**

```python
# core/compliance/tripwire.py

def _assert_audit_safe():
    """Crash if audit trail is broken."""
    # Verify audit file is immutable
    if not os.path.exists(AUDIT_PATH):
        raise RuntimeError("COMPLIANCE VIOLATION: Audit trail missing!")
    
    # Verify hash-chain
    if not verify_audit_chain():
        raise RuntimeError("COMPLIANCE VIOLATION: Audit trail corrupted!")
    
    # Verify recent events logged
    if not has_recent_audit_events():
        raise RuntimeError("COMPLIANCE VIOLATION: Audit trail stale!")

def _assert_consent_safe():
    """Crash if consent gate is broken."""
    if not registry.get("consent-gate"):
        raise RuntimeError("COMPLIANCE VIOLATION: Consent gate missing!")

def _assert_flow_guard_safe():
    """Crash if flow guard is broken."""
    if not registry.get("flow-guard"):
        raise RuntimeError("COMPLIANCE VIOLATION: Flow guard missing!")

def _assert_house_rules_safe():
    """Crash if house rules gate is broken."""
    if not registry.get("house-rules-gate"):
        raise RuntimeError("COMPLIANCE VIOLATION: House rules gate missing!")

# Boot sequence MUST call all tripwires
async def boot():
    _assert_audit_safe()
    _assert_consent_safe()
    _assert_flow_guard_safe()
    _assert_house_rules_safe()
    # ... continue only if all pass
```

**Result:** If someone deletes a mandatory mechanism or disables it, boot fails immediately (fail-closed).

---

## Immutability: Code-Level Guarantees

### Cannot Remove Mandatory Mechanisms

**Python module protection:**
```python
# core/compliance/__init__.py

# These are loaded BEFORE any plugins
# and they are ALWAYS REQUIRED

from .audit_core import MandatoryAuditTrail
from .consent_core import MandatoryConsentGate
from .flow_guard_core import MandatoryFlowGuard
from .house_rules_core import MandatoryHouseRulesGate

# These CANNOT be removed or made optional
__all__ = [
    "MandatoryAuditTrail",
    "MandatoryConsentGate", 
    "MandatoryFlowGuard",
    "MandatoryHouseRulesGate",
]

# Marker for code analysis
__compliance_critical__ = True
```

**Linting rule (pre-commit hook):**
```bash
# hooks/pre-commit

# Fail if anyone tries to:
# 1. Delete compliance files
# 2. Make compliance components pluginifiable
# 3. Add "compliance off" flags
# 4. Add "bypass" env vars

grep -r "COMPLIANCE.*disabled" src/ && exit 1
grep -r "if.*not.*compliance" src/ && exit 1
grep -r "feature_flag.*audit\|consent\|flow_guard\|house_rules" src/ && exit 1

exit 0
```

---

## Extensibility: Safe Plugin Model for Compliance

### What CAN Be Pluginified (with Mandatory Core)

```python
# core/compliance/extensible_model.py

# 1. Audit BACKENDS (storage) but core immutability REQUIRED
class AuditBackend(Plugin):
    # New: support Postgres, S3, etc.
    # But: hash-chain immutability MANDATORY
    async def write_event(self, event: dict) -> None:
        # Custom storage
        pass
    
    async def verify_chain(self) -> bool:
        # Verify hash-chain still works
        pass

# 2. Consent UI BACKENDS (how users consent) but core logic REQUIRED
class ConsentBackend(Plugin):
    # New: email consent, SMS consent, voice consent
    # But: deny-by-default MANDATORY, TTL enforcement MANDATORY
    async def request_consent(self, user_id: str, scope: str) -> bool:
        # Custom UI
        pass

# 3. Data Classifiers (detect PII) but core fail-closed REQUIRED
class DataClassifier(Plugin):
    # New: detect historical PII, industry-specific data
    # But: fail-closed on unknown data MANDATORY
    async def classify(self, text: str) -> Classification:
        # Custom classification
        pass

# 4. House Rules Definitions (org policies) but core gate REQUIRED
class HouseRulesPlugin(Plugin):
    # New: add custom policies (financial regs, GDPR, etc.)
    # But: core gate still enforces confidence threshold
    async def classify_request(self, text: str) -> dict:
        # Custom rules
        pass

# 5. Erasure Strategies (how to delete data) but core automation REQUIRED
class ErasureStrategy(Plugin):
    # New: custom deletion algorithms, notification strategies
    # But: GDPR Art. 17 automation still MANDATORY
    async def erase_user_data(self, user_id: str) -> dict:
        # Custom erasure
        pass
```

### What CANNOT Be Pluginified

```python
# ✗ This is FORBIDDEN

class AuditBackend(Plugin):
    async def write_event(self, event: dict) -> None:
        # CANNOT skip hash-chain
        # CANNOT make immutability optional
        pass

class ConsentGate(Plugin):
    async def should_allow(self, user_id: str) -> bool:
        # CANNOT return True without explicit consent
        # CANNOT skip TTL checks
        pass

class FlowGuard(Plugin):
    async def can_send_to(self, destination: str, data: dict) -> bool:
        # CANNOT skip PII classification
        # CANNOT fail-open (allow unknown data)
        pass
```

---

## Testing: Compliance Tripwire Suite

### Unit Tests (Verify Mechanisms Work)

```python
# core/compliance/tests/test_tripwires.py

def test_audit_tripwire_fires_on_missing_file():
    """If audit file deleted, boot fails."""
    os.remove(AUDIT_PATH)
    with pytest.raises(RuntimeError, match="Audit trail missing"):
        boot()

def test_consent_tripwire_fires_on_disabled_gate():
    """If consent gate disabled, boot fails."""
    registry.unregister("consent-gate")
    with pytest.raises(RuntimeError, match="Consent gate missing"):
        boot()

def test_house_rules_tripwire_fires():
    """If house rules gate removed, boot fails."""
    registry.unregister("house-rules-gate")
    with pytest.raises(RuntimeError, match="House rules gate missing"):
        boot()
```

### Integration Tests (Verify Extensions Don't Bypass)

```python
# core/compliance/tests/test_extensibility.py

async def test_audit_backend_plugin_preserves_immutability():
    """Custom audit backend cannot remove hash-chain."""
    # Create new backend
    backend = PostgresAuditBackend()
    
    # Write events
    await backend.write_event({
        "hash_chain": "abc123",
        "details": "..."
    })
    
    # Verify chain still works
    assert await backend.verify_chain()
    
    # Try to remove hash-chain from DB (SHOULD FAIL)
    with pytest.raises(Exception):
        # Attempt to delete hash_chain column
        db.execute("ALTER TABLE audit_log DROP COLUMN hash_chain")

async def test_house_rules_plugin_respects_confidence_threshold():
    """Custom house rules cannot bypass confidence threshold."""
    rules = ComplianceHouseRules()
    
    # Try to return low confidence
    result = await rules.classify_request("harmless request")
    
    # Core gate MUST reject if confidence < 0.90
    gate = MandatoryHouseRulesGate(threshold=0.90)
    
    with pytest.raises(HouseRulesBlockedError):
        await gate.evaluate("harmless request", rules)
```

---

## Documentation: Compliance Immutability Guarantee

### For Operators

```
┌─────────────────────────────────────────────────────┐
│  ⚠️  COMPLIANCE IMMUTABILITY GUARANTEE               │
├─────────────────────────────────────────────────────┤
│                                                     │
│  These components CANNOT be disabled:              │
│  • Audit trail (L16)                               │
│  • Consent gate (L18)                              │
│  • Flow guard (L34)                                │
│  • House rules (L44)                               │
│  • Erasure orchestrator (L36)                      │
│                                                     │
│  Attempting to disable them will cause boot to     │
│  FAIL IMMEDIATELY (fail-closed).                   │
│                                                     │
│  These CAN be extended with custom plugins:        │
│  • Audit backends (Postgres, S3, etc.)             │
│  • Consent UIs (email, SMS, voice)                 │
│  • Data classifiers (detect custom PII)            │
│  • House rules (org-specific policies)             │
│  • Erasure strategies (custom deletion)            │
│                                                     │
│  But: Extensions MUST respect core guarantees      │
│  or boot will fail.                                │
│                                                     │
│  Questions? See: COMPLIANCE_BASELINE.md            │
└─────────────────────────────────────────────────────┘
```

### For Legal/Compliance

```
COMPLIANCE ARCHITECTURE GUARANTEE (Non-Waivable)

CorvinOS v0.11+ implements the following as STRUCTURAL 
(not configurable) components:

1. Immutable Audit Trail (GDPR Art. 30, 32)
   - Hash-chained, fsync'd to disk
   - Cannot be disabled or modified
   - Tripwire: Missing audit file → boot fails

2. Deny-by-Default Consent Gate (GDPR Art. 6, 7)
   - Users must opt-in for every scope
   - TTL-capped (requests expire)
   - Cannot be bypassed
   - Tripwire: Missing consent gate → boot fails

3. Data Classification + Flow Guard (GDPR Art. 32)
   - Detect PII, PHI, financial data
   - Fail-closed: unknown data blocked
   - Cannot be disabled
   - Tripwire: Missing flow guard → boot fails

4. Acceptable-Use Gate (EU AI Act Art. 5, 50)
   - Blocks malicious/harmful requests
   - High confidence required (0.90+)
   - Cannot be disabled
   - Tripwire: Missing house rules → boot fails

5. GDPR Right-to-Erasure (GDPR Art. 17)
   - Automated user data deletion
   - Cannot be disabled
   - Tripwire: Erasure fails → audit event + alert

These are not feature flags. They are structural constraints
on the platform. Disabling them is not possible without 
modifying the source code and recompiling.

For extensions (new PII types, org policies, etc.), 
use the plugin system. All plugins inherit these 
core guarantees.
```

---

## Implementation Checklist (Phase 1)

- [ ] Document all mandatory mechanisms
- [ ] Add tripwire assertions to boot sequence
- [ ] Add pre-commit linting (prevent compliance removals)
- [ ] Test suite: verify tripwires fire correctly
- [ ] Test suite: verify plugins respect core guarantees
- [ ] Documentation: publish immutability guarantee
- [ ] Code review: all compliance code flagged as critical

---

## Related Documents

- `COMPLIANCE_BASELINE.md` — Full list of regulatory requirements
- `ADR-0030` — Plugin system (extensibility model)
- `STRUCTURED_LOGGING_SYSTEM.md` — Audit trail implementation
- `docs/claude-ref/layer-16-security.md` — Security layer details

---

## Summary

**CorvinOS compliance architecture is structured to be:**

- ✅ **Non-negotiable:** Core mechanisms hardcoded, cannot be disabled
- ✅ **Fail-closed:** Tripwires crash platform if mechanisms are broken
- ✅ **Extensible:** Plugins can add new classifiers, backends, policies
- ✅ **Auditable:** All decisions logged to immutable trail
- ✅ **Forward-compatible:** New regulations can be added without breaking existing architecture

**This is not a toggle. It's structural.**
