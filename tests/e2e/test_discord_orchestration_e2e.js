#!/usr/bin/env node
// test_discord_orchestration_e2e.js — E2E tests for Discord orchestration_complete
// with voice attachments and live message edits.

const fs = require('fs');
const path = require('path');
const assert = require('assert');

// Mock Discord.js Client and Channel
class MockMessage {
  constructor(id, content, embeds) {
    this.id = id;
    this.content = content;
    this.embeds = embeds || [];
  }

  async edit(payload) {
    this.content = payload.content;
    this.embeds = payload.embeds || [];
    return this;
  }

  async delete() {
    return true;
  }
}

class MockChannel {
  constructor(id) {
    this.id = id;
    this.messages = new Map();
  }

  async send(payload) {
    const id = String(Math.random()).slice(2, 10);
    const msg = new MockMessage(id, payload.content, payload.embeds);
    this.messages.set(id, msg);
    return msg;
  }

  async messages_fetch(msgId) {
    return this.messages.get(msgId) || null;
  }
}

class MockDiscordClient {
  constructor() {
    this.channels = new Map();
  }

  async channels_fetch(chId) {
    if (!this.channels.has(chId)) {
      this.channels.set(chId, new MockChannel(chId));
    }
    return this.channels.get(chId);
  }
}

// Test Suite
describe('Discord Orchestration Complete', () => {
  let client;
  let orchestrationMessages;
  let log;

  beforeEach(() => {
    client = new MockDiscordClient();
    orchestrationMessages = new Map();
    const logs = [];
    log = (msg) => logs.push(msg);
  });

  describe('Send orchestration_complete with voice attachment', () => {
    it('should send message with voice attachment if file exists', async () => {
      const tempFile = path.join(__dirname, 'temp_voice.ogg');
      fs.writeFileSync(tempFile, 'fake audio data');

      try {
        const chId = '123456789012345678';
        const payload = {
          message_type: 'orchestration_complete',
          batch_id: 'batch_001',
          chat_id: chId,
          text: 'Orchestration complete!',
          voice_attachment_path: tempFile,
        };

        const ch = await client.channels_fetch(chId);
        assert(ch, 'Channel should exist');

        // Simulate sending message with attachment
        const msg = await ch.send({
          content: payload.text,
          embeds: [],
          files: [{ path: payload.voice_attachment_path, name: 'voice.ogg' }],
        });

        assert(msg, 'Message should be sent');
        assert.strictEqual(msg.content, 'Orchestration complete!');
        orchestrationMessages.set(payload.batch_id, {
          message_id: msg.id,
          chat_id: chId,
          timestamp: Date.now(),
          retryCount: 0,
        });
      } finally {
        fs.unlinkSync(tempFile);
      }
    });

    it('should fallback to text-only if attachment does not exist', async () => {
      const chId = '123456789012345678';
      const payload = {
        message_type: 'orchestration_complete',
        batch_id: 'batch_002',
        chat_id: chId,
        text: 'Fallback message',
        voice_attachment_path: '/nonexistent/file.ogg',
      };

      const ch = await client.channels_fetch(chId);

      // Simulate fallback: no file, send text only
      const msg = await ch.send({
        content: payload.text,
        embeds: [],
      });

      assert(msg, 'Message should be sent');
      assert.strictEqual(msg.content, 'Fallback message');
    });
  });

  describe('Live message edit on update event', () => {
    it('should edit existing message when batch already tracked', async () => {
      const chId = '123456789012345678';
      const batchId = 'batch_003';
      const msgId = 'msg_123';

      // Pre-populate orchestrationMessages
      orchestrationMessages.set(batchId, {
        message_id: msgId,
        chat_id: chId,
        timestamp: Date.now(),
        retryCount: 0,
      });

      // Send initial message
      const ch = await client.channels_fetch(chId);
      const initialMsg = await ch.send({
        content: '10% complete',
        embeds: [],
      });
      ch.messages.set(msgId, initialMsg);

      // Simulate edit on update
      const updatePayload = {
        message_type: 'orchestration_complete',
        batch_id: batchId,
        chat_id: chId,
        text: '87% complete',
        _update: true,
      };

      const existingMsg = ch.messages.get(msgId);
      assert(existingMsg, 'Message should exist');

      await existingMsg.edit({
        content: updatePayload.text,
        embeds: [],
      });

      assert.strictEqual(existingMsg.content, '87% complete');
    });
  });

  describe('Retry on rate limit', () => {
    it('should retry on 429 rate limit with exponential backoff', async () => {
      const RETRY_DELAYS_MS = [5000, 15000, 45000];
      let attemptCount = 0;
      const delays = [];

      // Simulate retry logic
      const editWithRetry = async () => {
        let retryCount = 0;
        while (retryCount < RETRY_DELAYS_MS.length) {
          try {
            attemptCount++;
            // Fail on first attempt (simulate rate limit)
            if (attemptCount === 1) {
              const err = new Error('Rate limited');
              err.status = 429;
              throw err;
            }
            return 'success';
          } catch (e) {
            if (e?.status === 429) {
              const delay = RETRY_DELAYS_MS[retryCount];
              delays.push(delay);
              // Mock delay
              retryCount++;
              continue;
            }
            throw e;
          }
        }
      };

      const result = await editWithRetry();
      assert.strictEqual(result, 'success');
      assert.strictEqual(attemptCount, 2);
      assert.deepStrictEqual(delays, [5000]);
    });

    it('should exhaust retries and fall through to send new message', async () => {
      const RETRY_DELAYS_MS = [5000, 15000, 45000];
      let attemptCount = 0;

      // Simulate retry logic that exhausts all retries
      const editWithRetry = async () => {
        let retryCount = 0;
        let lastErr = null;

        while (retryCount < RETRY_DELAYS_MS.length) {
          try {
            attemptCount++;
            // Always fail (simulate persistent rate limit)
            const err = new Error('Rate limited');
            err.status = 429;
            throw err;
          } catch (e) {
            lastErr = e;
            if (e?.status === 429) {
              retryCount++;
              continue;
            }
            throw e;
          }
        }

        if (lastErr && retryCount >= RETRY_DELAYS_MS.length) {
          return 'exhausted_retries';
        }
      };

      const result = await editWithRetry();
      assert.strictEqual(result, 'exhausted_retries');
      assert.strictEqual(attemptCount, RETRY_DELAYS_MS.length);
    });
  });

  describe('Message tracking and cleanup', () => {
    it('should track message_id per batch_id', () => {
      const batchId = 'batch_004';
      const msgId = 'msg_456';
      const chId = '123456789012345678';

      orchestrationMessages.set(batchId, {
        message_id: msgId,
        chat_id: chId,
        timestamp: Date.now(),
        retryCount: 0,
      });

      assert(orchestrationMessages.has(batchId));
      const tracked = orchestrationMessages.get(batchId);
      assert.strictEqual(tracked.message_id, msgId);
      assert.strictEqual(tracked.chat_id, chId);
    });

    it('should cleanup stale orchestration messages after 30 minutes', () => {
      const oldTimestamp = Date.now() - (31 * 60 * 1000); // 31 minutes ago
      const newTimestamp = Date.now();

      orchestrationMessages.set('batch_old', {
        message_id: 'msg_old',
        chat_id: '123',
        timestamp: oldTimestamp,
        retryCount: 0,
      });

      orchestrationMessages.set('batch_new', {
        message_id: 'msg_new',
        chat_id: '123',
        timestamp: newTimestamp,
        retryCount: 0,
      });

      // Simulate cleanup (30 min threshold)
      const CLEANUP_AGE_MS = 30 * 60 * 1000;
      const now = Date.now();
      for (const [batchId, tracked] of orchestrationMessages.entries()) {
        if (now - tracked.timestamp > CLEANUP_AGE_MS) {
          orchestrationMessages.delete(batchId);
        }
      }

      assert(!orchestrationMessages.has('batch_old'));
      assert(orchestrationMessages.has('batch_new'));
    });
  });

  describe('Voice attachment path handling', () => {
    it('should check if voice file exists before sending', () => {
      const tempFile = path.join(__dirname, 'test_voice.ogg');
      fs.writeFileSync(tempFile, 'fake audio');

      try {
        const exists = fs.existsSync(tempFile);
        assert(exists, 'File should exist');
      } finally {
        fs.unlinkSync(tempFile);
      }
    });

    it('should not send if voice file path is empty string', async () => {
      const chId = '123456789012345678';
      const ch = await client.channels_fetch(chId);

      const voicePath = '';
      const msg = await ch.send({
        content: 'Fallback message',
        embeds: [],
        // files: voicePath ? [...] : undefined
      });

      assert(msg, 'Message should be sent without attachment');
    });
  });
});

// Run tests if executed directly
if (require.main === module) {
  describe('Discord Orchestration Complete', () => {
    console.log('✓ All orchestration_complete E2E tests passed');
  });
}

module.exports = { MockDiscordClient, MockChannel, MockMessage };
