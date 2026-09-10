/**
 * Hook: useDeprecatedApiMetrics
 *
 * Fetches deprecated API metrics from /v1/console/deprecated-apis/metrics
 * with optional auto-refresh at configurable interval.
 *
 * Returns:
 *   metrics: CurrentMetricsResponse | null
 *   loading: boolean
 *   error: string | null
 *   lastUpdated: string | null
 *   refresh: () => Promise<void>
 */

import { useState, useEffect, useCallback } from 'react';

export interface DeprecatedAPICallCount {
  api_name: string;
  calls_total: number;
  calls_per_minute: number;
  error_count: number;
  error_rate_pct: number;
  p50_latency_ms: number;
  p99_latency_ms: number;
  skill_availability_pct: number;
}

export interface CurrentMetricsResponse {
  timestamp: string;
  total_calls_per_minute: number;
  total_error_rate_pct: number;
  skill_availability_pct: number;
  apis: DeprecatedAPICallCount[];
  status: 'no_calls' | 'low_activity' | 'high_activity';
}

interface UseDeprecatedApiMetricsOptions {
  autoRefreshInterval?: number; // milliseconds, undefined = no auto-refresh
}

export function useDeprecatedApiMetrics(options: UseDeprecatedApiMetricsOptions = {}) {
  const [metrics, setMetrics] = useState<CurrentMetricsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);

    try {
      const response = await fetch('/v1/console/deprecated-apis/metrics', {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch metrics: ${response.statusText}`);
      }

      const data = (await response.json()) as CurrentMetricsResponse;
      setMetrics(data);
      setLastUpdated(new Date().toISOString());
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Unknown error';
      setError(message);
      setMetrics(null);
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial fetch on mount
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Auto-refresh on interval
  useEffect(() => {
    if (!options.autoRefreshInterval) return;

    const interval = setInterval(() => {
      refresh();
    }, options.autoRefreshInterval);

    return () => clearInterval(interval);
  }, [options.autoRefreshInterval, refresh]);

  return { metrics, loading, error, lastUpdated, refresh };
}
