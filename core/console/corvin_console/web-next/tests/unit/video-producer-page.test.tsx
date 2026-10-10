/**
 * Video Producer studio against MSW: the library lists the real jobs and
 * selects the first; the Quality tab renders what ffprobe measured (ring
 * with a named denominator, checks with their detail, scene timing caption),
 * never a value the response did not carry; Learning shows zero events
 * honestly and posts scene feedback with CSRF; a 503 build says so.
 * The chart itself is not asserted — happy-dom has no layout.
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
  return { ...mod, ResponsiveContainer: ({ children }: { children: React.ReactNode }) => <div data-testid="chart">{children}</div> };
});
vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ session: { tenant_id: "_default", csrf_token: "csrf-test", tier: "owner" }, loading: false, refresh: vi.fn(), logout: vi.fn() }),
}));
vi.mock("@/hooks/use-voice-input", () => ({ useVoiceInput: () => ({ recording: false, startRecording: vi.fn(), stopRecording: vi.fn() }) }));

import { MARKER_VIDEO, VideoProducerPage } from "@/pages/video-producer";

const B = "/v1/console/video";
const JOBS = { total: 3, jobs: [
  { id: "job_2626f1e8", task: "Erkläre kurz den Unterschied zwischen HTTP und HTTPS.", status: "complete", created_at: "2026-09-13T08:11:46", percent: 100 },
  { id: "job_rev", task: "Make scene 2 shorter", status: "complete", created_at: "2026-09-21T09:00:00", percent: 100, revision_of: "job_2626f1e8" },
  { id: "job_run", task: "Still rendering", status: "skills_running", created_at: "2026-09-20T10:00:00", percent: 40, current_step: "Rendering scene 3", current_scene: 3, total_scenes: 7 },
] };
const OVERVIEW = { jobs_total: 2, by_status: { complete: 1, skills_running: 1 }, videos: 1, runtime_s: 44.3, size_bytes: 798680, measured_videos: 1, mean_score_share: 0.75, last_activity: "2026-09-20T10:00:00", ffprobe_available: true, plugin_source: "/x/src" };
const QUALITY = {
  job_id: "job_2626f1e8", status: "complete", measured_at: "2026-09-20T10:05:00Z", ffprobe_available: true,
  source: { video: "/v/output.mp4", scenes_dir: "/v/scenes", storyboard_scenes: 2, metadata: null },
  container: { format: "mov", duration_s: 44.35, size_bytes: 798680, bitrate_kbps: 144.1 },
  video: { codec: "h264", width: 1280, height: 720, fps: 60, pixel_format: "yuv420p", bitrate_kbps: 28.6 },
  audio: { codec: "aac", sample_rate_hz: 24000, channels: 1, bitrate_kbps: 108.8 },
  subtitles: { streams: 0, files: [] },
  scenes: [
    { index: 1, id: "s1", kind: "title", planned_s: 3, actual_s: 1.85, drift_pct: -38.3, rendered: true, size_bytes: 32143, has_slide: true, has_voice: true, voice_s: 1.85, narration_words: 3 },
    { index: 2, id: "s2", kind: "narration", planned_s: 1, actual_s: 5.76, drift_pct: 476, rendered: true, size_bytes: 101280, has_slide: true, has_voice: true, voice_s: 5.76, narration_words: 11 },
  ],
  summary: { scenes_planned: 2, scenes_rendered: 2, planned_s: 4, rendered_s: 44.35, size_bytes: 798680 },
  production: { started_at: "2026-09-13T08:11:46", completed_at: "2026-09-13T08:12:16", seconds: 30 },
  checks: [
    { id: "playable", label: "Container readable", status: "pass", detail: "mov · 44.35 s" },
    { id: "subtitles", label: "No subtitles", status: "pass", detail: "no subtitle stream, no caption file" },
    { id: "timing", label: "Scene timing within 10 % of the storyboard", status: "fail", detail: "largest drift 476 %" },
  ],
  score: { passed: 1, warned: 1, failed: 1, total: 3, skipped: 0, share: 0.333 },
};
const seen: Array<{ path: string; csrf: string | null; body: unknown }> = [];
const posted: Array<{ csrf: string | null; body: Record<string, unknown> }> = [];

function handlers() {
  return [
    http.get(`${B}/overview`, () => HttpResponse.json(OVERVIEW)),
    http.get(`${B}/jobs`, () => HttpResponse.json(JOBS)),
    http.get(`${B}/jobs/job_2626f1e8`, () => HttpResponse.json({ ...JOBS.jobs[0], started_at: "2026-09-13T08:11:46", completed_at: "2026-09-13T08:12:16" })),
    http.get(`${B}/jobs/job_2626f1e8/quality-metrics`, () => HttpResponse.json(QUALITY)),
    http.get(`${B}/jobs/job_2626f1e8/learning-metrics`, () => HttpResponse.json({ job_id: "job_2626f1e8", total_feedback_events: 0, approved: 0, rejected: 0, average_confidence: null, events: [], source: "learning.event_store" })),
    http.get(`${B}/settings`, () => HttpResponse.json({ output_folder: "~/.corvin/video-producer/videos", tts_engine: "openai", tts_engines: ["openai", "auto", "gtts"], max_duration_minutes: 60, openai_configured: true, web_slides_available: false })),
    http.post(`${B}/jobs`, async ({ request }) => {
      posted.push({ csrf: request.headers.get("x-csrf-token"), body: (await request.json()) as Record<string, unknown> });
      return HttpResponse.json({ job_id: "job_new0001", status: "pending", created_at: "2026-09-22T10:00:00" });
    }),
    http.post(`${B}/attachments/extract`, async ({ request }) => {
      const names = ((await request.formData()).getAll("files") as File[]).map((f) => f.name);
      if (names.includes("evil.exe")) return HttpResponse.json({ detail: "evil.exe: unsupported type (text, Markdown, JSON, CSV, YAML or PDF)" }, { status: 415 });
      return HttpResponse.json({ sources: names.map((name) => ({ name, text: `text of ${name}`, chars: 42, truncated: false })) });
    }),
    http.post(`${B}/jobs/job_2626f1e8/scenes/:scene/feedback`, async ({ request, params }) => {
      seen.push({ path: String(params.scene), csrf: request.headers.get("x-csrf-token"), body: await request.json() });
      return HttpResponse.json({ status: "recorded" });
    }),
  ];
}

function renderIt(search = "") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(<MemoryRouter initialEntries={[`/app/video-producer${search}`]}><QueryClientProvider client={qc}><VideoProducerPage /></QueryClientProvider></MemoryRouter>);
}
afterEach(() => { cleanup(); seen.length = 0; posted.length = 0; });

describe("Video Producer studio", () => {
  it("the selected video fills the stage above the composer, the library follows - with no transcript or caption UI", async () => {
    server.use(...handlers());
    renderIt();
    await screen.findByTestId("video-producer");
    expect(screen.getByText(MARKER_VIDEO)).toBeInTheDocument();
    await screen.findByTestId("job-job_2626f1e8");
    const video = (await screen.findByTestId("stage-video")) as HTMLVideoElement;
    expect(video.getAttribute("src")).toBe("/v1/console/video/videos/job_2626f1e8/download");
    // order on the page: stage, then composer, then library
    const stage = screen.getByTestId("studio"), composer = screen.getByTestId("composer"), library = screen.getByTestId("library");
    expect(stage.compareDocumentPosition(composer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(composer.compareDocumentPosition(library) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("overview-tiles").textContent).toMatch(/1\s*Videos produced · 2 jobs in total/);
    expect(document.querySelector("track")).toBeNull();
    expect(screen.queryByText(/Transcript/i)).toBeNull();
  });

  it("a running job shows its progress on the stage instead of a player", async () => {
    server.use(http.get(`${B}/jobs/job_run`, () => HttpResponse.json(JOBS.jobs[2])), ...handlers());
    renderIt("?job=job_run");
    const pending = await screen.findByTestId("playback-pending");
    expect(pending.textContent).toMatch(/40\s*%/);
    expect(pending.textContent).toMatch(/Rendering scene 3/);
    expect(pending.textContent).toMatch(/Scene 3 of 7/);
    expect(screen.queryByTestId("stage-video")).toBeNull();
  });

  it("the composer starts a NEW video with CSRF, no revision base and no sources", async () => {
    server.use(...handlers());
    renderIt();
    const input = await screen.findByTestId("task-input");
    fireEvent.change(input, { target: { value: "Explain the sweep." } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].csrf).toBe("csrf-test");
    expect(posted[0].body).toEqual({ task: "Explain the sweep.", sources: [] });
    await waitFor(() => expect((input as HTMLTextAreaElement).value).toBe(""));
  });

  it("a change request on the selected video is sent as a revision of that video", async () => {
    server.use(...handlers());
    renderIt("?job=job_2626f1e8");
    await screen.findByTestId("stage-video");
    fireEvent.click(await screen.findByTestId("revise-job_2626f1e8"));
    expect(screen.getByTestId("mode-revise").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("mode-revise").textContent).toMatch(/Revise: Erkläre kurz/);
    fireEvent.change(screen.getByTestId("task-input"), { target: { value: "Make scene 2 shorter" } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].body).toMatchObject({ task: "Make scene 2 shorter", base_job_id: "job_2626f1e8" });
    // the new job is selected and the composer is back to "new video"
    await waitFor(() => expect(screen.getByTestId("mode-new").getAttribute("aria-selected")).toBe("true"));
  });

  it("revising needs a produced video, and a revision is marked in the library", async () => {
    server.use(...handlers());
    renderIt("?job=job_run");
    await screen.findByTestId("job-job_run");
    expect((screen.getByTestId("mode-revise") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("revise-job_run")).toBeNull();
    expect(screen.getByTestId("revision-job_rev").getAttribute("title")).toMatch(/Revision of: Erkläre kurz/);
    expect(screen.queryByTestId("revision-job_2626f1e8")).toBeNull();
  });

  it("attachments become source chips that travel with the job; a refused file says why", async () => {
    server.use(...handlers());
    renderIt();
    const attach = (await screen.findByTestId("attach-input")) as HTMLInputElement;
    fireEvent.change(attach, { target: { files: [new File(["x"], "notes.md", { type: "text/markdown" })] } });
    await waitFor(() => expect(screen.getAllByTestId("source-chip")).toHaveLength(1));
    expect(screen.getByTestId("source-chip").textContent).toMatch(/notes\.md/);
    fireEvent.change(attach, { target: { files: [new File(["MZ"], "evil.exe")] } });
    await waitFor(() => expect(screen.getByTestId("composer-error").textContent).toMatch(/unsupported type/));
    expect(screen.getAllByTestId("source-chip")).toHaveLength(1);
    fireEvent.change(screen.getByTestId("task-input"), { target: { value: "Explain my notes" } });
    fireEvent.click(screen.getByTestId("start-production"));
    await waitFor(() => expect(posted).toHaveLength(1));
    expect(posted[0].body.sources).toEqual([{ name: "notes.md", text: "text of notes.md" }]);
  });

  it("fullscreen: the button and the F key put the stage into fullscreen; F while typing does not", async () => {
    server.use(...handlers());
    const req = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: true });
    renderIt("?job=job_2626f1e8");
    await screen.findByTestId("stage-video");
    (screen.getByTestId("studio") as HTMLElement).requestFullscreen = req;
    fireEvent.click(screen.getByTestId("fullscreen"));
    expect(req).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "f" });
    expect(req).toHaveBeenCalledTimes(2);
    fireEvent.keyDown(screen.getByTestId("task-input"), { key: "f" });
    expect(req).toHaveBeenCalledTimes(2);
    Object.defineProperty(document, "fullscreenEnabled", { configurable: true, value: undefined });
  });

  it("the Quality tab shows what was measured, with a named denominator", async () => {
    server.use(...handlers());
    renderIt("?job=job_2626f1e8&tab=quality");
    await screen.findByTestId("quality-tab");
    expect(screen.getByTestId("score-ring").textContent).toMatch(/1 of 3 checks passed/);
    expect(screen.getByTestId("check-timing").textContent).toMatch(/largest drift 476 %/);
    expect(screen.getByTestId("check-subtitles").textContent).toMatch(/no subtitle stream, no caption file/);
    expect(screen.queryByText(/Captions/)).toBeNull();
    expect(screen.getByTestId("timing-caption").textContent).toMatch(/2 scenes, one shared scale/);
    expect(screen.getByTestId("quality-tab").textContent).toMatch(/1280×720 · 60 fps · h264/);
    expect(screen.getByTestId("quality-tab").textContent).toMatch(/aac · 24\.0 kHz · mono/);
    expect(screen.getByTestId("filmstrip").querySelectorAll("img")).toHaveLength(2);
  });

  it("the Learning tab is honest about zero events and posts scene feedback with CSRF", async () => {
    server.use(...handlers());
    renderIt("?job=job_2626f1e8&tab=learning");
    await screen.findByTestId("learning-tab");
    await waitFor(() => expect(screen.getByTestId("learning-caption").textContent).toMatch(/No feedback on this job yet/));
    await screen.findByLabelText("approve s1");
    fireEvent.click(screen.getByLabelText("approve s1"));
    await screen.findByText("Recorded: approve for s1.");
    expect(seen[0]).toMatchObject({ path: "s1", csrf: "csrf-test", body: { feedback_type: "approve" } });
  });

  it("Settings: OpenAI TTS is the selected default, the engines are labelled, and missing capabilities are said out loud", async () => {
    server.use(...handlers());
    renderIt();
    await screen.findByTestId("video-producer");
    fireEvent.click(await screen.findByText("Settings"));
    const select = (await screen.findByDisplayValue("OpenAI TTS (default)")) as HTMLSelectElement;
    expect(select.value).toBe("openai");
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "OpenAI TTS (default)", "Automatic: OpenAI, then edge-tts, then Piper", "Google TTS (gTTS, legacy)",
    ]);
    expect(screen.queryByTestId("tts-key-warning")).toBeNull();
    expect(screen.getByTestId("web-slides-note").textContent).toMatch(/need Playwright/);
  });

  it("Settings: no OpenAI key on the host is shown next to the engine choice", async () => {
    // an override must come BEFORE the defaults: the first matching handler wins
    server.use(http.get(`${B}/settings`, () => HttpResponse.json({ output_folder: "x", tts_engine: "openai", tts_engines: ["openai", "auto", "gtts"], max_duration_minutes: 60, openai_configured: false, web_slides_available: true })), ...handlers());
    renderIt();
    fireEvent.click(await screen.findByText("Settings"));
    await waitFor(() => expect(screen.getByTestId("tts-key-warning").textContent).toMatch(/jobs are refused/));
    expect(screen.queryByTestId("web-slides-note")).toBeNull();
  });

  it("a build without the plugin says so instead of an empty library", async () => {
    server.use(
      http.get(`${B}/overview`, () => HttpResponse.json({ detail: "Video Producer plugin not available" }, { status: 503 })),
      http.get(`${B}/jobs`, () => HttpResponse.json({ detail: "Video Producer plugin not available" }, { status: 503 })),
      http.get(`${B}/settings`, () => HttpResponse.json({}, { status: 503 })),
    );
    renderIt();
    await screen.findByText("The Video Producer plugin is not available on this build.");
  });
});
