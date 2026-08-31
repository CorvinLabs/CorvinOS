/**
 * execution_context_renderer.js — Safe rendering of execution context across all channels.
 *
 * Phase 4 K=2: Execution Context Rendering
 *
 * Handles graceful fallbacks for missing fields and provides platform-agnostic
 * normalization. Each bridge's daemon.js imports this and calls the appropriate
 * render function for its platform (Discord embed, WhatsApp footer, etc.).
 */

/**
 * Normalize execution context to ensure all expected fields exist with safe defaults.
 * @param {object} obj - Raw execution_context from outbox payload
 * @returns {object} - Normalized context with all required fields
 */
function normalizeExecutionContext(obj) {
  if (!obj || typeof obj !== 'object') {
    return getDefaultContext();
  }

  return {
    engine_id: String(obj.engine_id || 'unknown').toLowerCase(),
    model_source: String(obj.model_source || 'unknown').toLowerCase(),
    model_name: String(obj.model_name || 'unknown'),
    delegation_mode: String(obj.delegation_mode || 'native').toLowerCase(),
    duration_ms: parseInt(obj.duration_ms || 0, 10),
    tokens_input: obj.tokens_input ? parseInt(obj.tokens_input, 10) : null,
    tokens_output: obj.tokens_output ? parseInt(obj.tokens_output, 10) : null,
    tool_calls_count: parseInt(obj.tool_calls_count || 0, 10),
    started_at: obj.started_at || null,
    completed_at: obj.completed_at || null,
    exit_code: parseInt(obj.exit_code || 0, 10),
    acs_run_id: obj.acs_run_id || null,
    tde_router_decision: obj.tde_router_decision || null,
  };
}

/**
 * Get default empty context (for error cases).
 * @returns {object}
 */
function getDefaultContext() {
  return {
    engine_id: 'unknown',
    model_source: 'unknown',
    model_name: 'unknown',
    delegation_mode: 'native',
    duration_ms: 0,
    tokens_input: null,
    tokens_output: null,
    tool_calls_count: 0,
    started_at: null,
    completed_at: null,
    exit_code: 0,
    acs_run_id: null,
    tde_router_decision: null,
  };
}

/**
 * Check if execution context should be rendered per bridge config.
 * @param {object} message - Outbox message payload
 * @param {object} config - Bridge configuration object
 * @returns {boolean} - true if should render, false otherwise
 */
function shouldRenderContext(message, config) {
  // Config key defaults to true (render by default)
  const configEnabled = (config && config.show_execution_context !== false);
  const hasContext = message && message.execution_context;
  return configEnabled && hasContext;
}

/**
 * Format engine ID for display (e.g., 'claude_code' → 'Claude Code').
 * @param {string} engineId
 * @returns {string}
 */
function formatEngineId(engineId) {
  const map = {
    'claude_code': 'Claude Code',
    'acs': 'ACS',
    'tde': 'TDE',
    'hermes': 'Hermes',
    'unknown': 'Unknown',
  };
  return map[engineId] || engineId.replace(/_/g, ' ');
}

/**
 * Format delegation mode for display.
 * @param {string} mode
 * @returns {string}
 */
function formatDelegationMode(mode) {
  const map = {
    'native': 'Native',
    'acs': 'ACS',
    'tde': 'TDE',
    'fallback': 'Fallback',
    'unknown': 'Unknown',
  };
  return map[mode] || mode.replace(/_/g, ' ');
}

/**
 * Get color code for delegation mode (Discord embed color).
 * @param {string} delegationMode
 * @returns {number} - Discord color integer
 */
function getColorForMode(delegationMode) {
  const colorMap = {
    'native': 0x3B82F6,      // blue
    'acs': 0xA855F7,         // purple
    'tde': 0x10B981,         // green
    'fallback': 0xEA580C,    // orange
  };
  return colorMap[delegationMode] || 0x6B7280; // gray fallback
}

/**
 * Get emoji for delegation mode.
 * @param {string} delegationMode
 * @returns {string}
 */
function getEmojiForMode(delegationMode) {
  const emojiMap = {
    'native': '🔧',
    'acs': '☁️',
    'tde': '🎯',
    'fallback': '⚠️',
  };
  return emojiMap[delegationMode] || '⚙️';
}

/**
 * Get emoji for engine.
 * @param {string} engineId
 * @returns {string}
 */
function getEmojiForEngine(engineId) {
  const emojiMap = {
    'claude_code': '🤖',
    'acs': '☁️',
    'tde': '🎯',
    'hermes': '🛠️',
  };
  return emojiMap[engineId] || '⚙️';
}

/**
 * Format duration for display.
 * @param {number} ms - Duration in milliseconds
 * @returns {string}
 */
function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`;
  const secs = (ms / 1000).toFixed(1);
  return `${secs}s`;
}

/**
 * Format token count with commas.
 * @param {number|null} count
 * @returns {string}
 */
function formatTokens(count) {
  if (count === null || count === undefined) return '-';
  return count.toLocaleString ? count.toLocaleString() : String(count);
}

module.exports = {
  normalizeExecutionContext,
  getDefaultContext,
  shouldRenderContext,
  formatEngineId,
  formatDelegationMode,
  getColorForMode,
  getEmojiForMode,
  getEmojiForEngine,
  formatDuration,
  formatTokens,
};
