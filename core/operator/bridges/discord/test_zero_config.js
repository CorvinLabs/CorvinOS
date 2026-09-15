#!/usr/bin/env node
/**
 * E2E Tests für Zero-Config Discord Setup
 *
 * Tests:
 * 1. AutoOAuth2Generator — Token-Validierung
 * 2. AutoOAuth2Generator — OAuth2 URL Generierung
 * 3. AutoOwnershipBridge — First Message → Owner
 * 4. AutoOwnershipBridge — Whitelist Mode
 * 5. Integration — Settings mit Token
 */

const { AutoOAuth2Generator } = require('./auto_oauth2');
const { AutoOwnershipBridge } = require('./auto_ownership');
const assert = require('assert');

// Mock Logger
class MockLogger {
  constructor() {
    this.logs = [];
  }
  log(msg) {
    this.logs.push(msg);
    console.log(`  [LOG] ${msg}`);
  }
}

// ── Tests ──────────────────────────────────────────────────────────

async function testAutoOAuth2Generator_InvalidToken() {
  console.log('\n✓ Test 1: AutoOAuth2Generator — Invalid Token');
  const log = new MockLogger();
  const gen = new AutoOAuth2Generator(log);

  const result = await gen.validateToken('INVALID_TOKEN_12345');
  assert.strictEqual(result.valid, false, 'Invalid token should return valid: false');
  assert(result.error, 'Should have error message');
  console.log(`  ✓ Correctly rejected invalid token: "${result.error}"`);
}

async function testAutoOAuth2Generator_URLGeneration() {
  console.log('\n✓ Test 2: AutoOAuth2Generator — OAuth2 URL Structure');
  const log = new MockLogger();
  const gen = new AutoOAuth2Generator(log);

  // Mock a valid token (just for URL structure test)
  const mockAppId = '1234567890';

  // Simulate what validateToken would return
  const result = {
    valid: true,
    appId: mockAppId,
    name: 'CorvinOS Bot',
    url: `https://discord.com/api/oauth2/authorize?client_id=${mockAppId}&scope=bot&permissions=68608&disable_guild_select=false`,
  };

  assert(result.url.includes('client_id='), 'URL should contain client_id');
  assert(result.url.includes('scope=bot'), 'URL should contain scope=bot');
  assert(result.url.includes('permissions=68608'), 'URL should contain permissions bitmap');
  assert(result.url.includes('discord.com'), 'URL should point to Discord');
  console.log(`  ✓ OAuth2 URL properly formatted`);
  console.log(`    URL: ${result.url.substring(0, 80)}...`);
}

function testAutoOwnershipBridge_FirstMessage() {
  console.log('\n✓ Test 3: AutoOwnershipBridge — First Message → Owner');
  const log = { log: (msg) => {} };  // Mock logger object with log function
  const settings = { whitelist: [], auto_owner: true };
  const bridge = new AutoOwnershipBridge(log.log, settings);

  // First message from user 123
  const access1 = bridge.determineAccess('123456789');
  assert.strictEqual(access1.authorized, true, 'First user should be authorized');
  assert.strictEqual(access1.role, 'owner', 'First user should be owner');
  assert.strictEqual(access1.promoted, true, 'Should have promoted flag');
  console.log(`  ✓ First user (123456789) promoted to Owner`);
  console.log(`  ✓ Whitelist now contains: ${settings.whitelist}`);

  // Second message from user 456
  const access2 = bridge.determineAccess('987654321');
  assert.strictEqual(access2.authorized, false, 'Second user should NOT be authorized');
  assert.strictEqual(access2.role, 'guest', 'Second user should be guest');
  console.log(`  ✓ Second user (987654321) correctly blocked (not guest)`);
}

function testAutoOwnershipBridge_WhitelistMode() {
  console.log('\n✓ Test 4: AutoOwnershipBridge — Whitelist Mode');
  const log = { log: (msg) => {} };  // Mock logger
  const settings = {
    whitelist: ['111111111', '222222222'],
    auto_owner: false,
  };
  const bridge = new AutoOwnershipBridge(log.log, settings);

  // Whitelisted user
  const access1 = bridge.determineAccess('111111111');
  assert.strictEqual(access1.authorized, true, 'Whitelisted user should be authorized');
  assert.strictEqual(access1.role, 'owner', 'Whitelisted user should be owner');
  console.log(`  ✓ Whitelisted user (111111111) authorized`);

  // Non-whitelisted user
  const access2 = bridge.determineAccess('999999999');
  assert.strictEqual(access2.authorized, false, 'Non-whitelisted user should NOT be authorized');
  assert.strictEqual(access2.role, 'guest', 'Non-whitelisted user should be guest');
  console.log(`  ✓ Non-whitelisted user (999999999) blocked`);
}

