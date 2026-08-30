/**
 * MarketplaceTab - Integration wrapper for PluginCenterPage
 *
 * Wraps the MarketplacePanel with two sub-tabs:
 * 1. Browse: Discover and search marketplace plugins
 * 2. Installed: Show currently installed marketplace plugins (live-sync with PluginsPage)
 *
 * Phase 3: Added Installed Tab for live-sync state management.
 */

import React, { useState } from 'react'
import { MarketplacePanel } from '@/panels/marketplace'
import { InstalledTab } from './InstalledTab'

export const MarketplaceTab: React.FC = () => {
  const [view, setView] = useState<'browse' | 'installed'>('browse')

  return (
    <div className="mt-4 space-y-4">
      {/* Sub-tabs for Browse vs Installed */}
      <div className="flex gap-4 border-b border-slate-200 dark:border-slate-700">
        <button
          onClick={() => setView('browse')}
          className={`px-4 py-2 font-medium transition ${
            view === 'browse'
              ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600'
              : 'text-slate-600 dark:text-slate-400'
          }`}
          data-testid="marketplace-view-browse"
        >
          Browse
        </button>
        <button
          onClick={() => setView('installed')}
          className={`px-4 py-2 font-medium transition ${
            view === 'installed'
              ? 'text-blue-600 dark:text-blue-400 border-b-2 border-blue-600'
              : 'text-slate-600 dark:text-slate-400'
          }`}
          data-testid="marketplace-view-installed"
        >
          Installed
        </button>
      </div>

      {/* View content */}
      {view === 'browse' && <MarketplacePanel />}
      {view === 'installed' && <InstalledTab />}
    </div>
  )
}

export default MarketplaceTab
