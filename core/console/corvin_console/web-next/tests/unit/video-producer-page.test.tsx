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
    { index: 1, id: "s1", kind: "title", planned_s: 3, actual_s: 1.85, drift_pct: -38.3, voice_drift_pct: 0, rendered: true, size_bytes: 32143, has_slide: true, has_voice: true, voice_s: 1.85, narration_words: 3 },
    { index: 2, id: "s2", kind: "narration", planned_s: 1, actual_s: 5.76, drift_pct: 476, voice_drift_pct: 31.2, rendered: true, size_bytes: 101280, has_slide: true, has_voice: true, voice_s: 5.76, narration_words: 11 },
  ],
  summary: { scenes_planned: 2, scenes_rendered: 2, planned_s: 4, rendered_s: 44.35, size_bytes: 798680 },
  production: { started_at: "2026-09-13T08:11:46", completed_at: "2026-09-13T08:12:16", seconds: 30 },
  checks: [
    { id: "playable", label: "Container readable", status: "pass", detail: "mov · 44.35 s" },
    { id: "subtitles", label: "No subtitles", status: "pass", detail: "no subtitle stream, no caption file" },
    { id: "voice", label: "Every scene has a voice track", status: "fail", detail: "silent placeholder voice (no narration provider was available)" },
  ],
  score: { passed: 1, warned: 1, failed: 1, total: 3, skipped: 0, share: 0.333 },
  report: {
    state: "present", schema: 1, plugin_version: "1.4.1", status: "complete", storyboard_llm: "operator", total_seconds: 17.7,
    verdicts: { fail: 1, info: 1, pass: 1 },
    signals: [
      { id: "plan_estimate_error", kind: "runtime", value: -0.425, unit: "share", threshold: null, verdict: "info", basis: { n: 2, of: 2 }, valid: true, invalidated_by: null, reason: null, detail: { narrated_s: 9.2, plan_s: 16.0 } },
      { id: "layout_clean", kind: "runtime", value: 0, unit: "collisions kept", threshold: 0, verdict: "pass", basis: { n: 2, of: 2 }, valid: false, invalidated_by: "degradation_rate", reason: null, detail: null },
      { id: "degradation_rate", kind: "runtime", value: 1.0, unit: "share of scenes", threshold: { max_share: 0.1, pillow: 0 }, verdict: "fail", basis: { n: 2, of: 2 }, valid: true, invalidated_by: null, reason: "scene share above 10 %", detail: null },
    ],
  },
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

  it("a running job can be cancelled: POST /cancel with CSRF, then the stage says it was cancelled", async () => {
    let cancelled = false;
    const cancels: Array<string | null> = [];
    server.use(
      http.get(`${B}/jobs/job_run`, () => HttpResponse.json(cancelled ? { ...JOBS.jobs[2], status: "cancelled" } : JOBS.jobs[2])),
      http.post(`${B}/jobs/job_run/cancel`, ({ request }) => {
        cancels.push(request.headers.get("x-csrf-token"));
        cancelled = true;
        return HttpResponse.json({ job_id: "job_run", status: "cancelled" });
      }),
      ...handlers(),
    );
    renderIt("?job=job_run");
    fireEvent.click(await screen.findByTestId("cancel-job"));
    await waitFor(() => expect(cancels).toEqual(["csrf-test"]));
    expect(await screen.findByTestId("stage-cancelled")).toBeTruthy();
    expect(screen.queryByTestId("cancel-job")).toBeNull();
  });

  it("a finished video has no cancel button, and a refused cancel says why", async () => {
    server.use(
      http.get(`${B}/jobs/job_run`, () => HttpResponse.json(JOBS.jobs[2])),
      http.post(`${B}/jobs/job_run/cancel`, () => HttpResponse.json({ detail: "The job is not running in this console process" }, { status: 409 })),
      ...handlers(),
    );
    renderIt("?job=job_2626f1e8");
    await screen.findByTestId("stage-video");
    expect(screen.queryByTestId("cancel-job")).toBeNull();
    cleanup();
    renderIt("?job=job_run");
    fireEvent.click(await screen.findByTestId("cancel-job"));
    expect((await screen.findByTestId("cancel-error")).textContent).toMatch(/not running in this console process/);
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
    expect(screen.getByTestId("check-voice").textContent).toMatch(/silent placeholder voice/);
    expect(screen.queryByTestId("check-timing")).toBeNull();
    expect(screen.getByTestId("check-subtitles").textContent).toMatch(/no subtitle stream, no caption file/);
    expect(screen.queryByText(/Captions/)).toBeNull();
    expect(screen.getByTestId("timing-caption").textContent).toMatch(/2 scenes, one shared scale/);
    expect(screen.getByTestId("quality-tab").textContent).toMatch(/1280×720 · 60 fps · h264/);
    expect(screen.getByTestId("quality-tab").textContent).toMatch(/aac · 24\.0 kHz · mono/);
    expect(screen.getByTestId("filmstrip").querySelectorAll("img")).toHaveLength(2);
  });

  it("the Quality tab shows each production signal with value, threshold, verdict and n of N - gate first", async () => {
    server.use(...handlers());
    renderIt("?job=job_2626f1e8&tab=quality");
    await screen.findByTestId("signals-table");
    const rows = screen.getByTestId("signals-table").querySelectorAll("tbody tr");
    expect(rows[0].getAttribute("data-testid")).toBe("signal-degradation_rate");
    expect(screen.getByTestId("signal-value-degradation_rate").textContent).toBe("2 of 2 scenes");
    expect(screen.getByTestId("signal-threshold-degradation_rate").textContent).toBe("at most 10 % of scenes · no fallback slide");
    expect(screen.getByTestId("signal-verdict-degradation_rate").textContent).toBe("Fail");
    expect(screen.getByTestId("signal-basis-degradation_rate").textContent).toBe("2 of 2");
    expect(screen.getByTestId("signal-note-degradation_rate").textContent).toMatch(/too few to state as a percentage/);
    expect(screen.getByTestId("signal-verdict-layout_clean").textContent).toBe("Pass · not valid");
    expect(screen.getByTestId("signal-value-plan_estimate_error").textContent).toBe("9.2 s narrated · 16 s planned");
    expect(screen.getByTestId("signal-verdict-plan_estimate_error").textContent).toBe("Information");
    expect(screen.getByTestId("signals-gate-note").textContent).toMatch(/Degraded scenes" failed/);
    expect(screen.getByTestId("signals-summary").textContent).toMatch(/3 signals · 1 fail · 1 pass · 1 information/);
    expect(screen.getByTestId("signals-card").textContent).not.toMatch(/ADR-\d/);
  });

  it("an old job without a report says so and shows no signal", async () => {
    server.use(http.get(`${B}/jobs/job_2626f1e8/quality-metrics`, () => HttpResponse.json({ ...QUALITY, report: { state: "missing", signals: [] } })), ...handlers());
    renderIt("?job=job_2626f1e8&tab=quality");
    const absent = await screen.findByTestId("signals-absent");
    expect(absent.textContent).toMatch(/No production report for this job/);
    expect(screen.queryByTestId("signals-table")).toBeNull();
    expect(screen.getByTestId("score-ring")).toBeInTheDocument();
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

  it("a refused rating shows the server's reason, not a generic line", async () => {
    server.use(
      http.post(`${B}/jobs/job_2626f1e8/scenes/:scene/feedback`, () =>
        HttpResponse.json({ detail: "This video was produced more than 24 hours ago; ratings are only accepted for recent productions" }, { status: 503 })),
      ...handlers(), // the first matching handler wins, so the refusal above shadows the recording one
    );
    renderIt("?job=job_2626f1e8&tab=learning");
    await screen.findByTestId("learning-tab");
    await screen.findByLabelText("approve s1");
    fireEvent.click(screen.getByLabelText("approve s1"));
    await screen.findByText(/more than 24 hours ago/);
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

  it("the Code and Canvas tabs show the job's own code and canvas scenes, read from its cue log, and the canvas signals", async () => {
    const meta = { cues: [{ scene: 1, template: "hero" }, { scene: 2, template: "code" }, { scene: 3, template: "canvas" }, { scene: 4, template: "canvas" }],
      canvas_authoring: { total: 2, first_try: 1, after_repair: 2 } };
    const report = { ...QUALITY.report, degradations_by_kind: { canvas_degraded: 1 }, signals: [...QUALITY.report.signals,
      { id: "canvas_clean", kind: "runtime", value: 0.5, unit: "canvas scenes clean after layout", threshold: 1, verdict: "fail", basis: { n: 1, of: 2 }, valid: true, invalidated_by: null, reason: null, detail: null }] };
    server.use(http.get(`${B}/jobs/job_2626f1e8/quality-metrics`, () => HttpResponse.json({ ...QUALITY, source: { ...QUALITY.source, metadata: meta }, report })), ...handlers());
    renderIt("?job=job_2626f1e8&tab=code");
    await screen.findByTestId("code-scenes");
    expect(screen.getByTestId("code-scene-2").querySelector("img")!.getAttribute("src")).toBe("/v1/console/video/videos/job_2626f1e8/scenes/2/slide");
    expect(screen.queryByTestId("code-scene-1")).toBeNull();
    fireEvent.click(screen.getByTestId("tab-canvas"));
    await screen.findByTestId("canvas-scenes");
    expect(screen.getByTestId("canvas-scenes").querySelectorAll("li")).toHaveLength(2);
    expect(screen.getByTestId("canvas-signal-canvas_clean").textContent).toMatch(/Clean canvases.*Fail.*1 of 2/);
    expect(screen.getByTestId("canvas-authoring").textContent).toMatch(/2 canvases: 1 valid on the first try, 2 after the repair round/);
    expect(screen.getByTestId("canvas-ladder").textContent).toMatch(/1 canvas step walked the degrade ladder/);
    expect(screen.getByTestId("canvas-tab").textContent).not.toMatch(/ADR-\d/);
  });

  it("a video without code or canvas scenes says so instead of an empty gallery", async () => {
    server.use(http.get(`${B}/jobs/job_2626f1e8/quality-metrics`, () => HttpResponse.json({ ...QUALITY, source: { ...QUALITY.source, metadata: { cues: [{ scene: 1, template: "hero" }] } } })), ...handlers());
    renderIt("?job=job_2626f1e8&tab=code");
    await screen.findByTestId("code-none");
    fireEvent.click(screen.getByTestId("tab-canvas"));
    await screen.findByTestId("canvas-none");
    expect(screen.queryByTestId("canvas-signals")).toBeNull();
  });

  it("the Assets tab lists the workspace's images, uploads a PNG with caption and clicked hotspot (CSRF), and deletes after confirmation", async () => {
    const assets = [{ id: "ast_0123abcd", caption: "Pricing page", hotspots: [{ name: "plans", x: 0.4, y: 0.3, description: "" }], width: 1280, height: 720, bytes: 204800, created: 1, source: "https://example.com/pricing" }];
    const uploads: Array<{ csrf: string | null; caption: string; hotspots: string; file: string }> = [];
    const deletes: Array<string | null> = [];
    server.use(
      http.get(`${B}/assets`, () => HttpResponse.json({ assets, limits: { max_assets: 50, max_image_bytes: 2097152, max_upload_bytes: 8388608, max_caption: 160, max_hotspots: 8 } })),
      http.post(`${B}/assets`, async ({ request }) => {
        const f = await request.formData();
        uploads.push({ csrf: request.headers.get("x-csrf-token"), caption: String(f.get("caption")), hotspots: String(f.get("hotspots")), file: (f.get("file") as File).name });
        return HttpResponse.json({ asset: { ...assets[0], id: "ast_ffff0000" } }, { status: 201 });
      }),
      http.delete(`${B}/assets/:id`, ({ request }) => { deletes.push(request.headers.get("x-csrf-token")); return new HttpResponse(null, { status: 204 }); }),
      ...handlers(),
    );
    renderIt("?job=job_2626f1e8&tab=assets");
    await screen.findByTestId("asset-ast_0123abcd");
    expect(screen.getByTestId("asset-ast_0123abcd").textContent).toMatch(/Pricing page.*1280×720.*200 KB.*1 hotspot: plans/);
    expect(screen.getByTestId("asset-count").textContent).toBe("1 of 50 images");

    const URLs = globalThis.URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };
    URLs.createObjectURL = () => "blob:preview"; URLs.revokeObjectURL = () => undefined;
    fireEvent.change(screen.getByTestId("asset-input"), { target: { files: [new File([new Uint8Array([137, 80, 78, 71])], "shot.png", { type: "image/png" })] } });
    expect(await screen.findByTestId("asset-third-party")).toBeInTheDocument();
    expect(screen.getByTestId("asset-problem").textContent).toMatch(/caption is required/);
    const img = screen.getByTestId("asset-preview");
    img.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100, right: 200, bottom: 100, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.click(img, { clientX: 50, clientY: 25 });
    expect(screen.getByTestId("asset-spot-0").textContent).toMatch(/25 %, 25 %/);
    fireEvent.change(screen.getByTestId("asset-caption"), { target: { value: "Checkout form" } });
    fireEvent.click(screen.getByTestId("asset-upload"));
    await waitFor(() => expect(uploads).toHaveLength(1));
    expect(uploads[0]).toMatchObject({ csrf: "csrf-test", caption: "Checkout form", file: "shot.png" });
    expect(JSON.parse(uploads[0].hotspots)).toEqual([{ name: "spot_1", x: 0.25, y: 0.25, description: "" }]);

    fireEvent.click(screen.getByTestId("asset-delete-ast_0123abcd"));
    fireEvent.click(screen.getByTestId("asset-delete-confirm-ast_0123abcd"));
    await waitFor(() => expect(deletes).toEqual(["csrf-test"]));
  });

  it("a Penpot board (SVG) is posted for import with the CSRF token, reports what was dropped, and shows a refusal's reason", async () => {
    const limits = { max_assets: 50, max_image_bytes: 2097152, max_upload_bytes: 8388608, max_caption: 160, max_hotspots: 8 };
    const posts: Array<{ csrf: string | null; file: string }> = [];
    let refuse = false;
    server.use(
      http.get(`${B}/assets`, () => HttpResponse.json({ assets: [], limits })),
      http.post(`${B}/assets/import-penpot`, async ({ request }) => {
        const f = await request.formData();
        posts.push({ csrf: request.headers.get("x-csrf-token"), file: (f.get("file") as File).name });
        if (refuse) return HttpResponse.json({ detail: "This is not an SVG file. Export a board from Penpot as SVG." }, { status: 415 });
        return HttpResponse.json({ asset: { id: "ast_aaaa0000", caption: "board", hotspots: [], width: 1920, height: 1080, bytes: 9000, created: 1, source: null }, notes: ["1 image not imported"] }, { status: 201 });
      }),
      ...handlers(),
    );
    renderIt("?tab=assets");
    await screen.findByTestId("assets-empty");
    expect(screen.getByTestId("penpot-input").getAttribute("accept")).toBe(".svg,image/svg+xml");
    fireEvent.change(screen.getByTestId("penpot-input"), { target: { files: [new File(["<svg/>"], "board.svg", { type: "image/svg+xml" })] } });
    expect((await screen.findByTestId("penpot-done")).textContent).toMatch(/Board imported as an image \(not imported: 1 image not imported\)/);
    expect(posts).toEqual([{ csrf: "csrf-test", file: "board.svg" }]);
    refuse = true;
    fireEvent.change(screen.getByTestId("penpot-input"), { target: { files: [new File(["x"], "other.svg", { type: "image/svg+xml" })] } });
    expect((await screen.findByTestId("penpot-error")).textContent).toBe("This is not an SVG file. Export a board from Penpot as SVG.");
  });

  const CAP_SETTINGS = { output_folder: "x", tts_engine: "openai", tts_engines: ["openai"], max_duration_minutes: 60, openai_configured: true, web_slides_available: true };
  const LIMITS = { max_assets: 50, max_image_bytes: 4194304, max_upload_bytes: 8388608, max_caption: 160, max_hotspots: 8 };

  it("website capture is off by default: the button says why, and the Settings switch turns it on with CSRF", async () => {
    let enabled = false;
    const puts: Array<{ csrf: string | null; body: unknown }> = [];
    server.use(
      http.get(`${B}/settings`, () => HttpResponse.json({ ...CAP_SETTINGS, capture_enabled: enabled, capture_policy_configured: false })),
      http.put(`${B}/settings`, async ({ request }) => {
        const body = (await request.json()) as { capture_enabled?: boolean };
        puts.push({ csrf: request.headers.get("x-csrf-token"), body });
        enabled = !!body.capture_enabled;
        return HttpResponse.json({ settings: { ...CAP_SETTINGS, capture_enabled: enabled, capture_policy_configured: false } });
      }),
      http.get(`${B}/assets`, () => HttpResponse.json({ assets: [], limits: LIMITS })),
      ...handlers(),
    );
    renderIt("?job=job_2626f1e8&tab=assets");
    const pick = await screen.findByTestId("capture-pick");
    await waitFor(() => expect(screen.getByTestId("capture-blocked").textContent).toMatch(/off for this workspace.*Settings/));
    expect(pick).toBeDisabled();
    fireEvent.click(await screen.findByText("Settings"));
    const box = (await screen.findByTestId("capture-enabled")) as HTMLInputElement;
    expect(box.checked).toBe(false);
    fireEvent.click(box);
    await waitFor(() => expect(puts).toEqual([{ csrf: "csrf-test", body: { capture_enabled: true } }]));
    await waitFor(() => expect(screen.getByTestId("capture-policy-missing").textContent).toMatch(/No egress policy/));
    await waitFor(() => expect(screen.getByTestId("capture-blocked").textContent).toMatch(/needs an egress policy/));
    expect(screen.getByTestId("capture-pick")).toBeDisabled();
  });

  it("add website screenshot: captured with CSRF, then hotspots placed on the STORED picture are saved with PATCH", async () => {
    const captures: Array<{ csrf: string | null; body: Record<string, unknown> }> = [];
    const patches: Array<{ csrf: string | null; id: string; body: Record<string, unknown> }> = [];
    const shot = { id: "ast_cafe0001", caption: "Pricing", hotspots: [], width: 1920, height: 1080, bytes: 300000, created: 1, source: "https://www.acme.example/pricing" };
    server.use(
      http.get(`${B}/settings`, () => HttpResponse.json({ ...CAP_SETTINGS, capture_enabled: true, capture_policy_configured: true })),
      http.get(`${B}/assets`, () => HttpResponse.json({ assets: [], limits: LIMITS })),
      http.post(`${B}/capture`, async ({ request }) => {
        captures.push({ csrf: request.headers.get("x-csrf-token"), body: (await request.json()) as Record<string, unknown> });
        return HttpResponse.json({ asset: shot }, { status: 201 });
      }),
      http.patch(`${B}/assets/:id`, async ({ request, params }) => {
        const body = (await request.json()) as Record<string, unknown>;
        patches.push({ csrf: request.headers.get("x-csrf-token"), id: String(params.id), body });
        return HttpResponse.json({ asset: { ...shot, ...body } });
      }),
      ...handlers(),
    );
    renderIt("?job=job_2626f1e8&tab=assets");
    const pick = await screen.findByTestId("capture-pick");
    await waitFor(() => expect(pick).not.toBeDisabled());
    fireEvent.click(pick);
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByTestId("capture-viewport").textContent).toMatch(/412×915 at 2×/);
    fireEvent.change(screen.getByTestId("capture-url"), { target: { value: "https://acme.example/pricing" } });
    fireEvent.change(screen.getByTestId("capture-caption"), { target: { value: "Pricing" } });
    fireEvent.click(screen.getByTestId("capture-overlay")); // the overlay never discards what was typed
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("capture-submit"));
    await waitFor(() => expect(captures).toHaveLength(1));
    expect(captures[0]).toEqual({ csrf: "csrf-test", body: { hotspots: [], url: "https://acme.example/pricing", caption: "Pricing", viewport: "desktop" } });
    const img = await screen.findByTestId("capture-preview");
    expect(img.getAttribute("src")).toMatch(/\/video\/assets\/ast_cafe0001\/image$/);
    img.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200, right: 400, bottom: 200, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.click(img, { clientX: 100, clientY: 50 });
    fireEvent.change(screen.getByTestId("capture-caption-edit"), { target: { value: "Pricing with three plans" } });
    fireEvent.click(screen.getByTestId("capture-save"));
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ csrf: "csrf-test", id: "ast_cafe0001",
      body: { caption: "Pricing with three plans", hotspots: [{ name: "spot_1", x: 0.25, y: 0.25, description: "" }] } });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("Escape closes the capture dialog; a refused capture shows the server's reason", async () => {
    server.use(
      http.get(`${B}/settings`, () => HttpResponse.json({ ...CAP_SETTINGS, capture_enabled: true, capture_policy_configured: true })),
      http.get(`${B}/assets`, () => HttpResponse.json({ assets: [], limits: LIMITS })),
      http.post(`${B}/capture`, () => HttpResponse.json({ detail: "This address cannot be captured" }, { status: 422 })),
      ...handlers(),
    );
    renderIt("?job=job_2626f1e8&tab=assets");
    const pick = await screen.findByTestId("capture-pick");
    await waitFor(() => expect(pick).not.toBeDisabled());
    fireEvent.click(pick);
    fireEvent.change(await screen.findByTestId("capture-url"), { target: { value: "http://10.0.0.1/" } });
    fireEvent.change(screen.getByTestId("capture-caption"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("capture-overlay"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("capture-submit"));
    expect((await screen.findByTestId("capture-error")).textContent).toBe("This address cannot be captured");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("the composer offers to capture an address in the task BEFORE the job is created, and says why when it cannot", async () => {
    const captures: unknown[] = [];
    let on = true;
    server.use(
      http.get(`${B}/settings`, () => HttpResponse.json({ ...CAP_SETTINGS, capture_enabled: on, capture_policy_configured: true })),
      http.post(`${B}/capture`, async ({ request }) => {
        captures.push(await request.json());
        return HttpResponse.json({ asset: { id: "ast_beef0002", caption: "Acme", hotspots: [], width: 1920, height: 1080, bytes: 1, created: 1, source: "https://acme.example/" } }, { status: 201 });
      }),
      ...handlers(),
    );
    renderIt();
    const input = await screen.findByTestId("task-input");
    fireEvent.change(input, { target: { value: "Explain how https://acme.example/pricing works." } });
    await waitFor(() => expect(screen.getByTestId("capture-suggestion").textContent).toMatch(/screenshot of acme\.example/));
    fireEvent.click(screen.getByTestId("capture-suggestion-open"));
    expect((await screen.findByTestId("capture-url") as HTMLInputElement).value).toBe("https://acme.example/pricing");
    fireEvent.change(screen.getByTestId("capture-caption"), { target: { value: "Acme pricing" } });
    fireEvent.click(screen.getByTestId("capture-submit"));
    await waitFor(() => expect(captures).toHaveLength(1));
    await screen.findByTestId("capture-preview");
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.getByTestId("capture-suggestion").textContent).toMatch(/added as ast_beef0002/));
    expect(posted).toEqual([]); // nothing was produced yet

    cleanup();
    on = false;
    renderIt();
    fireEvent.change(await screen.findByTestId("task-input"), { target: { value: "See https://acme.example" } });
    await waitFor(() => expect(screen.getByTestId("capture-suggestion-off").textContent).toMatch(/off for this workspace/));
    expect(screen.queryByTestId("capture-suggestion-open")).toBeNull();
  });

  it("a host without Chrome says so on the capture button, whatever the switch says", async () => {
    server.use(
      http.get(`${B}/settings`, () => HttpResponse.json({ ...CAP_SETTINGS, capture_enabled: true, capture_policy_configured: true, capture_host_problem: "no_browser" })),
      http.get(`${B}/assets`, () => HttpResponse.json({ assets: [], limits: LIMITS })),
      ...handlers(),
    );
    renderIt("?job=job_2626f1e8&tab=assets");
    await waitFor(() => expect(screen.getByTestId("capture-blocked").textContent).toMatch(/Chrome or Chromium is not installed/));
    expect(screen.getByTestId("capture-pick")).toBeDisabled();
  });

  it("an existing image's hotspots can be edited by keyboard; Escape never drops unsaved hotspots", async () => {
    const asset = { id: "ast_0a0b0c0d", caption: "Home", hotspots: [], width: 1920, height: 1080, bytes: 1, created: 1, source: "https://acme.example/" };
    const patches: Array<Record<string, unknown>> = [];
    server.use(
      http.get(`${B}/settings`, () => HttpResponse.json({ ...CAP_SETTINGS, capture_enabled: false, capture_policy_configured: false })),
      http.get(`${B}/assets`, () => HttpResponse.json({ assets: [asset], limits: LIMITS })),
      http.patch(`${B}/assets/:id`, async ({ request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        patches.push(body);
        return HttpResponse.json({ asset: { ...asset, ...body } });
      }),
      ...handlers(),
    );
    renderIt("?job=job_2626f1e8&tab=assets");
    fireEvent.click(await screen.findByTestId("asset-edit-ast_0a0b0c0d"));
    expect(await screen.findByRole("dialog")).toHaveTextContent("Edit hotspots");
    expect(screen.queryByTestId("capture-discard")).toBeNull();
    fireEvent.click(screen.getByTestId("capture-add-spot"));
    fireEvent.change(screen.getByTestId("capture-spot-x-0"), { target: { value: "30" } });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByTestId("capture-unsaved").textContent).toMatch(/unsaved changes/);
    fireEvent.click(screen.getByTestId("capture-save"));
    await waitFor(() => expect(patches).toEqual([{ caption: "Home", hotspots: [{ name: "spot_1", x: 0.3, y: 0.5, description: "" }] }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("the composer's note is cleared when the screenshot is deleted again", async () => {
    server.use(
      http.get(`${B}/settings`, () => HttpResponse.json({ ...CAP_SETTINGS, capture_enabled: true, capture_policy_configured: true })),
      http.post(`${B}/capture`, () => HttpResponse.json({ asset: { id: "ast_dead0003", caption: "A", hotspots: [], width: 1920, height: 1080, bytes: 1, created: 1, source: "https://www.acme.example/" } }, { status: 201 })),
      http.delete(`${B}/assets/:id`, () => new HttpResponse(null, { status: 204 })),
      ...handlers(),
    );
    renderIt();
    fireEvent.change(await screen.findByTestId("task-input"), { target: { value: "About https://acme.example" } });
    fireEvent.click(await screen.findByTestId("capture-suggestion-open"));
    fireEvent.change(await screen.findByTestId("capture-caption"), { target: { value: "A" } });
    fireEvent.click(screen.getByTestId("capture-submit"));
    await screen.findByTestId("capture-preview");
    expect(screen.getByTestId("capture-suggestion-done").textContent).toMatch(/www\.acme\.example added as ast_dead0003/);
    fireEvent.click(screen.getByTestId("capture-discard"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.queryByTestId("capture-suggestion-done")).toBeNull();
    expect(screen.getByTestId("capture-suggestion-open")).toBeInTheDocument();
  });

  it("a refused upload shows the server's reason", async () => {
    server.use(
      http.get(`${B}/assets`, () => HttpResponse.json({ assets: [], limits: { max_assets: 50, max_image_bytes: 2097152, max_upload_bytes: 8388608, max_caption: 160, max_hotspots: 8 } })),
      http.post(`${B}/assets`, () => HttpResponse.json({ detail: "Only PNG images can be uploaded" }, { status: 422 })),
      ...handlers(),
    );
    renderIt("?tab=assets");
    await screen.findByTestId("assets-empty");
    const URLs = globalThis.URL as unknown as { createObjectURL?: unknown; revokeObjectURL?: unknown };
    URLs.createObjectURL = () => "blob:preview"; URLs.revokeObjectURL = () => undefined;
    fireEvent.change(screen.getByTestId("asset-input"), { target: { files: [new File([new Uint8Array([1])], "a.png", { type: "image/png" })] } });
    fireEvent.change(await screen.findByTestId("asset-caption"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("asset-upload"));
    expect((await screen.findByTestId("asset-upload-error")).textContent).toBe("Only PNG images can be uploaded");
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
