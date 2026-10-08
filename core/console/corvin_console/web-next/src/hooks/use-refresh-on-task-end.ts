/**
 * Re-read a chat's history when its running task ends.
 *
 * A reload (or closed tab) no longer kills a running task: the server lets it
 * finish. But the history is fetched once per visit, so a task still running at
 * that moment would finish unseen until the next reload. The server's task log
 * (the same query the sidebar indicator polls — one shared cache entry) tells us
 * when the task ends; then `refresh` pulls what was persisted meanwhile.
 *
 * Skipped while this tab is itself streaming: live events own the display then.
 */
import { useEffect, useRef } from "react";
import { useChatTaskStatus } from "@/hooks/use-chat-task-status";

export function useRefreshOnTaskEnd(
  sid: string,
  streaming: boolean,
  refresh: () => Promise<void> | void,
): void {
  const { phase } = useChatTaskStatus(sid);
  const previous = useRef(phase);
  const streamingRef = useRef(streaming);
  const refreshRef = useRef(refresh);
  streamingRef.current = streaming;
  refreshRef.current = refresh;

  useEffect(() => {
    const was = previous.current;
    previous.current = phase;
    const wasInFlight = was === "running" || was === "pending";
    const inFlight = phase === "running" || phase === "pending";
    if (wasInFlight && !inFlight && !streamingRef.current) {
      void Promise.resolve(refreshRef.current()).catch(() => {
        /* the next poll / visit retries; a failed re-read must not surface as a chat error */
      });
    }
  }, [phase]);
}