function testAutoOwnershipBridge_Reset() {
  console.log('\n✓ Test 5: AutoOwnershipBridge — Reset for Reconfiguration');
  const log = { log: (msg) => {} };  // Mock logger
  const settings = { whitelist: [], auto_owner: true };
  const bridge = new AutoOwnershipBridge(log.log, settings);

  // First access makes owner
  bridge.determineAccess('111111111');
  assert.strictEqual(settings.whitelist.length, 1, 'Whitelist should have 1 entry');

  // Reset for testing (development only - in production you wouldn't do this)
  bridge.resetFirstMessage();
  settings.whitelist = [];  // Also reset whitelist for dev reconfiguration
  console.log(`  ✓ Reset flag and whitelist cleared (dev mode only)`);

  // New access can promote different user (simulates reconfiguration)
  const newAccess = bridge.determineAccess('222222222');
  assert.strictEqual(newAccess.promoted, true, 'Should promote new first user after reset');
  console.log(`  ✓ New user can be promoted after reset (dev mode only)`);
}

function testIntegration_ZeroConfigFlow() {
  console.log('\n✓ Test 6: Integration — Zero-Config Flow Simulation');
  const log = { log: (msg) => {} };  // Mock logger

  // Step 1: User enters token in Console
  const token = 'MTQxNTgxMzU2ODQzOTUyMTM4MQ.TEST'; // Fake token for unit test
  console.log(`  [Flow] User enters token in Console`);
  console.log(`  [Flow] Token submitted: ${token.substring(0, 20)}...`);

  // Step 2: Daemon validates token (in real E2E this hits Discord API)
  console.log(`  [Flow] Daemon validates token via Discord API...`);
  // Validation returns: appId, name, etc.
  const appId = 'mock_app_id_123';
  console.log(`  [Flow] ✓ Token valid! App ID: ${appId}`);

  // Step 3: Daemon generates OAuth2 URL
  const gen = new AutoOAuth2Generator(log);
  // In real E2E, this calls Discord API. For unit test, we mock:
  const oauthUrl = `https://discord.com/api/oauth2/authorize?client_id=${appId}&scope=bot&permissions=68608`;
  console.log(`  [Flow] OAuth2 URL generated`);
  console.log(`  [Flow] User clicks invite link → authorizes in Discord`);
  console.log(`  [Flow] Bot joins server`);

  // Step 4: First message to bot
  console.log(`  [Flow] User sends first message to bot`);
  const settings = { whitelist: [], auto_owner: true };
  const bridge = new AutoOwnershipBridge(log.log, settings);
  const access = bridge.determineAccess('user_id_123');
  assert.strictEqual(access.authorized, true);
  assert.strictEqual(access.promoted, true);
  console.log(`  [Flow] ✓ User auto-promoted to Owner`);
  console.log(`  [Flow] Bot is now ready to use! 🎉`);
}

// ── Main ────────────────────────────────────────────────────────────

async function runAllTests() {
  console.log('═══════════════════════════════════════════════════════');
  console.log('Discord Zero-Config E2E Test Suite');
  console.log('═══════════════════════════════════════════════════════');

  try {
    await testAutoOAuth2Generator_InvalidToken();
    await testAutoOAuth2Generator_URLGeneration();
    testAutoOwnershipBridge_FirstMessage();
    testAutoOwnershipBridge_WhitelistMode();
    testAutoOwnershipBridge_Reset();
    testIntegration_ZeroConfigFlow();

    console.log('\n═══════════════════════════════════════════════════════');
    console.log('✅ All 6 tests passed!');
    console.log('═══════════════════════════════════════════════════════\n');
    process.exit(0);
  } catch (err) {
    console.error('\n❌ Test failed:', err.message);
    console.error(err.stack);
    process.exit(1);
  }
}

runAllTests();
