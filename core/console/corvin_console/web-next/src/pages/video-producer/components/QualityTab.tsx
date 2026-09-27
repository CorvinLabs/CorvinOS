/**
 * Quality Tab — ffprobe measurements, checklist, scene timing
 */

import React, { useState } from "react";
import { ChevronDown, ChevronRight, AlertCircle, CheckCircle2 } from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

interface Check {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail" | "skip";
  detail: string;
}

interface SceneRow {
  index: number;
  id: string;
  kind?: string | null;
  planned_s: number | null;
  actual_s: number | null;
  drift_pct: number | null;
  rendered: boolean;
  size_bytes: number | null;
  has_slide: boolean;
  has_voice: boolean;
  voice_s: number | null;
  narration_words: number | null;
}

export interface Quality {
  job_id: string;
  status: string;
  measured_at: string;
  ffprobe_available: boolean;
  source?: { video?: string | null } | null;
  container?: {
    format: string | null;
    duration_s: number | null;
    size_bytes: number;
    bitrate_kbps: number;
  } | null;
  video?: {
    codec: string | null;
    width: number | null;
    height: number | null;
    fps: number | null;
    pixel_format: string | null;
    bitrate_kbps: number | null;
  } | null;
  audio?: {
    codec: string | null;
    sample_rate_hz: number | null;
    channels: number | null;
    bitrate_kbps: number | null;
  } | null;
  captions?: {
    file: string;
    cues: number;
    covered_s: number;
    words: number;
    coverage: number | null;
    duplicate_consecutive: number;
  } | null;
  scenes: SceneRow[];
  summary: {
    scenes_planned: number;
    scenes_rendered: number;
    planned_s: number;
    rendered_s: number | null;
    size_bytes: number | null;
  };
  production: {
    started_at: string | null;
    completed_at: string | null;
    seconds: number | null;
  };
  checks: Check[];
  score: {
    passed: number;
    warned: number;
    failed: number;
    total: number;
    skipped: number;
    share: number | null;
  };
}

interface Job {
  id: string;
  status: string;
}

function fmtDur(s?: number | null) {
  if (s === null || s === undefined) return "—";
  return s >= 60 ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")} min` : `${s.toFixed(1)} s`;
}

