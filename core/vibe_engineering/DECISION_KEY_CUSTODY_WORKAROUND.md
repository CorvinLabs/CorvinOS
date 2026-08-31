# Decision: Key Custody Blocker — Workaround for Option B + C

**Date:** 2026-08-24  
**Decision Maker:** Operator (you)  
**Status:** RECOMMENDED WORKAROUND  

---

## Problem Statement

**Blocker:** Ed25519 maintainer key custody decision is **outstanding** (since 2026-07-27).

**Impact:**
- ✅ **DOES NOT block** Option B (Context Pipeline v2)
- ✅ **DOES NOT block** Option C (Self-Managed Sessions)
- ❌ **DOES block** Plugin-System Stage 6 (plugin install command)

**Original Decision Tree (from stage-6-plugin-install-blocked-on-key-custody.md):**
1. Generate Ed25519 maintainer key
2. Store private key (custody model TBD: KMS? Hardware token? Encrypted file?)
3. Register public key in `~/.corvin/global/plugin_trust_anchors.txt`
4. Implementation: `corvin plugin install <path>` now validates `origin=vetted` plugins against trust anchor

---

## Recommended Workaround: Parallel Path

### Tier 1 (THIS WEEK + Weeks 1-3): Skip Key Custody Entirely
**For Option B + C implementation:**
- Use `origin=builtin` and `origin=community` **only**
- **No `origin=vetted` support** in Sessions Phase 2.1
- Defer Plugin-System Stage 6 to **Week 4** (after C3 green)

**Why This Works:**
- Sessions Manager doesn't need plugin signing
- Brain v0.2 core plugins are all `builtin` (no signature required)
- Community plugins still work (with explicit per-install confirmation)

**Risk Level:** 🟢 LOW (no new security risk, just deferred feature)

---

### Tier 2 (Week 4+): Resolve Key Custody Decision
**After Option C Sprint 3 complete:**

**Decision Options** (pick one):

#### Option A: KMS-Based Custody
- Store key in Anthropic KMS (or AWS KMS)
- Access via `corvin key-unlock <name>` (interactive)
- **Pros:** High security, audit trail, not on filesystem
- **Cons:** Requires external service, latency
- **Timeline:** 2 days implementation

#### Option B: Encrypted File-Based
- Store key in `~/.corvin/global/maintainer_key.ed25519.encrypted` (AES-256)
- Passphrase required at startup (`corvin startup --key-passphrase`)
- **Pros:** Standalone, no external dependency
- **Cons:** Operator must remember passphrase
- **Timeline:** 1 day implementation

#### Option C: Hardware Token (Yubikey)
- Store key on physical USB device
- `corvin plugin sign --device /dev/yubikey0`
- **Pros:** Highest security, offline capable
- **Cons:** Requires hardware, operator must have device
- **Timeline:** 3 days implementation

#### Option D: Defer Entirely (Accept Risk)
- No Ed25519 signing support; plugins never achieve `vetted` status
- Stays 2-tier: `builtin` / `community`
- **Pros:** No implementation work, simple mental model
- **Cons:** Can't build supply-chain trust later; legacy technical debt
- **Timeline:** 0 days (accept risk)

---

## Recommendation: Option B (Encrypted File) + Later Migrate

**Why Option B:**
1. **Simplest to implement** (1 day, Week 4)
2. **Standalone** (no KMS dependency)
3. **Secure enough** (AES-256 at rest, passphrase at startup)
4. **Migratable** (later move to KMS if Tier-1 security needed)

**Migration Path (if needed later):**
```
Week 4: File-based (AES-256) ← START HERE
Week 8: KMS-based (AWS/Anthropic) ← OPTIONAL UPGRADE
```

**Risk Acceptance:**
- ✅ Acceptable for Week 1-4 (File-based)
- ⚠️ Revisit Week 5+ if Tier-1 security required (move to KMS/Hardware)

---

## Implementation Plan (Week 4)

### Subtask 1: Ed25519 Key Generation
```python
# corvin_key_manager.py
def generate_maintainer_key():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    return (private_key, public_key)

# CLI
$ corvin key generate --type ed25519 --name maintainer
```

**Output:** 
- Private key → encrypted + stored in `~/.corvin/global/maintainer_key.ed25519.encrypted`
- Public key → stored in `~/.corvin/global/plugin_trust_anchors.txt`
- Passphrase → operator's responsibility (environment var or interactive prompt)

