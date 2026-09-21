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

import { Suspense, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { MaturityDashboard } from './components/MaturityDashboard';
import { MonitoringTab } from './tabs/MonitoringTab';
import { ModelsTab } from './tabs/ModelsTab';
import { LearningLoopsTab } from './tabs/LearningLoopsTab';

type TabType = 'maturity' | 'loops' | 'metrics' | 'models';

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

export function VibeDashboard() {
  const [activeTab, setActiveTab] = useState<TabType>('maturity');

  return (
    <div data-testid="vibe-dashboard-panel" className="min-h-screen bg-background text-foreground">
      {/* Tab Navigation */}
      <div className="border-b bg-muted/50">
        <div className="flex gap-4 px-6 py-4">
          {[
            { id: 'maturity' as TabType, label: 'Maturity Metrics' },
            { id: 'loops' as TabType, label: 'Learning Loops' },
            { id: 'metrics' as TabType, label: 'System Metrics' },
            { id: 'models' as TabType, label: 'Models' },
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
          {activeTab === 'loops' && <LearningLoopsTab />}
          {activeTab === 'metrics' && <MonitoringTab />}
          {activeTab === 'models' && <ModelsTab />}
        </Suspense>
      </div>

      {/* Footer */}
      <div className="border-t bg-muted/50 p-4 text-xs text-muted-foreground">
        System metrics • Model registry • Maturity dashboard • Learning loops — All
        endpoints PII-safe • Audit events moved to Audit &amp; Compliance • Last updated:{' '}
        {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}

export default VibeDashboard;
