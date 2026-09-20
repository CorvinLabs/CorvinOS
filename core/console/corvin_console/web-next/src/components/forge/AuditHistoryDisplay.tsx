import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { ChevronDown, ChevronRight, CheckCircle, AlertCircle, Clock } from 'lucide-react';

export interface ForkAttempt {
  id: string;
  timestamp: string;
  skill_id: string;
  version: string;
  validation_passed: boolean;
  canary_verdict: 'approved' | 'deferred' | 'rolled_back' | null;
  operator_decision?: string;
  operator_id?: string;
  audit_hash?: string;
  hash_verified?: boolean;
  events?: AuditEvent[];
}

export interface AuditEvent {
  timestamp: string;
  event_type: string;
  details: string;
  hash: string;
}

interface AuditHistoryDisplayProps {
  attempts: ForkAttempt[];
  loading?: boolean;
}

/**
 * AuditHistoryDisplay: Timeline of fork attempts with expandable details
 */
export const AuditHistoryDisplay: React.FC<AuditHistoryDisplayProps> = ({
  attempts,
  loading = false,
}) => {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const formatDate = (timestamp: string) => {
    const date = new Date(timestamp);
    return date.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
  };

  const getVerdictBadge = (verdict: string | null) => {
    if (!verdict) return null;
    switch (verdict) {
      case 'approved':
        return <Badge className="bg-green-600">Approved</Badge>;
      case 'deferred':
        return <Badge className="bg-blue-600">Deferred</Badge>;
      case 'rolled_back':
        return <Badge variant="warn" className="text-xs">Rolled Back</Badge>;
      default:
        return <Badge variant="secondary" className="text-xs">{verdict}</Badge>;
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="py-6 text-center text-muted-foreground">
          Loading audit history...
        </CardContent>
      </Card>
    );
  }

  if (attempts.length === 0) {
    return (
      <Card>
        <CardContent className="py-6 text-center text-muted-foreground">
          No fork attempts yet
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audit History (Last 10 Attempts)</CardTitle>
      </CardHeader>

      <CardContent>
        <div className="space-y-3">
          {attempts.map((attempt, index) => (
            <div key={attempt.id} className="border rounded-lg">
              {/* Timeline entry header */}
              <div
                className="flex items-center justify-between p-3 cursor-pointer hover:bg-secondary/50 transition-colors"
                onClick={() =>
                  setExpandedId(expandedId === attempt.id ? null : attempt.id)
                }
              >
                <div className="flex items-start gap-3 flex-1">
                  <div className="pt-1">
                    {expandedId === attempt.id ? (
                      <ChevronDown className="w-4 h-4" />
                    ) : (
                      <ChevronRight className="w-4 h-4" />
                    )}
                  </div>

                  {/* Timeline dot and connector */}
                  <div className="relative">
                    <div className="flex items-center justify-center">
                      {attempt.validation_passed ? (
                        <CheckCircle className="w-5 h-5 text-green-500" />
                      ) : (
                        <AlertCircle className="w-5 h-5 text-red-500" />
                      )}
                    </div>
                    {index < attempts.length - 1 && (
                      <div className="absolute top-5 left-2 w-0.5 h-6 bg-border" />
                    )}
                  </div>

                  {/* Details */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-semibold text-sm">
                        {attempt.skill_id}
                      </span>
                      <code className="text-xs bg-secondary px-2 py-1 rounded">
                        v{attempt.version}
                      </code>
                      {attempt.validation_passed ? (
                        <Badge variant="ok" className="text-[10px]">
                          Validated
                        </Badge>
                      ) : (
                        <Badge variant="warn" className="text-[10px]">
                          Failed
                        </Badge>
                      )}
                    </div>

                    <div className="text-xs text-muted-foreground flex items-center gap-2">
                      <Clock className="w-3 h-3" />
                      {formatDate(attempt.timestamp)}
                    </div>
                  </div>

                  {/* Operator decision badge */}
                  {attempt.canary_verdict && (
                    <div className="ml-auto">{getVerdictBadge(attempt.canary_verdict)}</div>
                  )}
                </div>
              </div>

              {/* Expanded details */}
              {expandedId === attempt.id && (
                <div className="border-t px-3 py-3 bg-secondary/20 space-y-2 text-sm">
                  {/* Operator decision info */}
                  {attempt.operator_id && (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="text-muted-foreground">Operator Decision</span>
                        <span className="font-semibold">
                          {attempt.operator_decision || 'No reason given'}
                        </span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="text-muted-foreground">Operator ID</span>
                        <code className="text-xs bg-background px-2 py-1 rounded">
                          {attempt.operator_id}
                        </code>
                      </div>
                    </>
                  )}

                  {/* Hash chain verification */}
                  {attempt.audit_hash && (
                    <div className="pt-2 border-t">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-muted-foreground">Hash Chain Status</span>
                        <span
                          className={`text-xs font-semibold ${
                            attempt.hash_verified
                              ? 'text-green-600 dark:text-green-400'
                              : 'text-yellow-600 dark:text-yellow-400'
                          }`}
                        >
                          {attempt.hash_verified ? '✓ Verified' : '? Unverified'}
                        </span>
                      </div>
                      <code className="text-xs bg-background px-2 py-1 rounded block break-all text-muted-foreground">
                        {attempt.audit_hash}
                      </code>
                    </div>
                  )}

                  {/* Detailed events */}
                  {attempt.events && attempt.events.length > 0 && (
                    <div className="pt-2 border-t">
                      <div className="font-semibold mb-2 text-xs">Events</div>
                      <div className="space-y-1">
                        {attempt.events.map((event, i) => (
                          <div
                            key={i}
                            className="text-xs bg-background p-2 rounded border"
                          >
                            <div className="flex items-center justify-between mb-1">
                              <span className="font-mono text-muted-foreground">
                                {formatDate(event.timestamp)}
                              </span>
                              <Badge variant="outline" className="text-[10px]">
                                {event.event_type}
                              </Badge>
                            </div>
                            <div className="text-muted-foreground">
                              {event.details}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
};

export default AuditHistoryDisplay;
