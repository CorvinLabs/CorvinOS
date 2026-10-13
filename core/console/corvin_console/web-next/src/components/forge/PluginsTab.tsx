import React, { useState, useRef } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Package, Plus, Upload, Download } from 'lucide-react';
import {
  exportSingleItem,
  importBundle,
  isValidZipFile,
} from '@/lib/forge-bundles';

interface Plugin {
  id: string;
  name?: string;
  enabled?: boolean;
  description?: string;
  version?: string;
}

interface PluginsTabProps {
  plugins: Plugin[];
  setPlugins: (plugins: Plugin[]) => void;
  searchQuery: string;
  filterStatus: 'all' | 'enabled' | 'disabled';
}

export default function PluginsTab({
  plugins,
  setPlugins,
  searchQuery,
  filterStatus }: PluginsTabProps) {
  const [importing, setImporting] = useState(false);
  const [importStatus, setImportStatus] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);

  const filtered = React.useMemo(() => {
    return plugins.filter((plugin) => {
      const matchesSearch =
        searchQuery === '' ||
        plugin.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        (plugin.description?.toLowerCase().includes(searchQuery.toLowerCase()) ?? false);

      const matchesStatus =
        filterStatus === 'all' ||
        (filterStatus === 'enabled' && plugin.enabled) ||
        (filterStatus === 'disabled' && !plugin.enabled);

      return matchesSearch && matchesStatus;
    });
  }, [plugins, searchQuery, filterStatus]);

  const handleDownloadSingle = async (pluginId: string) => {
    const result = await exportSingleItem('plugins', pluginId);
    if (!result.success) {
      setImportStatus({ type: 'err', msg: `Download failed: ${result.error}` });
    }
  };

  const handleDownloadSelected = async () => {
    if (selectedIds.size === 0) {
      setImportStatus({ type: 'err', msg: 'No items selected' });
      return;
    }

    // For now, download each selected plugin individually
    for (const id of selectedIds) {
      await handleDownloadSingle(id);
    }
    setImportStatus({ type: 'ok', msg: `Downloaded ${selectedIds.size} plugins` });
    setSelectedIds(new Set());
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
          msg: `Imported successfully (${result.imported_count} plugins)`,
        });
        try {
          const res = await fetch('/v1/console/forge/plugins');
          if (res.ok) setPlugins((await res.json()).plugins || []);
        } catch (err) {
          console.error('Failed to refresh plugins:', err);
        }
      } else {
        setImportStatus({ type: 'err', msg: result.message });
      }
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const toggleSelection = (pluginId: string) => {
    const newSelected = new Set(selectedIds);
    if (newSelected.has(pluginId)) {
      newSelected.delete(pluginId);
    } else {
      newSelected.add(pluginId);
    }
    setSelectedIds(newSelected);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-semibold">Plugins</h2>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            accept=".zip"
            onChange={handleImport}
            className="hidden"
            aria-label="Import plugins bundle"
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
            New Plugin
          </Button>
        </div>
      </div>

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {plugins.length === 0
              ? 'No plugins yet. Import or forge one to get started.'
              : 'No plugins match your search.'}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((plugin) => (
            <Card
              key={plugin.id}
              className="cursor-pointer hover:border-accent/50 transition-colors"
              onClick={() => {}}
            >
              <CardContent className="py-4">
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-3 flex-1">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(plugin.id)}
                      onChange={() => toggleSelection(plugin.id)}
                      onClick={(e) => e.stopPropagation()}
                      className="mt-1"
                      aria-label={`Select ${plugin.name || plugin.id}`}
                    />
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-2">
                        <Package className="w-4 h-4 text-accent" />
                        <h3 className="font-semibold">{plugin.name || plugin.id}</h3>
                        <Badge variant={plugin.enabled ? 'ok' : 'secondary'} className="text-[10px]">
                          {plugin.enabled ? 'Enabled' : 'Disabled'}
                        </Badge>
                        {plugin.version && (
                          <Badge variant="outline" className="text-[10px]">
                            v{plugin.version}
                          </Badge>
                        )}
                      </div>
                      {plugin.description && (
                        <p className="text-sm text-muted-foreground line-clamp-2">
                          {plugin.description}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      aria-label={`Download ${plugin.name || plugin.id}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDownloadSingle(plugin.id);
                      }}
                    >
                      <Download className="w-4 h-4" />
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
