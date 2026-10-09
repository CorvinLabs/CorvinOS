/**
 * The text of the newest task recap that finished while this chat pane is open,
 * so the operator READS what the voice recap says. Same list (and query key) as
 * use-auto-play-task-summaries; recaps exist only while Voice is on.
 */
import { useCallback, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listTaskVoiceSummaries, type TaskVoiceSummary } from "@/lib/api";
import { pickLiveSummary, summaryKey } from "@/lib/auto-play-task-summaries";

export function useLiveTaskSummary(
  sid: string,
  enabled: boolean,
): { summary: TaskVoiceSummary | null; dismiss: () => void } {
  const openedAtMs = useRef(Date.now());
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(new Set());

  const q = useQuery({
    queryKey: ["task-voice-summaries", "chat", sid],
    queryFn: ({ signal }) => listTaskVoiceSummaries(sid, signal),
    enabled: enabled && !!sid,
    refetchInterval: 8_000,
    retry: false,
  });

  const data = q.data;
  // Server clock, not the browser's: completed_at is server epoch seconds.
  const sinceS = data ? data.now - (Date.now() - openedAtMs.current) / 1000 : Infinity;
  const summary = data
    ? pickLiveSummary({ summaries: data.summaries, sinceS, dismissed })
    : null;

  const dismiss = useCallback(() => {
    if (!summary) return;
    const key = summaryKey(summary);
    setDismissed((prev) => new Set(prev).add(key));
  }, [summary]);

  return { summary, dismiss };
}
