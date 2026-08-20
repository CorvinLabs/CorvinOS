/**
 * Skill Creator panel — browser-level coverage of the operator's loop:
 * generate → watch → inspect → refine → delete (ADR-0405).
 *
 * Runs against the live console. Every backend call is stubbed with
 * FICTIONAL skills: a real run spends minutes of engine time on the
 * operator's Claude subscription, which no test suite should do on every
 * pass. The server-side E2E (core/console/tests/test_skill_creator_e2e.py)
 * exercises the real endpoints, and one opt-in test there drives the real
 * engine end to end.
 */
import { test, expect, type Page } from '@playwright/test';

const SKILL_A = {
  name: 'assistant.check_json_syntax',
  description: 'Validates JSON files and reports every syntax error with its line number.',
  type: 'learned-experience',
  scope: 'user',
  created_by: 'skill-creator',
  n_grades: 1,
  mean_score: 0.3,
  injectable: true,
};

const SKILL_B = {
  name: 'assistant.lint_yaml',
  description: 'Checks YAML files for duplicate keys and bad indentation.',
  type: 'learned-experience',
  scope: 'user',
  created_by: 'skill-creator',
  n_grades: 0,
  mean_score: 0,
  injectable: false,
};

const BODY_A =
  '---\nname: assistant.check_json_syntax\ntype: learned-experience\n---\n\n' +
  '# Validate JSON\n\n1. Read the file\n2. Parse it strictly\n3. Report each error';

function listing(skills: Array<Record<string, unknown>>) {
  return {
    tenant_id: '_default',
    count: skills.length,
    injectable_count: skills.filter((s) => s.injectable).length,
    skills,
  };
}

function runStatus(over: Record<string, unknown> = {}) {
  return {
    run_id: 'run-stub000001',
    status: 'running',
    phase: 'ldd_iteration',
    progress: 50,
    message: 'Running LDD test loop…',
    engine: 'claude_code',
    phases: ['planning', 'validation', 'ldd_iteration', 'review', 'promotion'],
    error: null,
    base_skill: null,
    ...over,
  };
}

/** Stub the whole Skill-Creator surface. Returns captured requests. */
async function stubApi(
  page: Page,
  opts: {
    skills?: Array<Record<string, unknown>>;
    status?: Record<string, unknown>;
    deleteStatus?: number;
  } = {},
) {
  const captured: { generate: any[]; deleted: string[]; csrf: (string | undefined)[] } = {
    generate: [],
    deleted: [],
    csrf: [],
  };
  const skills = opts.skills ?? [SKILL_A, SKILL_B];

  await page.route('**/skill-creator/skills', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(listing(skills)),
    }),
  );

  await page.route('**/skill-creator/skills/*', async (route) => {
    const req = route.request();
    const name = decodeURIComponent(req.url().split('/').pop() || '');
    if (req.method() === 'DELETE') {
      captured.deleted.push(name);
      captured.csrf.push(req.headers()['x-csrf-token']);
      const status = opts.deleteStatus ?? 200;
      return route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(status === 200 ? { ok: true, name } : { detail: 'nope' }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...SKILL_A, name, sha256: 'abc123', grades: [
        { score: 0.3, notes: 'manual bootstrap seed by skill-creator — NOT earned usage' },
      ], body: BODY_A }),
    });
  });

  await page.route('**/skill-creator/generate', async (route) => {
    captured.generate.push(route.request().postDataJSON());
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({
        status: 'accepted',
        run_id: 'run-stub000001',
        engine: 'claude_code',
        base_skill: route.request().postDataJSON()?.base_skill ?? null,
        message: 'started',
      }),
    });
  });

  await page.route('**/skill-creator/status/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(runStatus(opts.status)),
    }),
  );

  return captured;
}

async function openPanel(page: Page) {
  await page.goto('/console/app/skills', { waitUntil: 'domcontentloaded' });
  await expect(
    page.getByRole('heading', { name: 'Skill Creator', exact: true }),
  ).toBeVisible({ timeout: 20000 });
}

