import { useState, useEffect } from "react";

interface LoopEntry {
  loop_id: string;
  plugin_id: string;
  skill_id?: string | null;
  status: "active" | "dormant" | "stale" | "degrading";
  health: { score: number; trend: "up" | "down" | "flat" };
  last_event?: string;
  event_count_7d: number;
  description?: string;
}

export const useLoops = () => {
  const [loops, setLoops] = useState<LoopEntry[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchLoops = async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetch("/v1/console/learning-loops/list", {
          headers: { "Content-Type": "application/json" },
        });
        if (!res.ok) {
          if (res.status === 503) {
            setError("Learning subsystem not available");
          } else {
            setError(`Failed to fetch learning loops (${res.status})`);
          }
          setLoops([]);
        } else {
          const data = await res.json();
          setLoops(data.loops || []);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown error");
        setLoops([]);
      } finally {
        setLoading(false);
      }
    };
    fetchLoops();
    const interval = setInterval(fetchLoops, 120000); // Poll every 2 minutes
    return () => clearInterval(interval);
  }, []);

  return { loops, loading, error };
};
