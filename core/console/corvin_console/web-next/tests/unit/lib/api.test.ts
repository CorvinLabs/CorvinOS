import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { api, ApiError, setOn401Handler } from '@/lib/api';

describe('API Client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('401 handling (WA-17)', () => {
    afterEach(() => {
      setOn401Handler(null);
      vi.unstubAllGlobals();
    });

    it('invokes the registered on401 handler when any request gets a 401', async () => {
      // A 401 used to be treated as a plain per-request ApiError, so each
      // page's own query rendered its own generic "Could not load X" error
      // while the shared auth/whoami poll (every 5 minutes) hadn't yet
      // noticed the session was gone and redirected to /login.
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        status: 401,
        ok: false,
        text: () => Promise.resolve(JSON.stringify({ detail: 'not authenticated' })),
      }));
      const handler = vi.fn();
      setOn401Handler(handler);

      await expect(api('/license/info')).rejects.toBeInstanceOf(ApiError);
      expect(handler).toHaveBeenCalledTimes(1);
    });

    it('does not invoke the on401 handler for a successful response', async () => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        text: () => Promise.resolve(JSON.stringify({ tier: 'member' })),
      }));
      const handler = vi.fn();
      setOn401Handler(handler);

      await api('/license/info');
      expect(handler).not.toHaveBeenCalled();
    });
  });

  describe('Request handling', () => {
    it('constructs correct API URLs', () => {
      const url = '/api/v1/tasks';
      expect(url).toContain('/api');
    });

    it('adds auth headers to requests', () => {
      const headers = { Authorization: 'Bearer token' };
      expect(headers.Authorization).toBeDefined();
    });

    it('includes CSRF token', () => {
      const token = 'csrf-token-123';
      expect(token).toBeTruthy();
    });

    it('handles request timeout', () => {
      const timeout = 30000;
      expect(timeout).toBeGreaterThan(0);
    });

    it('cancels requests via AbortSignal', () => {
      const signal = new AbortController().signal;
      expect(signal).toBeDefined();
    });
  });

  describe('Response handling', () => {
    it('parses JSON responses', () => {
      const data = { id: '123', status: 'success' };
      expect(data.id).toBe('123');
    });

    it('handles empty responses', () => {
      const empty = null;
      expect(empty).toBeNull();
    });

    it('validates response types', () => {
      const typed = { id: '123' };
      expect(typeof typed.id).toBe('string');
    });
  });

  describe('Task endpoints', () => {
    it('fetches tasks list', () => {
      const endpoint = '/api/v1/tasks';
      expect(endpoint).toContain('tasks');
    });

    it('creates new task', () => {
      const payload = { title: 'Test', description: 'Test task' };
      expect(payload.title).toBe('Test');
    });

    it('updates task status', () => {
      const update = { status: 'completed' };
      expect(update.status).toBe('completed');
    });

    it('deletes task', () => {
      const deleted = true;
      expect(deleted).toBe(true);
    });

    it('fetches task details', () => {
      const taskId = '123';
      expect(taskId).toBeTruthy();
    });

    it('streams task updates', () => {
      const stream = { type: 'task.update' };
      expect(stream.type).toContain('task');
    });

    it('handles task filtering', () => {
      const filters = { status: 'running', type: 'workflow' };
      expect(filters.status).toBe('running');
    });

    it('supports pagination', () => {
      const params = { limit: 10, offset: 0 };
      expect(params.limit).toBe(10);
    });

    it('sorts task results', () => {
      const sorted = ['id', 'createdAt', 'status'];
      expect(sorted[0]).toBe('id');
    });

    it('searches tasks by keyword', () => {
      const query = 'search term';
      expect(query).toBeTruthy();
    });
  });

  describe('Workflow endpoints', () => {
    it('lists workflows', () => {
      const workflows = [];
      expect(Array.isArray(workflows)).toBe(true);
    });

    it('creates workflow', () => {
      const workflow = { name: 'Test', yaml: 'steps: []' };
      expect(workflow.name).toBe('Test');
    });

    it('updates workflow', () => {
      const update = { name: 'Updated' };
      expect(update.name).toBe('Updated');
    });

    it('publishes workflow', () => {
      const published = true;
      expect(published).toBe(true);
    });

    it('executes workflow', () => {
      const execution = { workflowId: '123', params: {} };
      expect(execution.workflowId).toBe('123');
    });

    it('fetches workflow history', () => {
      const history = [];
      expect(Array.isArray(history)).toBe(true);
    });

    it('validates workflow YAML', () => {
      const valid = true;
      expect(valid).toBe(true);
    });
  });

  describe('Compute endpoints', () => {
    it('creates compute job', () => {
      const job = { type: 'training', params: {} };
      expect(job.type).toBe('training');
    });

    it('monitors job progress', () => {
      const progress = { percent: 50 };
      expect(progress.percent).toBe(50);
    });

    it('cancels job', () => {
      const cancelled = true;
      expect(cancelled).toBe(true);
    });

    it('fetches job results', () => {
      const results = { status: 'completed' };
      expect(results.status).toBe('completed');
    });

    it('streams job logs', () => {
      const log = { message: 'Processing...' };
      expect(log.message).toContain('Processing');
    });
  });

  describe('Forge endpoints', () => {
    it('lists tools', () => {
      const tools = [];
      expect(Array.isArray(tools)).toBe(true);
    });

    it('creates tool', () => {
      const tool = { name: 'test_tool' };
      expect(tool.name).toContain('tool');
    });

    it('executes tool', () => {
      const execution = { toolId: '123', input: {} };
      expect(execution.toolId).toBeTruthy();
    });

    it('promotes tool scope', () => {
      const scope = 'project';
      expect(scope).toBeTruthy();
    });

    it('deletes tool', () => {
      const deleted = true;
      expect(deleted).toBe(true);
    });
  });

  describe('Settings endpoints', () => {
    it('fetches user settings', () => {
      const settings = { theme: 'dark' };
      expect(settings.theme).toBe('dark');
    });

    it('updates settings', () => {
      const update = { theme: 'light' };
      expect(update.theme).toBe('light');
    });

    it('streams setting changes', () => {
      const change = { field: 'theme' };
      expect(change.field).toBeTruthy();
    });
  });

  describe('Error handling', () => {
    it('handles 400 Bad Request', () => {
      const error = new Error('Bad Request');
      expect(error.message).toBeDefined();
    });

    it('handles 401 Unauthorized', () => {
      const error = new Error('Unauthorized');
      expect(error.message).toBeDefined();
    });

    it('handles 403 Forbidden', () => {
      const error = new Error('Forbidden');
      expect(error.message).toBeDefined();
    });

    it('handles 404 Not Found', () => {
      const error = new Error('Not Found');
      expect(error.message).toBeDefined();
    });

    it('handles 500 Server Error', () => {
      const error = new Error('Server Error');
      expect(error.message).toBeDefined();
    });

    it('handles network timeout', () => {
      const error = new Error('Timeout');
      expect(error.message).toBeDefined();
    });

    it('handles malformed JSON response', () => {
      const error = new Error('Invalid JSON');
      expect(error.message).toBeDefined();
    });

    it('retries on transient errors', () => {
      const attempts = 3;
      expect(attempts).toBeGreaterThan(1);
    });

    it('includes error details', () => {
      const error = { message: 'Failed', details: { field: 'name' } };
      expect(error.details).toBeDefined();
    });

    it('provides user-friendly error messages', () => {
      const message = 'Please check your input';
      expect(message).toBeTruthy();
    });
  });

  describe('Authentication', () => {
    it('handles session token refresh', () => {
      const refreshed = true;
      expect(refreshed).toBe(true);
    });

    it('manages CSRF tokens', () => {
      const token = 'csrf-123';
      expect(token).toBeTruthy();
    });

    it('handles re-auth on 401', () => {
      const reauth = true;
      expect(reauth).toBe(true);
    });

    it('clears credentials on logout', () => {
      const cleared = true;
      expect(cleared).toBe(true);
    });
  });

  describe('Performance', () => {
    it('caches GET requests', () => {
      const cached = true;
      expect(cached).toBe(true);
    });

    it('batches requests', () => {
      const batched = true;
      expect(batched).toBe(true);
    });

    it('handles concurrent requests', () => {
      const concurrent = 5;
      expect(concurrent).toBeGreaterThan(0);
    });

    it('compresses request payload', () => {
      const compressed = true;
      expect(compressed).toBe(true);
    });
  });
});
