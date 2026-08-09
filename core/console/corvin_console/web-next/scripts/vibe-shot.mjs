// Standalone screenshot of the Vibe Engineering Context Pipeline page.
// Mocks whoami (auth) + the traces route, so it verifies the COMPONENT visually
// without the full gateway/login stack. Run with the vite dev server on :5173.
import { chromium } from "@playwright/test";

const OUT = process.env.SHOT_OUT || "/tmp/vibe-shots";
import { mkdirSync } from "fs";
mkdirSync(OUT, { recursive: true });

const now = Math.floor(Date.now() / 1000);
const TRACES = {
  tenant_id: "_default",
  available: true,
  sessions: [
    {
      session: "web:a1b2c3",
      path: "web:a1b2c3",
      traces: [
        {
          turn_id: "turn-7", ts: now - 30,
          trace: {
            task_preview: "erklär mir postgres partial indexes",
            stages: [
              { stage: "memory", status: "ok", confidence_tier: "high",
                sources: [{ id: "pg-indexes.md", score: 0.82 },
                          { id: "query-planner.md", score: 0.61 },
                          { id: "vacuum.md", score: 0.44 }],
                duration_ms: 118, tokens_in: 0, tokens_out: 452 },
              { stage: "graph", status: "ok", confidence_tier: "medium",
                sources: [{ id: "ADR-0269", score: 0.53 },
                          { id: "ADR-0275", score: 0.41 }], duration_ms: 76,
                tokens_in: 0, tokens_out: 210 },
              { stage: "skill", status: "ok", confidence_tier: "high",
                sources: [{ id: "sql-explain-first", score: 0.79 }], duration_ms: 41,
                tokens_in: 0, tokens_out: 88 },
            ],
          },
        },
        {
          turn_id: "turn-6", ts: now - 240,
          trace: {
            task_preview: "refactor the auth module to use the plugin registry",
            stages: [
              { stage: "memory", status: "ok", confidence_tier: "medium",
                sources: [{ id: "plugin-registry.md", score: 0.58 }], duration_ms: 95,
                tokens_in: 0, tokens_out: 180 },
              { stage: "graph", status: "failed", confidence_tier: "low",
                sources: [], duration_ms: 5012,
                tokens_in: 0, tokens_out: 0, error: "ADR loader timed out (5s)" },
              { stage: "skill", status: "ok", confidence_tier: "low",
                sources: [], duration_ms: 22, tokens_in: 0, tokens_out: 0 },
            ],
          },
        },
      ],
    },
    {
      session: "discord:99887766",
      path: "discord:99887766",
      traces: [
        {
          turn_id: "turn-11", ts: now - 600,
          trace: {
            task_preview: "was ist der status von TDE",
            degraded: "ce_budget_or_license",
            stages: [],
          },
        },
      ],
    },
  ],
};

const EMPTY = { tenant_id: "_default", available: true, sessions: [] };

async function shot(name, tracesBody) {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1400 } });
  const page = await ctx.newPage();

  // Playwright checks routes in REVERSE registration order, so register the
  // generic fallback FIRST and the specific mocks LAST (they win).
  await page.route("**/v1/console/**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.route("**/v1/console/auth/whoami", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ tier: "owner", tenant_id: "_default",
        fingerprint: "fp-demo", csrf_token: "csrf-demo",
        expires_at: now + 3600 }) }));
  await page.route("**/v1/console/setup/status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ setup_complete: true }) }));
  await page.route("**/v1/console/vibe-engineering/traces*", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(tracesBody) }));

  await page.goto("http://localhost:5173/console/app/vibe-engineering",
    { waitUntil: "networkidle" });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  console.log(`wrote ${OUT}/${name}.png`);
  await browser.close();
}

await shot("vibe-pipeline", TRACES);
await shot("vibe-empty", EMPTY);
