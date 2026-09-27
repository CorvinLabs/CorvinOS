/**
 * Video Producer Studio Panel — 3 Tabs (Playback | Quality | Learning)
 *
 * Migrated from Console native settings section (ADR-0892: One-Marketplace).
 * Single panel with integrated tabs for playback, quality metrics, and learning feedback.
 *
 * Backend: the console's own `/v1/console/video/*` routes (video production, ffprobe,
 * learning metrics). Every call goes through `api()` (session cookie, CSRF on writes).
 */

import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Loader2, RefreshCw } from "lucide-react";
import PlaybackTab from "./components/PlaybackTab";
import QualityTab, { type Quality } from "./components/QualityTab";
import LearningTab from "./components/LearningTab";

interface Job {
  id: string;
  task: string;
  status: string;
  created_at: string;
  percent: number;
  current_step?: string | null;
  current_scene?: number | null;
  total_scenes?: number | null;
  started_at?: string | null;
  completed_at?: string | null;
  error_message?: string | null;
  video_output_path?: string | null;
}

interface Overview {
  jobs_total: number;
  by_status: Record<string, number>;
  videos: number;
  runtime_s: number;
  size_bytes: number;
  measured_videos: number;
  mean_score_share: number | null;
  last_activity: string | null;
  ffprobe_available: boolean;
  plugin_source: string | null;
}

interface Learning {
  job_id: string;
  total_feedback_events: number;
  approved: number;
  rejected: number;
  average_confidence: number | null;
  events: Array<{ timestamp: string; outcome: string | null }>;
  source: string;
}

const BASE = "/v1/console/video";
const ACTIVE = ["pending", "storyboard_generating", "skills_running"];

export const VideoProducerPage: React.FC = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const qc = useQueryClient();

  const [selected, setSelected] = useState<string | null>(searchParams.get("job"));
  const [tab, setTab] = useState<"playback" | "quality" | "learning">(
    (searchParams.get("tab") as "playback" | "quality" | "learning") || "playback"
  );

  useEffect(() => {
    const p = new URLSearchParams();
    if (selected) p.set("job", selected);
    p.set("tab", tab);
    setSearchParams(p, { replace: true });
  }, [selected, tab, setSearchParams]);

  const overview = useQuery({
    queryKey: ["video", "overview"],
    queryFn: ({ signal }) => fetch(`${BASE}/overview`, { signal }).then((r) => r.json() as Promise<Overview>),
    retry: false,
    refetchInterval: 15_000,
  });

  const jobs = useQuery({
    queryKey: ["video", "jobs"],
    queryFn: ({ signal }) =>
      fetch(`${BASE}/jobs?limit=50`, { signal }).then((r) => r.json() as Promise<{ jobs: Job[]; total: number }>),
    retry: false,
    refetchInterval: (q) => (q.state.data?.jobs.some((j) => ACTIVE.includes(j.status)) ? 1500 : 10_000),
  });

  useEffect(() => {
    if (!selected && jobs.data?.jobs.length) {
      setSelected(jobs.data.jobs[0].id);
    }
  }, [jobs.data, selected]);

  const job = useQuery({
    queryKey: ["video", "job", selected],
    queryFn: ({ signal }) =>
      fetch(`${BASE}/jobs/${selected}`, { signal }).then((r) => r.json() as Promise<Job>),
    enabled: !!selected,
    retry: false,
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 1000 : false),
  });

  const quality = useQuery({
    queryKey: ["video", "quality", selected, job.data?.status],
    queryFn: ({ signal }) =>
      fetch(`${BASE}/jobs/${selected}/quality-metrics`, { signal }).then((r) => r.json() as Promise<Quality>),
    enabled: !!selected && job.data?.status === "complete",
    retry: false,
  });

  if (jobs.isError) {
    return (
      <div className="max-w-7xl mx-auto p-6">
        <div className="rounded-lg border border-destructive/30 bg-destructive/10 p-4 text-destructive text-sm">
          The Video Producer plugin is not available on this build.
        </div>
      </div>
    );
  }

  const j = job.data;
  const list = jobs.data?.jobs ?? [];

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6" data-testid="video-producer-panel">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-2">
            <Clapperboard className="w-7 h-7" /> Video Producer
          </h1>
          <p className="text-muted-foreground max-w-3xl">
            Produce a video from a task, watch it, see what ffprobe measured on the real artifacts, and teach the producer scene by scene.
          </p>
        </div>
        <button
          onClick={() => void qc.invalidateQueries({ queryKey: ["video"] })}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-border hover:bg-accent"
        >
          <RefreshCw className="w-4 h-4" /> Refresh
        </button>
      </div>

      <div className="border-b border-border flex gap-8">
        {(["playback", "quality", "learning"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 capitalize font-medium border-b-2 transition ${
              tab === t ? "border-accent text-foreground" : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t}
          </button>
        ))}
      </div>

      <div>
        {!j ? (
          <div className="text-center text-muted-foreground py-12">
            {job.isLoading ? <Loader2 className="w-6 h-6 animate-spin mx-auto mb-2" /> : "No job selected"}
          </div>
        ) : tab === "playback" ? (
          <PlaybackTab job={j} />
        ) : tab === "quality" ? (
          <QualityTab quality={quality.data} job={j} />
        ) : (
          <LearningTab job={j} />
        )}
      </div>
    </div>
  );
};

export default VideoProducerPage;
