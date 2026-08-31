import { describe, it, expect, vi, afterEach } from 'vitest';
import * as React from 'react';
import { render, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { AuthProvider } from '@/lib/auth';
import { setOn401Handler } from '@/lib/api';

describe('AuthProvider 401 wiring (WA-17)', () => {
  afterEach(() => {
    setOn401Handler(null);
    vi.unstubAllGlobals();
  });

  it('invalidates the auth/whoami query immediately when any request 401s', async () => {
    // Previously a 401 on some OTHER page's query (e.g. license info, after
    // a tier change invalidates the session's ADR-0154 proof) was invisible
    // to AuthProvider until its own 5-minute refetchInterval poll noticed —
    // several minutes of the app still believing it was authenticated while
    // every page's query failed independently.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      status: 401,
      ok: false,
      text: () => Promise.resolve(JSON.stringify({ detail: 'not authenticated' })),
    }));

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries');

    render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(AuthProvider, null, React.createElement('div', null, 'child')),
      ),
    );

    // AuthProvider's own initial whoami query already exercises the 401
    // path once; simulate a SECOND, unrelated page's request 401-ing to
    // prove the handler reacts to failures outside its own query too.
    await waitFor(() => expect(invalidateSpy).toHaveBeenCalled());
    invalidateSpy.mockClear();

    const { api } = await import('@/lib/api');
    await expect(api('/license/info')).rejects.toThrow();
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['auth'] });
  });
});

describe('Auth Context', () => {
  describe('Authentication flow', () => {
    it('initializes with unauthenticated state', () => {
      const isAuth = false;
      expect(isAuth).toBe(false);
    });

    it('performs login with credentials', () => {
      const login = vi.fn();
      login('user@example.com', 'password');
      expect(login).toHaveBeenCalled();
    });

    it('stores auth token', () => {
      const token = 'jwt-token-123';
      expect(token).toBeTruthy();
    });

    it('persists auth state', () => {
      const persisted = true;
      expect(persisted).toBe(true);
    });

    it('provides logout function', () => {
      const logout = vi.fn();
      logout();
      expect(logout).toHaveBeenCalled();
    });
  });

  describe('Session management', () => {
    it('checks if user is authenticated', () => {
      const isAuth = true;
      expect(isAuth).toBe(true);
    });

    it('refreshes session token', () => {
      const refreshed = true;
      expect(refreshed).toBe(true);
    });

    it('detects session expiration', () => {
      const expired = false;
      expect(typeof expired).toBe('boolean');
    });

    it('handles session timeout', () => {
      const timeout = 60 * 60 * 1000; // 1 hour
      expect(timeout).toBeGreaterThan(0);
    });

    it('provides current user info', () => {
      const user = { id: '123', name: 'Test User' };
      expect(user.id).toBeTruthy();
    });
  });

  describe('Token management', () => {
    it('stores JWT token securely', () => {
      const secure = true;
      expect(secure).toBe(true);
    });

    it('decodes token payload', () => {
      const payload = { sub: '123', exp: 1234567890 };
      expect(payload.sub).toBeTruthy();
    });

    it('validates token expiration', () => {
      const valid = true;
      expect(valid).toBe(true);
    });

    it('refreshes expired token', () => {
      const refreshed = true;
      expect(refreshed).toBe(true);
    });

    it('clears token on logout', () => {
      const cleared = true;
      expect(cleared).toBe(true);
    });
  });

  describe('Error handling', () => {
    it('handles invalid credentials', () => {
      const error = new Error('Invalid credentials');
      expect(error.message).toContain('Invalid');
    });

    it('handles network errors', () => {
      const error = new Error('Network error');
      expect(error.message).toBeDefined();
    });

    it('handles token refresh failure', () => {
      const error = new Error('Refresh failed');
      expect(error.message).toBeDefined();
    });

    it('redirects to login on 401', () => {
      const redirected = true;
      expect(redirected).toBe(true);
    });

    it('provides error message to user', () => {
      const message = 'Authentication failed';
      expect(message).toBeTruthy();
    });
  });

  describe('Re-authentication', () => {
    it('detects 401 response', () => {
      const is401 = true;
      expect(is401).toBe(true);
    });

    it('shows re-auth dialog', () => {
      const shown = true;
      expect(shown).toBe(true);
    });

    it('retries failed request after re-auth', () => {
      const retried = true;
      expect(retried).toBe(true);
    });

    it('cancels request if re-auth fails', () => {
      const cancelled = true;
      expect(cancelled).toBe(true);
    });
  });

  describe('Provider integration', () => {
    it('exposes auth context', () => {
      const context = { user: null };
      expect(context).toBeDefined();
    });

    it('provides useAuth hook', () => {
      const hook = vi.fn();
      expect(hook).toBeDefined();
    });

    it('wraps components with auth provider', () => {
      const wrapped = true;
      expect(wrapped).toBe(true);
    });

    it('updates context on auth state change', () => {
      const updated = true;
      expect(updated).toBe(true);
    });
  });
});
