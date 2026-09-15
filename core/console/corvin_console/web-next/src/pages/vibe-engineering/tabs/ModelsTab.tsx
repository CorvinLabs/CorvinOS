import { useEffect, useState } from 'react';
import { Loader2, AlertCircle, Zap } from 'lucide-react';

interface Model {
  id: string;
  name: string;
  provider: string;
  cost_per_1k: number;
  latency_ms: number;
  capabilities: string[];
}

export function ModelsTab() {
  const [models, setModels] = useState<Model[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchModels = async () => {
      try {
        const res = await fetch('/v1/models/available');
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();
        setModels(data.models || []);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchModels();
    const interval = setInterval(fetchModels, 60000); // Refresh every min
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>;

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Available Models</h3>
        {error && <span className="flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-4 w-4" />{error}</span>}
      </div>

      <div className="space-y-3">
        {models.map((m) => (
          <div key={m.id} className="border rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <h4 className="font-semibold">{m.name}</h4>
                <p className="text-xs text-muted-foreground">{m.provider}</p>
              </div>
              <div className="text-right space-y-1">
                <div className="text-sm font-mono">${m.cost_per_1k.toFixed(4)}/1K</div>
                <div className="flex items-center gap-1 text-xs text-blue-600">
                  <Zap className="h-3 w-3" />
                  <span>{m.latency_ms}ms</span>
                </div>
              </div>
            </div>

            <div className="flex flex-wrap gap-1">
              {m.capabilities.map((cap) => (
                <span key={cap} className="px-2 py-1 rounded-sm bg-muted text-xs font-medium">
                  {cap}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>

      {models.length === 0 && <div className="text-sm text-muted-foreground">No models available</div>}

      <div className="text-xs text-muted-foreground">
        Registry: Engine Manager • Last sync: {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}
