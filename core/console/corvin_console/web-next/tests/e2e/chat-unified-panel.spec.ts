/**
 * E2E: unified chat panel — sessions, group chats and A2A in one page.
 *
 * Runs against the real console (no mocked API): the main area renders
 * whatever /app/chat/... names, and the right sidebar switches between
 * Chats, Peers and A2A.
 *
 *  1. A group created through the real API opens at /app/chat/group/<id>,
 *     the sidebar shows it under Peers → Groups
 *  2. A message typed in the group round-trips through POST /messages
 *  3. Alt+3 switches to A2A (activity + confirmations), Alt+1 back to Chats
 *  4. A group created from the sidebar navigates into it
 *  5. The old /app/chat-groups bookmark lands on /app/chat
 *  6. On a phone-width viewport the sidebar is a drawer
 *  7. Deleting a group from its header removes it (the spec cleans up after itself)
 *  8. No uncaught page errors throughout
 */
import { test, expect, type BrowserContext, type Page } from "@playwright/test";

const ORIGIN = (process.env.CONSOLE_BASE_URL || "http://127.0.0.1:8765/console").replace(/\/console\/?$/, "");
const BASE_URL = `${ORIGIN}/console`;
const API_BASE = `${ORIGIN}/v1/console`;
const RUN = Date.now();
const GROUP_TITLE = `Unified Panel E2E ${RUN}`;

test.describe.configure({ mode: "serial" });

let ctx: BrowserContext;
let csrf = "";
let groupId = "";
const created: string[] = [];

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (err) => {
    if (/ResizeObserver loop|ChunkLoadError|Loading CSS chunk/.test(err.message)) return;
    errors.push(err.message);
  });
  return errors;
}

