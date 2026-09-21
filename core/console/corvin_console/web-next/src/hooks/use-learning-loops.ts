import { useCallback, useEffect, useState } from "react";
import type { ListWindow, LoopEntry, LoopListResponse } from "@/types/learning-loops";

/** Poll the learning-loop list. Returns [] with an error string on failure — never mock rows. */
export const useLoops = (pollMs = 120_000) => {
  const [loops, setLoops] = useState<LoopEntry[] | null>(null);
  const [window_, setWindow] = useState<ListWindow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchLoops = useCallback(async (signal?: AbortSignal) => {
    setError(null);
    try {
      const res = await fetch("/v1/console/learning-loops/list", {
        headers: { "Content-Type": "application/json" },
        signal,
      });
      if (!res.ok) {
        setError(
          res.status === 503
            ? "Learning subsystem not available on this build"
            : `Failed to load learning loops (HTTP ${res.status})`,
        );
        setLoops([]);
        return;
      }
      const data: LoopListResponse = await res.json();
      setLoops(data.loops ?? []);
      setWindow(data.window ?? null);
    } catch (err) {
      if ((err as Error)?.name === "AbortError") return;
      setError(err instanceof Error ? err.message : "Unknown error");
      setLoops([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ctrl = new AbortController();
    setLoading(true);
    void fetchLoops(ctrl.signal);
    const id = setInterval(() => void fetchLoops(), pollMs);
    return () => {
      ctrl.abort();
      clearInterval(id);
    };
  }, [fetchLoops, pollMs]);

  return { loops, window: window_, loading, error, refresh: () => void fetchLoops() };
};
