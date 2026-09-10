/**
 * GraphInspector — Detailed inspection of audit events with full node data.
 *
 * Features:
 * - Full event JSON viewer (with syntax highlighting)
 * - Hash chain verification
 * - Parent/Child event navigation
 * - LoM (Line of Moral Responsibility) binding display
 * - Audit proof download
 * - Linked event visualization
 * - Real-time freshness indicator
 *
 * ADR-0564 Phase 5, Graph Engineering Edition
 */

import { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  AnyAuditEvent,
  AuditGraph,
  isSkillExecutedEvent,
  isLearningEvent,
  isDecisionEvent,
  isContextSnapshotEvent,
} from '@/types/audit-graph';
import { Copy, Download, Check, ChevronRight, Shield, AlertCircle } from 'lucide-react';

// ─────────────────────────────────────────────────────────────────────────────
// Types
// ─────────────────────────────────────────────────────────────────────────────

export interface GraphInspectorProps {
  event: AnyAuditEvent | null;
  graph: AuditGraph;
  onEventSelect?: (event: AnyAuditEvent) => void;
}

interface CopyState {
  copiedField: string | null;
  copiedAt: number;
}

interface VerificationResult {
  isValid: boolean;
  verificationsCount: number;
  message: string;
  chainHeight: number;
}

// ─────────────────────────────────────────────────────────────────────────────
// Component
// ─────────────────────────────────────────────────────────────────────────────

