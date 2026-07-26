# Phase 4 K=2: Execution Context Rendering — Bridge-Specific Delivery

**Status:** Complete (2026-07-26)  
**ADR:** ADR-0243 Phase 1-2 (Execution Context integration)  
**Layer:** Bridge outbox rendering (Discord, WhatsApp, Telegram, Slack, Signal, Teams, Email)

---

## Summary

Phase 4 K=2 renders execution context (engine, model, delegation mode, duration, tokens, tool calls) visually in each messenger after every turn completes. Each bridge uses platform-native formatting:

- **Discord:** Embed with colored header (blue/native, purple/ACS, green/TDE, orange/fallback)
- **WhatsApp:** Text footer with separator lines
- **Telegram:** Markdown-formatted message
- **Slack:** Context block (footer-style compact display)
- **Signal:** Plain text footer (REST API text-only)
- **Teams:** Plain text activity card
- **Email:** Plain text footer appended to email body

All implementations are fail-closed: rendering errors do not block message delivery.

---

## Architecture

### Shared Utility Library

**File:** `operator/bridges/shared/js/execution_context_renderer.js`

Provides platform-agnostic normalization and formatting:

```javascript
normalizeExecutionContext(obj)       // Ensure all fields exist w/ safe defaults
getDefaultContext()                  // Empty context for error cases
shouldRenderContext(msg, config)     // Check if rendering enabled per config
formatEngineId(engineId)             // Format engine display name
formatDelegationMode(mode)           // Format delegation mode display
getColorForMode(delegationMode)      // Discord embed color code
getEmojiForEngine(engineId)          // Emoji by engine
getEmojiForMode(delegationMode)      // Emoji by delegation mode
formatDuration(ms)                   // Duration as "123ms" or "1.5s"
formatTokens(count)                  // Token count with comma separators
```

All functions handle null/undefined inputs gracefully (return safe defaults).

### Bridge Implementations

Each bridge's daemon receives execution_context in the outbox payload and renders it:

#### Discord (`operator/bridges/discord/daemon.js`)

```javascript
function renderExecutionContextEmbed(context) {
  // Returns Discord embed object with:
  // - Title: "⚙️ Execution Context"
  // - Color: getColorForMode(delegation_mode)
  // - Fields: Engine, Model, Delegation, Duration, (Tokens), (Tools)
  // - Timestamp: completed_at as ISO string
}
```

Sent after all media via `ch.send({ embeds: [embed] })`.

#### WhatsApp (`operator/bridges/whatsapp/daemon.js`)

```javascript
function renderExecutionContextFooter(context) {
  // Returns plain text footer:
  // ━━━━━━━━━━━━━━━━━━
  // ⚙️ Claude Code • claude-3-5-sonnet
  // ⚡ Native • 1.2s
  // 🪙 in: 100 | out: 50
  // 🔨 2 tools
  // ━━━━━━━━━━━━━━━━━━
}
```

Sent after all media via `safeSend(waSocket, payload.to, { text: footer })`.

#### Telegram (`operator/bridges/telegram/daemon.js`)

```javascript
function renderExecutionContextMarkdown(context) {
  // Returns markdown-formatted text:
  // *⚙️ Execution Context*
  // 🔧 Claude Code
  // 📊 claude-3-5-sonnet
  // ⚡ Native
  // ⏱️ 1.2s
  // (etc.)
}
```

Sent after all media via `bot.sendMessage(chatId, text, { parse_mode: 'Markdown' })`.

#### Slack (`operator/bridges/slack/daemon.js`)

```javascript
function renderExecutionContextBlock(context) {
  // Returns Slack context block:
  // {
  //   type: 'context',
  //   elements: [{
  //     type: 'mrkdwn',
  //     text: '🔧 Claude Code • 📊 claude-3-5-sonnet • ⚡ Native • ⏱️ 1.2s • 🪙 ...'
  //   }]
  // }
}
```

Sent after all media via `app.client.chat.postMessage({ channel: chId, blocks: [...] })`.

#### Signal (`operator/bridges/signal/handler.js`)

```javascript
function renderExecutionContextSignal(context) {
  // Returns plain text footer (Signal: text-only):
  // ━━━━━━━━━━━━━━━━
  // ⚙️ Claude Code • claude-3-5-sonnet
  // ⚡ Native • 1.2s
  // ...
  // ━━━━━━━━━━━━━━━━
}
```

Sent after all media via `sendSignal(recipient, footer)`.

#### Teams (`operator/bridges/teams/handler.js`)

```javascript
function renderExecutionContextTeams(context) {
  // Returns plain text activity:
  // {
  //   type: 'message',
  //   text: '⚙️ *Claude Code* • claude-3-5-sonnet\n⚡ Native • 1.2s\n...'
  // }
}
```

Sent after all media via `ctx.sendActivity(contextCard)`.

#### Email (`operator/bridges/email/daemon.js`)

```javascript
function renderExecutionContextEmail(context) {
  // Returns plain text footer appended to email body:
  // ────────────────────
  // Execution Context:
  // Engine: Claude Code
  // Model: claude-3-5-sonnet
  // Delegation: Native
  // Duration: 1.2s
  // Tokens: in=100, out=50
  // Tools: 2
  // ────────────────────
}
```

