import { useState, useEffect } from "react";

interface AuditEvent {
  timestamp: string;
  event_type: string;
  skill_id?: string;
  signal?: string;
  outcome?: string;
  metadata?: Record<string, unknown>;
}

interface HealthTrendPoint {
  date: string;
  health_score: number;
  event_count: number;
}

interface HealthTrend {
  points: HealthTrendPoint[];
  min_score: number;
  max_score: number;
  avg_score: number;
}

export const useDetail = (loopId: string) => {
  const [trend, setTrend] = useState<HealthTrend | null>(null);
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!loopId) return;

    const fetchDetail = async () => {
      setLoading(true);
      try {
        const [detailRes, eventsRes] = await Promise.all([
          fetch(`/v1/console/learning-loops/${loopId}/details`),
          fetch(`/v1/console/learning-loops/${loopId}/events?limit=100`),
        ]);

        if (detailRes.ok) {
          const data = await detailRes.json();
          setTrend(data.health_trend);
        }

        if (eventsRes.ok) {
          const data = await eventsRes.json();
          setEvents(data.events || []);
        }
      } catch (err) {
        console.error("Failed to fetch detail:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchDetail();
  }, [loopId]);

  return { trend, events, loading };
};
