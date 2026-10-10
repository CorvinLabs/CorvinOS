/**
 * Video Producer — studio: produce, watch, revise, measure, teach.
 *
 * One column, top to bottom: the selected video fills the panel (own fullscreen),
 * directly below it the composer (attachment, voice, hold Space to dictate) that either
 * starts a NEW video or sends a change request that produces a REVISION of the selected
 * one (the original stays), then the library of produced videos. Quality / Learning /
 * Settings sit in a collapsible section underneath:
 *   Quality   what ffprobe measured on the real artifacts
 *             (routes/video_producer_api.py → video_quality.py): stream facts,
 *             a checklist with a named denominator (incl. "no subtitles"), and every scene's
 *             planned vs rendered seconds;
 *   Learning  the feedback the operator gave on this job (ADR-0314 events)
 *             and the buttons to give more, per scene.
 *
 * Replaces the standalone "Video Quality" page (2026-09-20), which drew one
 * hard-coded record for any job id. Nothing here is invented: an unmeasured
 * value is "—", a missing ffprobe says so, and the score is "n of m checks".
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Clapperboard, Download, Film, Loader2, Maximize2, Mic, Minimize2, Paperclip, Pencil, RefreshCw, Send, Square, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { api, ApiError } from "@/lib/api/client";
import { useAuth } from "@/lib/auth";
import { useVoiceInput } from "@/hooks/use-voice-input";
import { extractVideoSources, MAX_VIDEO_SOURCES, VIDEO_SOURCE_ACCEPT, type VideoSource } from "@/lib/api/video-sources";
import { BUILTIN_STYLE_ID, STYLE_DECK_ACCEPT, isDeckFile, type StyleList } from "@/lib/api/video-styles";
import { StyleChip, StyleImportDialog, StylesSection } from "@/components/video-styles";

// ── Types (routes/video_producer_api.py, video_quality.py, video_learning_api.py) ──

export interface Job {
  id: string; task: string; revision_of?: string | null; status: string; created_at: string; percent: number;
  current_step?: string | null; current_scene?: number | null; total_scenes?: number | null;
  started_at?: string | null; completed_at?: string | null; error_message?: string | null; video_output_path?: string | null;
}
export interface Overview {
  jobs_total: number; by_status: Record<string, number>; videos: number; runtime_s: number; size_bytes: number;
  measured_videos: number; mean_score_share: number | null; last_activity: string | null; ffprobe_available: boolean; plugin_source: string | null;
}
export interface Check { id: string; label: string; status: "pass" | "warn" | "fail" | "skip"; detail: string }
export interface SceneRow {
  index: number; id: string; kind?: string | null; planned_s: number | null; actual_s: number | null; drift_pct: number | null; voice_drift_pct?: number | null;
  rendered: boolean; size_bytes: number | null; has_slide: boolean; has_voice: boolean; voice_s: number | null; narration_words: number | null;
}
export interface Quality {
  job_id: string; status: string; measured_at: string; ffprobe_available: boolean;
  source: { video: string | null; scenes_dir: string | null; storyboard_scenes: number; metadata: Record<string, unknown> | null };
  container: { format: string | null; duration_s: number | null; size_bytes: number; bitrate_kbps: number } | null;
  video: { codec: string | null; width: number | null; height: number | null; fps: number | null; pixel_format: string | null; bitrate_kbps: number | null } | null;
  audio: { codec: string | null; sample_rate_hz: number | null; channels: number | null; bitrate_kbps: number | null } | null;
  subtitles: { streams: number; files: string[] } | null;
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
const TTS_LABELS: Record<string, string> = {
  openai: "OpenAI TTS (default)",
  auto: "Automatic: OpenAI, then edge-tts, then Piper",
  gtts: "Google TTS (gTTS, legacy)",
};
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

// ── Studio tabs (below the fold) ─────────────────────────────────────────────

function QualityTab({ q }: { q: Quality }) {
  const [open, setOpen] = useState(false);
  const chart = q.scenes.map((s) => ({ name: s.id, planned: s.planned_s ?? 0, rendered: s.actual_s ?? 0 }));
  const groups: Array<[string, Check["status"][]]> = [["Needs attention", ["fail", "warn"]], ["Passed", ["pass"]], ["Not measurable", ["skip"]]];
  return (
    <div className="space-y-5" data-testid="quality-tab">
      <div className="grid gap-4 lg:grid-cols-[auto_1fr] items-start">
        <ScoreRing score={q.score} />
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
          <Fact k="Runtime" v={fmtDur(q.container?.duration_s)} />
          <Fact k="Picture" v={q.video ? <><span className="whitespace-nowrap">{q.video.width}×{q.video.height}</span> · {q.video.fps} fps · {q.video.codec}</> : "—"} />
          <Fact k="Sound" v={q.audio ? `${q.audio.codec} · ${q.audio.sample_rate_hz ? `${(q.audio.sample_rate_hz / 1000).toFixed(1)} kHz` : "—"} · ${q.audio.channels === 1 ? "mono" : q.audio.channels === 2 ? "stereo" : `${q.audio.channels} ch`}` : "none"} />
          <Fact k="File" v={q.container ? `${fmtBytes(q.container.size_bytes)} · ${q.container.bitrate_kbps} kbps` : "—"} />
          <Fact k="Subtitles" v={q.subtitles ? (q.subtitles.streams || q.subtitles.files.length ? "present" : "none") : "—"} />
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
                  <figcaption className="text-xs mt-1 truncate"><span className="font-mono">{s.id}</span> · {fmtDur(s.actual_s)}{s.voice_drift_pct != null ? <span className={Math.abs(s.voice_drift_pct) > 25 ? " text-destructive" : " text-muted-foreground"}> · {s.voice_drift_pct > 0 ? "+" : ""}{s.voice_drift_pct}%</span> : null}</figcaption>
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
                    <th className="text-left px-3 py-2">Scene</th><th className="text-left px-3 py-2">Kind</th><th className="text-right px-3 py-2">Planned</th><th className="text-right px-3 py-2">Rendered</th><th className="text-right px-3 py-2">vs. voice</th><th className="text-right px-3 py-2">Voice</th><th className="text-right px-3 py-2">Words</th><th className="text-right px-3 py-2">Size</th>
                  </tr></thead>
                  <tbody>
                    {q.scenes.map((s) => (
                      <tr key={s.index} className="border-b border-border last:border-b-0">
                        <td className="px-3 py-1.5 font-mono">{s.id}</td><td className="px-3 py-1.5">{s.kind ?? "—"}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmtDur(s.planned_s)}</td><td className="px-3 py-1.5 text-right tabular-nums">{fmtDur(s.actual_s)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{s.voice_drift_pct == null ? "—" : `${s.voice_drift_pct > 0 ? "+" : ""}${s.voice_drift_pct} %`}</td>
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

// ── Stage: the selected video fills the panel ────────────────────────────────

const isTyping = (el: EventTarget | null) =>
  el instanceof HTMLElement && (["input", "textarea", "select"].includes(el.tagName.toLowerCase()) || el.isContentEditable);

function VideoStage({ job, loading }: { job: Job | undefined; loading: boolean }) {
  const box = useRef<HTMLElement>(null);
  const [full, setFull] = useState(false);
  const canFull = typeof document !== "undefined" && !!document.fullscreenEnabled;
  const playable = job?.status === "complete";

  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === box.current);
    document.addEventListener("fullscreenchange", on);
    return () => document.removeEventListener("fullscreenchange", on);
  }, []);
  const toggle = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void box.current?.requestFullscreen?.();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() !== "f" || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target) || !playable || !canFull) return;
      e.preventDefault();
      toggle();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [playable, canFull, toggle]);

  const url = job ? `/v1/console${BASE}/videos/${job.id}` : "";
  let body: React.ReactNode;
  if (loading) body = <Loader2 className="w-8 h-8 animate-spin text-white/70" />;
  else if (!job) {
    body = (
      <div className="text-center text-white/70 space-y-2 px-6" data-testid="stage-empty">
        <Film className="w-10 h-10 mx-auto opacity-60" />
        <p className="text-sm">Describe a video below. It plays here, full size.</p>
      </div>
    );
  } else if (job.status === "error") {
    body = <div className="max-w-xl text-sm rounded-md border border-red-400/40 bg-red-500/10 p-4 text-red-200" data-testid="stage-error">{job.error_message || "The production failed."}</div>;
  } else if (!playable) {
    body = (
      <div className="w-full max-w-xl px-6 space-y-3 text-white" data-testid="playback-pending">
        <div className="text-5xl font-semibold tabular-nums">{job.percent ?? 0}<span className="text-2xl text-white/60"> %</span></div>
        <Progress value={job.percent ?? 0} />
        <div className="flex justify-between text-xs text-white/70"><span>{job.current_step || "Working…"}</span>{job.total_scenes ? <span>Scene {job.current_scene ?? 0} of {job.total_scenes}</span> : null}</div>
      </div>
    );
  } else {
    body = <video key={job.id} src={`${url}/download`} poster={`${url}/poster`} controls controlsList="nofullscreen nodownload" playsInline preload="metadata" className="h-full w-full object-contain bg-black [&::-webkit-media-controls-fullscreen-button]:hidden" data-testid="stage-video" />;
  }
  return (
    <section ref={box} data-testid="studio" data-fullscreen={full}
      className={`group relative flex items-center justify-center bg-black overflow-hidden ${full ? "h-screen w-screen" : "h-[calc(100vh-15.5rem)] min-h-[300px] w-full"}`}>
      {body}
      {playable && (
        <div className="absolute top-3 right-3 flex gap-2 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
          <a href={`${url}/download`} aria-label="Download video" title="Download" className="rounded-md bg-black/60 p-2 text-white hover:bg-black/80"><Download className="w-4 h-4" /></a>
          <button type="button" onClick={toggle} disabled={!canFull} aria-label={full ? "Exit fullscreen" : "Fullscreen"} title={full ? "Exit fullscreen (Esc)" : "Fullscreen (F)"} data-testid="fullscreen"
            className="rounded-md bg-black/60 p-2 text-white hover:bg-black/80 disabled:opacity-40">{full ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}</button>
        </div>
      )}
    </section>
  );
}

// ── Composer: new video or change request, with attachment and voice ─────────

type Mode = "new" | "revise";

function Composer({ csrf, selected, mode, setMode, inputRef, onCreated, styles, pickedStyle, onPickStyle, onImportStyle, onUseDeckAsStyle, importBlocked }: {
  csrf: string; selected: Job | undefined; mode: Mode; setMode: (m: Mode) => void;
  inputRef: React.RefObject<HTMLTextAreaElement>; onCreated: (jobId: string) => void;
  styles: StyleList | undefined; pickedStyle: string | null; onPickStyle: (id: string) => void;
  onImportStyle: () => void; onUseDeckAsStyle: (f: File) => void; importBlocked: string | null;
}) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [sources, setSources] = useState<VideoSource[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [deck, setDeck] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const canRevise = selected?.status === "complete";
  const revising = mode === "revise" && canRevise;

  const create = useMutation({
    mutationFn: () => api<{ job_id: string }>(`${BASE}/jobs`, {
      method: "POST", csrf,
      body: { task: text.trim(), base_job_id: revising ? selected!.id : undefined, style_id: pickedStyle ?? undefined, sources: sources.map(({ name, text: t }) => ({ name, text: t })) },
    }),
    onSuccess: (r) => { setText(""); setSources([]); setError(null); setMode("new"); onCreated(r.job_id); void qc.invalidateQueries({ queryKey: ["video"] }); },
    onError: (e) => setError(e instanceof ApiError ? e.message : "The production could not be started."),
  });
  const busy = create.isPending || uploading;
  const { recording, startRecording, stopRecording } = useVoiceInput({ value: text, onChange: setText, csrf, disabled: busy, onError: setError });

  const attach = async (files: File[]) => {
    // A PowerPoint file is never source material: it is offered as a style and never reaches text extraction.
    const decks = files.filter((f) => isDeckFile(f.name));
    files = files.filter((f) => !isDeckFile(f.name));
    if (decks.length > 0) setDeck(decks[0]);
    if (files.length === 0) { if (fileRef.current) fileRef.current.value = ""; return; }
    if (sources.length + files.length > MAX_VIDEO_SOURCES) { setError(`At most ${MAX_VIDEO_SOURCES} attachments per video.`); return; }
    setUploading(true); setError(null);
    try { const got = await extractVideoSources(files, csrf); setSources((cur) => [...cur, ...got]); }
    catch (e) { setError(e instanceof ApiError ? e.message : "The attachment could not be read."); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const send = () => { if (text.trim() && !busy && csrf) create.mutate(); };

  return (
    <div className="px-6 pt-3 pb-2 space-y-2" data-testid="composer">
      <div className="flex items-center gap-1 text-xs" role="tablist" aria-label="What to do">
        <button type="button" role="tab" aria-selected={!revising} onClick={() => setMode("new")} data-testid="mode-new"
          className={`px-2.5 py-1 rounded-md ${!revising ? "bg-accent/15 font-medium text-foreground" : "text-muted-foreground hover:text-foreground"}`}>New video</button>
        <button type="button" role="tab" aria-selected={revising} disabled={!canRevise} onClick={() => { setMode("revise"); inputRef.current?.focus(); }} data-testid="mode-revise"
          title={canRevise ? undefined : "Select a produced video to revise it"}
          className={`px-2.5 py-1 rounded-md inline-flex items-center gap-1 max-w-[28rem] ${revising ? "bg-accent/15 font-medium text-foreground" : "text-muted-foreground hover:text-foreground disabled:opacity-40 disabled:hover:text-muted-foreground"}`}>
          <Pencil className="w-3 h-3 shrink-0" /><span className="truncate">Revise{canRevise ? `: ${selected!.task}` : ""}</span>
        </button>
        <span className="ml-auto" />
        <StyleChip list={styles} picked={pickedStyle} onPick={onPickStyle} onImport={onImportStyle} importDisabledReason={importBlocked}
          label={pickedStyle ? (pickedStyle === BUILTIN_STYLE_ID ? styles?.builtin.name ?? "CorvinOS" : styles?.styles.find((s) => s.id === pickedStyle)?.name ?? "CorvinOS")
            : revising ? "same as original" : (styles?.styles.find((s) => s.id === styles.default_style_id)?.name ?? styles?.builtin.name ?? "CorvinOS")} />
      </div>
      {revising && <p className="text-xs text-muted-foreground -mt-1">A new version is produced; the original stays.</p>}
      {deck && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs" data-testid="deck-offer">
          <Paperclip className="w-3 h-3" /><span className="max-w-[14rem] truncate">{deck.name}</span>
          <span className="text-muted-foreground">PowerPoint files cannot be used as source material.</span>
          <Button type="button" variant="outline" size="sm" className="h-6 px-2" disabled={!!importBlocked || !csrf} title={importBlocked ?? undefined} data-testid="use-as-style"
            onClick={() => { onUseDeckAsStyle(deck); setDeck(null); }}>Use as style</Button>
          <button type="button" aria-label={`Dismiss ${deck.name}`} onClick={() => setDeck(null)}><X className="w-3 h-3" /></button>
        </div>
      )}
      {sources.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" data-testid="sources">
          {sources.map((s, i) => (
            <li key={`${s.name}-${i}`} data-testid="source-chip" className="inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-2 py-0.5 text-xs">
              <Paperclip className="w-3 h-3" /><span className="max-w-[14rem] truncate">{s.name}</span>
              <span className="text-muted-foreground">{s.chars.toLocaleString("en-US")} chars{s.truncated ? ", cut" : ""}</span>
              <button type="button" aria-label={`Remove ${s.name}`} onClick={() => setSources((c) => c.filter((_, k) => k !== i))}><X className="w-3 h-3" /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex items-end gap-2 rounded-xl border border-border bg-background p-2 focus-within:border-accent">
        <input ref={fileRef} type="file" multiple accept={`${VIDEO_SOURCE_ACCEPT},${STYLE_DECK_ACCEPT}`} className="hidden" data-testid="attach-input" onChange={(e) => void attach(Array.from(e.target.files ?? []))} />
        <Button type="button" variant="ghost" size="sm" aria-label="Attach files" title="Attach text, Markdown or PDF as source material, or a PowerPoint deck as a style" disabled={busy || sources.length >= MAX_VIDEO_SOURCES} onClick={() => fileRef.current?.click()}>
          {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Paperclip className="w-4 h-4" />}
        </Button>
        <textarea ref={inputRef} value={text} onChange={(e) => setText(e.target.value)} rows={2} data-testid="task-input" maxLength={4000}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
          placeholder={recording ? "Listening… release Space to stop" : revising ? "Describe the change: shorter scene 2, add a diagram, other title…" : "Explain in a short video what HTTPS adds to HTTP. Length: one minute."}
          className="flex-1 resize-none bg-transparent px-1 py-1.5 text-sm text-foreground outline-none placeholder:text-muted-foreground" />
        <Button type="button" variant={recording ? "destructive" : "ghost"} size="sm" data-testid="voice-button" disabled={busy}
          aria-label={recording ? "Stop voice input" : "Start voice input"} title={recording ? "Stop (or release Space)" : "Dictate (or hold Space)"}
          onClick={() => (recording ? stopRecording() : void startRecording())}>
          {recording ? <Square className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
        </Button>
        <Button type="button" variant="accent" size="sm" data-testid="start-production" aria-label={revising ? "Produce revision" : "Start production"} disabled={!text.trim() || busy || !csrf} onClick={send}>
          {create.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
        </Button>
      </div>
      <div className="flex justify-between gap-4 text-xs">
        <span className="text-muted-foreground">Hold <kbd className="rounded bg-muted px-1 font-mono">Space</kbd> to dictate · <kbd className="rounded bg-muted px-1 font-mono">Enter</kbd> to send · <kbd className="rounded bg-muted px-1 font-mono">F</kbd> for fullscreen</span>
        {error && <span className="text-destructive" role="alert" data-testid="composer-error">{error}</span>}
      </div>
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
  const initialTab = params.get("tab");
  const [tab, setTab] = useState<"quality" | "learning">(initialTab === "learning" ? "learning" : "quality");
  const [detailsOpen, setDetailsOpen] = useState(initialTab === "quality" || initialTab === "learning");
  const [mode, setMode] = useState<Mode>("new");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [stylesOpen, setStylesOpen] = useState(false);
  const [pickedStyle, setPickedStyle] = useState<string | null>(null);
  const [deckFile, setDeckFile] = useState<File | null>(null);
  const deckPicker = useRef<HTMLInputElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  const overview = useQuery({ queryKey: ["video", "overview"], queryFn: ({ signal }) => api<Overview>(`${BASE}/overview`, { signal }), retry: false, refetchInterval: 15_000 });
  const jobs = useQuery({
    queryKey: ["video", "jobs"], queryFn: ({ signal }) => api<{ jobs: Job[]; total: number }>(`${BASE}/jobs?limit=50`, { signal }), retry: false,
    refetchInterval: (q) => (q.state.data?.jobs.some((j) => ACTIVE.includes(j.status)) ? 1500 : 10_000),
  });
  useEffect(() => { if (!selected && jobs.data?.jobs.length) setSelected(jobs.data.jobs[0].id); }, [jobs.data, selected]);
  useEffect(() => {
    const p = new URLSearchParams(); if (selected) p.set("job", selected); if (detailsOpen) p.set("tab", tab);
    navigate({ search: `?${p.toString()}` }, { replace: true });
  }, [selected, tab, detailsOpen, navigate]);

  const job = useQuery({ queryKey: ["video", "job", selected], queryFn: ({ signal }) => api<Job>(`${BASE}/jobs/${selected}`, { signal }), enabled: !!selected, retry: false,
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 1000 : false) });
  const quality = useQuery({ queryKey: ["video", "quality", selected, job.data?.status], queryFn: ({ signal }) => api<Quality>(`${BASE}/jobs/${selected}/quality-metrics`, { signal }), enabled: !!selected && job.data?.status === "complete", retry: false });
  const styles = useQuery({ queryKey: ["video", "styles"], queryFn: ({ signal }) => api<StyleList>(`${BASE}/styles`, { signal }), retry: false });
  // A deleted style can no longer be the session choice.
  useEffect(() => {
    if (pickedStyle && pickedStyle !== BUILTIN_STYLE_ID && styles.data && !styles.data.styles.some((s) => s.id === pickedStyle)) setPickedStyle(null);
  }, [styles.data, pickedStyle]);
  const styleFull = styles.data ? styles.data.styles.length >= styles.data.limits.max_styles : false;
  const importBlocked = styles.isError ? "Custom styles are not available on this installation."
    : styleFull ? `You have reached the limit of ${styles.data!.limits.max_styles} styles. Delete one to import another.` : null;
  const settings = useQuery({ queryKey: ["video", "settings"], queryFn: ({ signal }) => api<{ output_folder: string; tts_engine: string; tts_engines: string[]; max_duration_minutes: number; openai_configured: boolean; web_slides_available: boolean }>(`${BASE}/settings`, { signal }), retry: false });
  const [form, setForm] = useState<{ tts_engine: string; max_duration_minutes: number } | null>(null);
  useEffect(() => { if (settings.data && !form) setForm({ tts_engine: settings.data.tts_engine, max_duration_minutes: settings.data.max_duration_minutes }); }, [settings.data, form]);
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
  const byId = new Map(list.map((it) => [it.id, it]));
  const j = job.data;

  return (
    <div className="-mx-6 -my-8 h-[calc(100vh-3.5rem)] overflow-y-auto" data-testid="video-producer">
      <div className="flex items-center gap-3 px-6 h-12 border-b border-border">
        <Clapperboard className="w-5 h-5 shrink-0" />
        <h1 className="text-base font-semibold shrink-0">Video Producer</h1>
        {j && <><span className="text-muted-foreground truncate min-w-0 flex-1 text-sm" title={j.task}>{j.task}</span><StatusPill status={j.status} /></>}
        <Button variant="ghost" size="sm" className="ml-auto shrink-0" aria-label="Refresh" onClick={() => void qc.invalidateQueries({ queryKey: ["video"] })}><RefreshCw className="w-4 h-4" /></Button>
      </div>

      <VideoStage job={j} loading={!!selected && job.isLoading} />

      <Composer csrf={csrf} selected={j} mode={mode} setMode={setMode} inputRef={inputRef}
        onCreated={(id) => { setSelected(id); }}
        styles={styles.data} pickedStyle={pickedStyle} onPickStyle={setPickedStyle} importBlocked={importBlocked}
        onImportStyle={() => deckPicker.current?.click()} onUseDeckAsStyle={setDeckFile} />
      <input ref={deckPicker} type="file" accept={STYLE_DECK_ACCEPT} className="hidden" data-testid="deck-input" aria-label="PowerPoint deck to import as a style"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) setDeckFile(f); e.target.value = ""; }} />
      {deckFile && (
        <StyleImportDialog file={deckFile} csrf={csrf} onClose={() => setDeckFile(null)}
          onSaved={(style) => {
            // Put the saved style in the cache first: the cleanup effect above would otherwise
            // drop the new choice while the refetched list does not contain it yet.
            qc.setQueryData<StyleList>(["video", "styles"], (old) => (old ? { ...old, styles: [...old.styles.filter((x) => x.id !== style.id), style] } : old));
            setDeckFile(null); setPickedStyle(style.id); setStylesOpen(true);
            void qc.invalidateQueries({ queryKey: ["video", "styles"] });
          }} />
      )}

      <div className="px-6 pb-8 space-y-6">
        <section>
          <div className="flex items-baseline justify-between mb-2">
            <h2 className="text-sm font-semibold flex items-center gap-2"><Film className="w-4 h-4" /> Produced videos</h2>
            <span className="text-xs text-muted-foreground">{jobs.isLoading ? "loading…" : `${list.length} of ${jobs.data?.total ?? list.length}`}</span>
          </div>
          {list.length === 0 && !jobs.isLoading ? <p className="text-sm text-muted-foreground">No videos yet. Describe one above.</p> : (
            <ul className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3" data-testid="library">
              {list.map((it) => {
                const base = it.revision_of ? byId.get(it.revision_of) : undefined;
                return (
                  <li key={it.id} className="relative">
                    <button type="button" onClick={() => setSelected(it.id)} data-testid={`job-${it.id}`} aria-current={selected === it.id}
                      className={`w-full text-left rounded-lg border p-2 transition ${selected === it.id ? "border-accent bg-accent/10" : "border-border hover:bg-muted/40"}`}>
                      <div className="aspect-video rounded-md overflow-hidden border border-border bg-muted/40">
                        {it.status === "complete" ? <img src={`/v1/console${BASE}/videos/${it.id}/poster`} alt="" className="w-full h-full object-cover" loading="lazy" onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} /> : null}
                      </div>
                      <div className="text-sm font-medium truncate mt-2">{it.task}</div>
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        <StatusPill status={it.status} />
                        {it.revision_of && <Badge variant="outline" title={base ? `Revision of: ${base.task}` : `Revision of ${it.revision_of}`} data-testid={`revision-${it.id}`}>revision</Badge>}
                        <span className="text-xs text-muted-foreground">{fmtWhen(it.created_at)}</span>
                      </div>
                      {ACTIVE.includes(it.status) && <div className="mt-1"><Progress value={it.percent ?? 0} /></div>}
                    </button>
                    {it.status === "complete" && (
                      <Button variant="ghost" size="sm" className="absolute top-3 right-3 h-7 px-2 bg-black/60 text-white hover:bg-black/80" aria-label={`Revise ${it.task}`} data-testid={`revise-${it.id}`}
                        onClick={() => { setSelected(it.id); setMode("revise"); inputRef.current?.focus(); }}><Pencil className="w-3 h-3" /> Revise</Button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="overview-tiles">
          {[
            ["Videos produced", ov ? String(ov.videos) : "—", ov ? `${ov.jobs_total} jobs in total` : ""],
            ["Total runtime", ov ? fmtDur(ov.runtime_s) : "—", ov ? fmtBytes(ov.size_bytes) : ""],
            ["Checks passed", ov && ov.mean_score_share !== null ? pct(ov.mean_score_share) : "—", ov ? (ov.measured_videos ? `mean over ${ov.measured_videos} measured video${ov.measured_videos === 1 ? "" : "s"}` : "nothing measured yet") : ""],
            ["Last activity", ov?.last_activity ? fmtWhen(ov.last_activity) : "—", ov?.plugin_source ? "plugin loaded" : ""],
          ].map(([label, value, sub]) => (
            <Card key={label}><CardContent className="pt-4 pb-3">
              <div className="text-xl font-bold tabular-nums">{value}</div>
              <div className="text-xs text-muted-foreground">{label}{sub ? ` · ${sub}` : ""}</div>
            </CardContent></Card>
          ))}
        </div>

        <Card>
          <button type="button" onClick={() => setDetailsOpen((o) => !o)} aria-expanded={detailsOpen} data-testid="details-toggle" className="w-full text-left px-6 py-3 flex items-center gap-2 text-sm font-semibold">
            {detailsOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />} Quality and learning
            <span className="text-xs font-normal text-muted-foreground">{MARKER_VIDEO}</span>
          </button>
          {detailsOpen && (
            <CardContent>
              {!j ? <p className="text-sm text-muted-foreground">Select a video.</p> : (
                <>
                  <div className="flex gap-1 mb-3" role="tablist">
                    {(["quality", "learning"] as const).map((t) => (
                      <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} data-testid={`tab-${t}`}
                        className={`px-3 py-1.5 rounded-md text-sm capitalize ${tab === t ? "bg-accent/15 text-foreground font-medium" : "text-muted-foreground hover:text-foreground"}`}>{t}</button>
                    ))}
                  </div>
                  {tab === "quality" && (j.status !== "complete" ? <p className="text-sm text-muted-foreground" data-testid="quality-pending">Quality is measured once the video is produced.</p>
                    : quality.isLoading ? <Loader2 className="w-6 h-6 animate-spin" /> : quality.isError ? <p className="text-sm text-destructive">The quality measurement failed.</p> : quality.data ? <QualityTab q={quality.data} /> : null)}
                  {tab === "learning" && <LearningTab job={j} scenes={quality.data?.scenes ?? []} />}
                </>
              )}
            </CardContent>
          )}
        </Card>

      <Card>
        <button type="button" onClick={() => setStylesOpen((o) => !o)} aria-expanded={stylesOpen} data-testid="styles-toggle" className="w-full text-left px-6 py-3 flex items-center gap-2 text-sm font-semibold">
          {stylesOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />} Styles
          <span className="text-xs font-normal text-muted-foreground">{styles.data ? `${styles.data.styles.length} of ${styles.data.limits.max_styles} saved` : ""}</span>
        </button>
        {stylesOpen && (
          <CardContent>
            <StylesSection list={styles.data} loading={styles.isLoading} unavailable={styles.isError} csrf={csrf} importDisabledReason={importBlocked}
              onImport={() => deckPicker.current?.click()} onChanged={() => void qc.invalidateQueries({ queryKey: ["video", "styles"] })} />
          </CardContent>
        )}
      </Card>

      <Card>
        <button type="button" onClick={() => setSettingsOpen((o) => !o)} className="w-full text-left px-6 py-3 flex items-center gap-2 text-sm font-semibold">
          {settingsOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />} Settings
          <span className="text-xs font-normal text-muted-foreground">{settings.data ? `${settings.data.tts_engine} · ${settings.data.output_folder}` : ""}</span>
        </button>
        {settingsOpen && form && (
          <CardContent className="grid gap-3 md:grid-cols-3">
            <label className="text-sm"><span className="text-xs text-muted-foreground">Output folder (this tenant's video directory)</span><Input value={settings.data?.output_folder ?? ""} readOnly disabled /></label>
            <label className="text-sm"><span className="text-xs text-muted-foreground">Text-to-speech</span>
              <select value={form.tts_engine} onChange={(e) => setForm({ ...form, tts_engine: e.target.value })} className="w-full mt-1 px-3 py-2 rounded-md border border-border bg-background text-sm">
                {(settings.data?.tts_engines ?? [form.tts_engine]).map((t) => <option key={t} value={t}>{TTS_LABELS[t] ?? t}</option>)}
              </select>
              {form.tts_engine !== "gtts" && settings.data && !settings.data.openai_configured ? (
                <span className="block text-xs mt-1 text-amber-600" data-testid="tts-key-warning">
                  {form.tts_engine === "openai" ? "No OpenAI key in the console's environment: jobs are refused until one is set or another engine is chosen." : "No OpenAI key in the console's environment: narration starts with edge-tts."}
                </span>
              ) : null}</label>
            <label className="text-sm"><span className="text-xs text-muted-foreground">Max duration (minutes, 1–60)</span><Input type="number" min={1} max={60} value={form.max_duration_minutes} onChange={(e) => setForm({ ...form, max_duration_minutes: Math.min(60, Math.max(1, parseInt(e.target.value) || 1)) })} /></label>
            {settings.data && !settings.data.web_slides_available ? (
              <p className="md:col-span-3 text-xs text-amber-600" data-testid="web-slides-note">Animated web slides need Playwright in the console environment; until it is installed every slide is the classic still.</p>
            ) : null}
            <div className="md:col-span-3 flex items-center gap-3"><Button variant="outline" size="sm" disabled={save.isPending || !csrf} onClick={() => save.mutate(form)}>{save.isSuccess ? "Saved" : "Save settings"}</Button>{save.isError ? <span className="text-xs text-destructive">Settings were not saved.</span> : null}</div>
          </CardContent>
        )}
      </Card>
      </div>
    </div>
  );
}

export default VideoProducerPage;
