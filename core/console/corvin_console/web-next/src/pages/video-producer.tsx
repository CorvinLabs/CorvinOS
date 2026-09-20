/**
 * Video Producer — studio: produce, watch, measure, teach.
 *
 * Left: a new production and the library (poster, status, runtime, checklist
 * share). Right: the selected video's studio with three tabs —
 *   Playback  the MP4 with its transcript;
 *   Quality   what ffprobe measured on the real artifacts
 *             (routes/video_producer_api.py → video_quality.py): stream facts,
 *             captions, a checklist with a named denominator, and every scene's
 *             planned vs rendered seconds;
 *   Learning  the feedback the operator gave on this job (ADR-0314 events)
 *             and the buttons to give more, per scene.
 *
 * Replaces the standalone "Video Quality" page (2026-09-20), which drew one
 * hard-coded record for any job id. Nothing here is invented: an unmeasured
 * value is "—", a missing ffprobe says so, and the score is "n of m checks".
 */
import { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Clapperboard, Film, Loader2, PlayCircle, RefreshCw, ThumbsDown, ThumbsUp, Wand2 } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { VideoPlayer } from "@/components/VideoPlayer";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";

// ── Types (routes/video_producer_api.py, video_quality.py, video_learning_api.py) ──

export interface Job {
  id: string; task: string; status: string; created_at: string; percent: number;
  current_step?: string | null; current_scene?: number | null; total_scenes?: number | null;
  started_at?: string | null; completed_at?: string | null; error_message?: string | null; video_output_path?: string | null;
}
export interface Overview {
  jobs_total: number; by_status: Record<string, number>; videos: number; runtime_s: number; size_bytes: number;
  measured_videos: number; mean_score_share: number | null; last_activity: string | null; ffprobe_available: boolean; plugin_source: string | null;
}
export interface Check { id: string; label: string; status: "pass" | "warn" | "fail" | "skip"; detail: string }
export interface SceneRow {
  index: number; id: string; kind?: string | null; planned_s: number | null; actual_s: number | null; drift_pct: number | null;
  rendered: boolean; size_bytes: number | null; has_slide: boolean; has_voice: boolean; voice_s: number | null; narration_words: number | null;
}
export interface Quality {
  job_id: string; status: string; measured_at: string; ffprobe_available: boolean;
  source: { video: string | null; captions: string | null; scenes_dir: string | null; storyboard_scenes: number; metadata: Record<string, unknown> | null };
  container: { format: string | null; duration_s: number | null; size_bytes: number; bitrate_kbps: number } | null;
  video: { codec: string | null; width: number | null; height: number | null; fps: number | null; pixel_format: string | null; bitrate_kbps: number | null } | null;
  audio: { codec: string | null; sample_rate_hz: number | null; channels: number | null; bitrate_kbps: number | null } | null;
  captions: { file: string; cues: number; covered_s: number; words: number; coverage: number | null; duplicate_consecutive: number } | null;
  scenes: SceneRow[];
  summary: { scenes_planned: number; scenes_rendered: number; planned_s: number; rendered_s: number | null; size_bytes: number | null };
  production: { started_at: string | null; completed_at: string | null; seconds: number | null };
  checks: Check[];
  score: { passed: number; warned: number; failed: number; total: number; skipped: number; share: number | null };
}
export interface Learning {
  job_id: string; total_feedback_events: number; approved: number; rejected: number; average_confidence: number | null;
  events: Array<{ timestamp: string; outcome: string | null; quality_rating: number | null; confidence: number | null; source: string | null }>;
  source: string;
}

const BASE = "/video";
const ACTIVE = ["pending", "storyboard_generating", "skills_running"];

/** Rendered caption — also the deploy marker. */
export const MARKER_VIDEO = "Produce a video from a task, watch it, see what ffprobe measured on the real artifacts, and teach the producer scene by scene.";

// ── formatting ───────────────────────────────────────────────────────────────

