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
import { MaturityDashboard } from './components/MaturityDashboard';

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
        <Suspense fallback={<LoadingFallback />}>
          <MaturityDashboard />
        </Suspense>
      </div>
    </>
  );
}

export default VibeDashboard;
