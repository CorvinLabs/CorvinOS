/**
 * Skill Learning Dashboard — per-skill learning metrics, feedback, proposals.
 *
 * NOT WIRED: no production caller as of 2026-09-27 (adversarial review). No
 * panel, route or component imports this page.
 *
 * Backend: routes/skill_learning_routes.py. No per-skill learning store is
 * wired behind it yet, so today it answers 404 for the metrics and
 * `{available: false}` with empty lists for feedback and proposals. This page
 * must render that honestly — it used to dereference `metrics.accuracy` on the
 * 404 body and crash, and it drew a placeholder "chart" and an Approve button
 * that called nothing.
 */
import React, { useEffect, useState } from "react";

export interface LearningMetrics {
  skill_id: string;
  version: string;
  total_executions: number;
  correct_outcomes: number;
  accuracy: number;
  avg_latency_ms: number;
  error_rate: number;
  avg_cost_usd: number;
  confidence_score: number;
  last_updated: string;
}

export interface FeedbackItem {
  execution_id: string;
  outcome_correct: boolean;
  rating: number;
  notes: string;
  timestamp: string;
  latency_ms: number;
}

export interface OptimizationProposal {
  proposal_id: string;
  skill_id: string;
  parameter_name: string;
  old_value: string;
  new_value: string;
  rationale: string;
  expected_improvement_pct: number;
  confidence: number;
  created_at: string;
  status: "pending" | "approved" | "rejected" | "applied";
}

export type SkillLearningState =
  | { kind: "unavailable"; reason: string }
  | { kind: "error"; message: string }
  | {
      kind: "ok";
      metrics: LearningMetrics;
      feedback: FeedbackItem[] | null; // null = source not available
      proposals: OptimizationProposal[] | null;
    };

const UNAVAILABLE = "Skill learning metrics are not available on this build.";

function isMetrics(v: unknown): v is LearningMetrics {
  const m = v as Partial<LearningMetrics> | null;
  return (
    !!m &&
    typeof m.skill_id === "string" &&
    typeof m.accuracy === "number" &&
    typeof m.total_executions === "number" &&
    typeof m.confidence_score === "number" &&
    typeof m.avg_latency_ms === "number" &&
    typeof m.error_rate === "number"
  );
}

/** Fetch and classify the three endpoints. Exported for tests. */
export async function loadSkillLearning(
  skillId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<SkillLearningState> {
  const base = `/v1/console/skills/${encodeURIComponent(skillId)}`;
  try {
    const metricsRes = await fetchImpl(`${base}/learning`);
    if (metricsRes.status === 404) return { kind: "unavailable", reason: UNAVAILABLE };
    if (!metricsRes.ok) return { kind: "error", message: `HTTP ${metricsRes.status}` };
    const metrics = await metricsRes.json();
    if (!isMetrics(metrics) || (metrics as { available?: boolean }).available === false) {
      return { kind: "unavailable", reason: UNAVAILABLE };
    }

    const listOrNull = async <T,>(url: string, key: string): Promise<T[] | null> => {
      const res = await fetchImpl(url);
      if (!res.ok) return null;
      const body = await res.json();
      if (!body || body.available === false || !Array.isArray(body[key])) return null;
      return body[key] as T[];
    };
    const feedback = await listOrNull<FeedbackItem>(`${base}/feedback/history?limit=20`, "recent");
    const proposals = await listOrNull<OptimizationProposal>(`${base}/optimization/proposals`, "proposals");
    return { kind: "ok", metrics, feedback, proposals };
  } catch (err) {
    return { kind: "error", message: err instanceof Error ? err.message : String(err) };
  }
}

function Notice({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-lg border border-dashed p-6 text-center" data-testid="skill-learning-notice">
      <p className="font-medium">{title}</p>
      <p className="mt-1 text-sm text-muted-foreground">{body}</p>
    </div>
  );
}

export const SkillLearningDashboard: React.FC<{ skillId: string }> = ({ skillId }) => {
  const [state, setState] = useState<SkillLearningState | null>(null);

  useEffect(() => {
    let cancelled = false;
    setState(null);
    loadSkillLearning(skillId).then((s) => {
      if (!cancelled) setState(s);
    });
    return () => {
      cancelled = true;
    };
  }, [skillId]);

  if (state === null) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;
  if (state.kind === "unavailable") {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Notice title={`${skillId} — learning`} body={state.reason} />
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <Notice title="Could not load skill learning data" body={state.message} />
      </div>
    );
  }

  const { metrics, feedback, proposals } = state;
  const cards = [
    {
      label: "Accuracy",
      value: `${(metrics.accuracy * 100).toFixed(1)}%`,
      detail: `${metrics.correct_outcomes} / ${metrics.total_executions} correct`,
    },
    { label: "Confidence score", value: `${(metrics.confidence_score * 100).toFixed(0)}%`, detail: "Skill reliability" },
    { label: "Avg latency", value: `${metrics.avg_latency_ms.toFixed(1)} ms`, detail: "Per execution" },
    { label: "Error rate", value: `${(metrics.error_rate * 100).toFixed(1)}%`, detail: "Failed executions" },
  ];

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-6">
      <h2 className="text-2xl font-semibold">{metrics.skill_id} — learning</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {cards.map((c) => (
          <div key={c.label} className="rounded-lg border p-4">
            <div className="text-xs uppercase text-muted-foreground">{c.label}</div>
            <div className="text-2xl font-bold">{c.value}</div>
            <div className="text-xs text-muted-foreground">{c.detail}</div>
          </div>
        ))}
      </div>

      <section className="rounded-lg border p-4">
        <h3 className="mb-3 font-semibold">Recent feedback</h3>
        {feedback === null ? (
          <p className="text-sm text-muted-foreground">Feedback history is not available on this build.</p>
        ) : feedback.length === 0 ? (
          <p className="text-sm text-muted-foreground">No feedback recorded yet.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {feedback.map((item) => (
              <li key={item.execution_id} className="flex gap-3">
                <span>{item.outcome_correct ? "correct" : "incorrect"}</span>
                <span className="flex-1">
                  {item.notes} — {item.rating}/5 ({item.latency_ms.toFixed(1)} ms)
                </span>
                <span className="text-xs text-muted-foreground">{item.timestamp}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border p-4">
        <h3 className="mb-3 font-semibold">Optimization proposals</h3>
        {proposals === null ? (
          <p className="text-sm text-muted-foreground">Optimization proposals are not available on this build.</p>
        ) : proposals.length === 0 ? (
          <p className="text-sm text-muted-foreground">No pending proposals.</p>
        ) : (
          <ul className="space-y-3 text-sm">
            {proposals.map((p) => (
              <li key={p.proposal_id} className="rounded border p-3">
                <div className="flex justify-between">
                  <span className="font-medium">{p.parameter_name}</span>
                  <span className="text-xs text-muted-foreground">{(p.confidence * 100).toFixed(0)}% confidence</span>
                </div>
                <p className="my-1 text-muted-foreground">{p.rationale}</p>
                <code className="text-xs">
                  {p.old_value} → {p.new_value} (expected +{p.expected_improvement_pct.toFixed(1)}%)
                </code>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
};

export default SkillLearningDashboard;
