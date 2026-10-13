import React, { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Loader2, ThumbsDown, ThumbsUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuth } from "@/lib/auth";
import { api, ApiError } from "@/lib/api/client";

/** Skills whose executions the feedback route accepts (mirrors VALID_SKILLS on the server). */
export const RATEABLE_SKILLS = new Set([
  "os.delegation_router",
  "os.workflow_optimizer",
  "os.security_orchestrator",
  "os.flow_guard",
]);

interface RecentDecision {
  event_id: string;
  timestamp: string;
  decision: string | null;
}
interface RecentResponse {
  skill_id: string;
  config_consumer: boolean;
  items: RecentDecision[];
}
interface SubmitResult {
  config_applied: boolean;
  trust_weight: number;
}

/** Turn a submit outcome into the one-line row status the operator sees. */
export function ratingStatus(result: SubmitResult | null, error: unknown): string {
  if (error instanceof ApiError) {
    const d = error.detail as { detail?: { reason?: string } } | undefined;
    if (error.status === 422 && d?.detail?.reason) return `Refused by the feedback gate: ${d.detail.reason}`;
    return `Not recorded (HTTP ${error.status})`;
  }
  if (error) return "Not recorded";
  if (!result) return "";
  return result.config_applied ? "Recorded — learned config updated" : "Recorded";
}

export const LearningLoopRating: React.FC<{ skillId: string }> = ({ skillId }) => {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const [rows, setRows] = useState<Record<string, string>>({});

  const recent = useQuery({
    queryKey: ["skill-recent-decisions", skillId],
    queryFn: () =>
      api<RecentResponse>(`/v1/console/feedback/skill/recent?skill_id=${encodeURIComponent(skillId)}&limit=5`),
  });

  const rate = useMutation({
    mutationFn: (args: { eventId: string; rating: 1 | -1 }) =>
      api<SubmitResult>("/v1/console/feedback/skill", {
        method: "POST",
        csrf,
        body: { skill_id: skillId, subject_id: args.eventId, rating: args.rating, category: "accuracy" },
      }),
    onSuccess: (res, args) => setRows((r) => ({ ...r, [args.eventId]: ratingStatus(res, null) })),
    onError: (err, args) => setRows((r) => ({ ...r, [args.eventId]: ratingStatus(null, err) })),
  });

  const items = recent.data?.items ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Rate recent decisions</CardTitle>
        <CardDescription>
          Was the skill's decision correct? A rating is checked against the recorded execution
          before it is stored, and only the rating travels — never free text.
          {recent.data?.config_consumer
            ? " This skill reads its learned config back, so a rating can move it."
            : " This skill does not read a learned config yet; the rating is stored and audited."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {recent.isLoading ? (
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        ) : recent.isError ? (
          <p className="text-sm text-destructive">Recent decisions could not be loaded.</p>
        ) : items.length === 0 ? (
          <p className="text-sm text-muted-foreground">No recorded execution of this skill yet.</p>
        ) : (
          <div className="space-y-2">
            {items.map((it) => (
              <div key={it.event_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm">
                <div className="min-w-0">
                  <span className="font-medium">{it.decision ?? "decision not shown"}</span>
                  <span className="text-muted-foreground"> · {new Date(it.timestamp).toLocaleString("en-US")}</span>
                </div>
                {rows[it.event_id] ? (
                  <span className="text-xs text-muted-foreground">{rows[it.event_id]}</span>
                ) : (
                  <div className="flex items-center gap-2">
                    <Button size="sm" variant="outline" disabled={!csrf || rate.isPending}
                            aria-label={`Correct — ${it.event_id}`}
                            onClick={() => rate.mutate({ eventId: it.event_id, rating: 1 })}>
                      <ThumbsUp className="mr-1 h-3.5 w-3.5" /> Correct
                    </Button>
                    <Button size="sm" variant="outline" disabled={!csrf || rate.isPending}
                            aria-label={`Wrong — ${it.event_id}`}
                            onClick={() => rate.mutate({ eventId: it.event_id, rating: -1 })}>
                      <ThumbsDown className="mr-1 h-3.5 w-3.5" /> Wrong
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default LearningLoopRating;
