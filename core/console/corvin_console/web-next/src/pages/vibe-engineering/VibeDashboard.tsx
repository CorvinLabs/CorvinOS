/**
 * Vibe Engineering Dashboard (ADR-0561 Phase 4, CONSOLE_REDESIGN_UNIFIED_CONCEPT)
 *
 * Unified 3-column dashboard replacing 5 separate panels.
 * Tabs: Dashboard (primary) + Brain Monitor + Context Intelligence + Learning Hub + Session Explorer
 * URL-synced via query param: /app/vibe-engineering?tab=brain-monitor
 */

import { useSearchParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { lazy, Suspense } from "react";
import { Loader2 } from "lucide-react";
import { useVibeData } from "./hooks/useVibeData";

// Lazy-load tab content (components already exist)
const BrainMonitorContent = lazy(() =>
  import("./components/BrainMonitor").then((m) => ({ default: m.BrainMonitor }))
);
const ContextIntelligenceContent = lazy(() =>
  import("./components/ContextIntelligence").then((m) => ({ default: m.ContextIntelligence }))
);
const LearningHubContent = lazy(() =>
  import("./components/LearningHub").then((m) => ({ default: m.LearningHub }))
);
const SessionExplorerContent = lazy(() =>
  import("./components/SessionExplorer").then((m) => ({ default: m.SessionExplorer }))
);

// Dashboard content: placeholder for now (can be enriched with widgets)
const DashboardContent = () => (
  <Card>
    <CardHeader>
      <CardTitle>Vibe Engineering Dashboard</CardTitle>
    </CardHeader>
    <CardContent>
      <p className="text-muted-foreground">
        Overview of system observability, context intelligence, learning metrics, and session state.
        Select a tab to explore specific areas.
      </p>
    </CardContent>
  </Card>
);

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

export function VibeDashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get("tab") || "dashboard";
  const vibeData = useVibeData(5000);

  // Show loading state while initial data is fetching
  if (vibeData.loading && Object.keys(vibeData).length < 10) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="h-8 w-8 animate-spin" />
          <p className="text-muted-foreground">Loading Vibe Engineering Dashboard...</p>
        </div>
      </div>
    );
  }

  const tabs = [
    { id: "dashboard", label: "Dashboard", icon: "📊" },
    { id: "brain-monitor", label: "Brain Monitor", icon: "🧠" },
    { id: "context-intelligence", label: "Context Intelligence", icon: "🧭" },
    { id: "learning-hub", label: "Learning Hub", icon: "💡" },
    { id: "session-explorer", label: "Session Explorer", icon: "🔍" },
  ];

  const handleTabChange = (tabId: string) => {
    setSearchParams({ tab: tabId });
  };

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold">Vibe Engineering</h1>
        <p className="text-muted-foreground text-sm mt-1">
          Unified dashboard for system observability, context management, and learning metrics
        </p>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
        <TabsList className="grid w-full grid-cols-5">
          {tabs.map((tab) => (
            <TabsTrigger key={tab.id} value={tab.id} className="text-xs sm:text-sm">
              <span className="hidden sm:inline">{tab.icon}</span>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {/* Tab Contents */}
        <TabsContent value="dashboard">
          <Suspense fallback={<LoadingFallback />}>
            <DashboardContent />
          </Suspense>
        </TabsContent>

        <TabsContent value="brain-monitor">
          <Suspense fallback={<LoadingFallback />}>
            {!vibeData.loading && !vibeData.error ? (
              <BrainMonitorContent />
            ) : (
              <LoadingFallback />
            )}
          </Suspense>
        </TabsContent>

        <TabsContent value="context-intelligence">
          <Suspense fallback={<LoadingFallback />}>
            {!vibeData.loading && !vibeData.error ? (
              <ContextIntelligenceContent data={vibeData} onQualityGateChange={() => {}} />
            ) : (
              <LoadingFallback />
            )}
          </Suspense>
        </TabsContent>

        <TabsContent value="learning-hub">
          <Suspense fallback={<LoadingFallback />}>
            {!vibeData.loading && !vibeData.error ? (
              <LearningHubContent data={vibeData} />
            ) : (
              <LoadingFallback />
            )}
          </Suspense>
        </TabsContent>

        <TabsContent value="session-explorer">
          <Suspense fallback={<LoadingFallback />}>
            <SessionExplorerContent />
          </Suspense>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default VibeDashboard;
