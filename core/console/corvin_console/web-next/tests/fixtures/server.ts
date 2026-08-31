import { setupServer } from 'msw/node';
import { http, HttpResponse } from 'msw';

export const handlers = [
  // Auth Endpoints
  http.post('/api/auth/login', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    if (body.email === 'test@example.com' && body.password === 'password123') {
      return HttpResponse.json(
        {
          token: 'jwt-test-token-abc123',
          user: {
            id: 'user-1',
            email: 'test@example.com',
            role: 'admin',
            name: 'Test User',
          },
        },
        { status: 200 }
      );
    }
    return HttpResponse.json(
      { error: 'Invalid credentials' },
      { status: 401 }
    );
  }),

  http.post('/api/auth/logout', () => {
    return HttpResponse.json({ success: true });
  }),

  http.get('/api/auth/me', () => {
    return HttpResponse.json({
      id: 'user-1',
      email: 'test@example.com',
      role: 'admin',
      name: 'Test User',
    });
  }),

  // Dashboard Endpoints
  http.get('/api/dashboard', () => {
    return HttpResponse.json({
      engines_online: 2,
      channels: ['discord', 'telegram', 'slack'],
      audit_events_today: 142,
      last_sync: '2026-06-02T10:30:00Z',
      uptime_percent: 99.8,
    });
  }),

  // Settings Endpoints
  http.get('/api/settings', () => {
    return HttpResponse.json({
      theme: 'light',
      notifications_enabled: true,
      default_engine: 'claude',
      timeout_ms: 30000,
      language: 'en',
    });
  }),

  http.put('/api/settings', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json(
      { ...body, updated_at: new Date().toISOString() },
      { status: 200 }
    );
  }),

  // Personas Endpoints
  http.get('/api/personas', () => {
    return HttpResponse.json({
      personas: [
        {
          id: 'assistant',
          name: 'Assistant',
          description: 'General assistant',
        },
        {
          id: 'coder',
          name: 'Coder',
          description: 'Code generation specialist',
        },
        {
          id: 'researcher',
          name: 'Researcher',
          description: 'Research and analysis',
        },
      ],
    });
  }),

  // Engines Endpoints
  http.get('/api/engines', () => {
    return HttpResponse.json({
      engines: [
        { id: 'claude', name: 'Claude', status: 'online', version: '4.0' },
        { id: 'hermes', name: 'Hermes', status: 'online', version: '1.0' },
      ],
    });
  }),

  http.put('/api/engines/:id/select', ({ params }) => {
    return HttpResponse.json({
      selected_engine: params.id,
      message: 'Engine selected successfully',
    });
  }),

  // Compute Job Endpoints
  http.get('/api/compute/jobs', () => {
    return HttpResponse.json({
      jobs: [
        {
          id: 'job-1',
          status: 'completed',
          created_at: '2026-06-02T09:00:00Z',
          completed_at: '2026-06-02T09:05:00Z',
          result_rows: 150,
          query: 'SELECT COUNT(*) FROM events',
        },
        {
          id: 'job-2',
          status: 'running',
          created_at: '2026-06-02T10:00:00Z',
          progress: 0.45,
          query: 'SELECT * FROM users LIMIT 1000',
        },
      ],
    });
  }),

  http.post('/api/compute/jobs', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        job_id: `job-${Date.now()}`,
        status: 'pending',
        created_at: new Date().toISOString(),
        query: body.query,
        engine: body.engine,
      },
      { status: 201 }
    );
  }),

  http.get('/api/compute/jobs/:jobId', ({ params }) => {
    if (params.jobId === 'job-error') {
      return HttpResponse.json(
        { error: 'Job not found' },
        { status: 404 }
      );
    }
    return HttpResponse.json({
      id: params.jobId,
      status: 'running',
      progress: 0.5,
      created_at: '2026-06-02T10:00:00Z',
      estimated_completion: '2026-06-02T10:05:00Z',
    });
  }),

  http.post('/api/compute/jobs/:jobId/cancel', ({ params }) => {
    return HttpResponse.json({
      id: params.jobId,
      status: 'cancelled',
      message: 'Job cancelled successfully',
    });
  }),

  http.get('/api/compute/jobs/:jobId/results', ({ params }) => {
    return HttpResponse.json({
      job_id: params.jobId,
      results: [
        { id: 1, name: 'Row 1', value: 100 },
        { id: 2, name: 'Row 2', value: 200 },
      ],
      row_count: 2,
      execution_time_ms: 5000,
    });
  }),

  // Forge Tool Endpoints
  http.get('/api/forge/tools', () => {
    return HttpResponse.json({
      tools: [
        {
          name: 'code.example_tool',
          description: 'Example tool for testing',
          scope: 'session',
          created_at: '2026-06-01T10:00:00Z',
        },
        {
          name: 'code.data_processor',
          description: 'Process data streams',
          scope: 'project',
          created_at: '2026-06-01T11:00:00Z',
        },
      ],
    });
  }),

  http.post('/api/forge/tools', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        name: body.name,
        description: body.description,
        scope: body.scope || 'session',
        created_at: new Date().toISOString(),
        message: 'Tool created successfully',
      },
      { status: 201 }
    );
  }),

  http.get('/api/forge/tools/:name', ({ params }) => {
    return HttpResponse.json({
      name: params.name,
      description: 'A test tool',
      scope: 'session',
      created_at: '2026-06-01T10:00:00Z',
      implementation: 'print("Hello")',
      schema: { type: 'object', properties: {} },
    });
  }),

  http.put('/api/forge/tools/:name', async ({ request, params }) => {
    const body = await request.json();
    return HttpResponse.json({
      name: params.name,
      ...body,
      updated_at: new Date().toISOString(),
      message: 'Tool updated successfully',
    });
  }),

  http.delete('/api/forge/tools/:name', ({ params }) => {
    return HttpResponse.json({
      name: params.name,
      deleted: true,
      message: 'Tool deleted successfully',
    });
  }),

  http.post('/api/forge/tools/:name/execute', async ({ request, params }) => {
    const { input } = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json({
      tool_name: params.name,
      output: { result: 'success', input_echo: input },
      execution_time_ms: 125,
    });
  }),

  // Skills Endpoints
  http.get('/api/skills', () => {
    return HttpResponse.json({
      skills: [
        {
          name: 'iterative-refinement',
          type: 'domain',
          description: 'Refine iteratively',
          scope: 'session',
        },
        {
          name: 'loop-driven-engineering',
          type: 'persona-style',
          description: 'Drive implementation via loops',
          scope: 'project',
        },
      ],
    });
  }),

  http.post('/api/skills', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        name: body.name,
        type: body.type,
        description: body.description,
        scope: 'session',
        created_at: new Date().toISOString(),
      },
      { status: 201 }
    );
  }),

  // Workflows Endpoints
  http.get('/api/workflows', () => {
    return HttpResponse.json({
      workflows: [
        {
          id: 'wf-1',
          name: 'Data Processing',
          status: 'completed',
          created_at: '2026-06-01T10:00:00Z',
        },
        {
          id: 'wf-2',
          name: 'Analytics',
          status: 'running',
          created_at: '2026-06-02T10:00:00Z',
        },
      ],
    });
  }),

  http.post('/api/workflows', async ({ request }) => {
    const body = (await request.json()) as Record<string, unknown>;
    return HttpResponse.json(
      {
        id: `wf-${Date.now()}`,
        name: body.name,
        status: 'pending',
        created_at: new Date().toISOString(),
      },
      { status: 201 }
    );
  }),

  http.get('/api/workflows/:id', ({ params }) => {
    return HttpResponse.json({
      id: params.id,
      name: 'Example Workflow',
      status: 'running',
      progress: 0.6,
      created_at: '2026-06-02T10:00:00Z',
    });
  }),

  // Compliance & Audit Endpoints
  http.get('/api/audit/events', () => {
    return HttpResponse.json({
      events: [
        {
          id: 'evt-1',
          type: 'tool.created',
          timestamp: '2026-06-02T10:00:00Z',
          actor: 'user-1',
          details: { tool: 'code.test' },
        },
        {
          id: 'evt-2',
          type: 'job.submitted',
          timestamp: '2026-06-02T09:30:00Z',
          actor: 'user-1',
          details: { job_id: 'job-1' },
        },
      ],
    });
  }),

  http.get('/api/compliance/status', () => {
    return HttpResponse.json({
      gdpr: 'compliant',
      eu_ai_act: 'compliant',
      audit_chain: 'intact',
      last_verified: '2026-06-02T10:00:00Z',
    });
  }),

  // File Upload Endpoints
  http.post('/api/files/upload', () => {
    return HttpResponse.json(
      {
        file_id: `file-${Date.now()}`,
        name: 'test-file.txt',
        size: 1024,
        url: '/api/files/test-file.txt',
        created_at: new Date().toISOString(),
      },
      { status: 201 }
    );
  }),

  http.get('/api/files', () => {
    return HttpResponse.json({
      files: [
        {
          id: 'file-1',
          name: 'data.csv',
          size: 2048,
          created_at: '2026-06-01T10:00:00Z',
        },
        {
          id: 'file-2',
          name: 'config.json',
          size: 512,
          created_at: '2026-06-02T10:00:00Z',
        },
      ],
    });
  }),
];

export const server = setupServer(...handlers);
