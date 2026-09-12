import React, { useState, useEffect } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { HistoryIcon, Download } from 'lucide-react';

interface AuditEvent {
  id: string;
  timestamp: string;
  type: string;
  resource_type: 'tool' | 'skill' | 'os-skill';
  resource_id: string;
  resource_name: string;
  action: string;
  user: string;
  details?: Record<string, any>;
  hash: string;
  prev_hash: string;
}

interface AuditTabProps {
  searchQuery: string;
  filterType: 'all' | 'tool' | 'skill' | 'os-skill';
}

export default function AuditTab({ searchQuery, filterType }: AuditTabProps) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedAction, setSelectedAction] = useState<'all' | 'create' | 'update' | 'delete' | 'enable' | 'disable'>('all');

  useEffect(() => {
    const fetchAudit = async () => {
      try {
        setLoading(true);
        const sinceParam = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
        const res = await fetch(`/v1/console/forge/audit?since=${sinceParam}&event_type=forge_*`);
        if (!res.ok) throw new Error('Failed to fetch audit events');
        const data = await res.json();
        setEvents(data.events ?? []);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error');
      } finally {
        setLoading(false);
      }
    };

    fetchAudit();
  }, []);

  const filtered = React.useMemo(() => {
    return events.filter((event) => {
      const matchesSearch =
        searchQuery === '' ||
        event.resource_name.toLowerCase().includes(searchQuery.toLowerCase());

      const matchesType =
        filterType === 'all' || event.resource_type === filterType;

      const matchesAction =
        selectedAction === 'all' ||
        event.action.toLowerCase().includes(selectedAction.toLowerCase());

      return matchesSearch && matchesType && matchesAction;
    });
  }, [events, searchQuery, filterType, selectedAction]);

  const getActionBadge = (action: string) => {
    switch (action.toLowerCase()) {
      case 'create':
        return <Badge variant="ok">Created</Badge>;
      case 'update':
        return <Badge variant="secondary">Updated</Badge>;
      case 'delete':
        return <Badge variant="danger">Deleted</Badge>;
      case 'enable':
        return <Badge variant="ok">Enabled</Badge>;
      case 'disable':
        return <Badge variant="secondary">Disabled</Badge>;
      default:
        return <Badge>{action}</Badge>;
    }
  };

  const getResourceBadge = (type: string) => {
    switch (type) {
      case 'os-skill':
        return 'OS-Skill';
      case 'skill':
        return 'Skill';
      case 'tool':
        return 'Tool';
      default:
        return type;
    }
  };

  const handleExport = async () => {
    try {
      const data = JSON.stringify(filtered, null, 2);
      const blob = new Blob([data], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `forge-audit-${new Date().toISOString().split('T')[0]}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('Failed to export audit log:', err);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <p className="text-muted-foreground">Loading audit trail...</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-4 items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold">Audit Trail</h2>
          <p className="text-sm text-muted-foreground">
            Complete hash-chained history of all Forge changes
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={handleExport}>
          <Download className="w-4 h-4 mr-2" />
          Export JSON
        </Button>
      </div>

      <div className="flex gap-4 items-center">
        <select
          value={selectedAction}
          onChange={(e) => setSelectedAction(e.target.value as any)}
          className="flex h-10 w-40 rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          <option value="all">All Actions</option>
          <option value="create">Created</option>
          <option value="update">Updated</option>
          <option value="delete">Deleted</option>
          <option value="enable">Enabled</option>
          <option value="disable">Disabled</option>
        </select>
      </div>

      {error && (
        <Card className="border-destructive/50 bg-destructive/5">
          <CardContent className="py-3 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            No audit events found.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-2">
          {filtered.map((event) => (
            <Card key={event.id} className="hover:border-accent/50 transition-colors">
              <CardContent className="py-3">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <HistoryIcon className="w-4 h-4 text-muted-foreground" />
                      <span className="font-mono text-sm">{event.resource_name}</span>
                      <Badge variant="outline" className="text-[10px]">
                        {getResourceBadge(event.resource_type)}
                      </Badge>
                      {getActionBadge(event.action)}
                    </div>
                    <p className="text-xs text-muted-foreground">
                      by <span className="font-medium">{event.user}</span> at{' '}
                      <span className="font-mono">
                        {new Date(event.timestamp).toLocaleString()}
                      </span>
                    </p>
                    {event.details && Object.keys(event.details).length > 0 && (
                      <div className="mt-2 text-xs text-muted-foreground space-y-1">
                        {Object.entries(event.details).map(([key, value]) => (
                          <p key={key}>
                            <span className="font-medium">{key}:</span>{' '}
                            {typeof value === 'object'
                              ? JSON.stringify(value)
                              : String(value)}
                          </p>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="text-right flex flex-col gap-1 text-xs">
                    <p className="font-mono text-[10px] text-muted-foreground">
                      {event.hash.slice(0, 8)}...
                    </p>
                    {event.prev_hash && (
                      <p className="font-mono text-[10px] text-muted-foreground">
                        ← {event.prev_hash.slice(0, 8)}...
                      </p>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
