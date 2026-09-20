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
vi.mock("@/components/VideoPlayer", () => ({ VideoPlayer: ({ videoPath }: { videoPath: string }) => <div data-testid="player">{videoPath}</div> }));

import { MARKER_VIDEO, VideoProducerPage } from "@/pages/video-producer";

const B = "/v1/console/video";
const JOBS = { total: 2, jobs: [
  { id: "job_2626f1e8", task: "Erkläre kurz den Unterschied zwischen HTTP und HTTPS.", status: "complete", created_at: "2026-09-13T08:11:46", percent: 100 },
  { id: "job_run", task: "Still rendering", status: "skills_running", created_at: "2026-09-20T10:00:00", percent: 40, current_step: "Rendering scene 3", current_scene: 3, total_scenes: 7 },
] };
const OVERVIEW = { jobs_total: 2, by_status: { complete: 1, skills_running: 1 }, videos: 1, runtime_s: 44.3, size_bytes: 798680, measured_videos: 1, mean_score_share: 0.75, last_activity: "2026-09-20T10:00:00", ffprobe_available: true, plugin_source: "/x/src" };
const QUALITY = {
  job_id: "job_2626f1e8", status: "complete", measured_at: "2026-09-20T10:05:00Z", ffprobe_available: true,
  source: { video: "/v/output.mp4", captions: "/v/output.srt", scenes_dir: "/v/scenes", storyboard_scenes: 2, metadata: null },
  container: { format: "mov", duration_s: 44.35, size_bytes: 798680, bitrate_kbps: 144.1 },
  video: { codec: "h264", width: 1280, height: 720, fps: 60, pixel_format: "yuv420p", bitrate_kbps: 28.6 },
  audio: { codec: "aac", sample_rate_hz: 24000, channels: 1, bitrate_kbps: 108.8 },
  captions: { file: "output.srt", cues: 10, covered_s: 44.3, words: 69, coverage: 0.999, duplicate_consecutive: 2 },
  scenes: [
    { index: 1, id: "s1", kind: "title", planned_s: 3, actual_s: 1.85, drift_pct: -38.3, rendered: true, size_bytes: 32143, has_slide: true, has_voice: true, voice_s: 1.85, narration_words: 3 },
    { index: 2, id: "s2", kind: "narration", planned_s: 1, actual_s: 5.76, drift_pct: 476, rendered: true, size_bytes: 101280, has_slide: true, has_voice: true, voice_s: 5.76, narration_words: 11 },
  ],
  summary: { scenes_planned: 2, scenes_rendered: 2, planned_s: 4, rendered_s: 44.35, size_bytes: 798680 },
  production: { started_at: "2026-09-13T08:11:46", completed_at: "2026-09-13T08:12:16", seconds: 30 },
  checks: [
    { id: "playable", label: "Container readable", status: "pass", detail: "mov · 44.35 s" },
    { id: "captions_dupes", label: "No repeated consecutive captions", status: "warn", detail: "2 repeated cue(s)" },
    { id: "timing", label: "Scene timing within 10 % of the storyboard", status: "fail", detail: "largest drift 476 %" },
  ],
  score: { passed: 1, warned: 1, failed: 1, total: 3, skipped: 0, share: 0.333 },
};
const seen: Array<{ path: string; csrf: string | null; body: unknown }> = [];

function handlers() {
  return [
    http.get(`${B}/overview`, () => HttpResponse.json(OVERVIEW)),
    http.get(`${B}/jobs`, () => HttpResponse.json(JOBS)),
    http.get(`${B}/jobs/job_2626f1e8`, () => HttpResponse.json({ ...JOBS.jobs[0], started_at: "2026-09-13T08:11:46", completed_at: "2026-09-13T08:12:16" })),
    http.get(`${B}/jobs/job_2626f1e8/quality-metrics`, () => HttpResponse.json(QUALITY)),
    http.get(`${B}/jobs/job_2626f1e8/learning-metrics`, () => HttpResponse.json({ job_id: "job_2626f1e8", total_feedback_events: 0, approved: 0, rejected: 0, average_confidence: null, events: [], source: "learning.event_store" })),
    http.get(`${B}/videos/job_2626f1e8/captions`, () => HttpResponse.json({ content: "1\n00:00:00,000 --> 00:00:01,848\nWelcome to Corvin\n\n2\n00:00:01,848 --> 00:00:07,608\nHTTP is the basic protocol.\n" })),
    http.get(`${B}/settings`, () => HttpResponse.json({ output_folder: "~/.corvin/video-producer/videos", tts_engine: "azure", max_duration_minutes: 60 })),
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
afterEach(() => { cleanup(); seen.length = 0; });

describe("Video Producer studio", () => {
  it("lists the library, selects the first video and plays it with its transcript", async () => {
    server.use(...handlers());
    renderIt();
    await screen.findByTestId("video-producer");
    expect(screen.getByText(MARKER_VIDEO)).toBeInTheDocument();
    await screen.findByTestId("job-job_2626f1e8");
    expect(screen.getByTestId("overview-tiles").textContent).toMatch(/1\s*Videos produced · 2 jobs in total/);
    expect(screen.getByTestId("overview-tiles").textContent).toMatch(/75 %\s*Checks passed · mean over 1 measured video/);
    await screen.findByTestId("player");
    expect(screen.getByTestId("player").textContent).toBe("/v1/console/video/videos/job_2626f1e8/download");
    await waitFor(() => expect(screen.getByTestId("transcript").textContent).toMatch(/Welcome to Corvin/));
  });

  it("the Quality tab shows what was measured, with a named denominator", async () => {
    server.use(...handlers());
    renderIt("?job=job_2626f1e8&tab=quality");
    await screen.findByTestId("quality-tab");
    expect(screen.getByTestId("score-ring").textContent).toMatch(/1 of 3 checks passed/);
    expect(screen.getByTestId("check-timing").textContent).toMatch(/largest drift 476 %/);
    expect(screen.getByTestId("check-captions_dupes").textContent).toMatch(/2 repeated cue/);
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
