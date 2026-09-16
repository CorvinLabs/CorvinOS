# Blocker 3: Phase 2 Readiness Report (2026-09-16)

## Status: READY (Waiting for Phase 1 Completion)

### What I've Done (Autonomous Preparation)

✅ **Phase 2 Automated Rotation Script Written**
- File: `scripts/rotate_corvin_keys_phase2.py`
- Implements: Replace live credentials → fail-closed placeholders
- Safety: Generates backups before rotation
- Audit: Logs all rotation events to audit trail

✅ **Blockers Identified**
- 14 credentials across 3 files
- 8 services affected (GitHub, Hetzner, Cloudflare, OpenAI, Gmail, PyPI, Resend, Ollama)
- All identified in BLOCKER_3_ROTATION_PLAN.md

✅ **Placeholder Strategy Defined**
- Format: `<SERVICE>_PLACEHOLDER_<TYPE>_<TIMESTAMP>`
- Examples:
  - `GITHUB_TOKEN=ghp_PLACEHOLDER_CorvinOS_20260916_131400`
  - `OPENAI_API_KEY=sk-proj-PLACEHOLDER-OpenAI-20260916-131400`
- Fail-closed guarantee: 401 Unauthorized when used

### What MUST Happen Before Phase 2 Runs

⚠️ **Phase 1: Manual Credential Revocation (BLOCKING)**

The operator MUST manually revoke credentials on each service:

| Service | Dashboard URL | Action |
|---------|---|---|
| **GitHub** | https://github.com/settings/tokens | Revoke all PATs with "corvin" |
| **Hetzner** | https://console.hetzner.cloud/account/security/api-tokens | Revoke API tokens |
| **Cloudflare** | https://dash.cloudflare.com/profile/api-tokens | Roll API tokens |
| **OpenAI** | https://platform.openai.com/account/api-keys | Delete old keys |
| **Gmail** | https://myaccount.google.com/apppasswords | Remove app password |
| **PyPI** | https://pypi.org/account/settings/ | Delete API token |
| **Resend** | https://resend.com/settings/api-keys | Delete old key |
| **Ollama** | N/A | Regenerate locally |

**Why Phase 1 MUST complete first:**
- Placeholders are intentionally non-functional
- If old credentials not revoked, they still work after replacement
- Creates security window where new credentials are invalid, old ones still active
- Defeats the purpose of rotation

### Phase 2 Execution (When Phase 1 is Done)

**Command:**
```bash
cd /home/shumway/projects/CorvinOS
python3 scripts/rotate_corvin_keys_phase2.py
```

**What it does:**
1. Backs up all credential files (mode 0600)
2. Replaces credentials with placeholders
3. Logs audit event to `~/.corvin/audit.jsonl`
4. Prints summary of rotations

**Estimated time:** 5 minutes

### Phase 3: Audit Trail Verification

After Phase 2:
```bash
# Verify audit event was logged
grep "secret_rotation_phase2" ~/.corvin/audit.jsonl

# Verify placeholders are in place
grep "PLACEHOLDER" /home/shumway/projects/CorvinOS/.env | wc -l  # Should be 7+
grep "PLACEHOLDER" ~/.config/corvin-voice/service.env | wc -l      # Should be 4+
grep "PLACEHOLDER" ~/.config/corvin-voice/secrets.json | wc -l     # Should be 2+
```

### Success Criteria

- [ ] Phase 1: All 8 services have credentials revoked
- [ ] Phase 2: `rotate_corvin_keys_phase2.py` runs without errors
- [ ] All credential files backed up
- [ ] All 14 credentials replaced with placeholders
- [ ] Audit trail shows `secret_rotation_phase2` event
- [ ] Verify: Service auth fails with 401 when using placeholders

### Next Steps

**For Operator (BLOCKING):**
1. Complete Phase 1 (manual revocation on web dashboards) — ~15 min
2. Confirm all revocations successful

**For Autonomous Worker (Tier 1):**
1. Await Phase 1 completion
2. Run: `python3 scripts/rotate_corvin_keys_phase2.py`
3. Verify audit trail
4. Commit: `[HANDOFF tier-1-blocker-3]`

---

**Generated:** 2026-09-16 21:00 UTC  
**Script Status:** ✅ Ready to run (Phase 1 blocking)  
**Blocker 3:** 🟡 WAITING FOR OPERATOR PHASE 1 COMPLETION
