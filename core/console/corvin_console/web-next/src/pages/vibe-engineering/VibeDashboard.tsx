/**
 * Vibe Engineering Dashboard v2.1 — Graph-Native Observability Hub
 *
 * ADR-0564 Phase 5: Complete redesign with:
 * - Audit Chain Graph visualization (Cytoscape.js)
 * - Full node inspection (GraphInspector)
 * - Immutable, hash-chained events
 * - Real-time freshness tracking
 * - GDPR/Compliance integration (LoM binding)
 *
 * Three main tabs:
 * 1. Graph View — Audit chain + decision tree visualization
 * 2. Graph Inspector — Detailed node inspection + verification
 * 3. Audit Timeline — Linear fallback when graph is too dense
 */

import { useSearchParams } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Suspense, useState, useCallback } from 'react';
import { Loader2, AlertCircle, Cloud } from 'lucide-react';
import { useAuditQuery } from './hooks/useAuditQuery';
import { AuditChainGraph } from './components/AuditChainGraph';
import { GraphInspector } from './components/GraphInspector';
import { AnyAuditEvent } from '@/types/audit-graph';
import LearningDashboard from './components/LearningDashboard';

const LoadingFallback = () => (
  <div className="flex justify-center py-12">
    <Loader2 className="h-6 w-6 animate-spin" />
  </div>
);

const ErrorFallback = ({ error }: { error: Error }) => (
  <Card className="border-red-300 bg-red-50">
    <CardHeader>
      <CardTitle className="flex items-center gap-2 text-red-900">
        <AlertCircle className="h-5 w-5" />
        Error Loading Audit Data
      </CardTitle>
    </CardHeader>
    <CardContent className="text-sm text-red-800">
      <p>{error.message}</p>
      <p className="mt-2 text-xs text-red-700">Showing cached snapshot if available.</p>
    </CardContent>
  </Card>
);

