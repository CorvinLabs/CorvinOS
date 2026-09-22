/**
 * test_orchestration_handler.js — Unit tests for orchestration_handler.js
 *
 * Tests cover:
 *   1. Text-only delivery (happy path)
 *   2. Text + voice attachment (happy path)
 *   3. Voice missing (graceful degradation)
 *   4. Voice send fails (non-blocking error)
 *   5. Text send fails (critical error)
 *   6. Invalid payload (validation)
 *   7. Summary formatting
 *
 * Mock Strategy:
 *   - Mock waSocket.sendMessage() to track calls and optionally fail
 *   - Mock fs.existsSync() and fs.readFileSync() for file operations
 *   - Track logged messages via a capture array
 *
 * Run: node test_orchestration_handler.js
 */

const assert = require('assert');
const fs = require('fs');
const path = require('path');

// Load the handler (requires manual require since we're not using Jest)
let handlerModule;
try {
  // Preserve original fs if we're mocking it
  const fsModule = fs;
  handlerModule = require('./orchestration_handler');
} catch (e) {
  console.error(`Failed to load orchestration_handler: ${e.message}`);
  process.exit(1);
}

const { handleOrchestrationComplete, formatOrchestrationSummary } = handlerModule;

/**
 * Test harness: mock waSocket and fs operations
 */
class MockWASocket {
  constructor(options = {}) {
    this.sentMessages = [];
    this.failOnSend = options.failOnSend || false;
    this.failOnAudio = options.failOnAudio || false;
  }

  async sendMessage(jid, content) {
    // Check for failures BEFORE tracking
    if (this.failOnSend) {
      throw new Error('simulated send failure (text)');
    }

    if (this.failOnAudio && content.audio) {
      throw new Error('simulated send failure (audio)');
    }

    // Track the send only if it will succeed
    this.sentMessages.push({ jid, content, timestamp: Date.now() });

    // Simulate successful send with Baileys-like response
    return {
      key: { remoteJid: jid, fromMe: true, id: `msg_${Date.now()}` },
      message: content,
    };
  }

  // Helpers for test assertions
  getTextMessages() {
    return this.sentMessages.filter(m => m.content.text);
  }

  getAudioMessages() {
    return this.sentMessages.filter(m => m.content.audio);
  }

  reset() {
    this.sentMessages = [];
  }
}

/**
 * Mock fs operations
 */
const mockFS = {
  existsSync: (filepath) => {
    // By default, assume files exist; tests can override
    return !filepath.includes('missing');
  },
  readFileSync: (filepath) => {
    // Return a fake buffer (simulated OGG audio)
    return Buffer.from('FAKE_OGG_DATA_' + filepath, 'utf8');
  },
};

// Override fs for testing (save original first)
const originalExistsSync = fs.existsSync;
const originalReadFileSync = fs.readFileSync;

function setupMockFS() {
  fs.existsSync = mockFS.existsSync;
  fs.readFileSync = mockFS.readFileSync;
}

function restoreMockFS() {
  fs.existsSync = originalExistsSync;
  fs.readFileSync = originalReadFileSync;
}

/**
 * Logger capture for test assertions
 */
class LogCapture {
  constructor() {
    this.logs = [];
  }

  log(msg) {
    this.logs.push({ level: 'info', message: msg, timestamp: Date.now() });
  }

  error(msg) {
    this.logs.push({ level: 'error', message: msg, timestamp: Date.now() });
  }

  warn(msg) {
    this.logs.push({ level: 'warn', message: msg, timestamp: Date.now() });
  }

  has(substring) {
    return this.logs.some(l => l.message.includes(substring));
  }

  getByLevel(level) {
    return this.logs.filter(l => l.level === level).map(l => l.message);
  }

  reset() {
    this.logs = [];
  }
}

/**
 * TEST SUITE
 */

