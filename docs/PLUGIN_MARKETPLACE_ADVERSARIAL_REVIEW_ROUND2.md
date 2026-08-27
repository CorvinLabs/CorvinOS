# Plugin Marketplace Adversarial Review — Round 2

**Date:** 2026-08-27  
**Phase:** After Phase 1 Code Implementation  
**Code Review Commit:** 773f0219

## Round 1 Verification (All FIXED ✅)

| Finding | Description | Code Location | Status |
|---------|-------------|----------------|--------|
| #1 | GitHub Fallback (24h cache) | github_client.py:GitHubCache | ✅ VERIFIED FIXED |
| #2 | Config Secrets Masking | plugin_registry.py:_mask_secrets() | ✅ VERIFIED FIXED |
| #3 | Audit Performance (async queue) | plugin_install_task.py:_emit_event() | ✅ VERIFIED FIXED |
| #4 | Disk Space Check | plugin_install_task.py:_check_disk_space() | ✅ VERIFIED FIXED |
| #5 | Directory Collision Check | plugin_install_task.py:_check_collision() | ✅ VERIFIED FIXED |

## Round 2 New Findings

### HIGH Severity

**Finding #6: Weak Manifest Validation**
- **Issue:** `_validate_manifest()` only checks `plugin.id`
- **Risk:** Malicious manifest with path traversal (../../../etc/passwd) could break isolation
- **Fix:** Validate all manifest keys, reject paths with `..`, validate schema
- **Priority:** BEFORE PHASE 2

**Finding #10: Event Queue Robustness**
- **Issue:** Silent drop on queue full (asyncio.TimeoutError logged as warning)
- **Risk:** Audit trail gaps = GDPR compliance issue
- **Fix:** Increase queue size OR block on critical events (manifest_validated, plugin_installed)
- **Priority:** BEFORE PRODUCTION

### MEDIUM Severity

**Finding #7: Git Clone Timeout Too Long**
- **Issue:** `timeout=30s` is long, UI default is 60s
- **Fix:** Reduce to 15s or add connection-timeout flag
- **Impact:** Better user experience on network failure

**Finding #8: No Permission Checks**
- **Issue:** registry.json and ~/.corvin/plugins/ not chmod'd
- **Fix:** `chmod 700 ~/.corvin/plugins/` in setup
- **Impact:** Multi-user systems (prevent other users reading hashes)

**Finding #9: No Rollback on Manifest Fetch Failure**
- **Issue:** If get_manifest fails, directory already created
- **Fix:** Move _check_* before git clone OR full rollback
- **Impact:** Orphaned directories on GitHub API failures

**Finding #11: Commit Hash Not Logged**
- **Issue:** manifest_fetched event logs repo but not commit_hash
- **Fix:** Add commit_hash to event data
- **Impact:** Can't detect if plugin code changed between installs

## Summary

**Round 1:** 5 findings → 5 fixed (100%)
**Round 2:** 6 new findings → 2 HIGH (BLOCKING), 4 MEDIUM (IMPORTANT)

**Overall Security Posture:** 5/11 findings fixed (45%) → Needs Round 3 before production

## Recommended Prioritization

1. **Before Phase 2:** Fix HIGH findings (#6, #10)
2. **Before Production:** Fix MEDIUM findings (#7–9, #11)
3. **Optional/Future:** Additional security scanning, formal threat model

---

**Next:** Round 3 review after applying HIGH priority fixes
