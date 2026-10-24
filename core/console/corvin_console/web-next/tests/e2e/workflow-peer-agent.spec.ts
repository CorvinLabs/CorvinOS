/**
 * Workflow `peer_agent` node - real browser against the live console.
 *
 * Run: CORVIN_E2E_ALLOW_LIVE=1 CONSOLE_BASE_URL=http://127.0.0.1:8765/console npx playwright test workflow-peer-agent
 *
 * Needs a console build that knows the node type (backend restarted after the change). A workflow
 * with id prefix qa-wf- is created through the API and deleted afterwards. The peer LISTS the picker
 * reads (/federation/peers, /federation/peer-agents) are answered by the test (this installation has
 * no paired peer): that is display data only. The node is added, edited and SAVED through the real
 * node routes, so the server-side validation of the node is real. Nothing is run, nothing is sent.
 */
import { test, expect, type APIRequestContext } from "@playwright/test";
import { resolveConsoleBaseUrl } from './sandbox-origin';

const ORIGIN = (resolveConsoleBaseUrl()).replace(/\/console\/?$/, "");
const API = `${ORIGIN}/v1/console`;
const WID = `qa-wf-peer-${Date.now().toString(36)}`;

const YAML = `awp: "1.0.0"
workflow:
  name: qa_peer
  description: peer agent e2e
orchestration:
  engine: dag
  graph:
    - id: draft
      type: approval
      depends_on: []
      message: ok?
`;

async function csrfOf(request: APIRequestContext): Promise<string> {
  let status = 0;
  for (let i = 0; i < 4; i++) {
    const who = await request.get(`${API}/auth/whoami`);
    status = who.status();
    if (status === 200) return (await who.json()).csrf_token as string;
    await new Promise((r) => setTimeout(r, 1000 * (i + 1)));
  }
  expect(status).toBe(200);
  return "";
}

test.beforeAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const h = { "X-CSRF-Token": await csrfOf(ctx) };
  const r = await ctx.post(`${API}/workflows`, { headers: h, data: { id: WID, title: "QA peer agent" } });
  expect(r.status(), await r.text()).toBeLessThan(300);
  const y = await ctx.put(`${API}/workflows/${WID}/yaml`, { headers: h, data: { yaml: YAML } });
  expect(y.status(), await y.text()).toBe(200);
  await ctx.dispose();
});

test.afterAll(async ({ playwright }) => {
  const ctx = await playwright.request.newContext({ storageState: "./tests/e2e/auth-state.json" });
  await ctx.delete(`${API}/workflows/${WID}`, { headers: { "X-CSRF-Token": await csrfOf(ctx) } });
  await ctx.dispose();
});

async function openPalette(page: import("@playwright/test").Page) {
  await page.goto(`/console/app/workflows/${WID}`);
  await page.getByTitle("Add node").click();
  await expect(page.getByTestId("quick-add-palette")).toBeVisible();
}

test("no paired peer: the node is not added and the reason is shown, no example data", async ({ page }) => {
  await page.route("**/v1/console/federation/peer-agents", (r) => r.fulfill({ json: { agents: [] } }));
  await page.route("**/v1/console/federation/peers", (r) => r.fulfill({ json: { peers: [] } }));
  await openPalette(page);
  await page.getByTestId("quick-add-peer_agent").click();
  await expect(page.getByText("No peer agent is available.")).toBeVisible();
  const yaml = await (await page.request.get(`${API}/workflows/${WID}`)).text();
  expect(yaml).not.toContain("peer_agent");
});

test("add the node from the palette, pick a peer agent, save: the server validates and keeps it", async ({ page }) => {
  const agents = [
    { agent_id: "reviewer", address: "agent://iid-acme-0001/reviewer", endpoint_id: "ep_acme",
      peer_instance_id: "iid-acme-0001", model: "claude-opus-5", capabilities: ["analysis"] },
    { agent_id: "summariser", address: "agent://iid-acme-0001/summariser", endpoint_id: "ep_acme",
      peer_instance_id: "iid-acme-0001", model: "claude-haiku-4-5", capabilities: ["analysis"] },
  ];
  await page.route("**/v1/console/federation/peer-agents", (r) => r.fulfill({ json: { agents } }));
  await page.route("**/v1/console/federation/peers", (r) =>
    r.fulfill({ json: { peers: [{ endpoint_id: "ep_acme", peer_instance_id: "iid-acme-0001", agent_count: 2, fresh: true }] } }),
  );
  await openPalette(page);
  await page.getByTestId("quick-add-peer_agent").click();

  const picker = page.getByTestId("peer-agent-picker");
  await expect(picker).toBeVisible();
  await expect(page.getByTestId("peer-agent-select")).toHaveValue("ep_acme\nreviewer");
  await page.getByTestId("peer-agent-select").selectOption("ep_acme\nsummariser");
  await page.getByTestId("node-field-instruction").fill("Summarise this public text in two sentences.");
  await page.getByTestId("node-field-timeout_s").fill("120");
  await page.getByTestId("node-save").click();
  await expect(page.getByTestId("node-error")).toHaveCount(0);

  await expect
    .poll(async () => (await (await page.request.get(`${API}/workflows/${WID}`)).text()))
    .toContain("agent_id: summariser");
  const body = await (await page.request.get(`${API}/workflows/${WID}`)).text();
  expect(body).toContain("type: peer_agent");
  expect(body).toContain("endpoint_id: ep_acme");
  expect(body).toContain("timeout_s: 120");
});

test("a stale catalog is named and can be refreshed", async ({ page, playwright }) => {
  // Self-contained: the workflow carries a peer node of its own (written through the real YAML route,
  // validated by the server), so this test does not depend on what the previous test saved.
  const ctx = await playwright.request.newContext({ storageState: "./tests/e2e/auth-state.json" });
  const stale = `awp: "1.0.0"
workflow:
  name: qa_peer_stale
  description: peer agent e2e
orchestration:
  engine: dag
  graph:
    - id: ask_peer
      type: peer_agent
      depends_on: []
      endpoint_id: ep_old
      agent_id: reviewer
      instruction: Review this public text.
`;
  const put = await ctx.put(`${API}/workflows/${WID}/yaml`, {
    headers: { "X-CSRF-Token": await csrfOf(ctx) }, data: { yaml: stale },
  });
  expect(put.status(), await put.text()).toBe(200);
  await ctx.dispose();

  await page.route("**/v1/console/federation/peer-agents", (r) => r.fulfill({ json: { agents: [] } }));
  await page.route("**/v1/console/federation/peers", (r) =>
    r.fulfill({ json: { peers: [{ endpoint_id: "ep_old", peer_instance_id: "iid-old-0001", agent_count: 1, fresh: false }] } }),
  );
  let refreshed = 0;
  await page.route("**/v1/console/federation/peers/ep_old/refresh", (r) => {
    refreshed += 1;
    return r.fulfill({ json: { endpoint_id: "ep_old", agents: [] } });
  });
  await page.goto(`/console/app/workflows/${WID}`);
  await page.getByText("ask_peer", { exact: true }).first().click();
  await expect(page.getByTestId("peer-agent-picker")).toBeVisible();
  const row = page.getByTestId("peer-stale-ep_old");
  await expect(row).toContainText("stale");
  await row.getByRole("button", { name: "Refresh" }).click();
  await expect.poll(() => refreshed).toBe(1);
});