async function runTests() {
  let passCount = 0;
  let failCount = 0;

  // ──────────────────────────────────────────────────────────────────────
  // TEST 1: Text-only delivery (no voice attachment)
  // ──────────────────────────────────────────────────────────────────────
  try {
    setupMockFS();
    const socket = new MockWASocket();
    const logger = new LogCapture();

    const payload = {
      to: '1234567890@c.us',
      text: '✅ 3 tasks completed successfully',
      msg_id: 'test_001',
      task_count: 3,
      success_count: 3,
      failed_tasks: [],
    };

    const result = await handleOrchestrationComplete(payload, 'test-ch-1', socket, logger.log.bind(logger));

    assert.strictEqual(result.sent, true, 'Result.sent should be true');
    assert.strictEqual(result.text, true, 'Result.text should be true');
    assert.strictEqual(result.voice, false, 'Result.voice should be false (no attachment)');
    assert.strictEqual(socket.getTextMessages().length, 1, 'Should have sent 1 text message');
    assert.strictEqual(socket.getAudioMessages().length, 0, 'Should have sent 0 audio messages');
    assert(logger.has('text summary sent successfully'), 'Should log text send success');

    console.log('✅ TEST 1 PASSED: Text-only delivery');
    passCount++;
  } catch (e) {
    console.error(`❌ TEST 1 FAILED: ${e.message}`);
    failCount++;
  } finally {
    restoreMockFS();
  }

  // ──────────────────────────────────────────────────────────────────────
  // TEST 2: Text + voice attachment (happy path)
  // ──────────────────────────────────────────────────────────────────────
  try {
    setupMockFS();
    const socket = new MockWASocket();
    const logger = new LogCapture();

    const payload = {
      to: '1234567890@c.us',
      text: '✅ 3 tasks completed successfully',
      voice_attachment_path: '/tmp/summary.ogg',
      voice_caption: 'Voice Summary',
      msg_id: 'test_002',
      task_count: 3,
      success_count: 3,
      failed_tasks: [],
    };

    const result = await handleOrchestrationComplete(payload, 'test-ch-2', socket, logger.log.bind(logger));

    assert.strictEqual(result.sent, true, 'Result.sent should be true');
    assert.strictEqual(result.text, true, 'Result.text should be true');
    assert.strictEqual(result.voice, true, 'Result.voice should be true');
    assert.strictEqual(socket.getTextMessages().length, 1, 'Should have sent 1 text message');
    assert.strictEqual(socket.getAudioMessages().length, 1, 'Should have sent 1 audio message');
    assert(logger.has('voice attachment sent successfully'), 'Should log voice send success');

    const audioMsg = socket.getAudioMessages()[0];
    assert.strictEqual(audioMsg.content.mimetype, 'audio/ogg; codecs=opus', 'Audio mimetype should be OGG Opus');
    assert.strictEqual(audioMsg.content.ptt, true, 'Audio should have ptt=true (voice-note)');

    console.log('✅ TEST 2 PASSED: Text + voice attachment');
    passCount++;
  } catch (e) {
    console.error(`❌ TEST 2 FAILED: ${e.message}`);
    failCount++;
  } finally {
    restoreMockFS();
  }

  // ──────────────────────────────────────────────────────────────────────
  // TEST 3: Voice attachment missing (graceful degradation)
  // ──────────────────────────────────────────────────────────────────────
  try {
    setupMockFS();
    const socket = new MockWASocket();
    const logger = new LogCapture();

    // Use a path with 'missing' to trigger mockFS.existsSync() to return false
    const payload = {
      to: '1234567890@c.us',
      text: '✅ 3 tasks completed',
      voice_attachment_path: '/tmp/missing.ogg',
      msg_id: 'test_003',
      task_count: 3,
      success_count: 3,
      failed_tasks: [],
    };

    const result = await handleOrchestrationComplete(payload, 'test-ch-3', socket, logger.log.bind(logger));

    assert.strictEqual(result.sent, true, 'Result.sent should be true (text succeeded)');
    assert.strictEqual(result.text, true, 'Result.text should be true');
    assert.strictEqual(result.voice, false, 'Result.voice should be false (file missing)');
    assert.strictEqual(socket.getTextMessages().length, 1, 'Should have sent text');
    assert.strictEqual(socket.getAudioMessages().length, 0, 'Should not have sent audio');
    assert(logger.has('voice attachment not found'), 'Should log file-not-found');

    console.log('✅ TEST 3 PASSED: Voice missing — graceful degradation');
    passCount++;
  } catch (e) {
    console.error(`❌ TEST 3 FAILED: ${e.message}`);
    failCount++;
  } finally {
    restoreMockFS();
  }

  // ──────────────────────────────────────────────────────────────────────
  // TEST 4: Voice send fails, but text succeeds (non-blocking error)
  // ──────────────────────────────────────────────────────────────────────
  try {
    setupMockFS();
    const socket = new MockWASocket({ failOnAudio: true });
    const logger = new LogCapture();

    const payload = {
      to: '1234567890@c.us',
      text: '✅ 3 tasks completed',
      voice_attachment_path: '/tmp/summary.ogg',
      msg_id: 'test_004',
      task_count: 3,
      success_count: 3,
      failed_tasks: [],
    };

    const result = await handleOrchestrationComplete(payload, 'test-ch-4', socket, logger.log.bind(logger));

    // Even though audio failed, text succeeded and overall result is true
    assert.strictEqual(result.sent, true, 'Result.sent should be true (text succeeded)');
    assert.strictEqual(result.text, true, 'Result.text should be true');
    assert.strictEqual(result.voice, false, 'Result.voice should be false (send failed)');
    assert.strictEqual(socket.getTextMessages().length, 1, 'Should have sent text');
    assert.strictEqual(socket.getAudioMessages().length, 0, 'Audio send should have failed silently');
    assert(logger.has('voice attachment send failed (non-blocking)'), 'Should log non-blocking error');

    console.log('✅ TEST 4 PASSED: Voice send fails — non-blocking');
    passCount++;
  } catch (e) {
    console.error(`❌ TEST 4 FAILED: ${e.message}`);
    failCount++;
  } finally {
    restoreMockFS();
  }

  // ──────────────────────────────────────────────────────────────────────
  // TEST 5: Text send fails (critical error)
  // ──────────────────────────────────────────────────────────────────────
  try {
    setupMockFS();
    const socket = new MockWASocket({ failOnSend: true });
    const logger = new LogCapture();

    const payload = {
      to: '1234567890@c.us',
      text: '✅ 3 tasks completed',
      msg_id: 'test_005',
      task_count: 3,
      success_count: 3,
      failed_tasks: [],
    };

    let exceptionThrown = false;
    let caughtError = null;
    try {
      await handleOrchestrationComplete(payload, 'test-ch-5', socket, logger.log.bind(logger));
    } catch (err) {
      exceptionThrown = true;
      caughtError = err;
    }

    assert.strictEqual(exceptionThrown, true, 'Should throw exception on text send failure');
    assert(caughtError && (caughtError.message.includes('send failure') || caughtError.message.includes('safeSend')),
      `Exception message should indicate failure, got: ${caughtError?.message}`);
    assert(logger.has('CRITICAL'), 'Should log CRITICAL error');

    console.log('✅ TEST 5 PASSED: Text send fails — throws exception');
    passCount++;
  } catch (e) {
    console.error(`❌ TEST 5 FAILED: ${e.message}`);
    failCount++;
  } finally {
    restoreMockFS();
  }

  // ──────────────────────────────────────────────────────────────────────
  // TEST 6: Invalid payload validation
  // ──────────────────────────────────────────────────────────────────────
  try {
    setupMockFS();
    const socket = new MockWASocket();
    const logger = new LogCapture();

    // Test: missing 'to' field
    let exceptionThrown = false;
    try {
      await handleOrchestrationComplete({ text: 'test' }, 'test-ch-6a', socket, logger.log.bind(logger));
    } catch (err) {
      exceptionThrown = true;
      assert(err.message.includes('payload.to'), 'Should validate to field');
    }
    assert.strictEqual(exceptionThrown, true, 'Should throw on missing to');

    // Test: missing 'text' field
    exceptionThrown = false;
    try {
      await handleOrchestrationComplete({ to: '123@c.us' }, 'test-ch-6b', socket, logger.log.bind(logger));
    } catch (err) {
      exceptionThrown = true;
      assert(err.message.includes('payload.text'), 'Should validate text field');
    }
    assert.strictEqual(exceptionThrown, true, 'Should throw on missing text');

    // Test: missing waSocket
    exceptionThrown = false;
    try {
      await handleOrchestrationComplete({ to: '123@c.us', text: 'test' }, 'test-ch-6c', null, logger.log.bind(logger));
    } catch (err) {
      exceptionThrown = true;
      assert(err.message.includes('waSocket'), 'Should validate socket');
    }
    assert.strictEqual(exceptionThrown, true, 'Should throw on missing socket');

    console.log('✅ TEST 6 PASSED: Payload validation');
    passCount++;
  } catch (e) {
    console.error(`❌ TEST 6 FAILED: ${e.message}`);
    failCount++;
  } finally {
    restoreMockFS();
  }

  // ──────────────────────────────────────────────────────────────────────
  // TEST 7: Summary formatting
  // ──────────────────────────────────────────────────────────────────────
  try {
    // Test: success case
    let summary = formatOrchestrationSummary({
      event_type: 'ORCHESTRATION_COMPLETE_SUCCESS',
      task_count: 3,
      success_count: 3,
      failed_tasks: [],
    });
    assert(summary.includes('✅'), 'Success should use ✅ emoji');
    assert(summary.includes('3/3'), 'Should show task count');

    // Test: mixed case
    summary = formatOrchestrationSummary({
      event_type: 'ORCHESTRATION_COMPLETE_MIXED',
      task_count: 5,
      success_count: 3,
      failed_tasks: [
        { task_id: 'task_1', error: 'timeout' },
        { task_id: 'task_2', error: 'api_error' },
      ],
    });
    assert(summary.includes('⚠️'), 'Mixed should use ⚠️ emoji');
    assert(summary.includes('3/5'), 'Should show partial success');
    assert(summary.includes('2 failed'), 'Should show failure count');
    assert(summary.includes('task_1'), 'Should list first failure');

    console.log('✅ TEST 7 PASSED: Summary formatting');
    passCount++;
  } catch (e) {
    console.error(`❌ TEST 7 FAILED: ${e.message}`);
    failCount++;
  }

  // ──────────────────────────────────────────────────────────────────────
  // SUMMARY
  // ──────────────────────────────────────────────────────────────────────
  console.log('\n' + '='.repeat(60));
  console.log(`Test Results: ${passCount} passed, ${failCount} failed`);
  console.log('='.repeat(60));

  if (failCount > 0) {
    process.exit(1);
  }
}

// Run all tests
runTests().catch(err => {
  console.error('Unexpected error during test run:', err);
  process.exit(1);
});
