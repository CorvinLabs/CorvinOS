// Standalone screenshot of the Vibe Engineering Context Pipeline + detail drawer.
// Mocks whoami/setup + the Layer-A traces route + the /explain brief, so it
// verifies the COMPONENT visually without the full gateway/login stack.
import { chromium } from "@playwright/test";
import { mkdirSync } from "fs";

const OUT = process.env.SHOT_OUT || "/tmp/vibe-shots";
mkdirSync(OUT, { recursive: true });
const now = Math.floor(Date.now() / 1000);
const BRIEF_SHA = "443247bcd61f06bf3789bd8f26e4aa5a3efd2f3bbd665e1ea33f86d2b9be08a2";

const TRACES = {
  tenant_id: "_default", available: true,
  sessions: [
    { session: "discord:1501540900529246251", turns: [
      { turn_id: "turn-msn2hwwn_e902aa", ts: now - 20, hash: "7384bdb31a29ae38",
        prev_hash: "f59a74f058ec9c5d", top_score: 0.6, stages_ok: 3,
        brief_sha256: BRIEF_SHA, brief_bytes: 641, stages: [
          { stage: "memory", status: "ok", confidence_tier: "high", duration_ms: 96,
            sources: [{ id: "adr-0222-tde-corrected-foundation.md", score: 1.0 },
                      { id: "audit-verify-fails-forge-chain.md", score: 0.83 },
                      { id: "feedback-dead-mechanism.md", score: 0.5 }] },
          { stage: "graph", status: "ok", confidence_tier: "medium",
            sources: [{ id: "ADR-0178", score: 0.5 }, { id: "ADR-0215", score: 0.5 }] },
          { stage: "skill", status: "ok", confidence_tier: "medium",
            sources: [{ id: "adr_gate", score: 0.6 }] },
          { stage: "approach_synthesis", status: "not_run", reason: "stage_inactive" },
          { stage: "blocker_id", status: "not_run", reason: "stage_inactive" },
        ] },
      { turn_id: "turn-msn0opj1_a51727", ts: now - 300, hash: "a91c2fe0b1",
        top_score: 0.0, stages_ok: 3, degraded: null, brief_sha256: null,
        stages: [
          { stage: "memory", status: "ok", confidence_tier: "low", sources: [] },
          { stage: "graph", status: "failed", confidence_tier: "low", sources: [],
            reason: "ADR loader timed out (5s)" },
          { stage: "skill", status: "ok", confidence_tier: "low", sources: [] },
          { stage: "approach_synthesis", status: "not_run", reason: "stage_inactive" },
          { stage: "blocker_id", status: "not_run", reason: "stage_inactive" },
        ] },
    ] },
  ],
};
const BRIEF_TEXT = `## Context brief (Vibe Engineering)
Relevant past memory:
  - ADR-0222 TDE corrected foundation
  - audit-verify fails: forge chain mac tampered
  - feedback: dead mechanism needs call-site test
Related decisions (ADRs):
  - ADR-0178: Two-tier self-improvement loop
  - ADR-0215: TDE token routing
Recommended skills:
  - adr_gate — Architectural Decision Record discipline`;
const EMPTY = { tenant_id: "_default", available: true, sessions: [] };

async function routes(page, tracesBody) {
  await page.route("**/v1/console/**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
  await page.route("**/v1/console/auth/whoami", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ tier: "owner", tenant_id: "_default",
        fingerprint: "fp", csrf_token: "c", expires_at: now + 3600 }) }));
  await page.route("**/v1/console/setup/status", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ setup_complete: true }) }));
  await page.route("**/v1/console/vibe-engineering/explain/**", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ found: true, brief_sha256: BRIEF_SHA, text: BRIEF_TEXT }) }));
  await page.route("**/v1/console/vibe-engineering/traces*", (r) =>
    r.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(tracesBody) }));
}

async function run() {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1400 } });

  // 1) overview
  let page = await ctx.newPage();
  await routes(page, TRACES);
  await page.goto("http://localhost:5173/console/app/vibe-engineering", { waitUntil: "networkidle" });
  await page.waitForTimeout(1000);
  await page.screenshot({ path: `${OUT}/vibe-pipeline.png`, fullPage: true });
  console.log("wrote vibe-pipeline.png");

  // 2) detail drawer — click the first turn card
  await page.getByText("turn-msn2hwwn_e902aa").first().click();
  await page.waitForTimeout(900);
  await page.screenshot({ path: `${OUT}/vibe-detail.png`, fullPage: false });
  console.log("wrote vibe-detail.png");
  await page.close();

  // 3) empty state
  page = await ctx.newPage();
  await routes(page, EMPTY);
  await page.goto("http://localhost:5173/console/app/vibe-engineering", { waitUntil: "networkidle" });
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/vibe-empty.png`, fullPage: false });
  console.log("wrote vibe-empty.png");

  await browser.close();
}
await run();
