import React from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api/client";

export interface LearningAnomaly {
  event: string;
  skill_id: string;
  since: string;
  [field: string]: unknown;
}

/** One line per anomaly, naming the measured numbers behind it. */
export function describeAnomaly(a: LearningAnomaly): string {
  const n = (k: string) => (typeof a[k] === "number" ? (a[k] as number) : null);
  switch (a.event) {
    case "skill.high_rejection_rate":
      return `${n("rejection_count")} of ${n("sample_count")} ratings refused in the last 24 h`;
    case "skill.config_oscillation":
      return `${n("change_count")} config versions in the last hour`;
    case "skill.confidence_regression":
      return `mean confidence ${n("confidence_before")?.toFixed(2)} → ${n("confidence_after")?.toFixed(2)} (last 24 h vs the 6 days before)`;
    default:
      return a.event;
  }
}

const TITLE: Record<string, string> = {
  "skill.high_rejection_rate": "High rejection rate",
  "skill.config_oscillation": "Config oscillation",
  "skill.confidence_regression": "Confidence regression",
};

export const LearningAnomalies: React.FC = () => {
  const q = useQuery({
    queryKey: ["learning-daemon-anomalies"],
    queryFn: () => api<{ data: { anomalies?: LearningAnomaly[]; saved_at: string | null } }>("/api/learning/daemon"),
    refetchInterval: 60_000,
  });
  const anomalies = q.data?.data.anomalies ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Anomalies</CardTitle>
        <CardDescription>
          Measured every five minutes by the learning host over the stored ratings, config versions
          and recorded skill confidence. Each one is written to the audit log once, when it starts.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {q.isLoading ? (
          <p className="text-sm text-muted-foreground">loading…</p>
        ) : q.isError ? (
          <p className="text-sm text-destructive">Anomaly state could not be loaded.</p>
        ) : anomalies.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No anomaly is active{q.data?.data.saved_at ? ` (state saved ${new Date(q.data.data.saved_at + "Z").toLocaleString("en-US")})` : ""}.
          </p>
        ) : (
          <ul className="space-y-2">
            {anomalies.map((a) => (
              <li key={`${a.event}:${a.skill_id}`} className="flex items-start gap-2 rounded-lg border px-3 py-2 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <div>
                  <p className="font-medium">{TITLE[a.event] ?? a.event} · {a.skill_id}</p>
                  <p className="text-xs text-muted-foreground">
                    {describeAnomaly(a)} · since {new Date(a.since).toLocaleString("en-US")}
                  </p>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
};

export default LearningAnomalies;
