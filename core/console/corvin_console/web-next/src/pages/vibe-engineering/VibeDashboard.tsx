/**
 * Vibe Engineering Dashboard — Phase 2 Live Data Wiring (2026-09-15)
 *
 * Integrated Maturity Metrics + Live Endpoints (ADR-0728)
 * - Monitoring: /v1/console/v1/monitoring/metrics (HealthMonitor + alerts)
 * - Models: /v1/console/v1/models/available (Engine registry)
 *
 * History: This route used to be a tabbed hub (Graph View, Inspector, Timeline, Learning).
 * On 2026-09-05 the operator requested the Learning view alone; on 2026-09-15 the
 * Phase 2 live-data tabs above replaced it (Maturity Metrics · Audit Events ·
 * System Metrics · Models). On 2026-09-20 the "Audit Events" tab moved to
 * /app/compliance as its "Learning events" section — the same
 * /v1/console/v1/licensing/audit-events store also backed a standalone
 * Licensing Audit panel, so one set of records had two homes and neither was
 * the compliance page an auditor opens. The route id stays `vibe-engineering`
 * for bookmark
 * stability. This directory is the page: a sibling FILE pages/vibe-engineering.tsx
 * wins over it silently (happened 2026-08-27 and 2026-09-17) — never add one.
 */

import { Suspense, useCallback, useEffect } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { MaturityDashboard } from './components/MaturityDashboard';
import { MonitoringTab } from './tabs/MonitoringTab';
import { LearningLoopsTab } from './tabs/LearningLoopsTab';
import { LicensingAuditTab } from './tabs/LicensingAuditTab';
import { ModelSelectionTab } from './tabs/ModelSelectionTab';
import { LearningLoopIntegrationTab } from './tabs/LearningLoopIntegrationTab';

type TabType = 'maturity' | 'loops' | 'metrics' | 'licensing' | 'models' | 'learning-integration';

const TAB_IDS: readonly TabType[] = ['maturity', 'loops', 'metrics', 'licensing', 'models', 'learning-integration'] as const;
const DEFAULT_TAB: TabType = 'maturity';
const isTabId = (v: string | null): v is TabType =>
  v !== null && (TAB_IDS as readonly string[]).includes(v);

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

export function VibeDashboard() {
  // Tab ↔ URL, the same contract the Models and Marketplace panels use: the tab
  // is `?tab=` so it is deep-linkable and a switch is a PUSH (browser back
  // returns to the previous tab). /app/learning-loops redirects to ?tab=loops,
  // which only lands on the right tab because the parameter is read here — a
  // redirect to a query string nothing consumes silently opens the default tab.
  const [params, setParams] = useSearchParams();
  const raw = params.get('tab');
  const activeTab: TabType = isTabId(raw) ? raw : DEFAULT_TAB;
  // The retired "Models" tab read the SAME /v1/models/available the Models
  // panel's Catalog tab reads, minus its filter, pins and "use for" action —
  // a poorer second view of one registry (retired 2026-09-21). Its links go to
  // the panel that owns the catalogue. Falling through to isTabId() instead
  // would drop such a link on Maturity Metrics with no explanation.
  const retiredToModelsPanel = raw === 'models';

  // Missing / unknown tab → canonical URL, without a history entry.
  useEffect(() => {
    if (retiredToModelsPanel) return; // redirected below; don't rewrite first
    if (raw !== activeTab) {
      const next = new URLSearchParams(params);
      next.set('tab', activeTab);
      setParams(next, { replace: true });
    }
  }, [raw, activeTab, params, setParams, retiredToModelsPanel]);

  const setActiveTab = useCallback(
    (tab: TabType) => {
      const next = new URLSearchParams(params);
      next.set('tab', tab);
      setParams(next); // PUSH — back returns to the previous tab
    },
    [params, setParams],
  );

  if (retiredToModelsPanel) {
    return <Navigate to="/app/models?tab=catalog" replace />;
  }

  return (
    <div data-testid="vibe-dashboard-panel" className="min-h-screen bg-background text-foreground">
      {/* Tab Navigation */}
      <div className="border-b bg-muted/50">
        <div className="flex gap-4 px-6 py-4">
          {[
            { id: 'maturity' as TabType, label: 'Maturity Metrics' },
            { id: 'licensing' as TabType, label: 'Licensing Audit' },
            { id: 'metrics' as TabType, label: 'System Metrics' },
            { id: 'loops' as TabType, label: 'Learning Loops' },
            { id: 'models' as TabType, label: 'Model Selection' },
            { id: 'learning-integration' as TabType, label: 'Learning Events' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 rounded font-medium text-sm transition-all ${
                activeTab === tab.id
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-muted/80'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab Content */}
      <div className="min-h-screen">
        <Suspense fallback={<LoadingFallback />}>
          {activeTab === 'maturity' && <MaturityDashboard />}
          {activeTab === 'licensing' && <LicensingAuditTab />}
          {activeTab === 'metrics' && <MonitoringTab />}
          {activeTab === 'loops' && <LearningLoopsTab />}
          {activeTab === 'models' && <ModelSelectionTab />}
          {activeTab === 'learning-integration' && <LearningLoopIntegrationTab />}
        </Suspense>
      </div>

      {/* Footer */}
      <div className="border-t bg-muted/50 p-4 text-xs text-muted-foreground">
        System metrics • Maturity dashboard • Learning loops — All endpoints PII-safe •
        Audit events moved to Audit &amp; Compliance • Model catalogue moved to Models •
        Last updated:{' '}
        {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}

export default VibeDashboard;
