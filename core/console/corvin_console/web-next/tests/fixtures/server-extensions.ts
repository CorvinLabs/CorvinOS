import { http, HttpResponse } from 'msw';

/**
 * MSW Handler extensions for Phase 8:
 * - Error case handlers (404, 500, timeout)
 * - SSE stream endpoints
 * - Missing API endpoints
 * - Rate limiting simulation
 */

export const errorHandlers = [
  // 404 Not Found handlers
  http.get('/api/notfound', () => {
    return HttpResponse.json(
      { error: 'Resource not found' },
      { status: 404 }
    );
  }),

  // 500 Server Error handlers
  http.get('/api/error', () => {
    return HttpResponse.json(
      { error: 'Internal server error' },
      { status: 500 }
    );
  }),

  // 401 Unauthorized handlers
  http.get('/api/unauthorized', () => {
    return HttpResponse.json(
      { error: 'Unauthorized' },
      { status: 401 }
    );
  }),

  // 403 Forbidden handlers
  http.get('/api/forbidden', () => {
    return HttpResponse.json(
      { error: 'Forbidden' },
      { status: 403 }
    );
  }),

  // 429 Rate Limited handlers
  http.get('/api/rate-limited', () => {
    return HttpResponse.json(
      { error: 'Too many requests' },
      { status: 429, headers: { 'Retry-After': '60' } }
    );
  }),

  // 400 Bad Request handlers
  http.post('/api/invalid', () => {
    return HttpResponse.json(
      { error: 'Bad request', details: { field: 'required' } },
      { status: 400 }
    );
  }),
];

export const streamHandlers = [
  // SSE endpoint for task updates
  http.get('/api/tasks/:id/events', ({ params }) => {
    const { id } = params;
    const stream = new ReadableStream({
      start(controller) {
        const interval = setInterval(() => {
          controller.enqueue(
            `data: ${JSON.stringify({
              taskId: id,
              type: 'progress',
              progress: Math.random() * 100,
            })}\n\n`
          );
        }, 1000);

        setTimeout(() => {
          clearInterval(interval);
          controller.close();
        }, 10000);
      },
    });

    return new HttpResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
    });
  }),

  // SSE endpoint for workflow execution
  http.get('/api/workflows/:id/stream', ({ params }) => {
    const { id } = params;
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(
          `data: ${JSON.stringify({
            workflowId: id,
            type: 'started',
            timestamp: new Date().toISOString(),
          })}\n\n`
        );

        const interval = setInterval(() => {
          controller.enqueue(
            `data: ${JSON.stringify({
              workflowId: id,
              type: 'step_completed',
              step: Math.floor(Math.random() * 5),
            })}\n\n`
          );
        }, 500);

        setTimeout(() => {
          clearInterval(interval);
          controller.enqueue(
            `data: ${JSON.stringify({
              workflowId: id,
              type: 'completed',
            })}\n\n`
          );
          controller.close();
        }, 5000);
      },
    });

    return new HttpResponse(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache',
      },
    });
  }),
];

export const apiHandlers = [
  // Tasks endpoints (missing from main server)
  http.get('/api/tasks', () => {
    return HttpResponse.json({
      tasks: [
        { id: '1', title: 'Task 1', status: 'completed' },
        { id: '2', title: 'Task 2', status: 'running' },
        { id: '3', title: 'Task 3', status: 'pending' },
      ],
    });
  }),

  http.post('/api/tasks', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json(
      {
        id: 'task-' + Date.now(),
        ...body,
        status: 'pending',
        created_at: new Date().toISOString(),
      },
      { status: 201 }
    );
  }),

  http.get('/api/tasks/:id', ({ params }) => {
    return HttpResponse.json({
      id: params.id,
      title: 'Test Task',
      status: 'running',
      progress: 45,
    });
  }),

  http.put('/api/tasks/:id', async ({ request, params }) => {
    const body = await request.json();
    return HttpResponse.json({
      id: params.id,
      ...body,
      updated_at: new Date().toISOString(),
    });
  }),

  http.delete('/api/tasks/:id', () => {
    return HttpResponse.json({ success: true }, { status: 204 });
  }),

  // Workflows endpoints (missing)
  http.get('/api/workflows', () => {
    return HttpResponse.json({
      workflows: [
        { id: '1', name: 'Workflow 1', status: 'published' },
        { id: '2', name: 'Workflow 2', status: 'draft' },
      ],
    });
  }),

  http.post('/api/workflows', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json(
      {
        id: 'workflow-' + Date.now(),
        ...body,
        created_at: new Date().toISOString(),
      },
      { status: 201 }
    );
  }),

  http.get('/api/workflows/:id', ({ params }) => {
    return HttpResponse.json({
      id: params.id,
      name: 'Test Workflow',
      yaml: 'steps: []',
      status: 'published',
    });
  }),

  // Compute endpoints (missing)
  http.get('/api/compute/jobs', () => {
    return HttpResponse.json({
      jobs: [
        { id: '1', type: 'training', status: 'running' },
        { id: '2', type: 'inference', status: 'completed' },
      ],
    });
  }),

  http.post('/api/compute/jobs', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json(
      {
        id: 'job-' + Date.now(),
        ...body,
        status: 'pending',
      },
      { status: 201 }
    );
  }),

  // Forge endpoints (missing)
  http.get('/api/forge/tools', () => {
    return HttpResponse.json({
      tools: [
        { id: '1', name: 'Tool 1', scope: 'session' },
        { id: '2', name: 'Tool 2', scope: 'project' },
      ],
    });
  }),

  http.post('/api/forge/tools', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json(
      {
        id: 'tool-' + Date.now(),
        ...body,
        scope: 'session',
      },
      { status: 201 }
    );
  }),

  // Voice endpoints (missing)
  http.get('/api/voice/config', () => {
    return HttpResponse.json({
      stt_provider: 'openai_whisper',
      tts_provider: 'openai',
      tts_voice: 'nova',
      language: 'en',
    });
  }),

  http.put('/api/voice/config', async ({ request }) => {
    const body = await request.json();
    return HttpResponse.json({
      ...body,
      updated_at: new Date().toISOString(),
    });
  }),

  // Compliance endpoints (missing)
  http.get('/api/compliance/audit', () => {
    return HttpResponse.json({
      events: [
        { id: '1', type: 'login', timestamp: new Date().toISOString() },
        { id: '2', type: 'api_call', timestamp: new Date().toISOString() },
      ],
    });
  }),

  http.get('/api/compliance/status', () => {
    return HttpResponse.json({
      gdpr_compliant: true,
      eu_ai_act_compliant: true,
      audit_chain_integrity: 'valid',
    });
  }),
];

export const allExtensions = [...errorHandlers, ...streamHandlers, ...apiHandlers];
