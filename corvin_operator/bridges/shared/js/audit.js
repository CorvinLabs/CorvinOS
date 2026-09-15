// audit.js — voice-bridge audit event emitter for the Node daemons.
//
// The Discord / Slack / Telegram / WhatsApp daemons all need to write
// security-relevant events (whitelist deny, PIN failure, channel rate
// limit, daemon start/stop) into the same SHA-chained audit log the
// Python adapter and the forge MCP server already write to. Instead of
// re-implementing the chain logic in JavaScript, this module shells out
// to `voice-audit emit` — a thin Python CLI built on the existing
// `forge.security_events.write_event` library.
//
// Design choices:
//   - **Fire-and-forget.** The hot path of a daemon (every inbound
//     message goes through whitelist + rate-limit checks) cannot wait
//     for a ~150ms python startup. Spawned with detached: false so the
//     process is awaited only by Node's event loop, never the daemon.
//   - **Silent on failure.** If voice-audit is missing or the audit
//     file is on a read-only mount, the daemon must not crash. Errors
//     go to stderr; the chain just gets a hole at that point, which
//     `voice-audit verify` will surface later.
//   - **Stable severity table.** The Python side owns the
//     event_type → severity mapping; this module passes the
//     event_type through unchanged.
//
// Public API:
//   const { auditEvent } = require('./audit');
//   auditEvent('bridge.whitelist_deny', {
//       channel: 'discord', user: 'hostile-id',
//       details: { reason: 'not in whitelist' }
//   });
//
// The event lands at the same VOICE_AUDIT_PATH / FORGE_ROOT-resolved
// path that voice-audit verify checks.

'use strict';

const { spawn } = require('child_process');
const path = require('path');

// Resolve the CLI path once. audit.js lives in
// operator/bridges/shared/js/, so going up three levels lands us at
// operator/, and operator/voice/scripts/voice_audit.py is below.
const PLUGIN_VOICE_ROOT = path.resolve(__dirname, '..', '..', '..', 'voice');
const VOICE_AUDIT_CLI = path.join(
  PLUGIN_VOICE_ROOT, 'scripts', 'voice_audit.py'
);
const PYTHON = process.env.PYTHON || 'python3';

const KNOWN_EVENT_TYPES = new Set([
  'bridge.login',
  'bridge.login_failed',
  'bridge.whitelist_deny',
  'bridge.read_only_drop',
  'bridge.observer_appended',
  'bridge.observer_transcript_consumed',
  'bridge.pin_failure',
  'bridge.rate_limit_exceeded',
  'bridge.message_received',
  'bridge.persona_routed',
  'bridge.tool_use',
  'bridge.cancel',
  'bridge.config_reloaded',
  'bridge.error',
  'daemon.started',
  'daemon.stopped',
  'daemon.error',
]);

/**
 * Emit one structured audit event. Fire-and-forget — never blocks.
 *
 * @param {string} eventType  e.g. 'bridge.whitelist_deny'
 * @param {object} [opts]
 * @param {string} [opts.channel]
 * @param {string} [opts.chatKey]
 * @param {string} [opts.user]
 * @param {string} [opts.persona]
 * @param {string} [opts.tool]
 * @param {string} [opts.runId]
 * @param {object} [opts.details]   extra fields, JSON-serializable
 */
function auditEvent(eventType, opts = {}) {
  if (typeof eventType !== 'string' || eventType.length === 0) return;
  if (!KNOWN_EVENT_TYPES.has(eventType) && process.env.VOICE_AUDIT_STRICT) {
    process.stderr.write(
      `audit.js: unknown event_type ${JSON.stringify(eventType)}\n`
    );
    return;
  }

  const args = [VOICE_AUDIT_CLI, 'emit', eventType];
  if (opts.channel) args.push('--channel',  String(opts.channel));
  if (opts.chatKey) args.push('--chat-key', String(opts.chatKey));
  if (opts.user)    args.push('--user',     String(opts.user));
  if (opts.persona) args.push('--persona',  String(opts.persona));
  if (opts.tool)    args.push('--tool',     String(opts.tool));
  if (opts.runId)   args.push('--run-id',   String(opts.runId));
  if (opts.details && typeof opts.details === 'object') {
    args.push('--details', JSON.stringify(opts.details));
  }

  let proc;
  try {
    proc = spawn(PYTHON, args, {
      stdio: ['ignore', 'ignore', 'pipe'],
      detached: false,
    });
  } catch (e) {
    process.stderr.write(
      `audit.js: failed to spawn ${PYTHON}: ${e.message}\n`
    );
    return;
  }
  proc.on('error', (e) => {
    process.stderr.write(`audit.js: emit error: ${e.message}\n`);
  });
  proc.stderr.on('data', (chunk) => {
    // surface real failures (e.g. malformed details) without spamming;
    // emit-internal "audit IO error" lines are still informative.
    process.stderr.write(`audit.js: ${chunk.toString().trim()}\n`);
  });
  // Don't wait for it. The `unref()` lets Node exit even if the child
  // is still finishing — fine for fire-and-forget semantics.
  if (typeof proc.unref === 'function') proc.unref();
}

/**
 * Synchronous variant — blocks until the event is on disk. Use sparingly,
 * only at daemon shutdown so `daemon.stopped` makes it into the log
 * before the process exits. Returns the child's exit code (0 on success).
 */
function auditEventSync(eventType, opts = {}) {
  const args = [VOICE_AUDIT_CLI, 'emit', eventType];
  if (opts.channel) args.push('--channel',  String(opts.channel));
  if (opts.chatKey) args.push('--chat-key', String(opts.chatKey));
  if (opts.user)    args.push('--user',     String(opts.user));
  if (opts.persona) args.push('--persona',  String(opts.persona));
  if (opts.tool)    args.push('--tool',     String(opts.tool));
  if (opts.runId)   args.push('--run-id',   String(opts.runId));
  if (opts.details && typeof opts.details === 'object') {
    args.push('--details', JSON.stringify(opts.details));
  }
  const { spawnSync } = require('child_process');
  const r = spawnSync(PYTHON, args, { stdio: ['ignore', 'ignore', 'pipe'] });
  if (r.status !== 0 && r.stderr) {
    process.stderr.write(`audit.js sync: ${r.stderr.toString().trim()}\n`);
  }
  return r.status;
}

module.exports = { auditEvent, auditEventSync, KNOWN_EVENT_TYPES };
