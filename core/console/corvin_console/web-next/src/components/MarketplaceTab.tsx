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
import { cn } from '@/lib/utils'
import { MarketplacePanel } from '@/panels/marketplace'
import { InstalledTab } from './InstalledTab'

export const MarketplaceTab: React.FC = () => {
  const [view, setView] = useState<'browse' | 'installed'>('browse')

  return (
    <div className="mt-4 space-y-4">
      {/* Sub-tabs for Browse vs Installed */}
      <div className="flex gap-4 border-b border-border">
        <button
          onClick={() => setView('browse')}
          className={cn(
            'px-4 py-2 font-medium transition',
            view === 'browse'
              ? 'text-accent border-b-2 border-accent'
              : 'text-muted-foreground hover:text-foreground',
          )}
          data-testid="marketplace-view-browse"
        >
          Browse
        </button>
        <button
          onClick={() => setView('installed')}
          className={cn(
            'px-4 py-2 font-medium transition',
            view === 'installed'
              ? 'text-accent border-b-2 border-accent'
              : 'text-muted-foreground hover:text-foreground',
          )}
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
