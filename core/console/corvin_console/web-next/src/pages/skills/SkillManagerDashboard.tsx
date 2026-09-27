/**
 * Skill Manager Dashboard — Phase 5 K=3 Implementation (2026-09-27)
 *
 * Tab-based skill management interface following ADR-2080 Vibe Dashboard pattern.
 * Tabs: Installed | Available | Upload
 * State: Shared InstallationProgress (visible across all tabs)
 * Auth: Admin-only gates (placeholder for ADR-0007 RBAC)
 */

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { SkillManagerProvider, useSkillManager } from './SkillManagerContext';
import { InstalledSkillsTab } from './tabs/InstalledSkillsTab';
import { AvailableSkillsTab } from './tabs/AvailableSkillsTab';
import { SkillUploadTab } from './tabs/SkillUploadTab';
import { InstallationProgress } from './components/InstallationProgress';

type TabType = 'installed' | 'available' | 'upload';
const TAB_IDS: readonly TabType[] = ['installed', 'available', 'upload'] as const;
const DEFAULT_TAB: TabType = 'installed';

const isTabId = (v: string | null): v is TabType => v !== null && (TAB_IDS as readonly string[]).includes(v);

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

interface Capabilities {
  user?: {
    is_admin?: boolean;
  };
}

function SkillManagerContent() {
  const { installation } = useSkillManager();
  const [params, setParams] = useSearchParams();
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [capabilitiesLoading, setCapabilitiesLoading] = useState(true);

  const raw = params.get('tab');
  const activeTab: TabType = isTabId(raw) ? raw : DEFAULT_TAB;
  const canInstall = capabilities?.user?.is_admin ?? false;

  // Sync tab param to state (canonicalize missing/unknown tab)
  useEffect(() => {
    if (raw !== activeTab) {
      const next = new URLSearchParams(params);
      next.set('tab', activeTab);
      setParams(next, { replace: true });
    }
  }, [raw, activeTab, params, setParams]);

  // Fetch capabilities on mount
  useEffect(() => {
    setCapabilitiesLoading(true);
    fetch('/v1/console/capabilities/manifest')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status}`);
        return r.json();
      })
      .then((cap) => setCapabilities(cap))
      .catch((err) => {
        console.error('Failed to load capabilities:', err);
        setCapabilities(null);
      })
      .finally(() => setCapabilitiesLoading(false));
  }, []);

  const setActiveTab = useCallback(
    (tab: TabType) => {
      const next = new URLSearchParams(params);
      next.set('tab', tab);
      setParams(next);
    },
    [params, setParams],
  );

  return (
    <div data-testid="skill-manager-dashboard" className="min-h-screen bg-background text-foreground">
      {/* Installation progress banner (visible across all tabs) */}
      {installation.taskId && <InstallationProgress />}

      {/* Tab navigation */}
      <div className="border-b bg-muted/50">
        <div className="flex gap-4 px-6 py-4">
          {[
            { id: 'installed' as TabType, label: 'Installed' },
            { id: 'available' as TabType, label: 'Available', disabled: !canInstall },
            { id: 'upload' as TabType, label: 'Upload', disabled: !canInstall },
          ].map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              disabled={tab.disabled}
              className={`px-4 py-2 rounded font-medium text-sm transition-all ${
                activeTab === tab.id
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted hover:bg-muted/80 disabled:opacity-50 disabled:cursor-not-allowed'
              }`}
              title={tab.disabled ? 'Admin only' : undefined}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="p-6">
        {capabilitiesLoading ? (
          <LoadingFallback />
        ) : (
          <Suspense fallback={<LoadingFallback />}>
            {activeTab === 'installed' && <InstalledSkillsTab canUninstall={canInstall} />}
            {activeTab === 'available' && canInstall && <AvailableSkillsTab />}
            {activeTab === 'upload' && canInstall && <SkillUploadTab />}
            {activeTab !== 'installed' && !canInstall && (
              <div className="text-center py-12 text-muted-foreground">
                Admin access required to manage skills.
              </div>
            )}
          </Suspense>
        )}
      </div>
    </div>
  );
}

export function SkillManagerDashboard() {
  return (
    <SkillManagerProvider>
      <SkillManagerContent />
    </SkillManagerProvider>
  );
}
