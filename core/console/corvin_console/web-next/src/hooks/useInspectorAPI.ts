/**
 * Phase 5 Stream 3: API Integration + State Management
 *
 * Centralized API client for:
 * - Analysis submission
 * - Results fetching
 * - Trend queries
 * - History management
 */

import { useState, useCallback } from "react";

export interface AnalysisResult {
  status: string;
  media_id: string;
  overall_confidence: number;
  pipeline_id: string;
}

export interface AnalysisDetails {
  media_id: string;
  pipeline_id: string;
  overall_confidence: number;
  audio_result?: {
    audio_id: string;
    quality: string;
    confidence_score: number;
    snr: number;
    silence_ratio: number;
  };
  video_result?: {
    video_id: string;
    quality: string;
    confidence_score: number;
    sharpness: number;
    color_saturation: number;
  };
  timestamp: string;
}

export interface TrendData {
  period_days: number;
  analyses: number;
  average_confidence: number;
  trend: string;
  samples: Array<{
    media_id: string;
    confidence: number;
    timestamp: string;
  }>;
}

export interface UseInspectorAPI {
  // Analysis
  submitAnalysis: (
    mediaId: string,
    audioPath?: string,
    videoPath?: string
  ) => Promise<AnalysisResult | null>;
  getAnalysisDetails: (mediaId: string) => Promise<AnalysisDetails | null>;

  // Results
  getResults: (mediaId: string) => Promise<AnalysisDetails | null>;

  // Trends
  getTrends: (days?: number) => Promise<TrendData | null>;

  // Feedback
  submitFeedback: (
    mediaId: string,
    score: number,
    type: string
  ) => Promise<boolean>;

  // State
  isLoading: boolean;
  error: string | null;
  lastAnalysis: AnalysisResult | null;
}

export function useInspectorAPI(): UseInspectorAPI {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastAnalysis, setLastAnalysis] = useState<AnalysisResult | null>(null);

  // Submit media for analysis
  const submitAnalysis = useCallback(
    async (
      mediaId: string,
      audioPath?: string,
      videoPath?: string
    ): Promise<AnalysisResult | null> => {
      setIsLoading(true);
      setError(null);

      try {
        const body: Record<string, any> = { media_id: mediaId };
        if (audioPath) body.audio_path = audioPath;
        if (videoPath) body.video_path = videoPath;

        const response = await fetch("/v1/inspection/analyze", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        if (!response.ok) {
          throw new Error(`Analysis failed: ${response.statusText}`);
        }

        const result = (await response.json()) as AnalysisResult;
        setLastAnalysis(result);
        return result;
      } catch (err) {
        const errorMsg =
          err instanceof Error ? err.message : "Unknown error occurred";
        setError(errorMsg);
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  // Get detailed analysis results
  const getAnalysisDetails = useCallback(
    async (mediaId: string): Promise<AnalysisDetails | null> => {
      setIsLoading(true);
      setError(null);

      try {
        const response = await fetch(`/v1/inspection/results/${mediaId}`);

        if (!response.ok) {
          throw new Error(`Failed to fetch results: ${response.statusText}`);
        }

        const details = (await response.json()) as AnalysisDetails;
        return details;
      } catch (err) {
        const errorMsg =
          err instanceof Error ? err.message : "Failed to fetch results";
        setError(errorMsg);
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  // Alias for getAnalysisDetails
  const getResults = useCallback(
    async (mediaId: string): Promise<AnalysisDetails | null> => {
      return getAnalysisDetails(mediaId);
    },
    [getAnalysisDetails]
  );

  // Get trend data
  const getTrends = useCallback(
    async (days = 7): Promise<TrendData | null> => {
      setIsLoading(true);
      setError(null);

      try {
        const response = await fetch(`/v1/inspection/trends?days=${days}`);

        if (!response.ok) {
          throw new Error(`Failed to fetch trends: ${response.statusText}`);
        }

        const trends = (await response.json()) as TrendData;
        return trends;
      } catch (err) {
        const errorMsg =
          err instanceof Error ? err.message : "Failed to fetch trends";
        setError(errorMsg);
        return null;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  // Submit user feedback
  const submitFeedback = useCallback(
    async (
      mediaId: string,
      score: number,
      type: string
    ): Promise<boolean> => {
      setIsLoading(true);
      setError(null);

      try {
        const response = await fetch("/v1/inspection/feedback", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            media_id: mediaId,
            score,
            type,
          }),
        });

        if (!response.ok) {
          throw new Error(`Feedback submission failed: ${response.statusText}`);
        }

        return true;
      } catch (err) {
        const errorMsg =
          err instanceof Error ? err.message : "Feedback submission failed";
        setError(errorMsg);
        return false;
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  return {
    submitAnalysis,
    getAnalysisDetails,
    getResults,
    getTrends,
    submitFeedback,
    isLoading,
    error,
    lastAnalysis,
  };
}

/**
 * State management for analysis history
 */
export function useAnalysisHistory(maxSize = 100) {
  const [history, setHistory] = useState<AnalysisDetails[]>([]);

  const addToHistory = useCallback((analysis: AnalysisDetails) => {
    setHistory((prev) => {
      const updated = [analysis, ...prev];
      return updated.slice(0, maxSize);
    });
  }, [maxSize]);

  const clearHistory = useCallback(() => {
    setHistory([]);
  }, []);

  const getHistoryStats = useCallback(() => {
    if (history.length === 0) {
      return {
        total: 0,
        avgConfidence: 0,
        highestConfidence: 0,
        lowestConfidence: 0,
      };
    }

    const confidences = history.map((a) => a.overall_confidence);
    return {
      total: history.length,
      avgConfidence: confidences.reduce((a, b) => a + b, 0) / confidences.length,
      highestConfidence: Math.max(...confidences),
      lowestConfidence: Math.min(...confidences),
    };
  }, [history]);

  return {
    history,
    addToHistory,
    clearHistory,
    getHistoryStats,
  };
}
