/**
 * Learning Dashboard — the single panel of the Vibe Engineering nav group.
 *
 * History, so the next edit does not "restore" something the operator removed:
 * this route used to be a tabbed hub. It carried Graph View, Inspector and
 * Timeline over the audit chain (ADR-0564) plus a Learning tab (ADR-0321). On
 * 2026-09-05 the operator asked for the Learning view alone — the three audit
 * tabs are gone and the page is named after what it shows.
 *
 * The route id stays `vibe-engineering` on purpose: it is what the sidebar, the
 * panel registry, the backend capability manifest and every existing bookmark
 * address. Only the visible name changed.
 *
 * The audit-graph pieces (`components/AuditChainGraph`, `components/GraphInspector`,
 * `hooks/useAuditQuery`) are deliberately left in the tree, unmounted — a
 * parallel line of work builds on them. They are not dead code to clean up
 * without asking.
 */

import { Suspense, useState } from 'react';
import { Loader2 } from 'lucide-react';
import MaturityDashboard from './components/LearningDashboard';
import MethodDiscoveryDashboard from '@/panels/LearningDashboard';

// Same PALETTE as LearningDashboard & Maturity Metrics
const PALETTE = {
  surface: {
    dark: '#0D1117',
    card: '#161B22',
    border: '#30363D',
    text: '#C9D1D9',
    muted: '#8B949E',
  },
};

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

export function VibeDashboard() {
  const [activeTab, setActiveTab] = useState<'maturity' | 'summary' | 'patterns' | 'config' | 'preferences'>('maturity');

  return (
    <>
      <style>{`
        [data-testid="learning-dashboard-panel"] {
          background-color: #0D1117 !important;
        }
        /* Override any parent containers */
        [data-testid="learning-dashboard-panel"] {
          --tw-bg-opacity: 1;
          background-color: rgb(13, 17, 23 / var(--tw-bg-opacity)) !important;
        }
      `}</style>
    <div data-testid="learning-dashboard-panel" style={{ backgroundColor: PALETTE.surface.dark, minHeight: '100vh', color: PALETTE.surface.text }} className="!bg-[#0D1117] dark:!bg-[#0D1117]">
      {/* Tab Navigation */}
      <div style={{ borderBottom: `1px solid ${PALETTE.surface.border}`, marginBottom: '24px', backgroundColor: PALETTE.surface.dark }}>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          {[
            { id: 'maturity', label: '📊 Maturity Metrics', icon: '📊' },
            { id: 'summary', label: '📝 Summary', icon: '📝' },
            { id: 'patterns', label: '📊 Patterns', icon: '📊' },
            { id: 'config', label: '⚙️ Config', icon: '⚙️' },
            { id: 'preferences', label: '👤 Preferences', icon: '👤' },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id as any)}
              style={{
                padding: '12px 16px',
                fontWeight: 500,
                fontSize: '14px',
                whiteSpace: 'nowrap',
                transition: 'all 0.2s',
                borderBottom: activeTab === tab.id ? `2px solid #4ECDC4` : 'none',
                color: activeTab === tab.id ? '#4ECDC4' : PALETTE.surface.muted,
                background: 'none',
                border: 'none',
                cursor: 'pointer',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab Content */}
      {activeTab === 'maturity' && (
        <Suspense fallback={<LoadingFallback />}>
          <MaturityDashboard />
        </Suspense>
      )}

      {activeTab !== 'maturity' && (
        <Suspense fallback={<LoadingFallback />}>
          <MethodDiscoveryDashboard
            activeTab={activeTab as 'summary' | 'patterns' | 'config' | 'preferences'}
            hideTabNavigation={true}
          />
        </Suspense>
      )}
    </div>
    </>
  );
}

export default VibeDashboard;
