/**
 * Shared run-tracking rules for the Forge generators (Skill / Tool / Plugin).
 *
 * A generation run lives on the server and outlasts the browser: switching
 * panels or hard-refreshing must only drop the VIEW. So the run id is kept in
 * sessionStorage, and a poll error ends the run only when the server really
 * says it does not exist (404). A network error, a 5xx or a 401 while the
 * console restarts is transient — keep polling through it.
 */
import { useCallback, useState } from "react";
import { ApiError } from "@/lib/api/client";

/** The server answered that this run id does not exist (the only terminal poll error). */
export function isRunGone(err: unknown): boolean {
  return err instanceof ApiError && err.status === 404;
}

export function storedRunId(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function storeRunId(key: string, runId: string | null): void {
  try {
    if (runId) window.sessionStorage.setItem(key, runId);
    else window.sessionStorage.removeItem(key);
  } catch {
    /* storage unavailable — the run is simply not re-attached */
  }
}

/** `useState` for a run id that survives a panel switch and a reload. */
export function usePersistedRunId(key: string): [string | null, (id: string | null) => void] {
  const [runId, setRunIdState] = useState<string | null>(() => storedRunId(key));
  const setRunId = useCallback(
    (id: string | null) => {
      storeRunId(key, id);
      setRunIdState(id);
    },
    [key],
  );
  return [runId, setRunId];
}
