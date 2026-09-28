/**
 * Task indicator for one chat in the sidebar. Reads the server's TaskManager
 * log for the session (view=summary) — the browser's IndexedDB task cache is
 * only filled for tasks it already knows about, so it cannot see a newly
 * started task.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError } from "@/lib/api/client";
import {
  fetchSessionTasks,
  pickIndicator,
  sessionTasksKey,
  type TaskIndicator,
} from "@/lib/chat-task-status";

// Fast while a task is in flight, slow at rest: N sidebar rows poll N times.
const POLL_ACTIVE_MS = 3_000;
const POLL_IDLE_MS = 20_000;

export interface ChatTaskStatus extends TaskIndicator {
  /** Server-clock epoch seconds, ticking once per second while a task is in flight. */
  nowS: number;
}

export function useChatTaskStatus(sid: string): ChatTaskStatus {
  const query = useQuery({
    queryKey: sessionTasksKey(sid),
    queryFn: async () => {
      const res = await fetchSessionTasks(sid);
      // Server minus browser clock, taken at response time.
      return { ...res, offsetS: res.now - Date.now() / 1000 };
    },
    enabled: !!sid,
    retry: false,
    refetchInterval: (q) => {
      // A deleted session answers 404 forever; stop asking.
      if (q.state.error instanceof ApiError && q.state.error.status === 404) return false;
      const d = q.state.data;
      if (!d) return POLL_IDLE_MS;
      const ind = pickIndicator(d.tasks, Date.now() / 1000 + d.offsetS);
      return ind.phase === "idle" || ind.stale ? POLL_IDLE_MS : POLL_ACTIVE_MS;
    },
  });

  const offsetS = query.data?.offsetS ?? 0;
  const [clientNowS, setClientNowS] = useState(() => Date.now() / 1000);
  const nowS = clientNowS + offsetS;
  const indicator = pickIndicator(query.data?.tasks ?? [], nowS);
  const inFlight = indicator.phase === "running" || indicator.phase === "pending";

  useEffect(() => {
    setClientNowS(Date.now() / 1000);
    if (!inFlight) return;
    const t = setInterval(() => setClientNowS(Date.now() / 1000), 1_000);
    return () => clearInterval(t);
  }, [inFlight, query.dataUpdatedAt]);

  return { ...indicator, nowS };
}
