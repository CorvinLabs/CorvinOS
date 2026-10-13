import React, { useState, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Hammer, Plus, Download, Upload } from 'lucide-react';
import { ForgeTool } from '@/types/forge';
import {
  exportSingleItem,
  exportBatchItems,
  importBundle,
  isValidZipFile,
} from '@/lib/forge-bundles';

interface ToolsTabProps {
  tools: ForgeTool[];
  setTools: (tools: ForgeTool[]) => void;
  searchQuery: string;
  filterStatus: 'all' | 'enabled' | 'disabled';
}

export default function ToolsTab({
  tools,
  setTools,
  searchQuery,
  filterStatus }: ToolsTabProps) {
  const [_selectedTool, setSelectedTool] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const filtered = React.useMemo(() => {
    return tools.filter((tool) => {
      const matchesSearch =
        searchQuery === '' ||
        tool.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (tool.description?.toLowerCase().includes(searchQuery.toLowerCase()) ?? false);

      const matchesStatus =
        filterStatus === 'all' ||
        (filterStatus === 'enabled' && tool.enabled) ||
        (filterStatus === 'disabled' && !tool.enabled);

      return matchesSearch && matchesStatus;
    });
  }, [tools, searchQuery, filterStatus]);

  const handleToggle = async (toolId: string) => {
    const tool = tools.find((t) => t.id === toolId);
    if (!tool) return;

    try {
      const endpoint = tool.enabled
        ? `/v1/console/forge/tools/${toolId}/disable`
        : `/v1/console/forge/tools/${toolId}/enable`;

      const res = await fetch(endpoint, { method: 'POST' });
      if (!res.ok) throw new Error('Failed to toggle tool');

      setTools(
        tools.map((t) => (t.id === toolId ? { ...t, enabled: !t.enabled } : t))
      );
    } catch (err) {
      console.error('Error toggling tool:', err);
    }
  };

  const handleDownloadSingle = async (toolId: string) => {
    const result = await exportSingleItem('tools', toolId);
    if (!result.success) {
      setImportStatus({ type: 'err', msg: `Download failed: ${result.error}` });
    }
  };

  const handleDownloadSelected = async () => {
    if (selectedIds.size === 0) {
      setImportStatus({ type: 'err', msg: 'No items selected' });
      return;
    }

    const result = await exportBatchItems('tools', Array.from(selectedIds));
    if (!result.success) {
      setImportStatus({ type: 'err', msg: `Download failed: ${result.error}` });
    } else {
      setImportStatus({ type: 'ok', msg: `Downloaded ${selectedIds.size} tools` });
      setSelectedIds(new Set());
    }
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!isValidZipFile(file.name)) {
      setImportStatus({ type: 'err', msg: 'Please select a .zip file' });
      return;
    }

    setImporting(true);
    try {
      const result = await importBundle(file);
      if (result.success) {
        setImportStatus({
          type: 'ok',
          msg: `Imported successfully (${result.imported_count} tools)`,
        });
        // Refresh tools list
        try {
          const res = await fetch('/v1/console/forge/tools');
          if (res.ok) setTools((await res.json()).tools || []);
        } catch (err) {
          console.error('Failed to refresh tools:', err);
        }
      } else {
        setImportStatus({ type: 'err', msg: result.message });
      }
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const toggleSelection = (toolId: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(toolId)) {
      newSelected.delete(toolId);
    } else {
      newSelected.add(toolId);
    }
    setSelectedIds(newSelected);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">MCP Tools</h2>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            onChange={handleImport}
            className="hidden"
            aria-label="Import tools bundle"
          />
          <Button
            size="sm"
            variant="outline"
            onClick={() => fileInputRef.current?.click()}
            disabled={importing}
          >
            <Upload className="w-4 h-4 mr-2" />
            {importing ? 'Importing...' : 'Import'}
          </Button>
          {selectedIds.size > 0 && (
            <Button
              size="sm"
              variant="outline"
              onClick={handleDownloadSelected}
            >
              <Download className="w-4 h-4 mr-2" />
              Download selected ({selectedIds.size})
            </Button>
          )}
          <Button size="sm">
            <Plus className="w-4 h-4 mr-2" />
            New Tool
          </Button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {tools.length === 0
              ? 'No tools yet. Create one to get started.'
              : 'No tools match your search.'}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((tool) => (
            <Card
              key={tool.id}
              className="cursor-pointer hover:border-accent/50 transition-colors"
              onClick={() => setSelectedTool(tool.id)}
            >
              <CardContent className="py-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3 flex-1">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(tool.id)}
                      onChange={() => toggleSelection(tool.id)}
                      onClick={(e) => e.stopPropagation()}
                      className="mt-1"
                      aria-label={`Select ${tool.name}`}
                    />
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <Hammer className="w-4 h-4 text-accent" />
                        <h3 className="font-semibold">{tool.name}</h3>
                        <Badge variant={tool.enabled ? 'ok' : 'secondary'} className="text-[10px]">
                          {tool.enabled ? 'Enabled' : 'Disabled'}
                        </Badge>
                        {tool.version && (
                          <Badge variant="outline" className="text-[10px]">
                            v{tool.version}
                          </Badge>
                        )}
                      </div>
                      {tool.description && (
                        <p className="text-sm text-muted-foreground line-clamp-2">
                          {tool.description}
                        </p>
                      )}
                      {tool.actions && (
                        <p className="text-xs text-muted-foreground mt-2">
                          {tool.actions.length} action{tool.actions.length === 1 ? '' : 's'}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Download ${tool.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownloadSingle(tool.id);
                      }}
                    >
                      <Download className="w-4 h-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleToggle(tool.id);
                      }}
                    >
                      {tool.enabled ? 'Disable' : 'Enable'}
                    </Button>
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {importStatus && (
        <div
          className={
            importStatus.type === 'ok'
              ? 'fixed bottom-6 right-6 z-50 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 shadow-lg dark:text-emerald-300'
              : 'fixed bottom-6 right-6 z-50 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive shadow-lg'
          }
          onClick={() => setImportStatus(null)}
        >
          {importStatus.msg}
        </div>
      )}
    </div>
  );
}
