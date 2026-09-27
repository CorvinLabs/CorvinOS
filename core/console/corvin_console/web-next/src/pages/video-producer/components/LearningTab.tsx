/**
 * Learning Tab — ADR-0314 feedback events per scene
 */

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ThumbsUp, ThumbsDown } from "lucide-react";

interface Job {
  id: string;
}

interface Learning {
  job_id: string;
  total_feedback_events: number;
  approved: number;
  rejected: number;
  average_confidence: number | null;
  events: Array<{
    timestamp: string;
    outcome: string | null;
    quality_rating: number | null;
    confidence: number | null;
    source: string | null;
  }>;
  source: string;
}

function pct(v?: number | null) {
  if (v === null || v === undefined) return "—";
  return `${Math.round(v * 100)} %`;
}

function fmtWhen(iso?: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-US");
}

function Fact({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="text-xs text-muted-foreground">{k}</div>
      <div className="text-sm font-medium">{v}</div>
    </div>
  );
}

interface LearningTabProps {
  job: Job;
}

const LearningTab: React.FC<LearningTabProps> = ({ job }) => {
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);

  const learning = useQuery({
    queryKey: ["video", "learning", job.id],
    queryFn: ({ signal }) =>
      fetch(`/v1/console/video/jobs/${job.id}/learning-metrics`, { signal }).then((r) =>
        r.json() as Promise<Learning>
      ),
    retry: false,
  });

  const give = useMutation({
    mutationFn: ({ type }: { type: "approve" | "reject" }) =>
      fetch(`/v1/console/video/jobs/${job.id}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ feedback_type: type, confidence: 0.8 }),
      }).then((r) => r.json()),
    onSuccess: (_, v) => {
      setMsg(`Recorded: ${v.type} for job.`);
      void qc.invalidateQueries({ queryKey: ["video", "learning", job.id] });
    },
    onError: () => setMsg("Feedback could not be recorded."),
  });

  const l = learning.data;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-2">
        <Fact k="Feedback events" v={l ? l.total_feedback_events : "—"} />
        <Fact k="Approved · rejected" v={l ? `${l.approved} · ${l.rejected}` : "—"} />
        <Fact k="Mean confidence" v={l && l.average_confidence !== null ? pct(l.average_confidence) : "—"} />
      </div>

      <p className="text-xs text-muted-foreground">
        {learning.isError
          ? "Learning events could not be loaded."
          : l && l.total_feedback_events === 0
            ? "No feedback on this job yet. Every approval or rejection becomes an ADR-0314 feedback event the optimizer reads."
            : l
              ? `Read from ${l.source}.`
              : "loading…"}
      </p>

      {msg && <p className="text-xs text-muted-foreground bg-accent/10 p-2 rounded">{msg}</p>}

      {l && l.events.length > 0 && (
        <ul className="text-xs space-y-1 max-h-40 overflow-y-auto">
          {l.events
            .slice()
            .reverse()
            .map((e, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-muted-foreground">{fmtWhen(e.timestamp)}</span>
                <span>
                  {e.outcome === "yes" ? "approved" : e.outcome === "no" ? "rejected" : e.outcome ?? "—"}
                </span>
                {e.confidence !== null && <span className="text-muted-foreground">· confidence {pct(e.confidence)}</span>}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
};

export default LearningTab;