const fmtWhen = (iso?: string | null) => (iso ? new Date(iso).toLocaleString("en-US") : "—");
const fmtDur = (s?: number | null) => (s === null || s === undefined ? "—" : s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")} min` : `${s.toFixed(1)} s`);
const fmtBytes = (n?: number | null) => (n === null || n === undefined ? "—" : n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(2)} MB`);
const pct = (v?: number | null) => (v === null || v === undefined ? "—" : `${Math.round(v * 100)} %`);
const STATUS_LABEL: Record<string, string> = { pending: "queued", storyboard_generating: "writing storyboard", skills_running: "rendering", complete: "produced", error: "failed" };

function StatusPill({ status }: { status: string }) {
  const variant = status === "complete" ? "ok" : status === "error" ? "danger" : ACTIVE.includes(status) ? "accent" : "outline";
  return <Badge variant={variant}>{STATUS_LABEL[status] ?? status.replace(/_/g, " ")}</Badge>;
}

function CheckIcon({ s }: { s: Check["status"] }) {
  if (s === "pass") return <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />;
  if (s === "warn") return <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />;
  if (s === "fail") return <AlertCircle className="w-4 h-4 text-destructive shrink-0" />;
  return <span className="w-4 h-4 rounded-full border border-border shrink-0" />;
}

function ScoreRing({ score }: { score: Quality["score"] }) {
  const share = score.share ?? 0;
  const r = 34, c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-4" data-testid="score-ring">
      <svg width="88" height="88" viewBox="0 0 88 88" role="img" aria-label={`${score.passed} of ${score.total} checks passed`}>
        <circle cx="44" cy="44" r={r} fill="none" stroke="var(--viz-grid)" strokeWidth="8" />
        <circle cx="44" cy="44" r={r} fill="none" stroke="var(--viz-role-os)" strokeWidth="8" strokeLinecap="round"
          strokeDasharray={`${c * share} ${c}`} transform="rotate(-90 44 44)" />
        <text x="44" y="49" textAnchor="middle" fontSize="18" fontWeight="700" fill="currentColor">{score.share === null ? "—" : `${Math.round(share * 100)}`}</text>
      </svg>
      <div>
        <div className="text-sm font-semibold">{score.share === null ? "Not measurable" : `${score.passed} of ${score.total} checks passed`}</div>
        <div className="text-xs text-muted-foreground">{score.warned} warning{score.warned === 1 ? "" : "s"} · {score.failed} failed{score.skipped ? ` · ${score.skipped} skipped` : ""}</div>
      </div>
    </div>
  );
}

function Fact({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{k}</div>
      <div className="text-sm font-medium tabular-nums break-words">{v}</div>
    </div>
  );
}

function parseSrt(text: string): Array<{ start: string; text: string }> {
  return text.split(/\n\s*\n/).map((b) => b.trim().split("\n")).filter((l) => l.length >= 3)
    .map((l) => ({ start: l[1].split("-->")[0].trim().replace(/,\d+$/, ""), text: l.slice(2).join(" ") }));
}

// ── Studio tabs ──────────────────────────────────────────────────────────────

function PlaybackTab({ job }: { job: Job }) {
  const captions = useQuery({ queryKey: ["video", "captions", job.id], queryFn: ({ signal }) => api<{ content: string }>(`${BASE}/videos/${job.id}/captions`, { signal }), retry: false, enabled: job.status === "complete" });
  if (job.status !== "complete") {
    return (
      <div className="space-y-3" data-testid="playback-pending">
        <div className="flex justify-between text-xs text-muted-foreground"><span>{job.current_step || "Working…"}</span><span>{job.percent ?? 0}%</span></div>
        <Progress value={job.percent ?? 0} />
        {job.total_scenes ? <p className="text-xs text-muted-foreground">Scene {job.current_scene ?? 0} of {job.total_scenes}</p> : null}
        {job.error_message && <div className="text-sm rounded-md border border-destructive/30 bg-destructive/10 p-3 text-destructive">{job.error_message}</div>}
      </div>
    );
  }
  const cues = captions.data ? parseSrt(captions.data.content) : [];
  return (
    <div className="space-y-4">
      <VideoPlayer videoPath={`/v1/console${BASE}/videos/${job.id}/download`} title={job.task} onDownload={() => { window.location.href = `/v1/console${BASE}/videos/${job.id}/download`; }} />
      <div>
        <div className="text-sm font-semibold mb-2">Transcript <span className="text-muted-foreground font-normal">· {captions.isError ? "no captions" : `${cues.length} cues`}</span></div>
        <ol className="max-h-56 overflow-y-auto space-y-1 text-sm" data-testid="transcript">
          {cues.map((c, i) => <li key={i} className="grid grid-cols-[5rem_1fr] gap-2"><span className="font-mono text-xs text-muted-foreground">{c.start}</span><span>{c.text}</span></li>)}
        </ol>
      </div>
    </div>
  );
}

function QualityTab({ q }: { q: Quality }) {
  const [open, setOpen] = useState(false);
  const chart = q.scenes.map((s) => ({ name: s.id, planned: s.planned_s ?? 0, rendered: s.actual_s ?? 0 }));
  const groups: Array<[string, Check["status"][]]> = [["Needs attention", ["fail", "warn"]], ["Passed", ["pass"]], ["Not measurable", ["skip"]]];
  return (
    <div className="space-y-5" data-testid="quality-tab">
      <div className="grid gap-4 lg:grid-cols-[auto_1fr] items-start">
        <ScoreRing score={q.score} />
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
          <Fact k="Runtime" v={fmtDur(q.container?.duration_s)} />
          <Fact k="Picture" v={q.video ? `${q.video.width}×${q.video.height} · ${q.video.fps} fps · ${q.video.codec}` : "—"} />
          <Fact k="Sound" v={q.audio ? `${q.audio.codec} · ${q.audio.sample_rate_hz ? `${(q.audio.sample_rate_hz / 1000).toFixed(1)} kHz` : "—"} · ${q.audio.channels === 1 ? "mono" : q.audio.channels === 2 ? "stereo" : `${q.audio.channels} ch`}` : "none"} />
          <Fact k="File" v={q.container ? `${fmtBytes(q.container.size_bytes)} · ${q.container.bitrate_kbps} kbps` : "—"} />
          <Fact k="Captions" v={q.captions ? `${q.captions.cues} cues · ${pct(q.captions.coverage)} covered` : "none"} />
          <Fact k="Scenes" v={`${q.summary.scenes_rendered} of ${q.summary.scenes_planned} rendered`} />
          <Fact k="Planned vs rendered" v={`${fmtDur(q.summary.planned_s)} → ${fmtDur(q.summary.rendered_s)}`} />
          <Fact k="Production time" v={q.production.seconds !== null ? fmtDur(q.production.seconds) : "—"} />
        </div>
      </div>
      {!q.ffprobe_available && <div className="text-sm rounded-md border border-amber-500/30 bg-amber-500/10 p-3">ffprobe is not installed on this host, so the stream facts could not be measured.</div>}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Checklist</CardTitle><CardDescription>Each check is a measurement on the produced file, not a rating.</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            {groups.map(([title, statuses]) => {
              const items = q.checks.filter((c) => statuses.includes(c.status));
              if (items.length === 0) return null;
              return (
                <div key={title}>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">{title}</div>
                  <ul className="space-y-1">
                    {items.map((c) => (
                      <li key={c.id} className="flex items-start gap-2 text-sm" data-testid={`check-${c.id}`}>
                        <CheckIcon s={c.status} /><span className="flex-1">{c.label}<span className="text-muted-foreground"> · {c.detail}</span></span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Scene timing</CardTitle><CardDescription data-testid="timing-caption">
            {chart.length === 0 ? "No scenes on this job." : `Seconds per scene — the storyboard's plan next to what was rendered (${chart.length} scenes, one shared scale).`}
          </CardDescription></CardHeader>
          {chart.length > 0 && (
            <CardContent>
              <div className="h-56">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={chart} margin={{ top: 4, right: 8, bottom: 4, left: 0 }}>
                    <CartesianGrid stroke="var(--viz-grid)" vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} stroke="var(--viz-grid)" />
                    <YAxis tickFormatter={(v) => `${v} s`} tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} stroke="var(--viz-grid)" width={44} />
                    <Tooltip formatter={(v: number | string) => `${Number(v).toFixed(1)} s`} contentStyle={{ background: "hsl(var(--background))", border: "1px solid hsl(var(--border))", borderRadius: 6, fontSize: 12 }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar dataKey="planned" name="planned" fill="var(--viz-tier-1)" radius={[3, 3, 0, 0]} minPointSize={2} isAnimationActive={false} />
                    <Bar dataKey="rendered" name="rendered" fill="var(--viz-role-os)" radius={[3, 3, 0, 0]} minPointSize={2} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          )}
        </Card>
      </div>

      {q.scenes.length > 0 && (
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-base">Scenes</CardTitle></CardHeader>
          <CardContent>
            <div className="flex gap-2 overflow-x-auto pb-2" data-testid="filmstrip">
              {q.scenes.map((s) => (
                <figure key={s.index} className="w-36 shrink-0">
                  <div className="aspect-video rounded-md overflow-hidden border border-border bg-muted/40">
                    {s.has_slide ? <img src={`/v1/console${BASE}/videos/${q.job_id}/scenes/${s.index}/slide`} alt={`Scene ${s.index}`} className="w-full h-full object-cover" loading="lazy" /> : <div className="w-full h-full flex items-center justify-center text-xs text-muted-foreground">no slide</div>}
                  </div>
                  <figcaption className="text-xs mt-1 truncate"><span className="font-mono">{s.id}</span> · {fmtDur(s.actual_s)}{s.drift_pct !== null ? <span className={Math.abs(s.drift_pct) > 25 ? " text-destructive" : " text-muted-foreground"}> · {s.drift_pct > 0 ? "+" : ""}{s.drift_pct}%</span> : null}</figcaption>
                </figure>
              ))}
            </div>
            <button type="button" onClick={() => setOpen((o) => !o)} className="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
              {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />} {open ? "Hide" : "Show"} scene table
            </button>
            {open && (
              <div className="mt-2 rounded-lg border border-border overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 border-b border-border"><tr>
                    <th className="text-left px-3 py-2">Scene</th><th className="text-left px-3 py-2">Kind</th><th className="text-right px-3 py-2">Planned</th><th className="text-right px-3 py-2">Rendered</th><th className="text-right px-3 py-2">Drift</th><th className="text-right px-3 py-2">Voice</th><th className="text-right px-3 py-2">Words</th><th className="text-right px-3 py-2">Size</th>
                  </tr></thead>
                  <tbody>
                    {q.scenes.map((s) => (
                      <tr key={s.index} className="border-b border-border last:border-b-0">
                        <td className="px-3 py-1.5 font-mono">{s.id}</td><td className="px-3 py-1.5">{s.kind ?? "—"}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmtDur(s.planned_s)}</td><td className="px-3 py-1.5 text-right tabular-nums">{fmtDur(s.actual_s)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{s.drift_pct === null ? "—" : `${s.drift_pct > 0 ? "+" : ""}${s.drift_pct} %`}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{s.has_voice ? fmtDur(s.voice_s) : "none"}</td><td className="px-3 py-1.5 text-right tabular-nums">{s.narration_words ?? "—"}</td><td className="px-3 py-1.5 text-right tabular-nums">{fmtBytes(s.size_bytes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      )}
      <div className="text-xs text-muted-foreground">Measured {fmtWhen(q.measured_at)}{q.source.video ? <> from <span className="font-mono">{q.source.video}</span></> : " — no output file"}.</div>
    </div>
  );
}

function LearningTab({ job, scenes }: { job: Job; scenes: SceneRow[] }) {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const learning = useQuery({ queryKey: ["video", "learning", job.id], queryFn: ({ signal }) => api<Learning>(`${BASE}/jobs/${job.id}/learning-metrics`, { signal }), retry: false });
  const [msg, setMsg] = useState<string | null>(null);
  const give = useMutation({
    mutationFn: ({ scene, type }: { scene: string; type: "approve" | "reject" }) =>
      api(`${BASE}/jobs/${job.id}/scenes/${encodeURIComponent(scene)}/feedback`, { method: "POST", csrf, body: { feedback_type: type, confidence: 0.8 } }),
    onSuccess: (_r, v) => { setMsg(`Recorded: ${v.type} for ${v.scene}.`); void qc.invalidateQueries({ queryKey: ["video", "learning", job.id] }); },
    onError: () => setMsg("Feedback could not be recorded."),
  });
  const l = learning.data;
  return (
    <div className="space-y-4" data-testid="learning-tab">
      <div className="grid grid-cols-3 gap-2">
        <Fact k="Feedback events" v={l ? l.total_feedback_events : "—"} />
        <Fact k="Approved · rejected" v={l ? `${l.approved} · ${l.rejected}` : "—"} />
        <Fact k="Mean confidence" v={l && l.average_confidence !== null ? pct(l.average_confidence) : "—"} />
      </div>
      <p className="text-xs text-muted-foreground" data-testid="learning-caption">
        {learning.isError ? "Learning events could not be loaded." : l && l.total_feedback_events === 0 ? "No feedback on this job yet. Every approval or rejection below becomes an ADR-0314 feedback event the optimizer reads." : l ? `Read from ${l.source}.` : "loading…"}
      </p>
      {scenes.length > 0 && (
        <div className="space-y-1">
          <div className="text-sm font-semibold">Teach per scene</div>
          <div className="flex flex-wrap gap-1.5">
            {scenes.map((s) => (
              <div key={s.id} className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
                <span className="font-mono">{s.id}</span>
                <Button variant="ghost" size="sm" className="h-6 px-1" aria-label={`approve ${s.id}`} disabled={!csrf || give.isPending} onClick={() => give.mutate({ scene: s.id, type: "approve" })}><ThumbsUp className="w-3 h-3" /></Button>
                <Button variant="ghost" size="sm" className="h-6 px-1" aria-label={`reject ${s.id}`} disabled={!csrf || give.isPending} onClick={() => give.mutate({ scene: s.id, type: "reject" })}><ThumbsDown className="w-3 h-3" /></Button>
              </div>
            ))}
          </div>
        </div>
      )}
      {msg && <p className="text-xs text-muted-foreground" data-testid="feedback-msg">{msg}</p>}
      {l && l.events.length > 0 && (
        <ul className="text-xs space-y-1 max-h-40 overflow-y-auto">
          {l.events.slice().reverse().map((e, i) => <li key={i} className="flex gap-2"><span className="text-muted-foreground">{fmtWhen(e.timestamp)}</span><span>{e.outcome === "yes" ? "approved" : e.outcome === "no" ? "rejected" : e.outcome ?? "—"}</span>{e.confidence !== null && <span className="text-muted-foreground">· confidence {pct(e.confidence)}</span>}</li>)}
        </ul>
      )}
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function VideoProducerPage() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { search } = useLocation();
  const params = useMemo(() => new URLSearchParams(search), [search]);
  const [selected, setSelected] = useState<string | null>(params.get("job"));
  const [tab, setTab] = useState<"playback" | "quality" | "learning">((params.get("tab") as "playback" | "quality" | "learning") || "playback");
  const [task, setTask] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);

  const overview = useQuery({ queryKey: ["video", "overview"], queryFn: ({ signal }) => api<Overview>(`${BASE}/overview`, { signal }), retry: false, refetchInterval: 15_000 });
  const jobs = useQuery({
    queryKey: ["video", "jobs"], queryFn: ({ signal }) => api<{ jobs: Job[]; total: number }>(`${BASE}/jobs?limit=50`, { signal }), retry: false,
    refetchInterval: (q) => (q.state.data?.jobs.some((j) => ACTIVE.includes(j.status)) ? 1500 : 10_000),
  });
  useEffect(() => { if (!selected && jobs.data?.jobs.length) setSelected(jobs.data.jobs[0].id); }, [jobs.data, selected]);
  useEffect(() => {
    const p = new URLSearchParams(); if (selected) p.set("job", selected); p.set("tab", tab);
    navigate({ search: `?${p.toString()}` }, { replace: true });
  }, [selected, tab, navigate]);

  const job = useQuery({ queryKey: ["video", "job", selected], queryFn: ({ signal }) => api<Job>(`${BASE}/jobs/${selected}`, { signal }), enabled: !!selected, retry: false,
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 1000 : false) });
  const quality = useQuery({ queryKey: ["video", "quality", selected, job.data?.status], queryFn: ({ signal }) => api<Quality>(`${BASE}/jobs/${selected}/quality-metrics`, { signal }), enabled: !!selected && job.data?.status === "complete", retry: false });
  const settings = useQuery({ queryKey: ["video", "settings"], queryFn: ({ signal }) => api<{ output_folder: string; tts_engine: string; max_duration_minutes: number }>(`${BASE}/settings`, { signal }), retry: false });
  const [form, setForm] = useState<{ output_folder: string; tts_engine: string; max_duration_minutes: number } | null>(null);
  useEffect(() => { if (settings.data && !form) setForm(settings.data); }, [settings.data, form]);

  const create = useMutation({
    mutationFn: (t: string) => api<{ job_id: string }>(`${BASE}/jobs`, { method: "POST", csrf, body: { task: t } }),
    onSuccess: (r) => { setTask(""); setSelected(r.job_id); setTab("playback"); void qc.invalidateQueries({ queryKey: ["video"] }); },
  });
  const save = useMutation({ mutationFn: (f: NonNullable<typeof form>) => api(`${BASE}/settings`, { method: "PUT", csrf, body: f }) });

  if (jobs.isError) {
    const off = jobs.error instanceof ApiError && (jobs.error.status === 503 || jobs.error.status === 404);
    return (
      <div className="max-w-7xl mx-auto p-6">
        <Card className="border-destructive/30 bg-destructive/10"><CardContent className="py-6 flex items-center gap-2 text-destructive text-sm">
          <AlertCircle size={18} /> {off ? "The Video Producer plugin is not available on this build." : "The video library could not be loaded."}
        </CardContent></Card>
      </div>
    );
  }

  const ov = overview.data;
  const list = jobs.data?.jobs ?? [];
  const j = job.data;

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="video-producer">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2"><Clapperboard className="w-7 h-7" /> Video Producer</h1>
          <p className="text-muted-foreground max-w-3xl">{MARKER_VIDEO}</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void qc.invalidateQueries({ queryKey: ["video"] })}><RefreshCw className="w-4 h-4" /> Refresh</Button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4" data-testid="overview-tiles">
        {[
          ["Videos produced", ov ? String(ov.videos) : "—", ov ? `${ov.jobs_total} jobs in total` : ""],
          ["Total runtime", ov ? fmtDur(ov.runtime_s) : "—", ov ? fmtBytes(ov.size_bytes) : ""],
          ["Checks passed", ov && ov.mean_score_share !== null ? pct(ov.mean_score_share) : "—", ov ? (ov.measured_videos ? `mean over ${ov.measured_videos} measured video${ov.measured_videos === 1 ? "" : "s"}` : "nothing measured yet") : ""],
          ["Last activity", ov?.last_activity ? fmtWhen(ov.last_activity) : "—", ov?.plugin_source ? "plugin loaded" : ""],
        ].map(([label, value, sub]) => (
          <Card key={label}><CardContent className="pt-5 pb-4">
            <div className="text-2xl font-bold tabular-nums">{value}</div>
            <div className="text-xs text-muted-foreground">{label}{sub ? ` · ${sub}` : ""}</div>
          </CardContent></Card>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-12">
        <div className="lg:col-span-4 space-y-6">
          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2"><Wand2 className="w-4 h-4" /> New production</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <textarea value={task} onChange={(e) => setTask(e.target.value)} rows={4} placeholder="Explain in a short video what HTTPS adds to HTTP. Length: one minute."
                className="w-full px-3 py-2 rounded-md border border-border bg-background text-foreground text-sm" data-testid="task-input" />
              <Button variant="accent" className="w-full" disabled={!task.trim() || create.isPending || !csrf} onClick={() => create.mutate(task)} data-testid="start-production">
                {create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <PlayCircle className="w-4 h-4" />} Start production
              </Button>
              {create.isError && <p className="text-xs text-destructive">The production could not be started.</p>}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2"><CardTitle className="text-base flex items-center gap-2"><Film className="w-4 h-4" /> Library</CardTitle><CardDescription>{jobs.isLoading ? "loading…" : `${list.length} of ${jobs.data?.total ?? list.length} jobs`}</CardDescription></CardHeader>
            <CardContent>
              {list.length === 0 && !jobs.isLoading ? <p className="text-sm text-muted-foreground">No videos yet. Start a production above.</p> : (
                <ul className="space-y-2 max-h-[34rem] overflow-y-auto pr-1" data-testid="library">
                  {list.map((it) => (
                    <li key={it.id}>
                      <button type="button" onClick={() => setSelected(it.id)} className={`w-full text-left rounded-lg border p-2 flex gap-3 transition ${selected === it.id ? "border-accent bg-accent/10" : "border-border hover:bg-muted/40"}`} data-testid={`job-${it.id}`}>
                        <div className="w-24 shrink-0 aspect-video rounded-md overflow-hidden border border-border bg-muted/40">
                          {it.status === "complete" ? <img src={`/v1/console${BASE}/videos/${it.id}/poster`} alt="" className="w-full h-full object-cover" loading="lazy" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} /> : null}
                        </div>
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium truncate">{it.task}</div>
                          <div className="flex items-center gap-2 mt-1"><StatusPill status={it.status} /><span className="text-xs text-muted-foreground">{fmtWhen(it.created_at)}</span></div>
                          {ACTIVE.includes(it.status) && <div className="mt-1"><Progress value={it.percent ?? 0} /></div>}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="lg:col-span-8">
          <Card className="min-h-[24rem]" data-testid="studio">
            {!j ? (
              <CardContent className="py-16 text-center text-sm text-muted-foreground">{selected ? <Loader2 className="w-6 h-6 animate-spin mx-auto" /> : "Select a video from the library."}</CardContent>
            ) : (
              <>
                <CardHeader className="pb-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <CardTitle className="text-lg flex-1 min-w-0 truncate">{j.task}</CardTitle>
                    <StatusPill status={j.status} />
                  </div>
                  <CardDescription>Created {fmtWhen(j.created_at)}{j.completed_at ? ` · produced ${fmtWhen(j.completed_at)}` : ""}{quality.data?.production.seconds ? ` in ${fmtDur(quality.data.production.seconds)}` : ""} · <span className="font-mono">{j.id}</span></CardDescription>
                  <div className="flex gap-1 mt-2" role="tablist">
                    {(["playback", "quality", "learning"] as const).map((t) => (
                      <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} data-testid={`tab-${t}`}
                        className={`px-3 py-1.5 rounded-md text-sm capitalize ${tab === t ? "bg-accent/15 text-foreground font-medium" : "text-muted-foreground hover:text-foreground"}`}>{t}</button>
                    ))}
                  </div>
                </CardHeader>
                <CardContent>
                  {tab === "playback" && <PlaybackTab job={j} />}
                  {tab === "quality" && (j.status !== "complete" ? <p className="text-sm text-muted-foreground" data-testid="quality-pending">Quality is measured once the video is produced.</p>
                    : quality.isLoading ? <Loader2 className="w-6 h-6 animate-spin" /> : quality.isError ? <p className="text-sm text-destructive">The quality measurement failed.</p> : quality.data ? <QualityTab q={quality.data} /> : null)}
                  {tab === "learning" && <LearningTab job={j} scenes={quality.data?.scenes ?? []} />}
                </CardContent>
              </>
            )}
          </Card>
        </div>
      </div>

      <Card>
        <button type="button" onClick={() => setSettingsOpen((o) => !o)} className="w-full text-left px-6 py-3 flex items-center gap-2 text-sm font-semibold">
          {settingsOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />} Settings
          <span className="text-xs font-normal text-muted-foreground">{settings.data ? `${settings.data.tts_engine} · ${settings.data.output_folder}` : ""}</span>
        </button>
        {settingsOpen && form && (
          <CardContent className="grid gap-3 md:grid-cols-3">
            <label className="text-sm"><span className="text-xs text-muted-foreground">Output folder</span><Input value={form.output_folder} onChange={(e) => setForm({ ...form, output_folder: e.target.value })} /></label>
            <label className="text-sm"><span className="text-xs text-muted-foreground">Text-to-speech</span>
              <select value={form.tts_engine} onChange={(e) => setForm({ ...form, tts_engine: e.target.value })} className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm">
                <option value="azure">Azure</option><option value="google">Google</option><option value="local">Local (Hermes)</option>
              </select></label>
            <label className="text-sm"><span className="text-xs text-muted-foreground">Max duration (minutes, 0 = unlimited)</span><Input type="number" min={0} value={form.max_duration_minutes} onChange={(e) => setForm({ ...form, max_duration_minutes: Math.max(0, parseInt(e.target.value) || 0) })} /></label>
            <div className="md:col-span-3"><Button variant="outline" size="sm" disabled={save.isPending || !csrf} onClick={() => save.mutate(form)}>{save.isSuccess ? "Saved" : "Save settings"}</Button></div>
          </CardContent>
        )}
      </Card>
    </div>
  );
}

export default VideoProducerPage;
