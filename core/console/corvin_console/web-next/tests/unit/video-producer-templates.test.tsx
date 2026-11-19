/**
 * Video template picker in the Video Producer composer, against MSW: the list the server owns is drawn grouped,
 * a template that cannot be produced yet is shown with its reason and cannot be chosen, a pick is remembered
 * through PUT /video/templates/default with CSRF and travels with the next job, and a film or a revision turns
 * the picker off instead of sending a template the server would ignore.
 */
import type React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { http, HttpResponse } from "msw";
import { server } from "../fixtures/server";

vi.mock("recharts", async (importOriginal) => {
  const mod = await importOriginal<typeof import("recharts")>();
  return { ...mod, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div>{children}</div> };
});
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" }, loading: false, refresh: vi.fn(), logout: vi.fn() }),
}));
vi.mock("@/hooks/use-voice-input", () => ({ useVoiceInput: () => ({ recording: false, startRecording: vi.fn(), stopRecording: vi.fn() }) }));

import { VideoProducerPage } from "@/pages/video-producer";

const B = "/v1/console/video";
const tpl = (id: string, name: string, category: string, over: Record<string, unknown> = {}) => ({
  id, name: { en: name, de: name }, summary: { en: `${name} summary`, de: "" }, category, audience: "Everyone", tone: "calm", aspect: "16:9",
  available: true, unavailable_reason: "", duration: { target_s: 240, min_s: 120, max_s: 480 }, scenes: { min: 4, max: 8 },
  structure: [{ kind: "title", purpose: "p", required: true }], hook: null, evidence: "none", recommended_model: "sonnet", ...over,
});
const TEMPLATES = {
  templates: [
    tpl("standard", "Standard", "explain"),
    tpl("short", "Short (9:16)", "format", { aspect: "9:16", available: false, unavailable_reason: "Needs the vertical 1080x1920 renderer", hook: { kind: "question", max_words: 12 }, duration: { target_s: 35, min_s: 25, max_s: 59 } }),
    tpl("marketing", "Marketing", "persuade", { duration: { target_s: 75, min_s: 45, max_s: 120 } }),
    tpl("code_debugging", "Code debugging", "engineer"),
  ],
  default_template_id: "standard", standard_template_id: "standard",
};
const JOBS = { total: 1, jobs: [{ id: "job_done", task: "A finished video", status: "complete", created_at: "2026-09-13T08:11:46", percent: 100 }] };
const posted: Array<Record<string, unknown>> = [];
const remembered: Array<{ csrf: string | null; body: Record<string, unknown> }> = [];

function handlers(templates: unknown = TEMPLATES) {
  return [
    http.get(`${B}/overview`, () => HttpResponse.json({ jobs_total: 1, by_status: { complete: 1 }, videos: 1, runtime_s: 1, size_bytes: 1, measured_videos: 0, mean_score_share: null, last_activity: null, ffprobe_available: true, plugin_source: "/x" })),
    http.get(`${B}/jobs`, () => HttpResponse.json(JOBS)),
    http.get(`${B}/jobs/job_done`, () => HttpResponse.json(JOBS.jobs[0])),
    http.get(`${B}/settings`, () => HttpResponse.json({ output_folder: "x", tts_engine: "auto", tts_engines: ["auto"], max_duration_minutes: 60, openai_configured: true, web_slides_available: false })),
    http.get(`${B}/templates`, () => (templates === null ? HttpResponse.json({ detail: "n/a" }, { status: 501 }) : HttpResponse.json(templates))),
    http.put(`${B}/templates/default`, async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      remembered.push({ csrf: request.headers.get("x-csrf-token"), body });
      return HttpResponse.json({ default_template_id: body.template_id });
    }),
    http.post(`${B}/jobs`, async ({ request }) => {
      posted.push((await request.json()) as Record<string, unknown>);
      return HttpResponse.json({ job_id: "job_new0001", status: "pending", created_at: "2026-09-22T10:00:00" });
    }),
  ];
}

function renderIt(search = "") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<MemoryRouter initialEntries={[`/app/video-producer${search}`]}><QueryClientProvider client={qc}><VideoProducerPage /></QueryClientProvider></MemoryRouter>);
}
/** The chip is disabled until the server's list has arrived, exactly as for a user. */
async function openMenu() {
  const chip = await screen.findByTestId("template-chip");
  await waitFor(() => expect(chip).toBeEnabled());
  fireEvent.click(chip);
}
afterEach(() => { cleanup(); posted.length = 0; remembered.length = 0; });

