# Universal Zero-Config Bridge Framework

**Goal:** All messenger bridges (Discord, Telegram, Slack, WhatsApp, Signal, Teams, Email) with **minimal setup friction** and **automatic configuration**.

---

## Implementation Status (adversarial review 2026-07-22)

| Channel | Zero-config endpoints | State |
|---|---|---|
| Discord | `POST /discord/validate-token`, `/discord/save-token` | **Working.** Hardened: writes the canonical runtime settings path (`_settings_path`), CSRF-gated, audited. |
| Telegram | `POST /telegram/validate-token`, `/telegram/save-token` | **Working.** Same hardening as Discord. |
| Slack | `/slack/oauth/*` | **501 Not Implemented.** The shipped flow called non-existent Slack API paths, sent JSON where Slack requires form-encoding, and structurally cannot yield the `xapp-` Socket-Mode token the daemon needs. |
| Teams | `/teams/oauth/*` | **501 Not Implemented.** The flow obtained a delegated user Graph token; the daemon authenticates with Bot Framework app credentials — the token can never start the bridge. |
| Email | `/email/oauth/*` | **501 Not Implemented.** RFC 6749 form-encoding violation + no runtime component consumes the OAuth token (daemon uses IMAP/SMTP credentials). |
| Signal | `/signal/generate-qr`, `/signal/poll-link` | **501 Not Implemented.** Provisioner was a mock (fake QR, unconditional `linked=true`). |
| WhatsApp | `/whatsapp/generate-qr`, `/whatsapp/poll-scan` | **501 Not Implemented.** Mock QR was not a Baileys pairing code; real pairing state lives in the daemon's `auth/` dir. |

All non-working channels are configured via the manual flow (`PUT
/bridges/{channel}/settings` in the Console), which is hardened and audited.
The `auto_*` JS modules for the 501 channels remain on disk as scaffolding
only — nothing calls them at runtime. The SetupDialog components
(`DiscordSetupDialog.tsx`, `TelegramSetupDialog.tsx`) are implemented but not
yet mounted in the SPA; mounting them requires passing the session's
`csrf_token` as the `csrf` prop.

---

## Current State Analysis

| Bridge | Current Setup | Complexity | Auth Method |
|--------|---------------|------------|------------|
| **Discord** | Token → OAuth2 → Done | ⭐ | API Token |
| **Telegram** | Token → Done | ⭐ | API Token |
| **Slack** | 2 Tokens + Scopes + Subscriptions | ⭐⭐⭐ | OAuth + Socket |
| **WhatsApp** | QR Scan → Persistent Auth | ⭐ | QR Code |
| **Signal** | Requires signal-cli-rest-api | ⭐⭐ | Local API |
| **Teams** | OAuth2 + App Permissions | ⭐⭐⭐ | OAuth |
| **Email** | SMTP Credentials | ⭐⭐ | Password/OAuth |

**Insight:** Bridges fall into 3 categories. Let's standardize setup within each.

---

## Bridge Categories & Setup Patterns

### Category A: API Token (Simple)
**Bridges:** Discord, Telegram

**Current Setup:**
1. Get token from provider (Bot creator / BotFather)
2. Paste in settings.json
3. Done

**Improvement (Zero-Config Phase):**
```
Console UI Dialog:
  "Add Telegram Bot"
  ├─ Input: Paste Telegram token
  ├─ Validate: Call Telegram API (like Discord)
  ├─ AutoProvisioning: Get bot info + test message
  └─ Auto-Owner: First message → owner

Same as Discord Phase 2, but for Telegram.
```

**Single Implementation for All Category A:**
- `bridges/shared/js/auto_token_provisioner.js` — generic token validator
- `bridges/shared/js/auto_oauth_generator.js` — generate OAuth URLs from tokens
- Template: `category_a_setup.md` (reusable for future token-based bridges)

---

### Category B: OAuth2 + Permissions (Medium)
**Bridges:** Slack, Teams, Email (OAuth), potentially Twitter/LinkedIn bots

**Current Pain Points:**
- Multiple tokens / secrets
- Scope configuration
- Event subscription setup (Slack)
- Permission grants (Teams)

**Zero-Config Approach:**

```
Console UI Wizard (3 tabs):

Tab 1: "Add Slack Bot"
  ├─ Option A: Quick Setup (Recommended)
  │    └─ Paste Slack App ID → Auto-generate OAuth URL
  │       (Console redirects to Slack → user authorizes)
  │       → Console intercepts callback → tokens saved
  │
  └─ Option B: Manual Setup
       ├─ Input: Bot Token (xoxb-...)
       ├─ Input: App Token (xapp-...)
       └─ Auto-validate both

Tab 2: "Permissions Check"
  ├─ Required Scopes: chat:write, files:read, ...
  ├─ Auto-Detect: Missing scopes
  └─ Link: "Fix permissions in Slack App Portal" (with direct URL)

Tab 3: "Event Subscriptions"
  ├─ Auto-Detect: Socket Mode enabled?
  ├─ Auto-Detect: Event types subscribed?
  ├─ Action: "Enable Socket Mode" (one-click redirection)
  └─ Status: Verify connection after enable

Result: Bot fully configured, no manual Portal clicks needed
```

