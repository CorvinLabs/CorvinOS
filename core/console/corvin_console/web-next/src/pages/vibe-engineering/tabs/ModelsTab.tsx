/**
 * Models tab — engine registry with published rates (ADR-0856).
 *
 * Was typed against a response that no longer exists, and in practice never
 * did: `cost_per_1k` collapsed two rates that differ 5x, `latency_ms` was a
 * constant the API invented (50/20/10), and the fetch omitted the /v1/console
 * prefix so every request 404'd.
 */
import { useEffect, useState } from 'react';
import { Loader2, AlertCircle } from 'lucide-react';

interface Model {
  id: string;
  name: string;
  engines: string[];
  turns: string[];
  input_usd_per_1k: number | null;
  output_usd_per_1k: number | null;
  priced: boolean;
}

/** "—" means not on the published rate card, never free. */
const perMillion = (perThousand: number | null | undefined): string =>
  perThousand === null || perThousand === undefined
    ? '—'
    : `$${(perThousand * 1000).toFixed(2)}`;

export function ModelsTab() {
  const [models, setModels] = useState<Model[]>([]);
  const [note, setNote] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchModels = async () => {
      try {
        const res = await fetch('/v1/console/v1/models/available');
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();
        setModels(data.models || []);
        setNote(data.available === false ? (data.detail || 'Model registry not available on this build.') : '');
        setError(null);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchModels();
    const interval = setInterval(fetchModels, 60000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>;

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">Available Models</h3>
        {error && <span className="flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-4 w-4" />{error}</span>}
      </div>

      {note && <div className="text-sm text-muted-foreground">{note}</div>}

      <div className="space-y-3">
        {models.map((m) => (
          <div key={m.id} className="border rounded-lg p-4 space-y-3">
            <div className="flex items-start justify-between gap-4">
              <div className="space-y-1 min-w-0">
                <h4 className="font-semibold break-all">{m.name}</h4>
                <p className="text-xs text-muted-foreground break-all">{m.id}</p>
              </div>
              <div className="text-right space-y-1 shrink-0">
                <div className="text-sm font-mono">{perMillion(m.input_usd_per_1k)} in</div>
                <div className="text-sm font-mono">{perMillion(m.output_usd_per_1k)} out</div>
                <div className="text-xs text-muted-foreground">per 1M tokens</div>
              </div>
            </div>

            <div className="flex flex-wrap gap-1">
              {m.engines.map((e) => (
                <span key={e} className="px-2 py-1 rounded-sm bg-muted text-xs font-medium">{e}</span>
              ))}
              {m.turns.map((t) => (
                <span key={t} className="px-2 py-1 rounded-sm border text-xs font-medium">{t}</span>
              ))}
            </div>
          </div>
        ))}
      </div>

      {models.length === 0 && !note && (
        <div className="text-sm text-muted-foreground">The engine registry declares no models.</div>
      )}
    </div>
  );
}
