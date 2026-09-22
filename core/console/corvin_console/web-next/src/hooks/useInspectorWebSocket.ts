/**
 * Phase 5 Stream 2: WebSocket Hook for Live Pipeline Updates
 *
 * Provides real-time:
 * - Pipeline stage updates (analyzing → aggregating → complete)
 * - Confidence score updates
 * - Error notifications
 */

import { useEffect, useState, useCallback, useRef } from "react";

export interface PipelineUpdate {
  type: "pipeline_stage" | "confidence_update" | "error";
  media_id: string;
  stage?: string;
  confidence?: number;
  error?: string;
  timestamp: string;
}

export interface UsePipelineState {
  isConnected: boolean;
  currentStage: string | null;
  currentConfidence: number | null;
  lastUpdate: PipelineUpdate | null;
  error: string | null;
}

const RECONNECT_INTERVAL = 3000;

export function useInspectorWebSocket(
  mediaId: string | null,
  onUpdate?: (update: PipelineUpdate) => void
): UsePipelineState {
  const [state, setState] = useState<UsePipelineState>({
    isConnected: false,
    currentStage: null,
    currentConfidence: null,
    lastUpdate: null,
    error: null,
  });

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout>();

  // Connect to WebSocket
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const ws = new WebSocket(`${protocol}//${window.location.host}/ws/inspection`);

      ws.onopen = () => {
        console.log("[Inspector WebSocket] Connected");
        setState((prev) => ({ ...prev, isConnected: true, error: null }));

        // Subscribe to media_id
        if (mediaId) {
          ws.send(
            JSON.stringify({
              action: "subscribe",
              media_id: mediaId,
            })
          );
        }
      };

      ws.onmessage = (event) => {
        try {
          const update: PipelineUpdate = JSON.parse(event.data);

          setState((prev) => ({
            ...prev,
            currentStage: update.stage || prev.currentStage,
            currentConfidence: update.confidence ?? prev.currentConfidence,
            lastUpdate: update,
            error: update.type === "error" ? update.error : null,
          }));

          onUpdate?.(update);
        } catch (err) {
          console.error("[Inspector WebSocket] Parse error:", err);
        }
      };

      ws.onerror = (event) => {
        console.error("[Inspector WebSocket] Error:", event);
        setState((prev) => ({
          ...prev,
          error: "WebSocket connection error",
        }));
      };

      ws.onclose = () => {
        console.log("[Inspector WebSocket] Disconnected");
        setState((prev) => ({
          ...prev,
          isConnected: false,
        }));

        // Auto-reconnect
        reconnectTimeoutRef.current = setTimeout(connect, RECONNECT_INTERVAL);
      };

      wsRef.current = ws;
    } catch (err) {
      console.error("[Inspector WebSocket] Connection failed:", err);
      setState((prev) => ({
        ...prev,
        error: "Failed to connect to WebSocket",
      }));

      reconnectTimeoutRef.current = setTimeout(connect, RECONNECT_INTERVAL);
    }
  }, [mediaId, onUpdate]);

  // Connect on mount / mediaId change
  useEffect(() => {
    connect();

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      wsRef.current?.close();
    };
  }, [connect]);

  return state;
}

/**
 * Hook for submitting feedback and listening for optimizer updates
 */
export function useInspectorFeedback() {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [lastFeedback, setLastFeedback] = useState<{
    media_id: string;
    score: number;
    timestamp: string;
  } | null>(null);

  const submitFeedback = useCallback(
    async (mediaId: string, score: number, feedbackType: string) => {
      setIsSubmitting(true);
      try {
        const response = await fetch("/v1/inspection/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            media_id: mediaId,
            score,
            type: feedbackType,
          }),
        });

        if (!response.ok) throw new Error("Feedback submission failed");

        setLastFeedback({
          media_id: mediaId,
          score,
          timestamp: new Date().toISOString(),
        });

        return true;
      } catch (err) {
        console.error("[Inspector Feedback] Error:", err);
        return false;
      } finally {
        setIsSubmitting(false);
      }
    },
    []
  );

  return { submitFeedback, isSubmitting, lastFeedback };
}

/**
 * Hook for live confidence tracking
 */
export function useConfidenceTrend(windowSize = 10) {
  const [confidences, setConfidences] = useState<number[]>([]);

  const addConfidence = useCallback((confidence: number) => {
    setConfidences((prev) => {
      const updated = [...prev, confidence];
      return updated.slice(-windowSize);
    });
  }, [windowSize]);

  const avgConfidence =
    confidences.length > 0
      ? confidences.reduce((a, b) => a + b, 0) / confidences.length
      : 0;

  const trend =
    confidences.length > 1
      ? confidences[confidences.length - 1] - confidences[0]
      : 0;

  return {
    confidences,
    avgConfidence,
    trend,
    addConfidence,
  };
}
