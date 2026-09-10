/**
 * Anomaly Alerts — Real-time detection of unusual drops/spikes
 *
 * Analyzes live measurements for:
 * - Sudden score drops (> 0.5 points in 5 min)
 * - High drift increases (> 0.01 delta)
 * - Convergence stalls (< 0.01 rate for 30 min)
 */

import React, { useEffect, useState } from 'react';
import { AlertTriangle, AlertCircle, CheckCircle, X } from 'lucide-react';

export interface Anomaly {
  id: string;
  type: 'drop' | 'drift-spike' | 'stall';
  loop: string;
  severity: 'critical' | 'warning' | 'info';
  message: string;
  timestamp: string;
  value: number;
  threshold: number;
}

interface AnomalyAlertsProps {
  windowSeconds?: number; // How far back to check (default 300 = 5 min)
}

export function AnomalyAlerts({ windowSeconds = 300 }: AnomalyAlertsProps) {
  const [anomalies, setAnomalies] = useState<Anomaly[]>([]);
  const [dismissed, setDismissed] = useState(new Set<string>());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const checkAnomalies = async () => {
      try {
        setLoading(true);
        const response = await fetch(
          `/v1/console/vibe/maturity/anomalies?window=${windowSeconds}`,
          { headers: { 'Content-Type': 'application/json' } }
        );

        if (!response.ok) {
          throw new Error(`API error ${response.status}`);
        }

        const result = await response.json();
        const detectedAnomalies = (result.anomalies || []).filter(
          (a: Anomaly) => !dismissed.has(a.id)
        );
        setAnomalies(detectedAnomalies);
      } catch (e) {
        console.warn('[AnomalyAlerts] Failed to fetch:', e);
      } finally {
        setLoading(false);
      }
    };

    checkAnomalies();
    // Re-check every 30 seconds
    const interval = setInterval(checkAnomalies, 30000);
    return () => clearInterval(interval);
  }, [windowSeconds, dismissed]);

  const dismissAlert = (id: string) => {
    const newDismissed = new Set(dismissed);
    newDismissed.add(id);
    setDismissed(newDismissed);
  };

  if (loading) {
    return null; // Don't show loading state, just empty
  }

  if (anomalies.length === 0) {
    return (
      <div className="bg-emerald-500/10 border border-emerald-500/40 rounded-lg p-4 flex items-center gap-3">
        <CheckCircle size={18} className="text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
        <span className="text-sm text-emerald-600 dark:text-emerald-400">All systems nominal — no anomalies detected</span>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {anomalies.map((anomaly) => {
        const icon =
          anomaly.severity === 'critical' ? (
            <AlertTriangle size={18} className="text-destructive flex-shrink-0" />
          ) : anomaly.severity === 'warning' ? (
            <AlertCircle size={18} className="text-amber-600 dark:text-amber-400 flex-shrink-0" />
          ) : (
            <AlertCircle size={18} className="text-accent flex-shrink-0" />
          );

        const bgColor =
          anomaly.severity === 'critical'
            ? 'bg-destructive/10'
            : anomaly.severity === 'warning'
              ? 'bg-amber-500/10'
              : 'bg-accent/10';

        const borderColor =
          anomaly.severity === 'critical'
            ? 'border-destructive/40'
            : anomaly.severity === 'warning'
              ? 'border-amber-500/40'
              : 'border-accent/40';

        const textColor =
          anomaly.severity === 'critical'
            ? 'text-destructive'
            : anomaly.severity === 'warning'
              ? 'text-amber-600 dark:text-amber-400'
              : 'text-accent';

        return (
          <div
            key={anomaly.id}
            className={`${bgColor} border ${borderColor} rounded-lg p-4 flex items-start justify-between`}
          >
            <div className="flex items-start gap-3 flex-1">
              {icon}
              <div>
                <div className={`text-sm font-medium ${textColor} mb-1`}>
                  {anomaly.loop} — {anomaly.type.replace('-', ' ')}
                </div>
                <div className={`text-sm ${textColor} opacity-90`}>{anomaly.message}</div>
                <div className={`text-xs ${textColor} opacity-75 mt-1`}>
                  {new Date(anomaly.timestamp).toLocaleTimeString()} • Δ{anomaly.value.toFixed(3)}
                </div>
              </div>
            </div>
            <button
              onClick={() => dismissAlert(anomaly.id)}
              className={`${textColor} hover:opacity-70 transition flex-shrink-0`}
            >
              <X size={18} />
            </button>
          </div>
        );
      })}
    </div>
  );
}
