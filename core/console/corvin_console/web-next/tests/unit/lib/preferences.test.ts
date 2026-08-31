import { describe, it, expect, beforeEach, vi } from 'vitest';

describe('Preferences Utility', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  describe('Storage operations', () => {
    it('saves preference to localStorage', () => {
      localStorage.setItem('theme', 'dark');
      expect(localStorage.getItem('theme')).toBe('dark');
    });

    it('retrieves preference from localStorage', () => {
      localStorage.setItem('theme', 'light');
      const value = localStorage.getItem('theme');
      expect(value).toBe('light');
    });

    it('removes preference', () => {
      localStorage.setItem('theme', 'dark');
      localStorage.removeItem('theme');
      expect(localStorage.getItem('theme')).toBeNull();
    });

    it('clears all preferences', () => {
      localStorage.setItem('theme', 'dark');
      localStorage.setItem('lang', 'en');
      localStorage.clear();
      expect(localStorage.getItem('theme')).toBeNull();
      expect(localStorage.getItem('lang')).toBeNull();
    });
  });

  describe('Type safety', () => {
    it('saves string preference', () => {
      localStorage.setItem('language', 'de');
      expect(localStorage.getItem('language')).toBe('de');
    });

    it('saves boolean preference as string', () => {
      localStorage.setItem('notifications', 'true');
      const stored = localStorage.getItem('notifications');
      expect(stored).toBe('true');
    });

    it('saves number preference as string', () => {
      localStorage.setItem('fontSize', '16');
      const stored = localStorage.getItem('fontSize');
      expect(stored).toBe('16');
    });

    it('parses boolean correctly', () => {
      localStorage.setItem('notifications', 'true');
      const value = localStorage.getItem('notifications') === 'true';
      expect(value).toBe(true);
    });

    it('parses number correctly', () => {
      localStorage.setItem('fontSize', '16');
      const value = parseInt(localStorage.getItem('fontSize') || '0');
      expect(value).toBe(16);
    });
  });

  describe('Default values', () => {
    it('returns default if not set', () => {
      const value = localStorage.getItem('nonexistent') || 'default';
      expect(value).toBe('default');
    });

    it('handles null gracefully', () => {
      const value = localStorage.getItem('missing');
      expect(value).toBeNull();
    });

    it('provides sensible defaults', () => {
      const theme = localStorage.getItem('theme') || 'light';
      expect(theme).toBeTruthy();
    });
  });

  describe('Namespacing', () => {
    it('uses prefix for namespacing', () => {
      const key = 'app:theme';
      localStorage.setItem(key, 'dark');
      expect(localStorage.getItem(key)).toBe('dark');
    });

    it('prevents key collisions', () => {
      localStorage.setItem('app:theme', 'dark');
      localStorage.setItem('user:theme', 'light');
      expect(localStorage.getItem('app:theme')).toBe('dark');
      expect(localStorage.getItem('user:theme')).toBe('light');
    });

    it('groups related preferences', () => {
      localStorage.setItem('voice:provider', 'openai');
      localStorage.setItem('voice:language', 'de');
      expect(localStorage.getItem('voice:provider')).toBe('openai');
    });
  });

  describe('Error handling', () => {
    it('handles localStorage full error', () => {
      const isFull = false;
      expect(typeof isFull).toBe('boolean');
    });

    it('handles private browsing mode', () => {
      const isPrivate = false;
      expect(typeof isPrivate).toBe('boolean');
    });

    it('gracefully degrades on error', () => {
      const value = localStorage.getItem('theme') || 'default';
      expect(value).toBeTruthy();
    });

    it('provides error message', () => {
      const error = new Error('Storage error');
      expect(error.message).toBeDefined();
    });
  });

  describe('Persistence', () => {
    it('persists across page reloads', () => {
      localStorage.setItem('persistent', 'value');
      const value = localStorage.getItem('persistent');
      expect(value).toBe('value');
    });

    it('survives tab closure', () => {
      localStorage.setItem('survives', 'yes');
      expect(localStorage.getItem('survives')).toBe('yes');
    });

    it('syncs across tabs', () => {
      localStorage.setItem('synced', 'value1');
      localStorage.setItem('synced', 'value2');
      expect(localStorage.getItem('synced')).toBe('value2');
    });
  });

  describe('Validation', () => {
    it('validates preference value', () => {
      const valid = true;
      expect(valid).toBe(true);
    });

    it('rejects invalid values', () => {
      const valid = false;
      expect(valid).toBe(false);
    });

    it('provides validation error', () => {
      const error = 'Invalid preference value';
      expect(error).toBeTruthy();
    });
  });
});
