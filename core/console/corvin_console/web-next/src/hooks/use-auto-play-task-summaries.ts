/**
 * Read a chat's finished-while-away task recaps aloud when Voice is on.
 * The decision lives in lib/auto-play-task-summaries.ts; this hook polls the
 * chat's recap list while the pane is mounted and starts at most one recap at a
 * time, only when nothing else is speaking (it never cuts a live reply off).
 */
import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { listTaskVoiceSummaries, type TaskVoiceSummary } from "@/lib/api";
import {
  decideAutoPlay,
  getLiveSpoken,
  getOrInitSince,
  loadHeard,
  rememberHeard,
  setServerOffset,
} from "@/lib/auto-play-task-summaries";

export function useAutoPlayTaskSummaries(
  sid: string,
  opts: {
    /** The chat's "Voice on" toggle. */
    enabled: boolean;
    /** Nothing is loading, playing or streaming right now. */
    idle: boolean;
    play: (summary: TaskVoiceSummary) => void;
  },
): void {
  const { enabled, idle } = opts;
  const playRef = useRef(opts.play);
  playRef.current = opts.play;

  const q = useQuery({
    queryKey: ["task-voice-summaries", "chat", sid],
    queryFn: ({ signal }) => listTaskVoiceSummaries(sid, signal),
    enabled: enabled && !!sid,
    refetchInterval: 15_000,
    retry: false,
  });

  useEffect(() => {
    const data = q.data;
    if (!enabled || !idle || !data) return;
    setServerOffset(data.now);
    const decision = decideAutoPlay({
      summaries: data.summaries,
      heard: loadHeard(),
      sinceS: getOrInitSince(data.now),
      liveSpokenS: getLiveSpoken(sid),
    });
    // Remember BEFORE starting: a blocked or failed start must not loop.
    rememberHeard(decision.markHeard);
    if (decision.play) playRef.current(decision.play);
  }, [enabled, idle, q.data, q.dataUpdatedAt, sid]);
}
