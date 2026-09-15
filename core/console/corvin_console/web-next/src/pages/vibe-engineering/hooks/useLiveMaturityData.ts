/**
 * useLiveMaturityData — Load 9D maturity scores from the live maturity endpoint.
 *
 * The backend (`/v1/console/vibe/maturity/measurements`) computes one live
 * measurement ON DEMAND from real sources — the ADR-0314 learning EventStore and
 * the tenant audit chain (see `routes/maturity_live.py`). The per-loop scores and
 * the meta/trend/recommendations are all SERVER-AUTHORITATIVE: this hook does not
 * re-derive them from raw metrics and does NOT fabricate a fallback. When no
 * measurement is available the hook returns `null`, and the dashboard renders an
 * explicit empty state rather than a frozen block of sample numbers.
 */

import { useEffect, useState, useRef } from 'react';
import { LoopScores } from '../components/maturity/types';

/** Server-computed trend/meta card values (all real, from maturity_live). */
export interface MaturityMeta {
  convergence_rate: number;
  threshold_updates: number;
  prediction_accuracy: number | null;
  optimizer_active: boolean;
  drift: number;
  trend: { direction: 'up' | 'down' | 'stable'; value: number; insufficient_history: boolean };
  projection_30d: number | null;
  recommendations: Array<{ severity: 'critical' | 'warning' | 'info'; text: string }>;
}

interface LiveMeasurement {
  timestamp: string;
  unix_time: number;
  tenant_id: string;
  window: string;
  learning: Record<string, number | null>;
  system: Record<string, number | null>;
  user_actions: Record<string, number>;
  component_health: { [key: string]: { active: boolean; contribution: number; drift: number } };
  loop_scores: LoopScores;
  meta: MaturityMeta;
  signals?: Record<string, unknown>;
}

export type TimeWindow = '7d' | '30d' | '90d' | 'today';

interface UseMaturityDataOptions {
  window?: TimeWindow;
  refreshIntervalMs?: number;
}

/** Load the current live measurement, or null when none is available. */
async function fetchLiveMeasurement(window: TimeWindow): Promise<LiveMeasurement | null> {
  const response = await fetch(`/v1/console/vibe/maturity/measurements?window=${window}`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  });
  if (!response.ok) {
    throw new Error(`API returned ${response.status}: ${response.statusText}`);
  }
  const data = await response.json();
  const measurements: LiveMeasurement[] = data.measurements || [];
  // The endpoint returns a single on-demand snapshot; take the freshest one.
  return measurements.length ? measurements[measurements.length - 1] : null;
}

/**
 * Hook: Load live maturity data with real-time refresh.
 * Returns `loopScores: null` and `meta: null` when the endpoint has no data —
 * callers must handle the empty state (no synthetic fallback).
 */
export function useLiveMaturityData(options: UseMaturityDataOptions = {}) {
  const { window = '7d', refreshIntervalMs = 60 * 1000 } = options; // refresh every minute
  const [loopScores, setLoopScores] = useState<LoopScores | null>(null);
  const [meta, setMeta] = useState<MaturityMeta | null>(null);
  const [measurement, setMeasurement] = useState<LiveMeasurement | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const refreshInterval = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadData = async () => {
    try {
      setLoading(true);
      const m = await fetchLiveMeasurement(window);
      if (m && m.loop_scores) {
        setLoopScores(m.loop_scores);
        setMeta(m.meta ?? null);
        setMeasurement(m);
        setLastUpdated(m.timestamp ? new Date(m.timestamp) : new Date());
      } else {
        // Honest empty state — do NOT invent scores.
        setLoopScores(null);
        setMeta(null);
        setMeasurement(null);
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unknown error');
      console.error('[useLiveMaturityData] Error:', e);
      setLoopScores(null);
      setMeta(null);
      setMeasurement(null);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    refreshInterval.current = setInterval(loadData, refreshIntervalMs);
    return () => {
      if (refreshInterval.current) {
        clearInterval(refreshInterval.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [window, refreshIntervalMs]);

  return {
    loopScores,
    meta,
    measurement,
    loading,
    error,
    lastUpdated,
    refresh: loadData,
  };
}