test.describe("Unified chat panel", () => {
  test.beforeAll(async ({ browser }) => {
    ctx = await browser.newContext({ storageState: "./tests/e2e/auth-state.json" });
    const who = await ctx.request.get(`${API_BASE}/auth/whoami`);
    expect(who.status()).toBe(200);
    csrf = (await who.json()).csrf_token ?? "";
    const create = await ctx.request.post(`${API_BASE}/chat/groups`, {
      headers: { "X-CSRF-Token": csrf },
      data: { title: GROUP_TITLE },
    });
    expect(create.status()).toBe(200);
    groupId = (await create.json()).group_id;
    expect(groupId).toBeTruthy();
    created.push(groupId);
  });

  test.afterAll(async () => {
    for (const gid of created) {
      await ctx.request.delete(`${API_BASE}/chat/groups/${gid}`, { headers: { "X-CSRF-Token": csrf } });
    }
    await ctx.close();
  });

  test("a group opens in the main area and is listed under Peers", async () => {
    const page = await ctx.newPage();
    const errors = collectErrors(page);
    await page.goto(`${BASE_URL}/app/chat/group/${groupId}`, { waitUntil: "load" });

    const convo = page.getByTestId("group-conversation");
    await expect(convo.getByText(GROUP_TITLE, { exact: true })).toBeVisible();
    await expect(page.getByRole("tab", { name: /Peers/ })).toHaveAttribute("aria-selected", "true");
    const sidebar = page.getByRole("complementary", { name: "Conversations" });
    await expect(sidebar.getByText("GROUPS")).toBeVisible();
    await expect(sidebar.getByText(GROUP_TITLE)).toBeVisible();
    await expect(sidebar.getByText("CONNECTED AGENTS")).toBeVisible();
    expect(errors).toEqual([]);
    await page.close();
  });

  test("a group message round-trips through the real API", async () => {
    const page = await ctx.newPage();
    const errors = collectErrors(page);
    await page.goto(`${BASE_URL}/app/chat/group/${groupId}`, { waitUntil: "load" });
    const text = `hello group ${RUN}`;
    const box = page.getByRole("textbox", { name: "Group message" });
    await box.fill(text);
    const [resp] = await Promise.all([
      page.waitForResponse((r) => r.url().includes(`/chat/groups/${groupId}/messages`) && r.request().method() === "POST"),
      box.press("Enter"),
    ]);
    expect(resp.status()).toBe(200);
    expect((await resp.json()).delivery).toBe("local");
    // Own messages render without a sender label (right-aligned, accent
    // bubble) — same convention as the single-session chat's user bubble.
    await expect(page.getByTestId("group-conversation").getByText(text)).toBeVisible();
    expect(errors).toEqual([]);
    await page.close();
  });

  test("Alt+3 / Alt+1 switch the sidebar between A2A and Chats", async () => {
    const page = await ctx.newPage();
    const errors = collectErrors(page);
    await page.goto(`${BASE_URL}/app/chat/group/${groupId}`, { waitUntil: "load" });
    await expect(page.getByTestId("group-conversation")).toBeVisible();

    await page.keyboard.press("Alt+3");
    await expect(page.getByRole("tab", { name: /A2A/ })).toHaveAttribute("aria-selected", "true");
    await expect(page.getByText("RECENT AGENT TRAFFIC")).toBeVisible();

    await page.keyboard.press("Alt+1");
    await expect(page.getByRole("tab", { name: /Chats/ })).toHaveAttribute("aria-selected", "true");
    const sidebar = page.getByRole("complementary", { name: "Conversations" });
    await expect(sidebar.getByRole("button", { name: "New", exact: true })).toBeVisible();

    // The group stays open in the main area while the sidebar switches.
    await expect(page.getByTestId("group-conversation")).toBeVisible();
    expect(errors).toEqual([]);
    await page.close();
  });

  test("a group created from the sidebar opens, and can be deleted", async () => {
    const page = await ctx.newPage();
    const errors = collectErrors(page);
    await page.goto(`${BASE_URL}/app/chat/group/${groupId}`, { waitUntil: "load" });
    await page.getByRole("tab", { name: /Peers/ }).click();
    await page.getByRole("button", { name: "New group" }).click();
    const title = `Sidebar Group ${RUN}`;
    await page.getByRole("textbox", { name: "Group name" }).fill(title);
    await page.getByRole("button", { name: "Create" }).click();
    await expect(page).toHaveURL(/\/app\/chat\/group\/[A-Za-z0-9_-]+$/);
    await expect(page.getByTestId("group-conversation").getByText(title, { exact: true })).toBeVisible();
    created.push(page.url().split("/").pop()!);

    // Delete it again from the header (two-step) — it leaves the list.
    await page.getByRole("button", { name: "Delete group" }).click();
    const [del] = await Promise.all([
      page.waitForResponse((r) => r.request().method() === "DELETE" && r.url().includes("/chat/groups/")),
      page.getByRole("button", { name: "Delete group" }).click(),
    ]);
    expect(del.status()).toBe(200);
    await expect(page).not.toHaveURL(/\/app\/chat\/group\//);
    await page.getByRole("tab", { name: /Peers/ }).click();
    await expect(page.getByRole("complementary", { name: "Conversations" }).getByText(title)).toHaveCount(0);
    expect(errors).toEqual([]);
    await page.close();
  });

  test("the old group-chat page redirects into the chat", async () => {
    const page = await ctx.newPage();
    await page.goto(`${BASE_URL}/app/chat-groups`, { waitUntil: "load" });
    await expect(page).toHaveURL(/\/app\/chat(\/[^/]+)?$/);
    await page.close();
  });

  test("on a phone-width screen the sidebar is a drawer", async () => {
    const page = await ctx.newPage();
    const errors = collectErrors(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${BASE_URL}/app/chat/group/${groupId}`, { waitUntil: "load" });
    await expect(page.getByTestId("group-conversation")).toBeVisible();
    const sidebar = page.getByRole("complementary", { name: "Conversations" });
    await expect(sidebar).toBeHidden();
    await page.getByRole("button", { name: "Open chats, peers and agent activity" }).click();
    await expect(sidebar).toBeVisible();
    await sidebar.getByText(GROUP_TITLE).click();
    await expect(sidebar).toBeHidden();
    expect(errors).toEqual([]);
    await page.close();
  });

  // Regression guard — same bug class as chat-attachments.spec.ts's drag-
  // drop test: GroupConversation.tsx destructured useFileDrop's result into
  // `_composerDropHandlers` (never spread onto any element) in the same
  // commit as the main chat. Covered separately per surface because each
  // is an independent JSX tree — a fix in one file says nothing about
  // whether the others were actually wired the same way.
  test("drag-and-drop onto the group composer uploads the file", async () => {
    const page = await ctx.newPage();
    const errors = collectErrors(page);
    await page.goto(`${BASE_URL}/app/chat/group/${groupId}`, { waitUntil: "load" });

    const dropzone = page.getByTestId("composer-dropzone");
    await expect(dropzone).toBeVisible();

    const handle = await dropzone.elementHandle();
    await page.evaluate(([el]: [Element]) => {
      const dt = new DataTransfer();
      dt.items.add(new File(["a,b\n1,2\n"], "group-drop.csv", { type: "text/csv" }));
      for (const type of ["dragenter", "dragover", "drop"]) {
        el.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
      }
    }, [handle] as unknown as [Element]);

    const chip = page.getByTestId("attachment-chip").first();
    await expect(chip).toBeVisible({ timeout: 8000 });
    await expect(chip).toContainText("group-drop.csv");
    expect(errors).toEqual([]);
    await page.close();
  });
});