**Implementation:**
- `bridges/shared/js/auto_oauth_flow.js` — OAuth callback interceptor
- `bridges/shared/js/permissions_checker.js` — scope/permission validator per bridge
- `bridges/slack/auto_setup.js` — Slack-specific wizard
- `bridges/teams/auto_setup.js` — Teams-specific wizard

---

### Category C: Stateful Auth (Unique)
**Bridges:** WhatsApp (QR), Signal (REST API state)

**WhatsApp: QR Scan (Already Good)**
```
Console UI:
  "Add WhatsApp Bot"
  ├─ Check: Is Baileys ready?
  ├─ Generate: QR code (embedded in Console)
  ├─ Instructions: "Scan with WhatsApp > Settings > Linked Devices"
  ├─ Auto-Poll: Check if auth succeeded every 2s
  └─ Result: "Connected as [Your Name]"

No additional work needed — WhatsApp is already minimal friction.
✓ But: Add QR display to Console (currently only terminal).
```

**Signal: REST API Provisioning**
```
Console UI:
  "Add Signal Bot"
  ├─ Check: Is signal-cli-rest-api running?
  ├─ If NO:
  │   ├─ Offer: "Install signal-cli-rest-api locally" (guide link)
  │   └─ Or: "Connect to remote signal-cli instance" (IP input)
  │
  ├─ If YES:
  │   ├─ Auto-Detect: Connected phone numbers
  │   ├─ Select: Which number to use as bot identity
  │   └─ Auto-Test: Send test message to verify
  │
  └─ Result: "Connected to Signal as [Number]"

No token setup — just point to the service.
```

---

## Implementation Roadmap

### Phase 1: Telegram (Fast Clone of Discord Phase 2)
**Timeline:** 1-2 days
- Create `AutoTelegramTokenProvisioner` (mirrors Discord)
- Console UI: Copy Discord setup dialog → adapt for Telegram
- Tests: E2E flow
- Effort: **Low** (proven pattern, just rename + adapt)

### Phase 2: Slack (Most Complex)
**Timeline:** 3-4 days
- `AutoSlackOAuthFlow` (capture OAuth callback in Console)
- Permission auto-detector (check scopes)
- Socket Mode toggle (auto-enable via API)
- Console UI: 3-tab wizard
- Tests: E2E with mock Slack API
- Effort: **High** (new OAuth pattern, multi-step)

### Phase 3: Teams (Medium)
**Timeline:** 2-3 days
- `AutoTeamsOAuthFlow` (Azure AD OAuth)
- Permission auto-detector (Graph API scopes)
- Console UI: Similar to Slack wizard
- Tests: E2E
- Effort: **Medium** (OAuth pattern learned from Slack)

### Phase 4: Email (Medium)
**Timeline:** 2 days
- `AutoEmailOAuthProvisioner` (Gmail / Outlook OAuth)
- Or: Password-gated SMTP validator
- Console UI: Simple input + test send
- Tests: E2E
- Effort: **Medium** (optional OAuth, simple password fallback)

### Phase 5: Signal (Low Priority)
**Timeline:** 1 day
- `AutoSignalRestApiDetector` (check if signal-cli-rest-api running)
- Service discovery (localhost:8080 by default)
- Console UI: Simple service picker
- Tests: E2E with mock signal-cli
- Effort: **Low** (stateless discovery)

### Phase 6: WhatsApp (Enhancement)
**Timeline:** 1 day
- Add QR display to Console (currently terminal-only)
- `AutoWhatsAppQRDisplay` (embed in browser)
- Auto-verify connection (poll auth state)
- Console UI: Minimal (already good)
- Effort: **Low** (enhancement, not new feature)

---

## Common Infrastructure (Shared by All)

### 1. Bridge Auto-Validator Registry
```javascript
// bridges/shared/js/bridge_validators.js

const validators = {
  discord: async (config) => {
    // Call Discord API to verify token
  },
  telegram: async (config) => {
    // Call Telegram API (same as Discord)
  },
  slack: async (config) => {
    // Verify Bot + App tokens
  },
  whatsapp: async (config) => {
    // Check Baileys auth state
  },
  // ... etc
};

module.exports = { validators };
```

### 2. Console Endpoint (Generic)
```python
# core/console/routes/bridge_setup.py

@router.post("/bridge/{bridge_name}/setup/validate")
async def validate_bridge_config(bridge_name: str, body: dict):
    """
    Route: /bridge/discord/setup/validate
           /bridge/telegram/setup/validate
           /bridge/slack/setup/validate
    
    Calls bridge-specific validator via Node.js subprocess.
    """
    validator = validators[bridge_name]
    result = await call_bridge_validator(validator, body)
    return result

@router.post("/bridge/{bridge_name}/setup/save")
async def save_bridge_config(bridge_name: str, body: dict):
    """Save to bridge-specific settings.json"""
    # Atomic write (same pattern as Discord)
    return {"success": True}
```

