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
import { LicensingAuditTab } from './tabs/LicensingAuditTab';
import { MonitoringTab } from './tabs/MonitoringTab';
import { ModelsTab } from './tabs/ModelsTab';

type TabType = 'maturity' | 'audit' | 'metrics' | 'models';

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
            { id: 'audit' as TabType, label: 'Audit Events' },
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
          {activeTab === 'audit' && <LicensingAuditTab />}
          {activeTab === 'metrics' && <MonitoringTab />}
          {activeTab === 'models' && <ModelsTab />}
        </Suspense>
      </div>

      {/* Footer */}
      <div className="border-t bg-muted/50 p-4 text-xs text-muted-foreground">
        Phase 2 Features: Live audit events • System metrics • Model registry • Maturity dashboard
        — All endpoints PII-safe • Last updated: {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}

export default VibeDashboard;
