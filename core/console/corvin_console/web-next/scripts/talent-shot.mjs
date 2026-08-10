// Screenshot the Your Talent page with realistic real-shaped data (mocked routes)
// to verify the page renders the new cel.decision-derived structure.
import { chromium } from "@playwright/test";
import { mkdirSync } from "fs";
const OUT = process.env.SHOT_OUT || "/tmp/vibe-shots";
mkdirSync(OUT, { recursive: true });
const now = Math.floor(Date.now() / 1000);

const SCORE = {
  talent_score: 6.2, trend: 0.4, empty: false,
  components: { accuracy: 0.72, learning_rate: 0.55, variety: 0.49, efficiency: 1.0 },
  ranking: [
    { id: "ADR-0155", rank: 1, medal: "🥇", status: "Top source", accuracy: 0.72, feedback_pct: 53.8 },
    { id: "ADR-0178", rank: 2, medal: "🥈", status: "Frequent", accuracy: 0.72, feedback_pct: 38.5 },
    { id: "ADR-0261", rank: 3, medal: "🥉", status: "Frequent", accuracy: 0.72, feedback_pct: 38.5 },
    { id: "tests-audit-chain-isolation.md", rank: 4, medal: "⭐", status: "Frequent", accuracy: 0.72, feedback_pct: 30.8 },
  ],
  events: [
    { timestamp: new Date().toISOString(), type: "milestone", title: "13 context-engineered turns", description: "Average context score 0.62", badge: "🧠" },
    { timestamp: new Date().toISOString(), type: "achievement", title: "Most-used source", description: "ADR-0155", badge: "📌" },
  ],
};
const HISTORY = { empty: false, daily: Array.from({ length: 5 }, (_, i) => ({
  date: `2026-08-0${i + 6}`, score: 5.5 + i * 0.2, accuracy: 0.72, learning_rate: 0.55,
  variety: 0.49, efficiency: 1.0, record_count: 2 + i })) };
const TASK_TYPES = { empty: false, task_types: [
  { type: "Graph Traversal", count: 60, accuracy: 0.72, feedback_percentage: 100, efficiency: 0.49 },
  { type: "Memory Lookup", count: 41, accuracy: 0.72, feedback_percentage: 100, efficiency: 0.49 },
  { type: "Skill Injection", count: 10, accuracy: 0.72, feedback_percentage: 100, efficiency: 0.49 },
  { type: "Approach Synthesis", count: 10, accuracy: 0.72, feedback_percentage: 100, efficiency: 0.49 },
] };
const CORR = { empty: false, correlation: { points: Array.from({ length: 13 }, (_, i) => ({ accuracy: 0.4 + (i % 6) * 0.1, efficiency: 0.3 + (i % 5) * 0.12 })) } };
const INSIGHTS = { empty: false,
  dimensions: [
    { dimension: "Context quality", icon: "🎯", current: 62, change: 0, status: "flat", narrative: "Average top-source relevance per turn", analysis: "x" },
    { dimension: "Learning rate", icon: "📚", current: 55, change: 0, status: "flat", narrative: "Is context quality trending up?", analysis: "x" },
    { dimension: "Source variety", icon: "🎨", current: 49, change: 0, status: "flat", narrative: "Diversity of sources used", analysis: "x" },
    { dimension: "Enriched rate", icon: "⚡", current: 100, change: 0, status: "flat", narrative: "Turns enriched, not plain", analysis: "x" },
  ],
  narratives: [{ icon: "🧠", title: "Context-engineered turns", description: "13 turns enriched so far" }],
  badges: [{ badge: "📌", title: "ADR-0155", context: "used 7×", level: "gold" },
           { badge: "📌", title: "ADR-0178", context: "used 5×", level: "silver" }] };
const STORY = { empty: false, story: {
  summary: "You have context-engineered 13 turns. Average context score 0.62, 100% enriched, 21 distinct sources drawn on.",
  score_start: 6.2, score_end: 6.2, score_change: 0.4, trend: "improving", milestone: "13 enriched turns" } };

const MAP = {
  "talent/score": SCORE, "talent/history": HISTORY, "talent/task-types": TASK_TYPES,
  "talent/correlation": CORR, "talent/insights": INSIGHTS, "talent/story": STORY,
};

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 1600 } })).newPage();
await page.route("**/v1/console/**", (r) => {
  const u = r.request().url();
  for (const [k, v] of Object.entries(MAP))
    if (u.includes("/talent/" + k.split("/")[1])) return r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(v) });
  return r.fulfill({ status: 200, contentType: "application/json", body: "{}" });
});
await page.route("**/v1/console/auth/whoami", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tier: "owner", tenant_id: "_default", fingerprint: "f", csrf_token: "c", expires_at: now + 3600 }) }));
await page.route("**/v1/console/setup/status", (r) => r.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ setup_complete: true }) }));
await page.goto("http://localhost:5173/console/app/talent", { waitUntil: "networkidle" });
await page.waitForTimeout(1500);
await page.screenshot({ path: `${OUT}/talent-real.png`, fullPage: true });
console.log("wrote talent-real.png");
await browser.close();
