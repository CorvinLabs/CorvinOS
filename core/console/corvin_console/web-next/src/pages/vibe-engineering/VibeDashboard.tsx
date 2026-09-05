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

import { Suspense } from 'react';
import { Loader2 } from 'lucide-react';
import LearningDashboard from './components/LearningDashboard';
import MethodDiscoveryDashboard from '@/panels/LearningDashboard';

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

export function VibeDashboard() {
  // LearningDashboard renders its own <h1> and page chrome; wrapping it in a
  // second header would show the title twice.
  return (
    <div data-testid="learning-dashboard-panel">
      <Suspense fallback={<LoadingFallback />}>
        <LearningDashboard />
      </Suspense>

      {/* Method Discovery 4-Tab Dashboard (ADR-0548 Phase 1) */}
      <div style={{ marginTop: '48px', borderTop: '1px solid #e5e7eb', paddingTop: '32px' }}>
        <Suspense fallback={<LoadingFallback />}>
          <MethodDiscoveryDashboard />
        </Suspense>
      </div>
    </div>
  );
}

export default VibeDashboard;
