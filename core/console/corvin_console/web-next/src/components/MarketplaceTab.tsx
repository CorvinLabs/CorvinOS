/**
 * MarketplaceTab - Integration wrapper for PluginCenterPage
 *
 * Wraps the MarketplacePanel to work within the tab-based PluginCenterPage layout.
 * Manages state sync between the marketplace (browse) and plugins (installed) views.
 */

import React from 'react'
import { MarketplacePanel } from '@/panels/marketplace'

export const MarketplaceTab: React.FC = () => {
  return (
    <div className="mt-4">
      <MarketplacePanel />
    </div>
  )
}

export default MarketplaceTab