test.describe('Skill Creator — panel', () => {
  test('renders the composer and the skill library', async ({ page }) => {
    await stubApi(page);
    await openPanel(page);

    await expect(page.getByTestId('skill-request')).toBeVisible();
    await expect(page.getByTestId('submit-generation')).toBeDisabled();
    await expect(page.locator('[data-testid="skill-row"]')).toHaveCount(2);
  });

  test('shows which skills are actually usable', async ({ page }) => {
    // A registered skill with no grade sits below the injection gate. The UI
    // must not present it as if it were in use.
    await stubApi(page);
    await openPanel(page);

    const rowA = page.locator(`[data-skill="${SKILL_A.name}"]`);
    const rowB = page.locator(`[data-skill="${SKILL_B.name}"]`);
    await expect(rowA.getByText('usable', { exact: true })).toBeVisible();
    await expect(rowB.getByText('inert', { exact: true })).toBeVisible();
  });

  test('submits a generation request to the right endpoint', async ({ page }) => {
    const captured = await stubApi(page);
    await openPanel(page);

    await page.getByTestId('skill-request').fill('Create a skill that validates JSON files');
    await page.getByTestId('submit-generation').click();

    await expect(page.getByTestId('phase-stepper')).toBeVisible();
    expect(captured.generate).toHaveLength(1);
    expect(captured.generate[0].user_request).toContain('validates JSON');
    expect(captured.generate[0].base_skill).toBeUndefined();
  });
});

test.describe('Skill Creator — progress', () => {
  test('renders the five real phases and the engine', async ({ page }) => {
    await stubApi(page);
    await openPanel(page);

    await page.getByTestId('skill-request').fill('Create a skill that validates JSON files');
    await page.getByTestId('submit-generation').click();

    const stepper = page.getByTestId('phase-stepper');
    await expect(stepper.locator('li')).toHaveCount(5);
    await expect(stepper.locator('li[data-phase="ldd_iteration"]')).toHaveAttribute(
      'data-state',
      'active',
    );
    await expect(stepper.locator('li[data-phase="planning"]')).toHaveAttribute(
      'data-state',
      'done',
    );
    await expect(page.getByTestId('engine-label')).toContainText('Claude subscription');
  });

  test('explains a low quality score with the review findings', async ({ page }) => {
    await stubApi(page, {
      status: {
        status: 'success',
        phase: 'promotion',
        progress: 100,
        message: 'done',
        skill: {
          name: 'assistant.phase_gated_executor',
          purpose: 'Runs multi-phase tasks to completion.',
          scope: 'assistant',
          quality: 0.0,
          iterations: 5,
          dependencies: [],
          injectable: true,
          findings: [
            { dimension: 'correctness', summary: 'step 3 cannot be executed', verdict: 'confirmed' },
            { dimension: 'scope_creep', summary: 'adds session management', verdict: 'plausible' },
          ],
        },
      },
    });
    await openPanel(page);

    await page.getByTestId('skill-request').fill('Create a skill that runs multi-phase tasks');
    await page.getByTestId('submit-generation').click();

    await expect(page.getByTestId('skill-iterations')).toHaveText('5');
    await expect(page.getByTestId('skill-injectable')).toContainText('registered');

    const findings = page.getByTestId('review-findings');
    await expect(findings).toContainText('2 review finding');
    await findings.locator('summary').click();
    await expect(findings).toContainText('step 3 cannot be executed');
  });

  test('surfaces an engine failure with its detail', async ({ page }) => {
    await stubApi(page, {
      status: {
        status: 'failed',
        phase: 'planning',
        progress: 10,
        message:
          'Claude Code CLI not found. Install it or set CORVIN_CLAUDE_BIN to its path — skill generation runs on your Claude subscription.',
        error: "claude binary not found: 'claude'",
      },
    });
    await openPanel(page);

    await page.getByTestId('skill-request').fill('Create a skill that validates JSON files');
    await page.getByTestId('submit-generation').click();

    const err = page.getByTestId('generation-error');
    await expect(err).toContainText('CORVIN_CLAUDE_BIN');
    await expect(err.locator('pre')).toContainText('claude binary not found');
  });
});

