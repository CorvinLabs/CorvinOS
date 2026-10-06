/**
 * QuarantinePanel — Review & Accept Imported Artifacts
 *
 * Shows a table of artifacts staged for import (in quarantine), with Accept/Reject
 * actions. Polls for updates every 5 seconds when tab is active.
 *
 * ADR-2229 Phase 4: Quarantine review component
 */
import React, { useState, useEffect, useCallback, useRef } from 'react';
import { CheckCircle2, XCircle, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { UnverifiedOriginBadge } from './UnverifiedOriginBadge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ForgeBundleQuarantineItem } from '@/types/forge';

interface QuarantineState {
  items: ForgeBundleQuarantineItem[];
  isLoading: boolean;
  error?: string;
  actionInProgress: Set<string>; // IDs of items being acted upon
}

/**
 * Format date string to readable format
 */
function formatDate(dateString: string): string {
  try {
    return new Date(dateString).toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return dateString;
  }
}

export const QuarantinePanel: React.FC = () => {
  const [state, setState] = useState<QuarantineState>({
    items: [],
    isLoading: true,
    actionInProgress: new Set(),
  });
  const pollIntervalRef = useRef<NodeJS.Timeout>();

  // Fetch quarantine items
  const fetchQuarantine = useCallback(async () => {
    try {
      const response = await fetch('/v1/console/forge-bundles/quarantine', {
        credentials: 'same-origin',
      });

      if (!response.ok) {
        throw new Error(`Failed to fetch quarantine items: ${response.statusText}`);
      }

      const data = await response.json();
      setState((prev) => ({
        ...prev,
        items: data.items || [],
        isLoading: false,
        error: undefined,
      }));
    } catch (error) {
      setState((prev) => ({
        ...prev,
        isLoading: false,
        error: error instanceof Error ? error.message : 'Unknown error',
      }));
    }
  }, []);

  // Accept artifact
  const handleAccept = useCallback(async (id: string) => {
    setState((prev) => ({
      ...prev,
      actionInProgress: new Set([...prev.actionInProgress, id]),
    }));

    try {
      const response = await fetch(`/v1/console/forge-bundles/quarantine/${id}/accept`, {
        method: 'POST',
        credentials: 'same-origin',
      });

      if (!response.ok) {
        throw new Error(`Accept failed: ${response.statusText}`);
      }

      // Remove from list
      setState((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== id),
        actionInProgress: new Set([...prev.actionInProgress].filter((x) => x !== id)),
      }));
    } catch (error) {
      setState((prev) => ({
        ...prev,
        actionInProgress: new Set([...prev.actionInProgress].filter((x) => x !== id)),
        error: error instanceof Error ? error.message : 'Unknown error',
      }));
    }
  }, []);

  // Reject artifact
  const handleReject = useCallback(async (id: string) => {
    setState((prev) => ({
      ...prev,
      actionInProgress: new Set([...prev.actionInProgress, id]),
    }));

    try {
      const response = await fetch(`/v1/console/forge-bundles/quarantine/${id}/reject`, {
        method: 'POST',
        credentials: 'same-origin',
      });

      if (!response.ok) {
        throw new Error(`Reject failed: ${response.statusText}`);
      }

      // Remove from list
      setState((prev) => ({
        ...prev,
        items: prev.items.filter((item) => item.id !== id),
        actionInProgress: new Set([...prev.actionInProgress].filter((x) => x !== id)),
      }));
    } catch (error) {
      setState((prev) => ({
        ...prev,
        actionInProgress: new Set([...prev.actionInProgress].filter((x) => x !== id)),
        error: error instanceof Error ? error.message : 'Unknown error',
      }));
    }
  }, []);

  // Setup polling
  useEffect(() => {
    // Initial fetch
    void fetchQuarantine();

    // Setup interval (poll every 5 seconds if tab is visible)
    const setupPolling = () => {
      if (document.visibilityState === 'visible') {
        pollIntervalRef.current = setInterval(() => {
          void fetchQuarantine();
        }, 5000);
      }
    };

    const handleVisibilityChange = () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      setupPolling();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    setupPolling();

    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, [fetchQuarantine]);

  if (state.isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (state.items.length === 0) {
    return (
      <Card className="border-dashed">
        <CardContent className="py-12 text-center">
          <CheckCircle2 className="w-12 h-12 mx-auto mb-3 text-green-600" />
          <h3 className="font-semibold mb-1">All clear!</h3>
          <p className="text-sm text-muted-foreground">
            No artifacts waiting for review. Imported bundles are ready to use.
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      {state.error && (
        <div className="p-3 bg-destructive/10 text-destructive rounded-md text-sm">
          {state.error}
        </div>
      )}

      <div className="rounded-md border overflow-hidden">
        <Table>
          <TableHeader className="bg-muted">
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Version</TableHead>
              <TableHead>Bundle</TableHead>
              <TableHead>Staged At</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {state.items.map((item) => (
              <TableRow key={item.id}>
                <TableCell>
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{item.name}</span>
                    <UnverifiedOriginBadge />
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant="outline">{item.type}</Badge>
                </TableCell>
                <TableCell className="text-sm">{item.version}</TableCell>
                <TableCell className="text-sm text-muted-foreground">{item.bundle_id}</TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {formatDate(item.staged_at)}
                </TableCell>
                <TableCell className="text-right space-x-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleAccept(item.id)}
                    disabled={state.actionInProgress.has(item.id)}
                    className="text-green-600 hover:text-green-700 hover:bg-green-50"
                  >
                    {state.actionInProgress.has(item.id) ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <>
                        <CheckCircle2 className="w-4 h-4 mr-1" />
                        Accept
                      </>
                    )}
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleReject(item.id)}
                    disabled={state.actionInProgress.has(item.id)}
                    className="text-destructive hover:text-destructive hover:bg-destructive/10"
                  >
                    {state.actionInProgress.has(item.id) ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <>
                        <XCircle className="w-4 h-4 mr-1" />
                        Reject
                      </>
                    )}
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      <p className="text-xs text-muted-foreground">
        This list updates automatically every 5 seconds. Click Accept to proceed with integration,
        or Reject to discard.
      </p>
    </div>
  );
};

export default QuarantinePanel;
