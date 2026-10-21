/**
 * Web slides in the CorvinTV settings: the panel renders the server's `web_slides.reason` as an English
 * note (never the bare boolean) and, for a missing browser, offers "Install browser", which calls
 * POST /video/web-slides/install with CSRF and reports every outcome the route has (202 / 200 / 409 / 503).
 * Nothing is installed by a GET. MSW on the real page; the texts come from the testable lib.
 */
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";
import { ApiError } from "@/lib/api/client";
import { installOutcomeText, webSlidesNote, webSlidesState } from "@/lib/api/video-web-slides";

vi.mock("recharts", async (importOriginal) => {
  const mod = await importOriginal<typeof import("recharts")>();
  return { ...mod, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div data-testid="chart">{children}</div> };
});
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" }, loading: false, refresh: vi.fn(), logout: vi.fn() }),
}));
vi.mock("@/hooks/use-voice-input", () => ({ useVoiceInput: () => ({ recording: false, startRecording: vi.fn(), stopRecording: vi.fn() }) }));

import { VideoProducerPage } from "@/pages/video-producer";

const B = "/v1/console/video";
const SETTINGS = { output_folder: "x", tts_engine: "auto", tts_engines: ["openai", "auto", "gtts"], max_duration_minutes: 60, openai_configured: true };
const installs: Array<{ csrf: string | null }> = [];

function handlers(webSlides: { available: boolean; reason: string | null }, install: () => HttpResponse) {
  let state = webSlides;
  return [
    http.get(`${B}/overview`, () => HttpResponse.json({ jobs_total: 0, by_status: {}, videos: 0, runtime_s: 0, size_bytes: 0, measured_videos: 0, mean_score_share: null, last_activity: null, ffprobe_available: true, plugin_source: "x" })),
    http.get(`${B}/jobs`, () => HttpResponse.json({ total: 0, jobs: [] })),
    http.get(`${B}/settings`, () => HttpResponse.json({ ...SETTINGS, web_slides_available: state.available, web_slides: state })),
    http.post(`${B}/web-slides/install`, ({ request }) => {
      installs.push({ csrf: request.headers.get("x-csrf-token") });
      const res = install();
      if (res.status === 202) state = { available: false, reason: "install_running" };
      return res;
    }),
  ];
}

async function openSettings() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<MemoryRouter initialEntries={["/app/video-producer"]}><QueryClientProvider client={qc}><VideoProducerPage /></QueryClientProvider></MemoryRouter>);
  await screen.findByTestId("video-producer");
  fireEvent.click(await screen.findByText("Settings"));
}
afterEach(() => { cleanup(); installs.length = 0; });

describe("web slides texts (lib)", () => {
  it("maps every reason to an English note and an available state to none", () => {
    expect(webSlidesNote({ available: true, reason: null })).toBeNull();
    expect(webSlidesNote({ available: false, reason: "browser_missing" })).toMatch(/need Playwright's Chromium build/);
    expect(webSlidesNote({ available: false, reason: "package_missing" })).toMatch(/pip install playwright/);
    expect(webSlidesNote({ available: false, reason: "install_running" })).toMatch(/being installed/);
  });
  it("an older payload with only the boolean reads as browser missing", () => {
    expect(webSlidesState({ web_slides_available: false })).toEqual({ available: false, reason: "browser_missing" });
    expect(webSlidesState({ web_slides_available: true })).toEqual({ available: true, reason: null });
    expect(webSlidesState({ web_slides_available: false, web_slides: { available: false, reason: "package_missing" } }).reason).toBe("package_missing");
  });
  it("every route outcome has its own sentence", () => {
    expect(installOutcomeText({ status: "started" })).toMatch(/being installed/);
    expect(installOutcomeText({ status: "running" })).toMatch(/already running/);
    expect(installOutcomeText({ status: "already_available" })).toMatch(/already installed/);
    expect(installOutcomeText(new ApiError(503, { detail: "not reachable" }))).toBe("The download host is not reachable; install when online.");
    expect(installOutcomeText(new ApiError(409, { detail: "no package" }))).toMatch(/pip install playwright/);
    expect(installOutcomeText(new ApiError(500, {}))).toMatch(/HTTP 500/);
  });
});

describe("CorvinTV settings: web slides", () => {
  it("a missing browser shows the note and 'Install browser'; the click POSTs with CSRF and the panel reports the start", async () => {
    server.use(...handlers({ available: false, reason: "browser_missing" }, () => HttpResponse.json({ status: "started" }, { status: 202 })));
    await openSettings();
    expect((await screen.findByTestId("web-slides-note")).textContent).toMatch(/need Playwright's Chromium build/);
    fireEvent.click(screen.getByTestId("install-browser"));
    await waitFor(() => expect(screen.getByTestId("install-browser-outcome").textContent).toMatch(/being installed/));
    expect(installs).toEqual([{ csrf: "csrf-test" }]);
    // the settings were re-read: the server now says the install is running and the button is gone
    await waitFor(() => expect(screen.getByTestId("web-slides-note").textContent).toMatch(/being installed/));
    expect(screen.queryByTestId("install-browser")).toBeNull();
  });

  it("offline (503) says to install when online; the button stays for a retry", async () => {
    server.use(...handlers({ available: false, reason: "browser_missing" }, () => HttpResponse.json({ detail: "The Playwright download host cdn.playwright.dev is not reachable" }, { status: 503 })));
    await openSettings();
    fireEvent.click(await screen.findByTestId("install-browser"));
    await waitFor(() => expect(screen.getByTestId("install-browser-outcome").textContent).toBe("The download host is not reachable; install when online."));
    expect(screen.getByTestId("install-browser")).toBeEnabled();
  });

  it("409 (no playwright package) is said in words", async () => {
    server.use(...handlers({ available: false, reason: "browser_missing" }, () => HttpResponse.json({ detail: "no package" }, { status: 409 })));
    await openSettings();
    fireEvent.click(await screen.findByTestId("install-browser"));
    await waitFor(() => expect(screen.getByTestId("install-browser-outcome").textContent).toMatch(/pip install playwright/));
  });

  it("package missing and install running are notes without a button; available shows nothing", async () => {
    server.use(...handlers({ available: false, reason: "package_missing" }, () => HttpResponse.json({}, { status: 500 })));
    await openSettings();
    expect((await screen.findByTestId("web-slides-note")).textContent).toMatch(/pip install playwright/);
    expect(screen.queryByTestId("install-browser")).toBeNull();
    cleanup();
    server.use(...handlers({ available: false, reason: "install_running" }, () => HttpResponse.json({}, { status: 500 })));
    await openSettings();
    expect((await screen.findByTestId("web-slides-note")).textContent).toMatch(/being installed/);
    expect(screen.queryByTestId("install-browser")).toBeNull();
    cleanup();
    server.use(...handlers({ available: true, reason: null }, () => HttpResponse.json({}, { status: 500 })));
    await openSettings();
    await screen.findByDisplayValue("OpenAI first, edge-tts as fallback (default)");
    expect(screen.queryByTestId("web-slides-note")).toBeNull();
    expect(installs).toEqual([]);
  });
});
