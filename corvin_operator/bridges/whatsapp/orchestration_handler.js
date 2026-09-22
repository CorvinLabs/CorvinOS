/**
 * orchestration_handler.js — WhatsApp handler for orchestration completion events.
 *
 * Routes standardized orchestration_complete envelopes from orchestration_router.py
 * to WhatsApp via Baileys. Sends text summary + optional voice attachment (OGG).
 *
 * Integration:
 *   1. orchestration_router.py writes envelope to shared outbox/
 *   2. daemon.js processOutbox() reads envelope (message_type == 'orchestration_complete')
 *   3. handleOrchestrationComplete() sends text + voice attachment
 *   4. Non-blocking: text always succeeds; voice fails gracefully
 *
 * Requirements:
 *   - Baileys client connection (waSocket)
 *   - Voice attachment path must exist (OGG format, opus codec)
 *   - Logging via console.log / console.error (matches daemon.js style)
 *
 * ADR Reference: ADR-2027 (Discord live feed + Orchestration routing)
 */

const fs = require('fs');
const path = require('path');

/**
 * Send orchestration completion summary via WhatsApp.
 *
 * @param {Object} payload - Orchestration envelope (from orchestration_router.py)
 * @param {string} payload.to - Target WhatsApp JID / chat ID
 * @param {string} payload.text - Plain-text summary (e.g., "✅ 3 tasks completed...")
 * @param {string} payload.voice_attachment_path - Path to OGG voice attachment (optional)
 * @param {string} payload.event_type - Event type (e.g., "ORCHESTRATION_COMPLETE_SUCCESS")
 * @param {number} payload.task_count - Total tasks in batch
 * @param {number} payload.success_count - Successfully completed tasks
 * @param {Array} payload.failed_tasks - List of failed task summaries
 * @param {string} chId - Chat ID (for logging/tracing)
 * @param {Object} waSocket - Baileys WhatsApp client connection
 * @param {Function} log - Logging function (e.g., console.log from daemon.js)
 * @throws {Error} Only throws on text send failure (critical); voice failure is silent
 *
 * Flow:
 *   1. Send text summary (BLOCKING — fails if send error)
 *   2. Send voice attachment if exists (NON-BLOCKING — skips silently on error)
 *   3. Return { sent: true, text: true, voice: bool }
 */
async function handleOrchestrationComplete(payload, chId, waSocket, log) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('orchestration_handler: payload must be an object');
  }
  if (!payload.to) {
    throw new Error('orchestration_handler: payload.to (WhatsApp JID) is required');
  }
  if (!payload.text) {
    throw new Error('orchestration_handler: payload.text (summary) is required');
  }
  if (!waSocket) {
    throw new Error('orchestration_handler: waSocket (Baileys client) is required');
  }
  if (typeof log !== 'function') {
    log = console.log; // Fallback to console.log if no logger provided
  }

  const targetJid = payload.to;
  let voiceSent = false;
  let textSent = false;

  try {
    // ── PHASE 1: Send text summary ─────────────────────────────────────
    // This is the primary delivery; voice is enhancement. Text must succeed
    // or the whole send is considered failed.
    if (payload.text) {
      log(`orchestration: sending text summary to ${targetJid} (msg_id=${payload.msg_id || 'unknown'}, task_count=${payload.task_count})`);

      // Construct text payload for WhatsApp
      const textPayload = {
        text: payload.text,
      };

      // Optional: Add extra context if available
      if (payload.event_type) {
        textPayload._event_type = payload.event_type; // Metadata (not sent, just tracking)
      }

      // Send via Baileys
      await safeSend(waSocket, targetJid, { text: payload.text });
      textSent = true;
      log(`orchestration: text summary sent successfully`);
    }

    // ── PHASE 2: Send voice attachment (non-blocking) ─────────────────────
    // If voice file exists and voice_attachment_path is set, attempt to send.
    // On any error (file not found, send failure, timeout), log warning but do NOT throw.
    // The text summary already delivered, so we don't fail the entire send.
    if (payload.voice_attachment_path) {
      try {
        if (!fs.existsSync(payload.voice_attachment_path)) {
          log(`orchestration: voice attachment not found: ${payload.voice_attachment_path}`);
        } else {
          log(`orchestration: sending voice attachment to ${targetJid}`);

          // Read OGG file and send as audio (ptt=true for voice-note style)
          const audioBuffer = fs.readFileSync(payload.voice_attachment_path);

          await safeSend(waSocket, targetJid, {
            audio: audioBuffer,
            mimetype: 'audio/ogg; codecs=opus',
            ptt: true,  // Voice-note style (plays inline, not downloadable)
            caption: payload.voice_caption || 'Voice Summary', // Optional caption
          });

          voiceSent = true;
          log(`orchestration: voice attachment sent successfully`);
        }
      } catch (audioErr) {
        // Non-blocking: voice attachment failed, but text already sent
        log(`orchestration: voice attachment send failed (non-blocking): ${audioErr.message}`);
        // Intentionally do NOT rethrow — the text summary is the critical delivery
      }
    }

    // ── Summary ────────────────────────────────────────────────────────
    const result = {
      sent: textSent,
      text: textSent,
      voice: voiceSent,
      targetJid,
      taskCount: payload.task_count || 0,
      successCount: payload.success_count || 0,
      failedCount: (payload.failed_tasks || []).length,
    };

    log(`orchestration: completed (text=${textSent}, voice=${voiceSent})`);
    return result;

  } catch (err) {
    // Text send failed — this is critical
    log(`orchestration: CRITICAL — text send failed: ${err.message}`);
    throw err;
  }
}

/**
 * Wrapper for Baileys client.sendMessage() with retry + error handling.
 * Matches daemon.js's safeSend() semantics.
 *
 * @param {Object} waSocket - Baileys WhatsApp client
 * @param {string} jid - Target WhatsApp JID
 * @param {Object} content - Message content (text, audio, etc.)
 * @returns {Object|null} - Sent message object or null on failure
 */
async function safeSend(waSocket, jid, content) {
  try {
    const result = await waSocket.sendMessage(jid, content);
    return result;
  } catch (err) {
    throw new Error(`safeSend failed: ${err.message}`);
  }
}

/**
 * Normalize orchestration event summary for WhatsApp display.
 *
 * Converts orchestration envelope data into human-readable text summary.
 * Used when the router doesn't provide a pre-formatted text field.
 *
 * @param {Object} payload - Orchestration envelope
 * @returns {string} - Formatted summary (e.g., "✅ 3/3 tasks completed")
 */
function formatOrchestrationSummary(payload) {
  if (!payload) return '(no summary available)';

  const { event_type, task_count, success_count, failed_tasks } = payload;
  const failCount = (failed_tasks || []).length;

  // Emoji based on outcome
  const emoji = event_type === 'ORCHESTRATION_COMPLETE_SUCCESS' ? '✅' : '⚠️';

  // Basic summary
  let summary = `${emoji} ${success_count}/${task_count} tasks completed`;

  // Add failure details if any
  if (failCount > 0) {
    summary += `\n❌ ${failCount} failed`;

    // List first 3 failures (avoid flooding the message)
    const failList = (failed_tasks || []).slice(0, 3);
    for (const fail of failList) {
      if (fail.task_id && fail.error) {
        summary += `\n  • ${fail.task_id}: ${fail.error}`;
      }
    }

    if (failCount > 3) {
      summary += `\n  • ...and ${failCount - 3} more`;
    }
  }

  return summary;
}

module.exports = {
  handleOrchestrationComplete,
  formatOrchestrationSummary,
  safeSend,
};
