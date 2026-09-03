/**
 * Mock Vibe Engineering data for E2E tests (Phase 4 k=3)
 *
 * Used with Playwright's page.route() to intercept /vibe-engineering/state
 * calls without needing a live backend.
 */

export const MOCK_VIBE_DATA = {
  active_task: {
    task_id: "test-task-001",
    description: "Process user query",
    status: "in_progress",
    started_at: new Date().toISOString(),
    progress_percent: 65,
  },
  workers: [
    {
      name: "Claude Code",
      status: "running" as const,
      latency_ms: 145,
      error_count: 0,
    },
    {
      name: "Hermes",
      status: "thinking" as const,
      latency_ms: 89,
      error_count: 0,
    },
    {
      name: "OpenCode",
      status: "idle" as const,
      latency_ms: undefined,
      error_count: 0,
    },
  ],
  original_context: {
    task_description: "Generate a chart showing monthly revenue trends",
    user_intent: "Visualize business metrics over time",
    hash_sha256: "abc123def456789",
    is_valid: true,
    created_at: new Date(Date.now() - 300000).toISOString(),
  },
  pipeline_context: {
    entropy_score: 0.32,
    tier_1_count: 3,
    tier_2_count: 5,
    tier_3_count: 2,
    preservation_rate: 0.94,
    injection_success_rate: 0.98,
  },
  talent: {
    score: 0.78,
    context_retention: 0.85,
    decision_quality: 0.72,
    learning_velocity: 0.68,
  },
  learning_events: [
    {
      id: "event-001",
      type: "confidence",
      skill_id: "os.routing",
      signal: 0.85,
      timestamp: new Date(Date.now() - 120000).toISOString(),
    },
    {
      id: "event-002",
      type: "outcome",
      skill_id: "os.context_adapter",
      signal: 0.92,
      timestamp: new Date(Date.now() - 60000).toISOString(),
    },
    {
      id: "event-003",
      type: "preference",
      skill_id: "os.delegation_router",
      signal: "prefer_opus",
      timestamp: new Date().toISOString(),
    },
  ],
  sessions: [
    {
      id: "session-001",
      status: "active",
      task_count: 12,
      decision_count: 45,
    },
    {
      id: "session-002",
      status: "completed",
      task_count: 8,
      decision_count: 31,
    },
  ],
  timestamp: new Date().toISOString(),
};

/**
 * Setup Playwright route interception for Vibe Engineering API
 * Call this in test.beforeEach() to mock all /vibe-engineering/state calls
 */
export async function setupVibeDataMock(page: any) {
  // Intercept and respond with mock data (do not abort; fulfill instead)
  await page.route("**/v1/console/vibe-engineering/state", (route: any) => {
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_VIBE_DATA),
    });
  });
}

/**
 * Setup route to simulate network errors (for error-handling tests)
 */
export async function setupVibeDataErrorMock(page: any, statusCode: number = 500) {
  await page.route("**/v1/console/vibe-engineering/state", (route: any) => {
    route.fulfill({
      status: statusCode,
      contentType: "application/json",
      body: JSON.stringify({ error: "Vibe Engineering service unavailable" }),
    });
  });
}

/**
 * Setup route to simulate slow network (for timeout/loading state tests)
 */
export async function setupVibeDataSlowMock(page: any, delayMs: number = 3000) {
  await page.route("**/v1/console/vibe-engineering/state", async (route: any) => {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_VIBE_DATA),
    });
  });
}
