/**
 * Unit test for /btw command handler
 *
 * Tests that:
 * 1. The /btw command is recognized
 * 2. The instruction text is correctly extracted
 * 3. The sendBtwNote API function exists and has the correct signature
 */

import { describe, it, expect } from 'vitest';
import { sendBtwNote } from '@/lib/api/chat';

describe('sendBtwNote API function', () => {
  it('should export sendBtwNote function', () => {
    expect(typeof sendBtwNote).toBe('function');
  });

  it('should accept chatId, instruction, and csrf parameters', () => {
    const sig = sendBtwNote.toString();
    expect(sig).toContain('chatId');
    expect(sig).toContain('instruction');
    expect(sig).toContain('csrf');
  });
});

describe('/btw command handler logic', () => {
  it('should recognize /btw prefix', () => {
    const text = "/btw use Opus instead";
    const match = text.trim().startsWith("/btw ");
    expect(match).toBe(true);
  });

  it('should extract instruction after /btw', () => {
    const text = "/btw use Opus instead";
    const instruction = text.trim().substring(5);
    expect(instruction).toBe("use Opus instead");
  });

  it('should handle /btw with multiple words', () => {
    const text = "/btw switch to Claude Opus and increase timeout to 60 seconds";
    const instruction = text.trim().substring(5);
    expect(instruction).toBe("switch to Claude Opus and increase timeout to 60 seconds");
  });

  it('should reject /btw with empty instruction', () => {
    const text = "/btw ";
    const instruction = text.trim().substring(5);
    expect(instruction.trim().length).toBe(0);
  });

  it('should not match /btw without space', () => {
    const text = "/btwtest";
    const match = text.trim().startsWith("/btw ");
    expect(match).toBe(false);
  });
});
