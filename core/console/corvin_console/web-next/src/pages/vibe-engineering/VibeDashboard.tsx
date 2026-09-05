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

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

export function VibeDashboard() {
  const [activeTab, setActiveTab] = useState<'maturity' | 'summary' | 'patterns' | 'config' | 'preferences'>('maturity');

  return (
    <div data-testid="learning-dashboard-panel">
      {/* Tab Navigation */}
      <div className="border-b border-slate-200 dark:border-slate-700 mb-6">
        <div className="flex gap-2 flex-wrap">
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
              className={`px-4 py-3 font-medium text-sm whitespace-nowrap transition border-b-2 ${
                activeTab === tab.id
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-slate-600 dark:text-slate-400 hover:text-slate-900 dark:hover:text-slate-200'
              }`}
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
  );
}

export default VibeDashboard;
