/**
 * StandardPanelLayout — unified panel structure for T3.3 console unification
 *
 * Provides consistent:
 * - Header with title + subtitle + refresh button
 * - Tab navigation (if children provided)
 * - Loading state + error boundary
 * - Telemetry hooks for page tracking
 * - Responsive grid layout
 *
 * Usage:
 *   <StandardPanelLayout
 *     title="License Hub"
 *     subtitle="Subscription, audit, and settings"
 *     tabs={[
 *       { label: "Overview", id: "overview" },
 *       { label: "Audit", id: "audit" },
 *     ]}
 *     activeTab={activeTab}
 *     onTabChange={setActiveTab}
 *     isLoading={loading}
 *   >
 *     <TabContent tab="overview">...</TabContent>
 *     <TabContent tab="audit">...</TabContent>
 *   </StandardPanelLayout>
 */

import React, { useEffect } from "react";
import { Loader2, RefreshCw, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface PanelTab {
  id: string;
  label: string;
  icon?: React.ReactNode;
  badge?: string | number;
}

export interface StandardPanelLayoutProps {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  tabs?: PanelTab[];
  activeTab?: string;
  onTabChange?: (tabId: string) => void;
  isLoading?: boolean;
  error?: string | null;
  onRefresh?: () => void | Promise<void>;
  children?: React.ReactNode;
  className?: string;
  headerExtra?: React.ReactNode;
}

export function StandardPanelLayout({
  title,
  subtitle,
  icon,
  tabs,
  activeTab,
  onTabChange,
  isLoading = false,
  error = null,
  onRefresh,
  children,
  className,
  headerExtra,
}: StandardPanelLayoutProps) {
  // Track page view for telemetry
  useEffect(() => {
    // Emit page view event (implementation depends on telemetry client)
    const event = new CustomEvent("panel:view", {
      detail: { panel: title, timestamp: new Date().toISOString() },
    });
    window.dispatchEvent(event);
  }, [title]);

  // Emit tab change event
  useEffect(() => {
    if (activeTab) {
      const event = new CustomEvent("panel:tab-change", {
        detail: { panel: title, tab: activeTab, timestamp: new Date().toISOString() },
      });
      window.dispatchEvent(event);
    }
  }, [activeTab, title]);

  const handleRefresh = async () => {
    if (onRefresh) {
      await onRefresh();
      const event = new CustomEvent("panel:refresh", {
        detail: { panel: title, timestamp: new Date().toISOString() },
      });
      window.dispatchEvent(event);
    }
  };

  return (
    <div className={cn("space-y-6", className)}>
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {icon && <span className="text-lg">{icon}</span>}
            <div>
              <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
              {subtitle && (
                <p className="text-sm text-muted-foreground">{subtitle}</p>
              )}
            </div>
          </div>
          <div className="flex items-center gap-2">
            {onRefresh && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefresh}
                disabled={isLoading}
                aria-label="Refresh data"
              >
                <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} />
              </Button>
            )}
            {headerExtra}
          </div>
        </div>
      </div>

      {/* Error State */}
      {error && (
        <Card className="border-red-200 bg-red-50 p-4">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-red-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium text-red-900">{title} Error</p>
              <p className="text-sm text-red-800 mt-1">{error}</p>
            </div>
          </div>
        </Card>
      )}

      {/* Tabs */}
      {tabs && tabs.length > 0 && (
        <div className="border-b">
          <div className="flex gap-6">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => onTabChange?.(tab.id)}
                className={cn(
                  "px-1 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap",
                  activeTab === tab.id
                    ? "border-foreground text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground hover:border-muted-foreground"
                )}
              >
                <span className="flex items-center gap-2">
                  {tab.icon}
                  {tab.label}
                  {tab.badge && (
                    <span className="ml-1 text-xs bg-muted px-2 py-0.5 rounded">
                      {tab.badge}
                    </span>
                  )}
                </span>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="grid min-h-96 place-items-center">
          <div className="flex flex-col items-center gap-3">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Loading {title.toLowerCase()}…</p>
          </div>
        </div>
      ) : error ? (
        <Card className="p-8 text-center">
          <p className="text-sm text-muted-foreground">
            Failed to load {title.toLowerCase()}. Please try again.
          </p>
        </Card>
      ) : (
        <div className="space-y-4">{children}</div>
      )}
    </div>
  );
}

/**
 * TabContent — helper to conditionally render content based on active tab
 */
export interface TabContentProps {
  tab: string;
  activeTab?: string;
  children: React.ReactNode;
}

export function TabContent({ tab, activeTab, children }: TabContentProps) {
  if (activeTab !== tab) return null;
  return <>{children}</>;
}
