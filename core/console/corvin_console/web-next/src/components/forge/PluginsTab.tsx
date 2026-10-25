import React, { useState, useRef } from 'react';
import { Link } from 'react-router-dom';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Package, Upload } from 'lucide-react';
import { api } from '@/lib/api';
import { importBundle, isValidZipFile } from '@/lib/forge-bundles';

/** A staged plugin written by Plugin Forge (GET /forge/plugins). Staged plugins are never
 *  installed: install and review happen in Marketplace -> Forged. */
interface Plugin {
  id: string;
  name?: string;
  enabled?: boolean;
  description?: string;
  version?: string;
  plugin_id?: string | null;
  tier?: string | null;
  quality?: string | null;
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
  const fileInputRef = useRef<HTMLInputElement>(null);

  const filtered = React.useMemo(() => {
    const q = searchQuery.toLowerCase();
    return plugins.filter((plugin) => {
      const matchesSearch =
        q === '' ||
        (plugin.name?.toLowerCase().includes(q) ?? false) ||
        (plugin.plugin_id?.toLowerCase().includes(q) ?? false) ||
        (plugin.description?.toLowerCase().includes(q) ?? false);
      // Staged plugins are never enabled, so "Enabled" matches none of them.
      const matchesStatus =
        filterStatus === 'all' ||
        (filterStatus === 'enabled' && !!plugin.enabled) ||
        (filterStatus === 'disabled' && !plugin.enabled);
      return matchesSearch && matchesStatus;
    });
  }, [plugins, searchQuery, filterStatus]);

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
        setImportStatus({ type: 'ok', msg: 'Bundle accepted. Plugins wait for approval before they run.' });
        try {
          const data = await api<{ plugins?: Plugin[] }>('/forge/plugins');
          setPlugins(data.plugins || []);
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

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <div>
          <h2 className="text-xl font-semibold">Plugins</h2>
          <p className="text-sm text-muted-foreground">
            Plugins the Plugin Forge staged for review. Install or delete them in{' '}
            <Link className="underline" to="/app/marketplace?tab=forged">Marketplace, Forged</Link>.
          </p>
        </div>
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
        </div>
      </div>

      {filtered.length === 0 ? (
        <Card className="border-dashed">
          <CardContent className="py-12 text-center text-sm text-muted-foreground">
            {plugins.length === 0
              ? 'No staged plugins yet. Forge one from the Generator tab.'
              : 'No plugins match your search.'}
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-3">
          {filtered.map((plugin) => (
            <Card key={plugin.id} data-testid="forge-plugin-row">
              <CardContent className="py-4">
                <div className="flex items-center gap-2 mb-2">
                  <Package className="w-4 h-4 text-accent" />
                  <h3 className="font-semibold">{plugin.name || plugin.id}</h3>
                  <Badge variant="secondary" className="text-[10px]">Staged</Badge>
                  {plugin.tier && (
                    <Badge variant="outline" className="text-[10px]">tier {plugin.tier}</Badge>
                  )}
                  {plugin.quality && (
                    <Badge variant="outline" className="text-[10px]">{plugin.quality}</Badge>
                  )}
                </div>
                {plugin.plugin_id && (
                  <p className="text-xs font-mono text-muted-foreground">{plugin.plugin_id}</p>
                )}
                {plugin.description && (
                  <p className="text-sm text-muted-foreground line-clamp-2">{plugin.description}</p>
                )}
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