test.describe('Skill Creator — inspect', () => {
  test('View opens the skill body and its grades', async ({ page }) => {
    await stubApi(page);
    await openPanel(page);

    await page.locator(`[data-skill="${SKILL_A.name}"]`).getByRole('button', { name: 'View' }).click();

    const viewer = page.getByTestId('skill-viewer');
    await expect(viewer).toBeVisible();
    await expect(viewer.locator('pre')).toContainText('# Validate JSON');
    await viewer.locator('summary').click();
    await expect(viewer).toContainText('bootstrap seed');
  });

  test('clicking the row itself also opens the viewer', async ({ page }) => {
    await stubApi(page);
    await openPanel(page);

    await page.locator(`[data-skill="${SKILL_A.name}"] button`).first().click();
    await expect(page.getByTestId('skill-viewer')).toBeVisible();
  });

  test('the filter narrows the library', async ({ page }) => {
    const many = Array.from({ length: 6 }, (_, i) => ({
      ...SKILL_B,
      name: `assistant.skill_${i}`,
      description: i === 3 ? 'the needle' : 'haystack',
    }));
    await stubApi(page, { skills: many });
    await openPanel(page);

    await expect(page.locator('[data-testid="skill-row"]')).toHaveCount(6);
    await page.getByTestId('skill-filter').fill('needle');
    await expect(page.locator('[data-testid="skill-row"]')).toHaveCount(1);
    await expect(page.locator('[data-testid="skill-row"]')).toContainText('assistant.skill_3');
  });
});

test.describe('Skill Creator — refine', () => {
  test('Refine targets the existing skill and sends base_skill', async ({ page }) => {
    const captured = await stubApi(page);
    await openPanel(page);

    await page.getByTestId(`refine-${SKILL_A.name}`).click();

    const banner = page.getByTestId('refine-banner');
    await expect(banner).toContainText(SKILL_A.name);
    await expect(banner).toContainText('replaced in place');
    await expect(page.getByTestId('submit-generation')).toContainText('Refine Skill');

    await page.getByTestId('skill-request').fill('also report duplicate keys as warnings');
    await page.getByTestId('submit-generation').click();

    expect(captured.generate).toHaveLength(1);
    expect(captured.generate[0].base_skill).toBe(SKILL_A.name);
    expect(captured.generate[0].user_request).toContain('duplicate keys');
  });

  test('cancelling refine returns to plain generation', async ({ page }) => {
    const captured = await stubApi(page);
    await openPanel(page);

    await page.getByTestId(`refine-${SKILL_A.name}`).click();
    await page.getByTestId('cancel-refine').click();
    await expect(page.getByTestId('refine-banner')).toHaveCount(0);
    await expect(page.getByTestId('submit-generation')).toContainText('Generate Skill');

    await page.getByTestId('skill-request').fill('a completely different new skill please');
    await page.getByTestId('submit-generation').click();
    expect(captured.generate[0].base_skill).toBeUndefined();
  });

  test('Refine can be started from the viewer', async ({ page }) => {
    await stubApi(page);
    await openPanel(page);

    await page.locator(`[data-skill="${SKILL_A.name}"]`).getByRole('button', { name: 'View' }).click();
    await page.getByTestId('viewer-refine').click();

    await expect(page.getByTestId('refine-banner')).toContainText(SKILL_A.name);
  });
});

test.describe('Skill Creator — delete', () => {
  test('asks before deleting and then calls DELETE with a CSRF token', async ({ page }) => {
    const captured = await stubApi(page);
    await openPanel(page);

    await page.getByTestId(`delete-${SKILL_A.name}`).click();

    // Destructive and not undoable — it must confirm first.
    const dialog = page.getByRole('dialog');
    await expect(dialog).toContainText('Delete skill?');
    await expect(dialog).toContainText(SKILL_A.name);
    expect(captured.deleted).toHaveLength(0);

    await page.getByTestId('confirm-delete').click();

    await expect(page.getByTestId('skill-toast')).toContainText('Deleted');
    expect(captured.deleted).toEqual([SKILL_A.name]);
    expect(captured.csrf[0]).toBeTruthy();
  });

  test('cancelling the dialog deletes nothing', async ({ page }) => {
    const captured = await stubApi(page);
    await openPanel(page);

    await page.getByTestId(`delete-${SKILL_A.name}`).click();
    await page.getByRole('button', { name: 'Cancel' }).click();

    await expect(page.getByRole('dialog')).toHaveCount(0);
    expect(captured.deleted).toHaveLength(0);
  });

  test('a failed delete is reported, not swallowed', async ({ page }) => {
    await stubApi(page, { deleteStatus: 404 });
    await openPanel(page);

    await page.getByTestId(`delete-${SKILL_A.name}`).click();
    await page.getByTestId('confirm-delete').click();

    await expect(page.getByTestId('skill-toast')).toBeVisible();
    await expect(page.locator('[data-testid="skill-row"]')).toHaveCount(2);
  });
});
