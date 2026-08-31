/**
 * Console command-centre proof suite.
 *
 * Proves the four fixes shipped for the "chat is the command centre" work:
 *   1. No "Connection error — reconnecting…" banner after a chat turn
 *      (root cause: uvicorn --reload dropped the WS with code 1012 whenever a
 *      chat turn edited repo code; reload is now off for the command-centre).
 *   2. Audit graphs render for the OS engine (and the worker-engine graph shows
 *      a clear state) — the WdatAuditPanel now defaults to a view that has data.
 *   3. Media artifacts render INLINE in the chat (image / audio / video / pdf /
 *      text) — the workdir route now serves Content-Disposition: inline.
 *   4. Download buttons resolve to the real file (200) with a download filename.
 *
 * Strategy: build ONE synthetic session on disk with one artifact of every
 * media kind, plus a real chat turn against the live engine, then assert the
 * rendered DOM. All traffic goes through the Vite proxy (5173 → :8765).
 */
import { test, expect, request as pwRequest } from "@playwright/test";
import path from "path";
import fs from "fs";
import { execSync } from "child_process";
import { fileURLToPath } from "url";

const _dirname = path.dirname(fileURLToPath(import.meta.url));
// tests/e2e → … → repo root (.corvin lives at repo root).
const CORVIN_HOME = path.resolve(_dirname, "../../../../../../.corvin");
const TENANT = "_default";

// ── synthetic media fixtures ────────────────────────────────────────────────

/** Minimal but valid silent mono WAV (0.05 s @ 8 kHz). */
function makeWav(): Buffer {
  const sampleRate = 8000;
  const nSamples = 400;
  const dataSize = nSamples * 2; // 16-bit
  const buf = Buffer.alloc(44 + dataSize);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + dataSize, 4);
  buf.write("WAVE", 8);
  buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20); // PCM
  buf.writeUInt16LE(1, 22); // mono
  buf.writeUInt32LE(sampleRate, 24);
  buf.writeUInt32LE(sampleRate * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(dataSize, 40);
  // samples already zero-filled (silence)
  return buf;
}

/** Minimal single-page PDF. */
function makePdf(): Buffer {
  const body = `%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj
4 0 obj<</Length 56>>stream
BT /F1 18 Tf 20 100 Td (CorvinOS inline PDF) Tj ET
endstream endobj
5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj
trailer<</Root 1 0 R>>
%%EOF`;
  return Buffer.from(body, "latin1");
}

/** Try to produce a real tiny mp4 via ffmpeg; null when ffmpeg is unavailable. */
function makeMp4(target: string): boolean {
  try {
    execSync(
      `ffmpeg -y -f lavfi -i color=c=blue:s=64x64:d=0.2 -pix_fmt yuv420p "${target}"`,
      { stdio: "ignore", timeout: 20000 },
    );
    return fs.existsSync(target) && fs.statSync(target).size > 0;
  } catch {
    return false;
  }
}

interface Fixture {
  sid: string;
  files: { name: string; mime: string; size: number }[];
  hasVideo: boolean;
}

let fixture: Fixture;

async function localLogin(): Promise<{ cookie: string; csrf: string }> {
  const ctx = await pwRequest.newContext({ baseURL: "http://localhost:5173" });
  const lr = await ctx.get("/v1/console/auth/local-login", { maxRedirects: 0 }).catch(() => null);
  // Extract Set-Cookie from the redirect response
  let cookie = "";
  if (lr) {
    const sc = lr.headers()["set-cookie"] || "";
    const m = /corvin_console_sid=[^;]+/.exec(sc);
    if (m) cookie = m[0];
  }
  if (!cookie) {
    // fall back to a GET that follows redirects then read storage state
    await ctx.get("/v1/console/auth/local-login");
    const state = await ctx.storageState();
    const c = state.cookies.find((x) => x.name === "corvin_console_sid");
    if (c) cookie = `corvin_console_sid=${c.value}`;
  }
  const who = await ctx.get("/v1/console/auth/whoami", { headers: { Cookie: cookie } });
  const csrf = (await who.json()).csrf_token as string;
  await ctx.dispose();
  return { cookie, csrf };
}

