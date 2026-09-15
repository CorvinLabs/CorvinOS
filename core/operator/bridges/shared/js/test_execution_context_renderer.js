/**
 * test_execution_context_renderer.js — Unit tests for execution context rendering.
 * Phase 4 K=2 test suite.
 */

const {
  normalizeExecutionContext,
  getDefaultContext,
  shouldRenderContext,
  formatEngineId,
  formatDelegationMode,
  getColorForMode,
  getEmojiForEngine,
  formatDuration,
  formatTokens,
} = require('./execution_context_renderer');

describe('execution_context_renderer', () => {
  describe('normalizeExecutionContext', () => {
    it('should normalize a complete execution context', () => {
      const ctx = {
        engine_id: 'claude_code',
        model_source: 'claude',
        model_name: 'claude-3-5-sonnet-20241022',
        delegation_mode: 'native',
        duration_ms: 1234,
        tokens_input: 100,
        tokens_output: 50,
        tool_calls_count: 3,
        started_at: '2024-01-01T00:00:00Z',
        completed_at: '2024-01-01T00:00:01Z',
        exit_code: 0,
      };

      const result = normalizeExecutionContext(ctx);

      expect(result.engine_id).toBe('claude_code');
      expect(result.model_name).toBe('claude-3-5-sonnet-20241022');
      expect(result.delegation_mode).toBe('native');
      expect(result.duration_ms).toBe(1234);
      expect(result.tokens_input).toBe(100);
      expect(result.tokens_output).toBe(50);
      expect(result.tool_calls_count).toBe(3);
      expect(result.exit_code).toBe(0);
    });

    it('should return default context for null/undefined', () => {
      expect(normalizeExecutionContext(null)).toEqual(getDefaultContext());
      expect(normalizeExecutionContext(undefined)).toEqual(getDefaultContext());
    });

    it('should handle missing fields gracefully', () => {
      const partial = { engine_id: 'acs' };
      const result = normalizeExecutionContext(partial);

      expect(result.engine_id).toBe('acs');
      expect(result.model_name).toBe('unknown');
      expect(result.tokens_input).toBeNull();
      expect(result.tokens_output).toBeNull();
    });
  });

  describe('shouldRenderContext', () => {
    it('should return true when context enabled and execution_context present', () => {
      const msg = { execution_context: { engine_id: 'native' } };
      const config = { show_execution_context: true };
      expect(shouldRenderContext(msg, config)).toBe(true);
    });

    it('should return false when show_execution_context is false', () => {
      const msg = { execution_context: { engine_id: 'native' } };
      const config = { show_execution_context: false };
      expect(shouldRenderContext(msg, config)).toBe(false);
    });

    it('should return false when execution_context is missing', () => {
      const msg = { text: 'hello' };
      const config = { show_execution_context: true };
      expect(shouldRenderContext(msg, config)).toBe(false);
    });

    it('should default to true when config is empty', () => {
      const msg = { execution_context: { engine_id: 'native' } };
      const config = {};
      expect(shouldRenderContext(msg, config)).toBe(true);
    });
  });

  describe('formatEngineId', () => {
    it('should format claude_code as Claude Code', () => {
      expect(formatEngineId('claude_code')).toBe('Claude Code');
    });

    it('should format other engines', () => {
      expect(formatEngineId('acs')).toBe('ACS');
      expect(formatEngineId('tde')).toBe('TDE');
      expect(formatEngineId('hermes')).toBe('Hermes');
    });

    it('should replace underscores for unknown engines', () => {
      expect(formatEngineId('my_engine')).toBe('my engine');
    });
  });

  describe('formatDelegationMode', () => {
    it('should format delegation modes', () => {
      expect(formatDelegationMode('native')).toBe('Native');
      expect(formatDelegationMode('acs')).toBe('ACS');
      expect(formatDelegationMode('tde')).toBe('TDE');
      expect(formatDelegationMode('fallback')).toBe('Fallback');
    });
  });

  describe('getColorForMode', () => {
    it('should return color codes for each mode', () => {
      expect(getColorForMode('native')).toBe(0x3B82F6);     // blue
      expect(getColorForMode('acs')).toBe(0xA855F7);        // purple
      expect(getColorForMode('tde')).toBe(0x10B981);        // green
      expect(getColorForMode('fallback')).toBe(0xEA580C);   // orange
    });

    it('should return gray for unknown mode', () => {
      expect(getColorForMode('unknown')).toBe(0x6B7280);
    });
  });

  describe('getEmojiForEngine', () => {
    it('should return emoji for each engine', () => {
      expect(getEmojiForEngine('claude_code')).toBe('🤖');
      expect(getEmojiForEngine('acs')).toBe('☁️');
      expect(getEmojiForEngine('tde')).toBe('🎯');
      expect(getEmojiForEngine('hermes')).toBe('🛠️');
    });

    it('should return default emoji for unknown engine', () => {
      expect(getEmojiForEngine('unknown')).toBe('⚙️');
    });
  });

  describe('formatDuration', () => {
    it('should format milliseconds', () => {
      expect(formatDuration(500)).toBe('500ms');
      expect(formatDuration(999)).toBe('999ms');
    });

    it('should format seconds for values >= 1000', () => {
      expect(formatDuration(1000)).toBe('1.0s');
      expect(formatDuration(1500)).toBe('1.5s');
      expect(formatDuration(5000)).toBe('5.0s');
    });
  });

  describe('formatTokens', () => {
    it('should format token counts', () => {
      expect(formatTokens(null)).toBe('-');
      expect(formatTokens(undefined)).toBe('-');
      expect(formatTokens(100)).toBe('100');
      expect(formatTokens(1000)).toBe('1,000');
      expect(formatTokens(1000000)).toBe('1,000,000');
    });
  });
});