### 3. Bridge Setup Component (Template)
```typescript
// Console UI Component (reusable template)

<BridgeSetupDialog bridge="telegram" />
<BridgeSetupDialog bridge="slack" />
<BridgeSetupDialog bridge="teams" />

// Props configure UI for each bridge type
```

### 4. Auto-Owner Promotion (Unified)
All bridges use the same `AutoOwnershipBridge`:
- First user message → auto-promote to owner
- Fallback: manual whitelist
- Works for: Discord, Telegram, Slack, WhatsApp, Signal, Email

---

## Per-Bridge Specializations

### Discord / Telegram (Already Done / Simple Clone)
```
Discord:  Phase 1 (Complete) ✓
Telegram: Phase 1 (Clone + 1 day)
```

### Slack (Most Complex)
```javascript
// bridges/slack/auto_oauth_flow.js
class AutoSlackOAuthFlow {
  async startOAuthFlow(appId, scopes) {
    // 1. Generate OAuth URL
    // 2. Open in user's browser
    // 3. Listen for callback (via HTTP endpoint in Console)
    // 4. Exchange code for tokens
    // 5. Save to settings.json
  }

  async checkPermissions() {
    // Call Slack API to verify scopes are granted
  }

  async enableSocketMode() {
    // Call Slack API to activate Socket Mode
  }
}
```

### Teams (Azure AD OAuth)
```javascript
// bridges/teams/auto_oauth_flow.js
class AutoTeamsOAuthFlow {
  async startOAuthFlow(clientId, tenantId) {
    // OAuth to Azure AD
    // Request Microsoft Graph permissions
    // Save tokens
  }
}
```

### WhatsApp (QR Enhancement)
```javascript
// bridges/whatsapp/qr_display.js
class AutoWhatsAppQRDisplay {
  async generateQR() {
    // Get QR from Baileys
    // Encode as data URL
    // Return to Console
  }

  async pollAuthStatus() {
    // Check every 2s: is auth complete?
  }
}
```

---

## User Flow (End-to-End Example: Slack)

```
1. User opens Console → Bridges → Slack
   ├─ Sees: "Add Slack Bot" button

2. Click "Add Slack Bot"
   ├─ Dialog: "Quick Setup?" (YES / NO)

3. YES (Recommended)
   ├─ Input: Slack App ID
   ├─ Console generates OAuth URL
   ├─ Opens browser (Discord OAuth same pattern)
   ├─ User authorizes in Slack
   ├─ Browser redirects to Console callback
   ├─ Console saves both tokens
   ├─ Checks: Scopes OK? Socket Mode OK?
   ├─ If missing: "Fix in Slack Portal [Link]"
   └─ Result: "Connected! ✓"

TIME: ~2 minutes (vs 10+ minutes currently)
ERRORS: ~1% (vs 30% currently)
```

---

## Success Metrics

After full implementation:

| Bridge | Setup Time | Error Rate | Friction Points |
|--------|-----------|-----------|-----------------|
| Discord | 2 min | < 2% | None |
| Telegram | 2 min | < 2% | None |
| Slack | 3 min | < 5% | Scopes / Socket Mode auto-checked |
| Teams | 3 min | < 5% | Tenant ID auto-detected |
| WhatsApp | 2 min | < 1% | QR embedded in Console |
| Signal | 2 min | < 5% | Auto-detect service |
| Email | 2 min | < 5% | SMTP / OAuth toggle |

---

## Implementation Strategy

### Keep It DRY
- Shared validators in `bridges/shared/js/`
- Template Console component (generic wrapper)
- Reuse Discord Phase 2 code (copy + adapt)
- Same error handling patterns

### Incremental Rollout
1. Telegram first (easy win, proves pattern)
2. Slack (most complex, gets hardened)
3. Teams (learn from Slack)
4. Rest (fast followers)

### Testing
- E2E for each bridge (mock API)
- Integration test: all validators work
- Browser test: all dialogs render

---

## Open Decisions

1. **OAuth Callback Redirect:** Console listens on `http://localhost:8765/bridge-setup/callback`?
   - Pros: No external URL needed
   - Cons: Only works for local setup
   - Solution: Support both localhost + remote (tunneled)

2. **Manual Fallback:** Always support "paste token" even if OAuth available?
   - Yes: Advanced users, automation

3. **Whitelist in Console:** Should Console also allow editing whitelist/permissions?
   - Yes: Future enhancement (Bridges tab → Edit Whitelist)

4. **Service Discovery (Signal, local Ollama):** Auto-detect localhost services?
   - Yes: Scan common ports (8080, 8888, etc.)
   - With opt-out: "Connect to remote service [IP:port]"

---

## Conclusion

**Universal Zero-Config Framework makes all bridges as simple as Discord:**
- Category A (Tokens): 1 day per bridge (proven pattern)
- Category B (OAuth): 2-4 days per bridge (more complex, but reusable)
- Category C (Stateful): Enhancements only (already minimal)

**Total effort:** ~2-3 weeks to launch all bridges  
**Benefit:** 5-10x setup time reduction, <5% error rate across all bridges

**This is the path to making CorvinOS the zero-friction multi-messenger platform.**
