/**
 * Vibe Engineering Dashboard — Phase 2 Live Data Wiring (2026-09-15)
 *
 * Integrated Maturity Metrics + Live Endpoints (ADR-0728)
 * - Licensing: /v1/licensing/audit-events (EventStore + PII filtering)
 * - Monitoring: /v1/monitoring/metrics (HealthMonitor + alerts)
 * - Models: /v1/models/available (Engine registry)
 *
 * History: This route used to be a tabbed hub (Graph View, Inspector, Timeline, Learning).
 * On 2026-09-05 the operator requested Learning view alone — tabs are gone.
 * The route id stays `vibe-engineering` for bookmark stability.
 */

import { Suspense, useEffect, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { MaturityDashboard } from './components/MaturityDashboard';

interface Phase2Context {
  auditEvents?: any[];
  metrics?: any[];
  models?: any[];
  timestamp?: string;
}

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

/**
 * Fetch Phase 2 live data from feature endpoints.
 * Falls back gracefully if endpoints unavailable (demo mode).
 */
async function fetchPhase2Context(): Promise<Phase2Context> {
  try {
    const [auditRes, metricsRes, modelsRes] = await Promise.all([
      fetch('/v1/licensing/audit-events?limit=50').catch(() => null),
      fetch('/v1/monitoring/metrics?range=1h').catch(() => null),
      fetch('/v1/models/available').catch(() => null),
    ]);

    return {
      auditEvents: auditRes?.ok ? (await auditRes.json()).events : [],
      metrics: metricsRes?.ok ? (await metricsRes.json()).metrics : [],
      models: modelsRes?.ok ? (await modelsRes.json()).models : [],
      timestamp: new Date().toISOString(),
    };
  } catch (e) {
    console.warn('Phase 2 Context Load Failed:', e);
    return { timestamp: new Date().toISOString() };
  }
}

export function VibeDashboard() {
  const [phase2Context, setPhase2Context] = useState<Phase2Context | null>(null);

  useEffect(() => {
    // Load Phase 2 live data on mount + periodic refresh (5min)
    const load = () => fetchPhase2Context().then(setPhase2Context);
    load();
    const interval = setInterval(load, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div
      data-testid="vibe-dashboard-panel"
      className="min-h-screen bg-background text-foreground"
      data-phase2-context={phase2Context ? 'live' : 'fallback'}
    >
      <Suspense fallback={<LoadingFallback />}>
        <MaturityDashboard />
      </Suspense>
      {phase2Context && (
        <div className="text-xs text-muted-foreground p-4 border-t">
          Phase 2 Data: {phase2Context.auditEvents?.length || 0} audit events ·{' '}
          {phase2Context.metrics?.length || 0} metrics · {phase2Context.models?.length || 0} models
          · Last sync: {new Date(phase2Context.timestamp!).toLocaleTimeString()}
        </div>
      )}
    </div>
  );
}

export default VibeDashboard;
