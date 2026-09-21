import { useState, useEffect } from "react";

interface LoopUpdate {
  loop_id: string;
  health_score: number;
  last_event_ts: string;
  status: "active" | "dormant" | "stale" | "degrading";
  event_count: number;
}

export const useWebSocketLoopUpdates = (loopId: string | null) => {
  const [update, setUpdate] = useState<LoopUpdate | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!loopId) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/v1/console/learning-loops/${loopId}/updates`;

    let ws: WebSocket | null = null;
    let reconnectTimeout: NodeJS.Timeout;

    const connect = () => {
      try {
        ws = new WebSocket(url);
        ws.onopen = () => {
          setConnected(true);
          setError(null);
        };
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            setUpdate(data);
          } catch (err) {
            console.error("Failed to parse WebSocket message:", err);
          }
        };
        ws.onerror = (event) => {
          console.error("WebSocket error:", event);
          setError("WebSocket connection error");
          setConnected(false);
        };
        ws.onclose = () => {
          setConnected(false);
          // Attempt to reconnect after 3 seconds
          reconnectTimeout = setTimeout(connect, 3000);
        };
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to connect");
        setConnected(false);
      }
    };

    connect();

    return () => {
      if (ws) ws.close();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
    };
  }, [loopId]);

  return { update, connected, error };
};