export function VibeDashboard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const activeTab = searchParams.get('tab') || 'graph';
  const [selectedEvent, setSelectedEvent] = useState<AnyAuditEvent | null>(null);

  // Query audit events (last hour, limit 100). The window is fixed ONCE per
  // mount: `filter` is part of the React Query key, and computing `since`
  // inline gave every render a new key → a new query → a new fetch → a
  // re-render, forever. On 2026-09-04 that was ~33 requests/s against the live
  // console, a spinner that never resolved and "Live • 0 events".
  const [auditFilter] = useState(() => ({
    since: new Date(Date.now() - 3600000).toISOString(),
    limit: 100,
  }));
  const auditQuery = useAuditQuery({
    filter: auditFilter,
  });

  const handleTabChange = (tabId: string) => {
    setSearchParams({ tab: tabId }, { replace: true });
  };

  const handleRefresh = useCallback(() => {
    auditQuery.refetch?.();
  }, [auditQuery.refetch]);

  const handleNodeSelected = useCallback((event: AnyAuditEvent) => {
    setSelectedEvent(event);
    handleTabChange('inspector');
  }, []);

  // Offline mode indicator
  const isOffline = auditQuery.isCached && auditQuery.data?.snapshotFreshness_ms;
  const freshness = auditQuery.data?.snapshotFreshness_ms ?? 0;

  return (
    <div className="flex flex-col gap-6 p-6">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-3xl font-bold">Vibe Engineering</h1>
          <p className="text-sm text-muted-foreground">
            Immutable audit trail visualization • Hash-chained events • ACP Skills integration
          </p>
        </div>

        {/* Status Indicator */}
        <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-4 py-2 text-xs">
          {isOffline ? (
            <>
              <Cloud className="h-4 w-4 text-amber-600" />
              <span className="text-amber-700">
                Offline mode • {freshness}ms ago
              </span>
            </>
          ) : (
            <>
              <div className="h-2 w-2 rounded-full bg-green-600" />
              <span className="text-green-700">Live • {auditQuery.data?.graph.metadata.nodeCount ?? 0} events</span>
            </>
          )}
        </div>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
        <TabsList className="grid w-full grid-cols-4">
          <TabsTrigger value="graph">📊 Graph View</TabsTrigger>
          <TabsTrigger value="inspector">🔍 Inspector</TabsTrigger>
          <TabsTrigger value="timeline">📋 Timeline</TabsTrigger>
          <TabsTrigger value="learning">📈 Learning</TabsTrigger>
        </TabsList>

        {/* Graph View Tab */}
        <TabsContent value="graph" className="space-y-4">
          {auditQuery.isLoading && !auditQuery.data && <LoadingFallback />}
          {auditQuery.error && !auditQuery.data && <ErrorFallback error={auditQuery.error} />}

          {auditQuery.data && (
            <Suspense fallback={<LoadingFallback />}>
              <AuditChainGraph
                graph={auditQuery.data.graph}
                isLoading={auditQuery.isLoading}
                onNodeSelected={handleNodeSelected}
                onRefresh={handleRefresh}
              />
            </Suspense>
          )}
        </TabsContent>

        {/* Inspector Tab */}
        <TabsContent value="inspector" className="space-y-4">
          {auditQuery.isLoading && !auditQuery.data && <LoadingFallback />}
          {auditQuery.error && !auditQuery.data && <ErrorFallback error={auditQuery.error} />}

          {auditQuery.data && (
            <div className="grid grid-cols-3 gap-4">
              {/* Inspector Panel (takes 2 columns) */}
              <div className="col-span-2">
                <Suspense fallback={<LoadingFallback />}>
                  <GraphInspector
                    event={selectedEvent}
                    graph={auditQuery.data.graph}
                    onEventSelect={handleNodeSelected}
                  />
                </Suspense>
              </div>

              {/* Mini Graph (1 column) */}
              <div>
                <Card className="h-full">
                  <CardHeader>
                    <CardTitle className="text-sm">Recent Events</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {auditQuery.data.events.slice(0, 10).map((event) => (
                      <button
                        key={event.id}
                        onClick={() => setSelectedEvent(event)}
                        className={`block w-full rounded px-2 py-1 text-left text-xs transition-colors ${
                          selectedEvent?.id === event.id
                            ? 'bg-blue-100 text-blue-900'
                            : 'hover:bg-muted'
                        }`}
                      >
                        <p className="font-mono font-semibold">{event.type.substring(0, 8)}</p>
                        <p className="text-muted-foreground">
                          {new Date(event.timestamp).toLocaleTimeString()}
                        </p>
                      </button>
                    ))}
                  </CardContent>
                </Card>
              </div>
            </div>
          )}
        </TabsContent>

        {/* Timeline Tab (Linear Fallback) */}
        <TabsContent value="timeline" className="space-y-4">
          {auditQuery.isLoading && !auditQuery.data && <LoadingFallback />}
          {auditQuery.error && !auditQuery.data && <ErrorFallback error={auditQuery.error} />}

          {auditQuery.data && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Audit Timeline</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 max-h-[600px] overflow-y-auto">
                {auditQuery.data.events.map((event) => (
                  <button
                    key={event.id}
                    onClick={() => {
                      setSelectedEvent(event);
                      handleTabChange('inspector');
                    }}
                    className="block w-full rounded-lg border border-border bg-card p-3 text-left hover:bg-muted"
                  >
                    <div className="flex items-start justify-between">
                      <div>
                        <p className="font-semibold text-sm">{event.type.replace('_', ' ')}</p>
                        <p className="text-xs text-muted-foreground">
                          {new Date(event.timestamp).toLocaleString()}
                        </p>
                      </div>
                      <span className="text-xs font-mono text-muted-foreground">
                        {event.id.substring(0, 12)}...
                      </span>
                    </div>

                    {/* Event-specific info */}
                    {event.type === 'skill_executed' && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Skill: {(event as any).skill_id}
                      </p>
                    )}
                    {event.type === 'learning_event' && (
                      <p className="mt-1 text-xs text-muted-foreground">
                        Feedback: {(event as any).event_type} {(event as any).confidence_delta && `(Δ ${(event as any).confidence_delta > 0 ? '+' : ''}${(event as any).confidence_delta.toFixed(3)})`}
                      </p>
                    )}
                  </button>
                ))}
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* Learning Dashboard Tab (ADR-0321) */}
        <TabsContent value="learning" className="space-y-4">
          <Suspense fallback={<LoadingFallback />}>
            <LearningDashboard />
          </Suspense>
        </TabsContent>
      </Tabs>

      {/* Footer Info */}
      <div className="rounded-lg border border-border bg-muted/50 p-4 text-xs text-muted-foreground">
        <p>
          <strong>Audit Trail:</strong> {auditQuery.data?.graph.metadata.chainHeight ?? '—'} total
          events • <strong>Freshness:</strong> {freshness}ms •{' '}
          <strong>Status:</strong> {auditQuery.isFetching ? 'updating...' : 'ready'}
        </p>
        <p className="mt-1">
          All events are immutable, hash-chained, and bound to source code (ADR-0537 LoM). Zero
          PII, GDPR-compliant.
        </p>
      </div>
    </div>
  );
}

export default VibeDashboard;