function fmtBytes(n?: number | null) {
  if (n === null || n === undefined) return "—";
  return n < 1048576 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1048576).toFixed(2)} MB`;
}

function pct(v?: number | null) {
  if (v === null || v === undefined) return "—";
  return `${Math.round(v * 100)} %`;
}

function CheckIcon({ s }: { s: Check["status"] }) {
  if (s === "pass") return <CheckCircle2 className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />;
  if (s === "warn") return <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />;
  if (s === "fail") return <AlertCircle className="w-4 h-4 text-destructive shrink-0" />;
  return <span className="w-4 h-4 rounded-full border border-border shrink-0" />;
}

function ScoreRing({ score }: { score: Quality["score"] }) {
  const share = score.share ?? 0;
  const r = 34,
    c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-4">
      <svg width="88" height="88" viewBox="0 0 88 88" role="img">
        <circle cx="44" cy="44" r={r} fill="none" stroke="var(--viz-grid)" strokeWidth="8" />
        <circle
          cx="44"
          cy="44"
          r={r}
          fill="none"
          stroke="var(--viz-role-os)"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={`${c * share} ${c}`}
          transform="rotate(-90 44 44)"
        />
        <text x="44" y="49" textAnchor="middle" fontSize="18" fontWeight="700" fill="currentColor">
          {score.share === null ? "—" : `${Math.round(share * 100)}`}
        </text>
      </svg>
      <div>
        <div className="text-sm font-semibold">{score.share === null ? "Not measurable" : `${score.passed} of ${score.total} checks passed`}</div>
        <div className="text-xs text-muted-foreground">
          {score.warned} warning{score.warned === 1 ? "" : "s"} · {score.failed} failed
          {score.skipped ? ` · ${score.skipped} skipped` : ""}
        </div>
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

interface QualityTabProps {
  quality?: Quality;
  job: Job;
}

const QualityTab: React.FC<QualityTabProps> = ({ quality, job }) => {
  const [open, setOpen] = useState(false);

  if (!quality) {
    return <div className="text-muted-foreground py-12">Quality data not available. Video must be complete.</div>;
  }

  const q = quality;
  const chart = q.scenes.map((s) => ({
    name: s.id,
    planned: s.planned_s ?? 0,
    rendered: s.actual_s ?? 0,
  }));

  const groups: Array<[string, Check["status"][]]> = [
    ["Needs attention", ["fail", "warn"]],
    ["Passed", ["pass"]],
    ["Not measurable", ["skip"]],
  ];

  return (
    <div className="space-y-5">
      {/* Score Ring + Facts */}
      <div className="grid gap-4 lg:grid-cols-[auto_1fr] items-start">
        <ScoreRing score={q.score} />
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-2">
          <Fact k="Runtime" v={fmtDur(q.container?.duration_s)} />
          <Fact
            k="Picture"
            v={
              q.video ? (
                <>
                  <span className="whitespace-nowrap">
                    {q.video.width}×{q.video.height}
                  </span>{" "}
                  · {q.video.fps} fps · {q.video.codec}
                </>
              ) : (
                "—"
              )
            }
          />
          <Fact
            k="Sound"
            v={
              q.audio
                ? `${q.audio.codec} · ${q.audio.sample_rate_hz ? `${(q.audio.sample_rate_hz / 1000).toFixed(1)} kHz` : "—"} · ${
                    q.audio.channels === 1 ? "mono" : q.audio.channels === 2 ? "stereo" : `${q.audio.channels} ch`
                  }`
                : "none"
            }
          />
          <Fact k="File" v={q.container ? `${fmtBytes(q.container.size_bytes)} · ${q.container.bitrate_kbps} kbps` : "—"} />
          <Fact k="Captions" v={q.captions ? `${q.captions.cues} cues · ${pct(q.captions.coverage)} covered` : "none"} />
          <Fact k="Scenes" v={`${q.summary.scenes_rendered} of ${q.summary.scenes_planned} rendered`} />
          <Fact k="Planned vs rendered" v={`${fmtDur(q.summary.planned_s)} → ${fmtDur(q.summary.rendered_s)}`} />
          <Fact k="Production time" v={q.production.seconds !== null ? fmtDur(q.production.seconds) : "—"} />
        </div>
      </div>

      {!q.ffprobe_available && (
        <div className="text-sm rounded-md border border-amber-500/30 bg-amber-500/10 p-3">
          ffprobe is not installed on this host, so the stream facts could not be measured.
        </div>
      )}

      {/* Checklist + Scene Timing */}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="rounded-lg border border-border">
          <div className="px-6 py-4 border-b border-border">
            <h3 className="text-base font-semibold">Checklist</h3>
            <p className="text-sm text-muted-foreground">Each check is a measurement on the produced file, not a rating.</p>
          </div>
          <div className="px-6 py-4 space-y-3">
            {groups.map(([title, statuses]) => {
              const items = q.checks.filter((c) => statuses.includes(c.status));
              if (items.length === 0) return null;
              return (
                <div key={title}>
                  <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">{title}</div>
                  <ul className="space-y-1">
                    {items.map((c) => (
                      <li key={c.id} className="flex items-start gap-2 text-sm">
                        <CheckIcon s={c.status} />
                        <span className="flex-1">
                          {c.label}
                          <span className="text-muted-foreground"> · {c.detail}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        </div>

        <div className="rounded-lg border border-border">
          <div className="px-6 py-4 border-b border-border">
            <h3 className="text-base font-semibold">Scene timing</h3>
            <p className="text-sm text-muted-foreground">
              {chart.length === 0
                ? "No scenes on this job."
                : `Seconds per scene — the storyboard's plan next to what was rendered (${chart.length} scenes, one shared scale).`}
            </p>
          </div>
          {chart.length > 0 && (
            <div className="px-6 py-4">
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
            </div>
          )}
        </div>
      </div>

      {/* Scene Filmstrip */}
      {q.scenes.length > 0 && (
        <div className="rounded-lg border border-border">
          <div className="px-6 py-4 border-b border-border">
            <h3 className="text-base font-semibold">Scenes</h3>
          </div>
          <div className="px-6 py-4">
            <div className="flex gap-2 overflow-x-auto pb-2">
              {q.scenes.map((s) => (
                <figure key={s.index} className="w-36 shrink-0">
                  <div className="aspect-video rounded-md overflow-hidden border border-border bg-muted/40">
                    {s.has_slide ? (
                      <img
                        src={`/v1/console/video/videos/${q.job_id}/scenes/${s.index}/slide`}
                        alt={`Scene ${s.index}`}
                        className="w-full h-full object-cover"
                        loading="lazy"
                      />
                    ) : (
                      <div className="w-full h-full flex items-center justify-center text-xs text-muted-foreground">no slide</div>
                    )}
                  </div>
                  <figcaption className="text-xs mt-1 truncate">
                    <span className="font-mono">{s.id}</span> · {fmtDur(s.actual_s)}
                    {s.drift_pct !== null ? (
                      <span className={Math.abs(s.drift_pct) > 25 ? " text-destructive" : " text-muted-foreground"}>
                        {" "}· {s.drift_pct > 0 ? "+" : ""}
                        {s.drift_pct}%
                      </span>
                    ) : null}
                  </figcaption>
                </figure>
              ))}
            </div>

            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="mt-2 flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              {open ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />} {open ? "Hide" : "Show"} scene table
            </button>

            {open && (
              <div className="mt-2 rounded-lg border border-border overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="bg-muted/40 border-b border-border">
                    <tr>
                      <th className="text-left px-3 py-2">Scene</th>
                      <th className="text-left px-3 py-2">Kind</th>
                      <th className="text-right px-3 py-2">Planned</th>
                      <th className="text-right px-3 py-2">Rendered</th>
                      <th className="text-right px-3 py-2">Drift</th>
                      <th className="text-right px-3 py-2">Voice</th>
                      <th className="text-right px-3 py-2">Words</th>
                      <th className="text-right px-3 py-2">Size</th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.scenes.map((s) => (
                      <tr key={s.index} className="border-b border-border last:border-b-0">
                        <td className="px-3 py-1.5 font-mono">{s.id}</td>
                        <td className="px-3 py-1.5">{s.kind ?? "—"}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmtDur(s.planned_s)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmtDur(s.actual_s)}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{s.drift_pct === null ? "—" : `${s.drift_pct > 0 ? "+" : ""}${s.drift_pct} %`}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{s.has_voice ? fmtDur(s.voice_s) : "none"}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{s.narration_words ?? "—"}</td>
                        <td className="px-3 py-1.5 text-right tabular-nums">{fmtBytes(s.size_bytes)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="text-xs text-muted-foreground">
        Measured {new Date(q.measured_at).toLocaleString("en-US")}
        {q.source?.video ? (
          <>
            {" "}
            from <span className="font-mono">{q.source.video}</span>
          </>
        ) : (
          " — no output file"
        )}
        .
      </div>
    </div>
  );
};

export default QualityTab;