describe("Video template picker", () => {
  it("shows the remembered template on the chip and lists every template grouped by kind", async () => {
    server.use(...handlers());
    renderIt();
    await openMenu();
    expect(screen.getByTestId("template-chip").textContent).toMatch(/Template: Standard/);
    const menu = await screen.findByTestId("template-menu");
    for (const id of ["standard", "short", "marketing", "code_debugging"]) expect(screen.getByTestId(`template-option-${id}`)).toBeInTheDocument();
    expect(menu.querySelectorAll('[role="group"]').length).toBe(4);
    expect(screen.getByTestId("template-option-marketing").textContent).toMatch(/about 1:15 min · 16:9 · 4-8 scenes/);
    expect(screen.getByTestId("template-option-standard").getAttribute("aria-selected")).toBe("true");
  });

  it("a template that cannot be produced yet says why and cannot be chosen", async () => {
    server.use(...handlers());
    renderIt();
    await openMenu();
    const short = await screen.findByTestId("template-option-short");
    expect(short).toBeDisabled();
    expect(screen.getByTestId("template-reason-short").textContent).toMatch(/Needs the vertical 1080x1920 renderer/);
    expect(short.textContent).toMatch(/vertical 9:16 · opens with a question/);
    fireEvent.click(short);
    expect(remembered).toHaveLength(0);
  });

  it("a pick is remembered with CSRF and travels with the next job", async () => {
    server.use(...handlers());
    renderIt();
    await openMenu();
    fireEvent.click(await screen.findByTestId("template-option-marketing"));
    await waitFor(() => expect(remembered).toHaveLength(1));
    expect(remembered[0]).toEqual({ csrf: "csrf-test", body: { template_id: "marketing" } });
    expect(screen.getByTestId("template-chip").textContent).toMatch(/Template: Marketing/);
    expect(screen.queryByTestId("template-menu")).toBeNull();
    fireEvent.change(screen.getByTestId("task-input"), { target: { value: "Promote the new panel." } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ task: "Promote the new panel.", video_template: "marketing" });
  });

  it("the remembered template from the server is used without picking again", async () => {
    server.use(...handlers({ ...TEMPLATES, default_template_id: "code_debugging" }));
    renderIt();
    await waitFor(() => expect(screen.getByTestId("template-chip").textContent).toMatch(/Template: Code debugging/));
    fireEvent.change(screen.getByTestId("task-input"), { target: { value: "Why does the loop skip an item?" } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ video_template: "code_debugging" });
    expect(remembered).toHaveLength(0);
  });

  it("a cinematic film turns the picker off and sends no template", async () => {
    server.use(...handlers({ ...TEMPLATES, default_template_id: "marketing" }));
    renderIt();
    await waitFor(() => expect(screen.getByTestId("template-chip").textContent).toMatch(/Marketing/));
    fireEvent.click(await screen.findByTestId("film-toggle"));
    const chip = screen.getByTestId("template-chip");
    expect(chip).toBeDisabled();
    expect(chip.getAttribute("title")).toMatch(/film has its own structure/i);
    fireEvent.change(screen.getByTestId("task-input"), { target: { value: "Show the product." } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ mode: "cinematic" });
    expect(posted[0]).not.toHaveProperty("video_template");
  });

  it("a revision turns the picker off and sends no template", async () => {
    server.use(...handlers({ ...TEMPLATES, default_template_id: "marketing" }));
    renderIt("?job=job_done");
    fireEvent.click(await screen.findByTestId("revise-job_done"));
    expect(screen.getByTestId("template-chip")).toBeDisabled();
    expect(screen.getByTestId("template-chip").getAttribute("title")).toMatch(/revision keeps the structure/i);
    fireEvent.change(screen.getByTestId("task-input"), { target: { value: "Shorter intro" } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).toMatchObject({ base_job_id: "job_done" });
    expect(posted[0]).not.toHaveProperty("video_template");
  });

  it("on a build without templates the chip says so and the video is produced as before", async () => {
    server.use(...handlers(null));
    renderIt();
    expect((await screen.findByTestId("template-unavailable")).textContent).toMatch(/Templates unavailable/);
    expect(screen.queryByTestId("template-chip")).toBeNull();
    fireEvent.change(screen.getByTestId("task-input"), { target: { value: "Explain caching." } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0]).not.toHaveProperty("video_template");
  });
});
