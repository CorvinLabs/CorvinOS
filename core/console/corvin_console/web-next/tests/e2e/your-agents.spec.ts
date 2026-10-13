/**
 * (Peer chat header → Agents.) Fresh install, in the real browser on the served bundle: register the Claude Code agent with one click and
 * share it with paired peers by an explicit switch. The federation API is stubbed at the network boundary so the
 * state machine is deterministic; page, bundle, router, query layer and CSRF header handling are the real ones.
 * (Before this control existed `/ask @peer` could not work on a fresh install: the console could only LIST agents.)
 */
import { test, expect } from "@playwright/test";

type Agent = { agent_id: string; engine_type: string; model: string; capabilities: string[]; federable: boolean };

test("fresh install: register the agent, then share it — nothing is shared by registering", async ({ page }) => {
  let agents: Agent[] = [];
  const seen: { method: string; url: string; csrf: string | undefined; body: unknown }[] = [];

  await page.route(/\/v1\/console\/federation\/conversations(\?.*)?$/, (r) => r.fulfill({ json: { conversations: [] } }));
  await page.route(/\/v1\/console\/federation\/default-agent$/, (r) => {
    const req = r.request();
    seen.push({ method: req.method(), url: req.url(), csrf: req.headers()["x-csrf-token"], body: null });
    agents = [{ agent_id: "claude-code", engine_type: "claude_code", model: "claude-code",
                capabilities: ["analysis", "code_execution"], federable: false }];
    return r.fulfill({ status: 201, json: agents[0] });
  });
  await page.route(/\/v1\/console\/federation\/agents(\/[^/?]+)?(\?.*)?$/, (r) => {
    const req = r.request();
    if (req.method() === "PATCH") {
      const body = req.postDataJSON();
      seen.push({ method: "PATCH", url: req.url(), csrf: req.headers()["x-csrf-token"], body });
      agents = agents.map((a) => ({ ...a, federable: body.federable }));
      return r.fulfill({ json: agents[0] });
    }
    return r.fulfill({ json: { agents } });
  });

  await page.route(/\/v1\/console\/a2a\/feed(\?.*)?$/, (r) => r.fulfill({ json: {
    tenant_id: "_default", ts: Math.floor(Date.now() / 1000), retention_days: 30, has_more: false, last_seq: 0,
    peers: [{ peer_id: "peer-e2e-agents", label: "E2E Peer", state: "ACTIVE", can_send: true, can_receive: true,
      enabled: true, presence: "online", task_capacity: "ok" }],
    messages: [], stages: {},
  } }));
  // "Your agents" lives in the peer chat header since the standalone page was removed.
  await page.goto("/console/app/chat/peer/peer-e2e-agents");
  await page.getByTestId("peer-agents-toggle").click();
  const panel = page.getByTestId("your-agents");
  await expect(panel).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("your-agents-empty")).toBeVisible();

  await page.getByTestId("register-default-agent").click();
  const sw = page.getByRole("switch", { name: "Share claude-code with paired peers" });
  await expect(sw).toBeVisible();
  await expect(sw).toHaveAttribute("aria-checked", "false");          // registering is not consenting
  expect(seen.filter((s) => s.method === "PATCH")).toHaveLength(0);

  await sw.click();
  await expect(sw).toHaveAttribute("aria-checked", "true");
  const patch = seen.find((s) => s.method === "PATCH");
  expect(patch?.body).toEqual({ federable: true });
  expect(patch?.csrf, "a mutating call must carry the CSRF token").toBeTruthy();
  expect(seen[0].csrf, "registration too").toBeTruthy();

  await sw.click();
  await expect(sw).toHaveAttribute("aria-checked", "false");
  expect(seen.filter((s) => s.method === "PATCH").pop()?.body).toEqual({ federable: false });

  const box = await panel.boundingBox();
  expect(box && box.width > 150 && box.height > 40).toBeTruthy();
  await page.screenshot({ path: "test-results/your-agents.png" });
});

test("the removed Agent conversations page redirects to the chat and is gone from the sidebar", async ({ page }) => {
  await page.goto("/console/app/agent-conversations");
  await expect(page).toHaveURL(/\/console\/app\/chat/, { timeout: 20_000 });
  await expect(page.locator('nav a[href$="/app/agent-conversations"]')).toHaveCount(0);
});
