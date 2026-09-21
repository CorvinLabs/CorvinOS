import { useEffect, useState } from "react";
import type { LoopEvent, LoopTrend } from "@/types/learning-loops";

/**
 * Per-loop detail: the dated trend, the event log and the backend's
 * recommendations. The loop id contains `:` and `.` (e.g. `core:os.delegation_router`),
 * so it is encoded rather than interpolated raw.
 */
export const useDetail = (loopId: string) => {
  const [trend, setTrend] = useState<LoopTrend | null>(null);
  const [events, setEvents] = useState<LoopEvent[] | null>(null);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!loopId) return;
    const ctrl = new AbortController();
    const id = encodeURIComponent(loopId);

    const fetchDetail = async () => {
      setLoading(true);
      setError(null);
      try {
        const [detailRes, eventsRes] = await Promise.all([
          fetch(`/v1/console/learning-loops/${id}/details?days=14`, { signal: ctrl.signal }),
          fetch(`/v1/console/learning-loops/${id}/events?limit=100`, { signal: ctrl.signal }),
        ]);

        if (detailRes.ok) {
          const data = await detailRes.json();
          setTrend(data.health_trend ?? null);
          setRecommendations(data.recommendations ?? []);
        } else {
          setError(`Details unavailable (HTTP ${detailRes.status})`);
          setTrend(null);
        }

        if (eventsRes.ok) {
          const data = await eventsRes.json();
          setEvents(data.events ?? []);
        } else {
          setEvents([]);
        }
      } catch (err) {
        if ((err as Error)?.name === "AbortError") return;
        setError(err instanceof Error ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    };

    void fetchDetail();
    return () => ctrl.abort();
  }, [loopId]);

  return { trend, events, recommendations, loading, error };
};
