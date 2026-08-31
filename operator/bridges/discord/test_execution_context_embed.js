/**
 * test_execution_context_embed.js — Discord execution context embed tests.
 * Phase 4 K=2: Verify embed rendering for execution context.
 */

const { describe, it, expect } = require('@jest/globals');

// Mock the execution context renderer since Discord daemon isn't directly exportable
const {
  normalizeExecutionContext,
  getColorForMode,
} = require('../shared/js/execution_context_renderer');

function renderExecutionContextEmbed(context) {
  if (!context) return null;

  const ctx = normalizeExecutionContext(context);
  const embed = {
    title: '⚙️ Execution Context',
    color: getColorForMode(ctx.delegation_mode),
    fields: [
      {
        name: '🔧 Engine',
        value: ctx.engine_id.replace(/_/g, ' ').toUpperCase(),
        inline: true,
      },
      {
        name: '📊 Model',
        value: ctx.model_name,
        inline: true,
      },
      {
        name: '⚡ Delegation',
        value: ctx.delegation_mode.replace(/_/g, ' ').toUpperCase(),
        inline: true,
      },
      {
        name: '⏱️ Duration',
        value: ctx.duration_ms < 1000 ? `${ctx.duration_ms}ms` : `${(ctx.duration_ms / 1000).toFixed(1)}s`,
        inline: true,
      },
    ],
  };

  if (ctx.tokens_input !== null || ctx.tokens_output !== null) {
    embed.fields.push({
      name: '🪙 Tokens',
      value: `in: ${ctx.tokens_input ?? '-'} | out: ${ctx.tokens_output ?? '-'}`,
      inline: true,
    });
  }

  if (ctx.tool_calls_count > 0) {
    embed.fields.push({
      name: '🔨 Tools',
      value: String(ctx.tool_calls_count),
      inline: true,
    });
  }

  if (ctx.completed_at) {
    try {
      embed.timestamp = new Date(ctx.completed_at).toISOString();
    } catch {}
  }

  return embed;
}

describe('Discord execution context embed', () => {
  it('should build a valid embed with all fields', () => {
    const context = {
      engine_id: 'claude_code',
      model_name: 'claude-3-5-sonnet',
      delegation_mode: 'native',
      duration_ms: 1234,
      tokens_input: 100,
      tokens_output: 50,
      tool_calls_count: 2,
      completed_at: '2024-01-01T00:00:00Z',
    };

    const embed = renderExecutionContextEmbed(context);

    expect(embed).not.toBeNull();
    expect(embed.title).toBe('⚙️ Execution Context');
    expect(embed.color).toBe(0x3B82F6); // native = blue
    expect(embed.fields.length).toBeGreaterThanOrEqual(4);
  });

  it('should set correct color for each delegation mode', () => {
    const modes = [
      { mode: 'native', expectedColor: 0x3B82F6 },
      { mode: 'acs', expectedColor: 0xA855F7 },
      { mode: 'tde', expectedColor: 0x10B981 },
      { mode: 'fallback', expectedColor: 0xEA580C },
    ];

    for (const { mode, expectedColor } of modes) {
      const context = { delegation_mode: mode };
      const embed = renderExecutionContextEmbed(context);
      expect(embed.color).toBe(expectedColor);
    }
  });

  it('should include tokens field when tokens are present', () => {
    const context = {
      tokens_input: 100,
      tokens_output: 50,
    };

    const embed = renderExecutionContextEmbed(context);
    const tokensField = embed.fields.find(f => f.name === '🪙 Tokens');

    expect(tokensField).toBeDefined();
    expect(tokensField.value).toContain('100');
    expect(tokensField.value).toContain('50');
  });

  it('should include tools field when tool_calls_count > 0', () => {
    const context = {
      tool_calls_count: 3,
    };

    const embed = renderExecutionContextEmbed(context);
    const toolsField = embed.fields.find(f => f.name === '🔨 Tools');

    expect(toolsField).toBeDefined();
    expect(toolsField.value).toBe('3');
  });

  it('should format duration as milliseconds for < 1000ms', () => {
    const context = { duration_ms: 500 };
    const embed = renderExecutionContextEmbed(context);
    const durationField = embed.fields.find(f => f.name === '⏱️ Duration');

    expect(durationField.value).toBe('500ms');
  });

  it('should format duration as seconds for >= 1000ms', () => {
    const context = { duration_ms: 2500 };
    const embed = renderExecutionContextEmbed(context);
    const durationField = embed.fields.find(f => f.name === '⏱️ Duration');

    expect(durationField.value).toBe('2.5s');
  });

  it('should handle null context gracefully', () => {
    const embed = renderExecutionContextEmbed(null);
    expect(embed).toBeNull();
  });

  it('should not include timestamp if completed_at is invalid', () => {
    const context = { completed_at: 'invalid-date' };
    const embed = renderExecutionContextEmbed(context);

    expect(embed.timestamp).toBeUndefined();
  });
});