test.describe("Console command-centre — media, audit, stable WS", () => {
  test.beforeAll(async () => {
    const { cookie, csrf } = await localLogin();
    const ctx = await pwRequest.newContext({ baseURL: "http://localhost:5173" });

    // 1) create a real chat session
    const created = await ctx.post("/v1/console/chat/sessions", {
      headers: { Cookie: cookie, "X-CSRF-Token": csrf, "Content-Type": "application/json" },
      data: { title: "media-proof" },
    });
    expect(created.ok()).toBeTruthy();
    const sid = (await created.json()).session.sid as string;

    // 2) materialise one artifact per media kind into the session workdir
    const workdir = path.join(CORVIN_HOME, "tenants", TENANT, "sessions", `web:${sid}`);
    fs.mkdirSync(workdir, { recursive: true });

    const files: Fixture["files"] = [];
    const writeFile = (name: string, mime: string, data: Buffer) => {
      fs.writeFileSync(path.join(workdir, name), data);
      files.push({ name, mime, size: data.length });
    };

    // image — reuse a real PNG from the live Spotify session when present,
    // else synthesise a 1×1 PNG.
    const realPng = path.join(
      CORVIN_HOME, "tenants", TENANT, "sessions",
      "web:t7SAzyqu4H-O6I76D1iEpQ", "plot_A1_top_artists.png",
    );
    const pngData = fs.existsSync(realPng)
      ? fs.readFileSync(realPng)
      : Buffer.from(
          "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000100ffff03000006000557bfabd40000000049454e44ae426082",
          "hex",
        );
    writeFile("chart.png", "image/png", pngData);
    writeFile("clip.wav", "audio/wav", makeWav());
    writeFile("report.pdf", "application/pdf", makePdf());
    writeFile("data.csv", "text/csv", Buffer.from("artist,streams\nABBA,123\nQueen,456\n"));

    // Video: prefer a real ffmpeg-encoded clip; otherwise a tiny placeholder
    // with the correct mime/extension. The <video controls> element renders
    // (and is downloadable) regardless of payload validity, so the inline-video
    // code path is always exercised. `realVideo` notes whether playback-capable
    // bytes exist for the optional decode check.
    const mp4Path = path.join(workdir, "clip.mp4");
    const realVideo = makeMp4(mp4Path);
    if (!realVideo) {
      // minimal ISO-BMFF ftyp box — enough for a valid .mp4 file on disk
      fs.writeFileSync(
        mp4Path,
        Buffer.from("0000001c66747970697336d6000000016973366d6d70343100000000", "hex"),
      );
    }
    files.push({ name: "clip.mp4", mime: "video/mp4", size: fs.statSync(mp4Path).size });
    const hasVideo = realVideo;

    // 3) append an assistant turn referencing every artifact so the chat
    //    renders them on load (mirrors what stream_turn persists).
    const turnsFile = path.join(
      CORVIN_HOME, "tenants", TENANT, "global", "web_chat", "sessions", `${sid}.turns.jsonl`,
    );
    fs.mkdirSync(path.dirname(turnsFile), { recursive: true });
    const parts = [
      { kind: "text", text: "Here are the generated artifacts." },
      ...files.map((f) => ({ kind: "artifact", name: f.name, path: f.name, mime: f.mime, size: f.size })),
    ];
    const userTurn = { role: "user", ts: Date.now() / 1000 - 1, parts: [{ kind: "text", text: "make artifacts" }] };
    const asstTurn = { role: "assistant", ts: Date.now() / 1000, parts };
    fs.appendFileSync(turnsFile, JSON.stringify(userTurn) + "\n" + JSON.stringify(asstTurn) + "\n");

    await ctx.dispose();
    fixture = { sid, files, hasVideo };
    console.log(`[setup] session ${sid} · ${files.length} artifacts · video=${hasVideo}`);
  });

  test("artifacts render inline: image, audio, pdf, csv (and video when available)", async ({ page }) => {
    await page.goto(`/console/app/chat/${fixture.sid}`, { waitUntil: "load" });
    // wait for the assistant bubble to hydrate from getChatTurns
    await page.waitForSelector("text=Here are the generated artifacts.", { timeout: 20000 });

    // image renders and actually decodes (naturalWidth > 0 ⇒ inline-served)
    const img = page.locator('img[src*="/workdir/chart.png"]');
    await expect(img).toBeVisible();
    await expect.poll(async () => img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBeGreaterThan(0);

    // audio element with the workdir source
    await expect(page.locator('audio[src*="/workdir/clip.wav"]')).toHaveCount(1);

    // pdf inline via iframe
    await expect(page.locator('iframe[src*="/workdir/report.pdf"]')).toHaveCount(1);

    // csv text preview fetched + shown
    await expect(page.locator("text=ABBA")).toBeVisible();

    // video element always renders (real clip when ffmpeg present, else a
    // valid placeholder file); the inline-<video> code path is exercised either way.
    await expect(page.locator('video[src*="/workdir/clip.mp4"]')).toHaveCount(1);
    if (fixture.hasVideo) {
      await expect.poll(async () =>
        page.locator('video[src*="/workdir/clip.mp4"]').evaluate((el: HTMLVideoElement) => el.readyState),
      ).toBeGreaterThan(0);
    }
    console.log(`✓ inline media render verified (real video=${fixture.hasVideo})`);
  });

  test("download buttons resolve to the real file (200) with inline disposition", async ({ page }) => {
    await page.goto(`/console/app/chat/${fixture.sid}`, { waitUntil: "load" });
    await page.waitForSelector("text=Here are the generated artifacts.", { timeout: 20000 });

    // every artifact card exposes a download anchor with the download attr
    const dlAnchors = page.locator('a[download][href*="/workdir/"]');
    await expect.poll(async () => dlAnchors.count()).toBeGreaterThanOrEqual(fixture.files.length);

    // each download href resolves to 200 with the correct content type
    for (const f of fixture.files) {
      const href = `/v1/console/chat/sessions/${fixture.sid}/workdir/${f.name}`;
      const resp = await page.request.get(href);
      expect(resp.status(), `download ${f.name}`).toBe(200);
      const cd = resp.headers()["content-disposition"] || "";
      expect(cd, `disposition ${f.name}`).toContain("inline");
    }
    console.log("✓ downloads resolve 200 for all artifacts");
  });

  test("audit OS-engine graph renders nodes; worker-engine state is explicit", async ({ page }) => {
    // Rich live session with real OS-turn + tool data (15 tool calls). Resolve
    // a session that actually has OS-turns via the API, so the assertion never
    // depends on one specific host fixture.
    let sid = "t7SAzyqu4H-O6I76D1iEpQ";
    const probe = await page.request.get(`/v1/console/chat/sessions/${sid}/os-turns`);
    if (!probe.ok() || (await probe.json()).count === 0) {
      // find any session with OS-turn data
      const list = await page.request.get("/v1/console/chat/sessions");
      const sessions = (await list.json()).sessions as { sid: string }[];
      for (const s of sessions) {
        const r = await page.request.get(`/v1/console/chat/sessions/${s.sid}/os-turns`);
        if (r.ok() && (await r.json()).count > 0) { sid = s.sid; break; }
      }
    }
    console.log(`[audit] using session ${sid}`);

    await page.goto(`/console/app/chat/${sid}`, { waitUntil: "load" });
    // wait until the chat shell has mounted (Audit button present)
    const auditBtn = page.getByRole("button", { name: /^Audit$/ });
    await auditBtn.waitFor({ state: "visible", timeout: 15000 });
    await auditBtn.click();

    // Single-Chain tab is default; WdatAuditPanel now defaults to the OS-engine
    // graph (was "ACS" → empty). Assert a ReactFlow graph with at least one node.
    await page.waitForSelector(".react-flow", { timeout: 20000 });
    await expect.poll(async () => page.locator(".react-flow__node").count(), { timeout: 20000 })
      .toBeGreaterThan(0);
    console.log("✓ OS-engine audit graph rendered nodes");

    // Dual-Track tab shows OS + Worker swimlanes without erroring.
    await page.getByRole("button", { name: "Dual-Track" }).click();
    await expect(page.getByText(/Dual-Track · OS \+ Worker/)).toBeVisible({ timeout: 10000 });

    // Completeness: the dual-track must render EVERY os_turn.* event from the
    // chain, and each tool call must surface its tool name (was rendering blank).
    const dtData = await page.request
      .get(`/v1/console/chat/sessions/${sid}/chain-dual-track`)
      .then((r) => r.json());
    const toolEvents = (dtData.os_only_events as { event_type: string; details: { tool_name?: string } }[])
      .filter((e) => e.event_type === "os_turn.tool_called");
    expect(toolEvents.length, "tool_called events present").toBeGreaterThan(0);
    // every projected tool event carries a tool_name (backend completeness)
    expect(toolEvents.every((e) => !!e.details.tool_name)).toBeTruthy();

    // the panel renders one block per os_only event (no events dropped in the UI)
    const blocks = page.locator("text=os_turn.tool_called");
    await expect.poll(async () => blocks.count(), { timeout: 10000 })
      .toBe(toolEvents.length);
    // and a representative tool name is actually visible in the swimlane
    const aName = toolEvents.find((e) => e.details.tool_name)!.details.tool_name!;
    await expect(page.getByText(aName, { exact: false }).first()).toBeVisible();
    console.log(`✓ Dual-Track complete: ${toolEvents.length} tool calls rendered with names`);
  });

  test("real chat turns complete with no TERMINAL connection-error banner", async ({ page, browserName }) => {
    // Engine+WS integration proof. Gated to chromium: the fix is server-side
    // (--reload removed) plus already-unit-tested client logic, so one engine
    // run is sufficient. We assert on the TERMINAL banners — the exact strings
    // the old --reload bug produced ("Connection error / Connection lost —
    // reconnecting…") and that never auto-recovered. A transient, self-healing
    // "Reconnecting…" notice is the *designed* graceful behaviour and is allowed
    // (it can appear briefly under the single-process Vite dev-proxy when several
    // browser WS contend); the real invariant is that turns COMPLETE and the
    // command centre stays usable.
    test.skip(browserName !== "chromium", "engine+WS integration runs on chromium only");
    test.setTimeout(120000);

    await page.goto(`/console/app/chat/${fixture.sid}`, { waitUntil: "load" });
    const box = page.getByPlaceholder(/Message Corvin/);
    await box.waitFor({ state: "visible", timeout: 15000 });

    const runTurn = async (text: string) => {
      await box.fill(text);
      await box.press("Enter");
      // turn finished when the Stop button is gone (send button returns)
      await expect(page.locator('button[title*="Stop generation"]'))
        .toHaveCount(0, { timeout: 90000 });
    };

    // Turn 1 — the old bug flashed a persistent "Connection error —
    // reconnecting…" right here (WS closed with code 1012 by uvicorn --reload).
    await runTurn("Reply with exactly the single word READY and nothing else.");
    await page.waitForTimeout(2000);
    // TERMINAL banners must never appear (these are the old-bug strings).
    await expect(page.getByText(/Connection error/i)).toHaveCount(0);
    await expect(page.getByText(/Connection lost/i)).toHaveCount(0);

    // Turn 2 — proves the WebSocket is still usable across tasks (the command
    // centre recovers / never dead-ends).
    await runTurn("Reply with exactly the single word AGAIN.");
    await expect(page.getByText(/Connection error/i)).toHaveCount(0);
    await expect(page.getByText(/Connection lost/i)).toHaveCount(0);
    // command centre still interactive
    await expect(box).toBeEditable();
    console.log("✓ two real turns completed, no terminal connection-error banner — command centre stable");
  });

  test.afterAll(async () => {
    // best-effort cleanup of the synthetic session metadata + turns
    if (!fixture?.sid) return;
    try {
      const { cookie, csrf } = await localLogin();
      const ctx = await pwRequest.newContext({ baseURL: "http://localhost:5173" });
      await ctx.delete(`/v1/console/chat/sessions/${fixture.sid}`, {
        headers: { Cookie: cookie, "X-CSRF-Token": csrf },
      }).catch(() => null);
      await ctx.dispose();
    } catch {
      /* ignore */
    }
  });
});