Appended to body before `sendReply()` call.

---

## Configuration

### Per-Bridge Settings

Add to each bridge's `settings.json` (in `<corvin_home>/bridges/<channel>/`):

```json
{
  "show_execution_context": true
}
```

**Default:** `true` (render by default)  
**Effect:** When `false`, execution context is silently skipped  
**Changes:** Hot-reloaded via `currentSettings()` on next turn (no restart needed)

**Future:** K=3 will expose this as a Console UI toggle in Settings → Features panel.

### Outbox Payload Structure

The adapter (core/console/chat_runtime.py) adds execution_context to outbox payloads:

```json
{
  "chat_id": "...",
  "text": "...",
  "execution_context": {
    "engine_id": "claude_code",
    "model_source": "claude",
    "model_name": "claude-3-5-sonnet",
    "delegation_mode": "native",
    "duration_ms": 1234,
    "tokens_input": 100,
    "tokens_output": 50,
    "tool_calls_count": 2,
    "started_at": "2024-01-01T00:00:00Z",
    "completed_at": "2024-01-01T00:00:01Z",
    "exit_code": 0
  }
}
```

---

## Error Handling

### Graceful Fallbacks

All rendering functions are wrapped in try-catch:

```javascript
if (shouldRenderContext(payload, currentSettings())) {
  try {
    const embed = renderExecutionContextEmbed(payload.execution_context);
    if (embed) {
      await ch.send({ embeds: [embed] });
      log(`execution context embed sent for ${msgId}`);
    }
  } catch (e) {
    log(`execution context embed failed: ${e && e.message || e}`);
  }
}
```

**Fail-closed policy:** If rendering fails, the message is delivered without context. No blocking, no retry.

### Missing Fields

`normalizeExecutionContext()` ensures all fields exist:

```javascript
{
  engine_id: String(obj.engine_id || 'unknown').toLowerCase(),
  model_name: String(obj.model_name || 'unknown'),
  // ... all fields with safe defaults
}
```

If execution_context is missing entirely, `shouldRenderContext()` returns false → no attempt to render.

---

## Testing

### Shared Renderer Unit Tests

**File:** `operator/bridges/shared/js/test_execution_context_renderer.js`

- `normalizeExecutionContext()` — null handling, partial objects, defaults
- `shouldRenderContext()` — config flags, missing context
- `formatEngineId()`, `formatDelegationMode()` — string formatting
- `getColorForMode()` — Discord color codes
- `formatDuration()` — ms vs. seconds
- `formatTokens()` — comma separators

**Run:**

```bash
jest operator/bridges/shared/js/test_execution_context_renderer.js
```

### Bridge-Specific Tests

**Discord:** `operator/bridges/discord/test_execution_context_embed.js`

- Embed structure (title, color, fields)
- Color selection per delegation mode
- Token/tools field inclusion
- Duration formatting
- Timestamp handling

**Run:**

```bash
jest operator/bridges/discord/test_execution_context_embed.js
```

Similar test suites can be created for WhatsApp, Telegram, Slack, Signal, Teams, Email.

### E2E Validation

Each bridge should be tested with a real turn:

1. Start bridge daemon
2. Send a message via messenger
3. Verify execution context renders correctly after reply
4. Check formatting matches platform conventions

---

## Deliverables K=2

- ✅ Shared utility library: `execution_context_renderer.js`
- ✅ Discord: Embed rendering + integration
- ✅ WhatsApp: Text footer + integration
- ✅ Telegram: Markdown + integration
- ✅ Slack: Context block + integration
- ✅ Signal: Plain text footer + integration
- ✅ Teams: Plain text activity + integration
- ✅ Email: Plain text footer + integration
- ✅ Configuration flag: `show_execution_context` (default true)
- ✅ Unit tests: Shared renderer + Discord embed
- ✅ Syntax validation: All daemons + handlers
- ✅ Error handling: Fail-closed, no blocking
- ✅ Documentation: This file

---

## Next Steps: K=3

**K=3 (Console UI Integration):**

- Expose `show_execution_context` toggle in Console Settings → Features panel
- Per-chat override support (some chats always show, others never show context)
- Frontend rendering of execution context in message metadata display
- Historical view of execution context trends per session/user

---

## References

- **ADR-0243:** Layer axis + layered boot (Phase 1-2)
- **ADR-0171:** Engine span + execution context capture
- **CLAUDE.md:** Compliance baseline, LDD mandatory
- **Layer-Plugins (L4):** Plugin registry architecture
- **Layer-Bridges:** Messenger integration layer

---

## Related ADRs

- ADR-0243 Phase 1-2: Execution context foundation (K=1 captured to outbox)
- ADR-0171: Engine span tracking + performance metrics
- ADR-0037: Direct claude subprocess (engine_id = claude_code)
- ADR-0114: ACS delegation (engine_id = acs)
- ADR-0222: Tiered Delegation Engine (engine_id = tde)

---

## Author

Implemented as part of Phase 4 K=2, 2026-07-26.
