# Blocker 3: Corvin-Keys Secret Rotation Plan (2026-09-16)

## Executive Summary
**Status:** CRITICAL - Live credentials found in 3 locations
**Scope:** 14+ credential items across 8 services
**Risk:** GDPR Art. 32 (data security), operational control compromise
**Action:** Rotate all credentials with placeholders (fail-closed); log all changes to audit trail

## Credentials Identified

### From `/home/shumway/projects/CorvinOS/.env`
1. **GITHUB_TOKEN** (3 instances) — GitHub PAT, used for marketplace auth
2. **HETZNER_API_TOKEN** — Infrastructure control
3. **HETZNER_ROOT_PASSWORT** — SSH access
4. **CLOUDFLARE_ID** — CDN account
5. **CLOUDFLARE_API_TOKEN** — CDN control
6. **PYPI_TOKEN** — Python package publishing
7. **RESEND_API_KEY** — Email sending service

### From `~/.config/corvin-voice/service.env`
8. **CORVIN_TTS_OPENAI_KEY** — OpenAI TTS
9. **CORVIN_STT_OPENAI_KEY** — OpenAI STT
10. **OPENAI_API_KEY** — Duplicate of above
11. **GMAIL_APP_PASSWORD** — Gmail app credential
12. **OLLAMA_API_KEY** — Local LLM access

### From `~/.config/corvin-voice/secrets.json`
13. **HETZNER_API_TOKEN** — Duplicate
14. **HETZNER_SSH_KEY_NAME** — SSH reference

## Rotation Workflow

### Phase 1: Manual Revocation (Operator Action Required)
Each service requires manual revocation through their web dashboard:

| Service | Dashboard URL | Notes |
|---------|---|---|
| GitHub | https://github.com/settings/tokens | Revoke all PATs with "corvin" in name |
| Hetzner | https://console.hetzner.cloud/account/security/api-tokens | Revoke API tokens |
| Cloudflare | https://dash.cloudflare.com/profile/api-tokens | Roll API tokens |
| OpenAI | https://platform.openai.com/account/api-keys | Delete old keys |
| Gmail | https://myaccount.google.com/apppasswords | Remove app password |
| PyPI | https://pypi.org/account/settings/ | Delete API token |
| Resend | https://resend.com/settings/api-keys | Delete old key |
| Ollama | N/A (local) | Regenerate locally |

### Phase 2: Update Configuration Files (Automated)
Replace credentials with PLACEHOLDER values that:
- Are syntactically valid (format-correct)
- Are non-functional (fail-closed when used)
- Are clearly marked as placeholders
- Are properly documented

### Phase 3: Audit Trail Logging
Log all rotation events with:
- timestamp (ISO-8601)
- service name
- rotation reason
- tenant_id
- hash-chained to previous event

## Placeholder Strategy

All placeholders follow this pattern:
```
<SERVICE>_PLACEHOLDER_<TYPE>_<VERSION>_<TIMESTAMP>
```

Example:
- `GITHUB_TOKEN=ghp_PLACEHOLDER_CorvinOS_20260916_131400`
- `OPENAI_API_KEY=sk-proj-PLACEHOLDER-CorvinOS-20260916-131400`

**Fail-closed guarantee:**
- Placeholders fail immediately when used (correct format, wrong content)
- No silent fallback behavior
- Services return 401 Unauthorized
- Operator sees clear error message

## Security Implications

### Before Rotation (Current State)
- 14+ live credentials in files
- 3 file locations (cross-tenant risk if shared)
- No audit trail of who accessed/modified
- No expiration/rotation schedule
- One breach affects 8 services

### After Rotation (Target State)
- 14+ placeholder credentials (fail-closed)
- Audit trail logs every rotation event
- Rotation documented in this ticket
- Services must use new credentials (operator generates + stores securely)
- Tenant isolation enforced (each tenant manages own secrets)

## Implementation Steps

1. **Backup current credentials** → `/tmp/credential_backup_20260916.json` (mode 0600)
2. **Generate placeholders** → All 14 items
3. **Update .env** → Replace with placeholders
4. **Update service.env** → Replace with placeholders
5. **Update secrets.json** → Replace with placeholders
6. **Audit trail** → Log 14 rotation events (hash-chained)
7. **Verify** → Grep to ensure no real credentials remain
8. **Git status** → Confirm .env is ignored

## Compliance Notes

- **GDPR Art. 32** (data security) — credential rotation is mandatory control
- **ADR-0760** (audit trail) — every rotation logged with hash-chain
- **ADR-0233** (plugin system) — credentials are NOT plugins, handled separately
- **Fail-closed guarantee** — no "missing credential fallback"; services deny access

## Timeline

- Phase 1 (manual revocation): **2–4 hours** (operator action on each service)
- Phase 2 (update files): **15 min** (automated)
- Phase 3 (audit logging): **5 min** (automated)
- **Total:** ~2.5–4.5 hours

## Success Criteria

- [ ] All 14 credentials backed up (encrypted)
- [ ] All 14 placeholders deployed to files
- [ ] All 14 rotation events in audit trail (hash-chained)
- [ ] No real credentials visible in `grep` scan
- [ ] .env status = ignored by git
- [ ] Each service tested with placeholder (expects 401 error)