---

### Subtask 2: Plugin Signature Verification
```python
# trust.py (existing)
def verify_plugin_origin(manifest: dict, origin: str, public_key: bytes):
    if origin == "builtin":
        return True  # No signature check needed
    elif origin == "community":
        # Requires per-install confirmation (ADR-0249)
        return get_operator_confirmation()
    elif origin == "vetted":
        # NEW: Verify signature against public key
        signature = manifest.get("signature")
        content = manifest_bytes(manifest)
        return Ed25519PublicKey.verify(content, signature, public_key)
    else:
        raise ValueError(f"Unknown origin: {origin}")
```

---

### Subtask 3: Stage 6 Plugin Install Command
```python
# ops/launcher/corvin/plugin_cmd.py
def install_plugin(path: str):
    """corvin plugin install /path/to/plugin"""
    manifest = load_manifest(path)
    origin = manifest.get("origin", "community")
    
    if origin == "vetted":
        public_key = load_trust_anchor("maintainer")
        verify_plugin_origin(manifest, origin, public_key)
    
    # Write to tenant.corvin.yaml
    add_plugin_to_config(manifest.get("class_path"))
    
    # Log audit event
    audit_log({
        "event": "plugin_installed",
        "plugin_id": manifest["id"],
        "origin": origin,
        "timestamp": now(),
        "operator": getenv("USER")
    })
```

---

### Subtask 4: Operator Documentation
- **Startup:** `corvin startup --key-passphrase <passphrase>` or `export CORVIN_KEY_PASSPHRASE=...`
- **Key Rotation:** `corvin key rotate --type ed25519`
- **Plugin Signing:** `corvin plugin sign --manifest /path/to/manifest`
- **Troubleshooting:** "Key decryption failed" → check passphrase

---

## Decision Record

| Aspect | Decision | Rationale |
|---|---|---|
| **Key Type** | Ed25519 | Fast, deterministic, NIST-approved |
| **Custody Model** | File (AES-256) encrypted | Standalone, simple, migratable |
| **Access Control** | Passphrase at startup | Operator-controlled, no external dependencies |
| **Trust Anchor** | `~/.corvin/global/plugin_trust_anchors.txt` | Follows existing pattern (ADR-0248) |
| **Implementation Timeline** | Week 4 (after C3 green) | Parallel to Sessions Sprint 3, no critical path |
| **Risk Level** | LOW | File-based is acceptable interim solution |
| **Fallback** | Option D (defer entirely) | If implementation blocked, accept 2-tier limitation |

---

## Next Steps

### This Week (Option B + C Design)
- [ ] Confirm you accept the workaround (skip key custody until Week 4)
- [ ] Commit OPTION_B_C_IMPLEMENTATION_PLAN.md + CHECKPOINT_MONITORING_SPEC.md
- [ ] Start Context Pipeline v2 validation (k=1-3)

### Week 1-3 (Sessions Sprint 1-3)
- [ ] Follow checkpoint monitoring dashboard
- [ ] Update DECISION_KEY_CUSTODY_WORKAROUND.md if blockers arise

### Week 4 (After C3 Green)
- [ ] Decide which custody model: Option A/B/C/D
- [ ] Implement chosen approach
- [ ] Complete Plugin-System Stage 6
- [ ] Commit: `feat(plugins): Stage 6 plugin install + trust enforcement`

---

## Risk Assessment

| Risk | Probability | Impact | Mitigation |
|---|---|---|---|
| **Week 1-3: No key custody implementation** | 100% | LOW | Planned deferral, no Sessions dependency |
| **Week 4: Key custody decision delayed** | 20% | MEDIUM | Fallback to 2-tier model (Option D) |
| **Encrypted key file compromised** | <5% | HIGH | Rotate key, re-sign all plugins |
| **Passphrase lost** | 10% | HIGH | Keep passphrase in secure location (KMS/LastPass) |

---

## Approval

- [ ] Operator confirms: **Accept Week 4 deferral, proceed with Option B + C**
- [ ] Operator selects: **Preferred key custody model** (A/B/C/D)

---

**Recommendation:** ✅ PROCEED with Workaround  
**Status:** AWAITING YOUR CONFIRMATION  
**Next:** Start Context Pipeline v2 validation (this session)
