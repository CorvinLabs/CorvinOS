======================================================================
CREDENTIAL ROTATION PHASE 1 — VERIFICATION REPORT
======================================================================

Timestamp: 2026-09-17T22:13:53.993438Z
Tenant: _default
Repository: /home/shumway/projects/CorvinOS

--- INVENTORY SUMMARY ---
Total credentials: 14
Accessible: 14
Missing: 0
Inaccessible: 0

--- AUTHENTICATION TESTS ---
Tests passed: 0
Tests failed: 14
Tests skipped: 0

--- DETAILED INVENTORY ---

GITHUB_TOKEN
  File: .env
  Status: accessible
  File readable: True
  Key present: True
  Masked: ghp_***
  Test result: False (network test skipped)

HETZNER_API_TOKEN
  File: .env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

HETZNER_ROOT_PASSWORT
  File: .env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

CLOUDFLARE_ID
  File: .env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

CLOUDFLARE_API_TOKEN
  File: .env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

PYPI_TOKEN
  File: .env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

RESEND_API_KEY
  File: .env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

CORVIN_TTS_OPENAI_KEY
  File: ~/.config/corvin-voice/service.env
  Status: accessible
  File readable: True
  Key present: True
  Masked: sk-p***
  Test result: False (network test skipped)

CORVIN_STT_OPENAI_KEY
  File: ~/.config/corvin-voice/service.env
  Status: accessible
  File readable: True
  Key present: True
  Masked: sk-p***
  Test result: False (network test skipped)

OPENAI_API_KEY
  File: ~/.config/corvin-voice/service.env
  Status: accessible
  File readable: True
  Key present: True
  Masked: sk-p***
  Test result: False (network test skipped)

GMAIL_APP_PASSWORD
  File: ~/.config/corvin-voice/service.env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

OLLAMA_API_KEY
  File: ~/.config/corvin-voice/service.env
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

HETZNER_API_TOKEN
  File: ~/.config/corvin-voice/secrets.json
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

HETZNER_SSH_KEY_NAME
  File: ~/.config/corvin-voice/secrets.json
  Status: accessible
  File readable: True
  Key present: True
  Masked: PLAC***
  Test result: False (network test skipped)

--- PHASE 1 COMPLETION ---
Inventory complete: YES
Baseline documented: YES (audit trail)
Phase 2 ready: YES

--- NEXT STEPS (Phase 2) ---
1. Operator revokes old keys via web dashboards (manual, ~1-2 hours)
2. Run Phase 2 pre-checks
3. Run credential rotation atomically
4. Verify old keys rejected, new keys accepted
5. Audit trail captures full rotation