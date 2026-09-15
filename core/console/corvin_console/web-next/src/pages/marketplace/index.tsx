import { useEffect, useState } from 'react';
import { Download, Trash2, Settings } from 'lucide-react';

export default function MarketplacePage() {
  const [tab, setTab] = useState<'available' | 'installed'>('available');
  const [plugins, setPlugins] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const url = tab === 'installed' ? '/v1/marketplace/plugins/installed' : '/v1/marketplace/plugins/available';
    fetch(url)
      .then(r => r.json())
      .then(d => { setPlugins(d.plugins || []); setLoading(false); })
      .catch(e => { console.error(e); setLoading(false); });
  }, [tab]);

  const handleInstall = (id: string) => {
    fetch(`/v1/marketplace/plugins/${id}/install`, {method: 'POST'})
      .then(() => alert(`Installing ${id}...`));
  };

  const handleUninstall = (id: string) => {
    fetch(`/v1/marketplace/plugins/${id}/uninstall`, {method: 'POST'})
      .then(() => alert(`Uninstalling ${id}...`));
  };

  return (
    <div className="min-h-screen bg-background text-foreground p-6 space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">Marketplace</h1>
        <p className="text-muted-foreground">Install, manage, and configure plugins</p>
      </div>

      {/* Tabs */}
      <div className="flex gap-4 border-b">
        {['available', 'installed'].map((t) => (
          <button
            key={t}
            onClick={() => setTab(t as any)}
            className={`px-4 py-2 font-medium transition-all ${tab === t ? 'border-b-2 border-primary' : 'text-muted-foreground hover:text-foreground'}`}
          >
            {t === 'available' && 'Available Plugins'}
            {t === 'installed' && 'Installed Plugins'}
          </button>
        ))}
      </div>

      {/* Plugin List */}
      <div className="grid gap-4 grid-cols-1 md:grid-cols-2">
        {plugins.map((p: any) => (
          <div key={p.id} className="border rounded-lg p-4 space-y-4 hover:bg-muted/50 transition-colors">
            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <h3 className="font-semibold">{p.name || p.id}</h3>
                <span className="text-xs font-mono bg-muted px-2 py-1 rounded">{p.version}</span>
              </div>
              <p className="text-sm text-muted-foreground">{p.description}</p>
              {p.category && <p className="text-xs text-muted-foreground capitalize">📁 {p.category}</p>}
            </div>

            {/* Actions */}
            <div className="flex gap-2">
              {tab === 'available' && (
                <button
                  onClick={() => handleInstall(p.id)}
                  className="flex items-center gap-2 px-3 py-2 text-sm bg-green-500/20 text-green-700 hover:bg-green-500/30 rounded font-medium transition-all"
                >
                  <Download className="h-4 w-4" /> Install
                </button>
              )}

              {tab === 'installed' && (
                <>
                  <button className="flex items-center gap-2 px-3 py-2 text-sm bg-blue-500/20 text-blue-700 hover:bg-blue-500/30 rounded">
                    <Settings className="h-4 w-4" /> Settings
                  </button>
                  <button
                    onClick={() => handleUninstall(p.id)}
                    className="flex items-center gap-2 px-3 py-2 text-sm bg-red-500/20 text-red-700 hover:bg-red-500/30 rounded"
                  >
                    <Trash2 className="h-4 w-4" /> Remove
                  </button>
                </>
              )}
            </div>
          </div>
        ))}

        {plugins.length === 0 && (
          <div className="col-span-full text-center py-12 text-muted-foreground">
            {loading ? 'Loading...' : tab === 'available' ? 'No plugins available' : 'No plugins installed yet'}
          </div>
        )}
      </div>
    </div>
  );
}
