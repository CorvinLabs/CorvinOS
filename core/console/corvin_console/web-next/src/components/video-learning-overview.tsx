/**
 * Video Producer — what the recorded ratings say across ALL of this workspace's videos.
 *
 * Reads the four learning endpoints that answered 501 until 2026-10-26 (models, confidence, select-model,
 * report-quality). Everything shown is counted from recorded feedback; nothing here chooses a variant for
 * a job. A mean appears only with enough ratings behind it, and the advice says so when it has none.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api/client";

const BASE = "/video";

export interface VariantRow {
  variant: string; jobs: number; rated_scenes: number; positive: number; negative: number;
  enough_samples: boolean; mean_rating: number | null;
}
export interface VariantStats { dimension: string; min_samples: number; variants: VariantRow[]; note?: string }
export interface ConfidenceStats { samples: number; feedback_events: number; average: number | null; min: number | null; max: number | null }
export interface Advice {
  advice: { variant: string; mean_rating: number; over: string; over_mean_rating: number } | null;
  reason: "ok" | "insufficient_data" | "no_clear_difference"; applied: boolean; min_samples: number; margin: number;
}

/** The sentence the advice endpoint's answer becomes. Pure, so it is tested on its own. */
export function adviceText(a: Advice): string {
  if (a.reason === "ok" && a.advice) {
    return `${a.advice.variant} rated best (mean ${a.advice.mean_rating.toFixed(1)} of 5) against ${a.advice.over} (${a.advice.over_mean_rating.toFixed(1)}). Advice only — nothing uses it to choose a variant for a video.`;
  }
  if (a.reason === "no_clear_difference") {
    return `The best-rated variants are within ${a.margin} points of each other, so there is no advice.`;
  }
  return `Not enough ratings yet: a variant needs at least ${a.min_samples} rated scenes, and two variants have to be compared.`;
}

export function LearningOverview({ jobId, csrf }: { jobId: string; csrf: string }) {
  const qc = useQueryClient();
  const models = useQuery({ queryKey: ["video", "learning", "variants"], queryFn: ({ signal }) => api<VariantStats>(`${BASE}/learning/models`, { signal }), retry: false });
  const confidence = useQuery({ queryKey: ["video", "learning", "confidence"], queryFn: ({ signal }) => api<ConfidenceStats>(`${BASE}/learning/confidence`, { signal }), retry: false });
  const [advice, setAdvice] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const ask = useMutation({
    mutationFn: () => api<Advice>(`${BASE}/learning/select-model`, { method: "POST", csrf, body: {} }),
    onSuccess: (a) => setAdvice(adviceText(a)),
    onError: () => setAdvice("The suggestion could not be loaded."),
  });
  const rate = useMutation({
    mutationFn: (rating: number) => api(`${BASE}/learning/report-quality`, { method: "POST", csrf, body: { job_id: jobId, rating } }),
    onSuccess: (_r, rating) => {
      setMsg(`Recorded: overall rating ${rating} of 5.`);
      void qc.invalidateQueries({ queryKey: ["video", "learning"] });
    },
    onError: (e) => setMsg(e instanceof ApiError && e.message ? e.message : "The rating could not be recorded."),
  });

  const m = models.data;
  const c = confidence.data;
  const rated = m ? m.variants.reduce((n, v) => n + v.rated_scenes, 0) : null;
  return (
    <div className="space-y-3 border-t border-border pt-3" data-testid="learning-overview">
      <div className="text-sm font-semibold">Across all videos</div>
      <div className="grid grid-cols-3 gap-2 text-xs">
        <div><div className="text-muted-foreground">Rated scenes</div><div data-testid="overview-rated">{rated ?? "—"}</div></div>
        <div><div className="text-muted-foreground">Mean confidence stated</div><div data-testid="overview-confidence">{c && c.average !== null ? c.average.toFixed(2) : "—"}</div></div>
        <div><div className="text-muted-foreground">Confidence samples</div><div>{c ? c.samples : "—"}</div></div>
      </div>
      {models.isError ? (
        <p className="text-xs text-muted-foreground">Ratings per variant could not be loaded.</p>
      ) : m && m.variants.length === 0 ? (
        <p className="text-xs text-muted-foreground" data-testid="overview-empty">No rated videos yet.</p>
      ) : m ? (
        <ul className="text-xs space-y-1" data-testid="overview-variants">
          {m.variants.map((v) => (
            <li key={v.variant} className="flex gap-2">
              <span className="font-mono">{v.variant}</span>
              <span className="text-muted-foreground">{v.jobs} {v.jobs === 1 ? "video" : "videos"} · {v.rated_scenes} rated scenes</span>
              <span>{v.mean_rating !== null ? `mean ${v.mean_rating.toFixed(1)} of 5` : `needs ${m.min_samples} ratings for a mean`}</span>
            </li>
          ))}
        </ul>
      ) : null}
      <p className="text-xs text-muted-foreground">Variants are the narration tier each video's own report names. This is a statistic: no video is produced differently because of it.</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={!csrf || ask.isPending} onClick={() => ask.mutate()} data-testid="ask-advice">Which variant rated best?</Button>
        {advice && <span className="text-xs" data-testid="advice-text">{advice}</span>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5" data-testid="overall-rating">
        <span className="text-xs text-muted-foreground">Rate this video overall</span>
        {[1, 2, 3, 4, 5].map((n) => (
          <Button key={n} variant="ghost" size="sm" className="h-6 w-6 px-0 text-xs" aria-label={`rate video ${n}`} disabled={!csrf || rate.isPending} onClick={() => rate.mutate(n)}>{n}</Button>
        ))}
      </div>
      {msg && <p className="text-xs text-muted-foreground" data-testid="overall-msg">{msg}</p>}
    </div>
  );
}