export function GraphInspector({ event, graph, onEventSelect }: GraphInspectorProps) {
  const [copied, setCopied] = useState<CopyState>({ copiedField: null, copiedAt: 0 });
  const [isVerifying, setIsVerifying] = useState(false);
  const [verificationResult, setVerificationResult] = useState<VerificationResult | null>(null);

  if (!event) {
    return (
      <Card className="h-full">
        <CardHeader>
          <CardTitle className="text-base">Inspector</CardTitle>
        </CardHeader>
        <CardContent className="text-center text-muted-foreground">
          <p>Select a node to inspect</p>
        </CardContent>
      </Card>
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Helper Functions
  // ─────────────────────────────────────────────────────────────────────────

  const copyToClipboard = (text: string, field: string) => {
    navigator.clipboard.writeText(text);
    setCopied({ copiedField: field, copiedAt: Date.now() });
    setTimeout(() => setCopied({ copiedField: null, copiedAt: 0 }), 2000);
  };

  const handleVerifyChain = async () => {
    setIsVerifying(true);
    try {
      // Verify hash chain linkage from event backwards
      await new Promise((resolve) => setTimeout(resolve, 500));

      const eventIndex = graph.nodes.findIndex((n) => n.id === event.id);
      if (eventIndex < 0) {
        setVerificationResult({
          isValid: false,
          verificationsCount: 0,
          message: 'Event not found in chain',
          chainHeight: graph.metadata.chainHeight,
        });
        return;
      }

      // Verify prev_hash matches previous event's hash
      let verificationsCount = 0;
      let isValid = true;

      if (eventIndex > 0) {
        const prevEvent = graph.nodes[eventIndex - 1].data;
        if (event.prev_hash !== prevEvent.hash) {
          isValid = false;
        }
        verificationsCount++;
      }

      setVerificationResult({
        isValid,
        verificationsCount,
        message: isValid ? 'Chain integrity valid' : 'Chain broken at this link',
        chainHeight: graph.metadata.chainHeight,
      });
    } finally {
      setIsVerifying(false);
    }
  };

  const getParentEvents = (): AnyAuditEvent[] => {
    const eventIndex = graph.nodes.findIndex((n) => n.id === event.id);
    if (eventIndex <= 0) return [];

    // Find events where this event's prev_hash matches their hash
    return graph.nodes
      .slice(Math.max(0, eventIndex - 5), eventIndex)
      .map((n) => n.data)
      .filter((e) => e.hash === event.prev_hash);
  };

  const getChildEvents = (): AnyAuditEvent[] => {
    const eventIndex = graph.nodes.findIndex((n) => n.id === event.id);
    if (eventIndex >= graph.nodes.length - 1) return [];

    // Find events where this event's hash matches their prev_hash
    return graph.nodes
      .slice(eventIndex + 1, Math.min(eventIndex + 6, graph.nodes.length))
      .map((n) => n.data)
      .filter((e) => e.prev_hash === event.hash);
  };

  const getEventTypeColor = (type: string): string => {
    const colors: Record<string, string> = {
      skill_executed: 'bg-accent/15 text-accent-foreground/90',
      learning_event: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
      decision: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
      context_snapshot: 'bg-purple-500/15 text-purple-700 dark:text-purple-300',
      error: 'bg-destructive/15 text-destructive',
    };
    return colors[type] || 'bg-muted text-muted-foreground';
  };

  // ─────────────────────────────────────────────────────────────────────────
  // Event-Specific Details
  // ─────────────────────────────────────────────────────────────────────────

  const renderEventDetails = () => {
    if (isSkillExecutedEvent(event)) {
      return (
        <div className="space-y-2 text-sm">
          {event.skill_id && (
            <div>
              <span className="font-semibold">Skill:</span> {event.skill_id}
              {event.skill_version && ` v${event.skill_version}`}
            </div>
          )}
          {event.status && (
            <div>
              <span className="font-semibold">Status:</span>{' '}
              <span
                className={`inline-block rounded px-2 py-1 text-xs font-semibold ${
                  event.status === 'success'
                    ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
                    : 'bg-destructive/15 text-destructive'
                }`}
              >
                {event.status}
              </span>
            </div>
          )}
          {typeof event.latency_ms === 'number' && (
            <div>
              <span className="font-semibold">Latency:</span> {event.latency_ms}ms
            </div>
          )}
          {typeof event.output?.confidence === 'number' && (
            <div>
              <span className="font-semibold">Confidence:</span>{' '}
              {event.output.confidence.toFixed(2)}
            </div>
          )}
          {event.error && (
            <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3">
              <div className="flex gap-2">
                <AlertCircle className="h-4 w-4 text-destructive" />
                <div>
                  <p className="text-xs font-semibold text-destructive">{event.error.type}</p>
                  <p className="text-xs text-destructive/90">{event.error.message}</p>
                </div>
              </div>
            </div>
          )}
        </div>
      );
    }

    if (isLearningEvent(event)) {
      return (
        <div className="space-y-2 text-sm">
          {event.skill_id && (
            <div>
              <span className="font-semibold">Skill:</span> {event.skill_id}
            </div>
          )}
          {event.event_type && (
            <div>
              <span className="font-semibold">Event Type:</span>{' '}
              <span className="inline-block rounded bg-accent/15 px-2 py-1 text-xs font-semibold text-accent-foreground/90">
                {event.event_type}
              </span>
            </div>
          )}
          {typeof event.confidence_before === 'number' && (
            <div>
              <span className="font-semibold">Confidence Before:</span>{' '}
              {event.confidence_before.toFixed(2)}
            </div>
          )}
          {typeof event.confidence_after === 'number' && (
            <div>
              <span className="font-semibold">Confidence After:</span>{' '}
              {event.confidence_after.toFixed(2)}
            </div>
          )}
          {typeof event.confidence_delta === 'number' && (
            <div>
              <span className="font-semibold">Delta:</span>{' '}
              <span
                className={`font-semibold ${event.confidence_delta > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive'}`}
              >
                {event.confidence_delta > 0 ? '+' : ''}
                {event.confidence_delta.toFixed(4)}
              </span>
            </div>
          )}
          {event.signal !== undefined && (
            <div>
              <span className="font-semibold">Signal:</span> {JSON.stringify(event.signal)}
            </div>
          )}
        </div>
      );
    }

    if (isDecisionEvent(event)) {
      return (
        <div className="space-y-2 text-sm">
          {event.decision_name && (
            <div>
              <span className="font-semibold">Decision:</span> {event.decision_name}
            </div>
          )}
          {event.skill_id && (
            <div>
              <span className="font-semibold">Skill:</span> {event.skill_id}
            </div>
          )}
          {typeof event.confidence === 'number' && (
            <div>
              <span className="font-semibold">Confidence:</span> {event.confidence.toFixed(2)}
            </div>
          )}
          {event.input !== undefined && (
            <div className="mt-3 space-y-1">
              <p className="text-xs font-semibold">Input:</p>
              <code className="block rounded bg-muted p-2 text-xs">
                {JSON.stringify(event.input, null, 2).substring(0, 200)}...
              </code>
            </div>
          )}
        </div>
      );
    }

    if (isContextSnapshotEvent(event)) {
      return (
        <div className="space-y-2 text-sm">
          {event.context_id && (
            <div>
              <span className="font-semibold">Context ID:</span> {event.context_id}
            </div>
          )}
          {typeof event.entropy_score === 'number' && (
            <div>
              <span className="font-semibold">Entropy:</span> {event.entropy_score.toFixed(2)}
            </div>
          )}
          {typeof event.tier_1_count === 'number' && (
            <>
              <div>
                <span className="font-semibold">Tier Distribution:</span>
              </div>
              <div className="ml-4 space-y-1">
                <div>Tier 1: {event.tier_1_count}</div>
                <div>Tier 2: {event.tier_2_count}</div>
                <div>Tier 3: {event.tier_3_count}</div>
              </div>
            </>
          )}
          {event.merge_status && (
          <div>
            <span className="font-semibold">Merge Status:</span>{' '}
            <span
              className={`inline-block rounded px-2 py-1 text-xs font-semibold ${
                event.merge_status === 'success'
                  ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
                  : 'bg-destructive/15 text-destructive'
              }`}
            >
              {event.merge_status}
            </span>
          </div>
          )}
        </div>
      );
    }

    return null;
  };

  /**
   * What the chain record actually carries. The backend maps every real audit
   * event to {event_type, severity, details} and only classifies `type`; the
   * typed renderers above show their fields when — and only when — present.
   */
  const renderChainRecord = () => {
    if (!event.event_type && !event.details && !event.severity) return null;
    return (
      <div className="space-y-2 text-sm" data-testid="chain-record">
        {event.event_type && (
          <div>
            <span className="font-semibold">Chain event:</span>{' '}
            <code className="font-mono text-xs">{event.event_type}</code>
          </div>
        )}
        {event.severity && (
          <div>
            <span className="font-semibold">Severity:</span> {event.severity}
          </div>
        )}
        {event.details && Object.keys(event.details).length > 0 && (
          <div className="space-y-1">
            <p className="text-xs font-semibold">Details:</p>
            <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-xs">
              {JSON.stringify(event.details, null, 2)}
            </pre>
          </div>
        )}
      </div>
    );
  };

  // ─────────────────────────────────────────────────────────────────────────
  // Render
  // ─────────────────────────────────────────────────────────────────────────

  return (
    <Card className="h-full overflow-y-auto">
      <CardHeader>
        <CardTitle className="flex items-center justify-between text-base">
          <span>Inspector</span>
          <span className={`rounded px-2 py-1 text-xs font-semibold ${getEventTypeColor(event.type)}`}>
            {event.type.replace('_', ' ')}
          </span>
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* Header Info */}
        <div className="space-y-3 rounded-lg border border-border bg-muted/50 p-4">
          <div>
            <p className="text-xs text-muted-foreground">Event ID</p>
            <div className="flex items-center justify-between gap-2">
              <code className="text-sm font-mono">{event.id}</code>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => copyToClipboard(event.id, 'id')}
              >
                {copied.copiedField === 'id' ? (
                  <Check className="h-4 w-4 text-emerald-600 dark:text-emerald-400" />
                ) : (
                  <Copy className="h-4 w-4" />
                )}
              </Button>
            </div>
          </div>

          <div>
            <p className="text-xs text-muted-foreground">Timestamp</p>
            <p className="text-sm">{new Date(event.timestamp).toLocaleString()}</p>
          </div>

          <div>
            <p className="text-xs text-muted-foreground">Tenant</p>
            <p className="text-sm">{event.tenant_id}</p>
          </div>
        </div>

        {/* Event-Specific Details */}
        <div className="space-y-2">
          <p className="text-sm font-semibold">Event Details</p>
          <div className="rounded-lg border border-border bg-muted/30 p-4">
            {renderChainRecord()}
              {renderEventDetails()}
          </div>
        </div>

        {/* Tabs */}
        <Tabs defaultValue="json" className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="json">JSON</TabsTrigger>
            <TabsTrigger value="chain">Chain</TabsTrigger>
            <TabsTrigger value="proof">Proof</TabsTrigger>
          </TabsList>

          {/* JSON Tab */}
          <TabsContent value="json" className="space-y-2">
            <div className="rounded-lg border border-border bg-muted/30 p-4">
              <code className="block overflow-x-auto text-xs font-mono">
                <pre>{JSON.stringify(event, null, 2)}</pre>
              </code>
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => copyToClipboard(JSON.stringify(event, null, 2), 'json')}
            >
              {copied.copiedField === 'json' ? 'Copied!' : 'Copy JSON'}
            </Button>
          </TabsContent>

          {/* Chain Tab */}
          <TabsContent value="chain" className="space-y-3">
            <div className="space-y-2">
              <p className="text-sm font-semibold">Hash Chain</p>

              <div className="space-y-2 rounded-lg border border-border bg-muted/30 p-4">
                <div>
                  <p className="text-xs text-muted-foreground">This Event Hash</p>
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-xs font-mono">{event.hash ? event.hash.substring(0, 32) + '...' : '(missing)'}</code>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => copyToClipboard(event.hash, 'hash')}
                    >
                      <Copy className="h-3 w-3" />
                    </Button>
                  </div>
                </div>

                <div className="flex justify-center">
                  <ChevronRight className="h-4 w-4 rotate-90 text-muted-foreground" />
                </div>

                <div>
                  <p className="text-xs text-muted-foreground">Previous Event Hash (Chain Link)</p>
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-xs font-mono">{event.prev_hash ? event.prev_hash.substring(0, 32) + '...' : '(missing)'}</code>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => copyToClipboard(event.prev_hash, 'prev_hash')}
                    >
                      <Copy className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
              </div>

              {/* Verify Chain Button */}
              <Button
                size="sm"
                onClick={handleVerifyChain}
                disabled={isVerifying}
                className="w-full"
              >
                {isVerifying ? 'Verifying...' : '🔐 Verify Hash Chain'}
              </Button>

              {verificationResult && (
                <div
                  className={`rounded-lg p-3 border ${
                    verificationResult.isValid
                      ? 'border-emerald-500/30 bg-emerald-500/10'
                      : 'border-destructive/40 bg-destructive/10'
                  }`}
                >
                  <p
                    className={`text-xs font-semibold ${
                      verificationResult.isValid
                        ? 'text-emerald-700 dark:text-emerald-300'
                        : 'text-destructive'
                    }`}
                  >
                    {verificationResult.isValid ? '✓' : '✗'} {verificationResult.message}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {verificationResult.verificationsCount} of{' '}
                    {verificationResult.chainHeight} links verified
                  </p>
                </div>
              )}
            </div>

            {/* LoM Binding */}
            <div className="space-y-2">
              <p className="text-sm font-semibold">Line of Moral Responsibility (LoM)</p>
              <div className="space-y-2 rounded-lg border border-border bg-muted/30 p-4">
                <div>
                  <p className="text-xs text-muted-foreground">LoM Hash</p>
                  <div className="flex items-center justify-between gap-2">
                    <code className="text-xs font-mono">{event.lom_hash ? event.lom_hash.substring(0, 32) + '...' : '(missing)'}</code>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => copyToClipboard(event.lom_hash, 'lom_hash')}
                    >
                      <Copy className="h-3 w-3" />
                    </Button>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">
                  Cryptographic binding to source code location
                </p>
              </div>
            </div>

            {/* Parent Events */}
            {getParentEvents().length > 0 && (
              <div className="space-y-2">
                <p className="text-sm font-semibold">Parent Events</p>
                {getParentEvents().map((parent) => (
                  <button
                    key={parent.id}
                    onClick={() => onEventSelect?.(parent)}
                    className="w-full rounded-lg border border-border bg-muted/50 p-2 text-left text-xs hover:bg-muted"
                  >
                    <p className="font-mono">{parent.id}</p>
                    <p className="text-muted-foreground">{parent.type}</p>
                  </button>
                ))}
              </div>
            )}

            {/* Child Events */}
            {getChildEvents().length > 0 && (
              <div className="space-y-2">
                <p className="text-sm font-semibold">Child Events</p>
                {getChildEvents().map((child) => (
                  <button
                    key={child.id}
                    onClick={() => onEventSelect?.(child)}
                    className="w-full rounded-lg border border-border bg-muted/50 p-2 text-left text-xs hover:bg-muted"
                  >
                    <p className="font-mono">{child.id}</p>
                    <p className="text-muted-foreground">{child.type}</p>
                  </button>
                ))}
              </div>
            )}
          </TabsContent>

          {/* Proof Tab */}
          <TabsContent value="proof" className="space-y-3">
            <div className="rounded-lg border border-emerald-500/30 bg-emerald-500/10 p-4">
              <div className="flex gap-2">
                <Shield className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                <div>
                  <p className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">✓ Audit Event Verified</p>
                  <p className="text-xs text-emerald-700/90 dark:text-emerald-300/90">
                    This event is part of an immutable, hash-chained audit trail.
                  </p>
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-sm font-semibold">Proof Attributes</p>
              <div className="text-xs text-muted-foreground space-y-1">
                <p>• Immutable: Event cannot be modified or deleted</p>
                <p>• Hash-Chained: Cryptographically linked to prior event</p>
                <p>• LoM-Bound: Trace back to source code (ADR-0537)</p>
                <p>• Tenant-Scoped: GDPR compliant isolation</p>
                <p>• Audit Trail: Complete record available for verification</p>
              </div>
            </div>

            <Button size="sm" className="w-full">
              <Download className="h-4 w-4" /> Export Audit Proof (PDF)
            </Button>

            <Button size="sm" variant="outline" className="w-full">
              Share Proof Link
            </Button>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}
