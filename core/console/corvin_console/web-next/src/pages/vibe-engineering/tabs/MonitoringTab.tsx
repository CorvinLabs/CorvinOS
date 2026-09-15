import { useEffect, useState } from 'react';
import { Loader2, AlertCircle, TrendingUp } from 'lucide-react';

interface Metric {
  name: string;
  value: number;
  unit: string;
  status?: string;
}

export function MonitoringTab() {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const res = await fetch('/v1/monitoring/metrics?range=1h');
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();
        setMetrics(data.metrics || []);
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>;

  const getStatusColor = (status?: string) => {
    if (status === 'ok') return 'bg-green-500/20 text-green-700';
    if (status === 'warning') return 'bg-yellow-500/20 text-yellow-700';
    return 'bg-red-500/20 text-red-700';
  };

  return (
    <div className="space-y-4 p-4">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold">System Metrics (Last 1h)</h3>
        {error && <span className="flex items-center gap-2 text-xs text-red-500"><AlertCircle className="h-4 w-4" />{error}</span>}
      </div>

      <div className="grid grid-cols-2 gap-4">
        {metrics.map((m) => (
          <div key={m.name} className="border rounded-lg p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted-foreground">{m.name}</span>
              {m.status && <span className={`px-2 py-1 rounded text-xs font-medium ${getStatusColor(m.status)}`}>{m.status}</span>}
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-bold">{m.value.toFixed(2)}</span>
              <span className="text-xs text-muted-foreground">{m.unit}</span>
            </div>
            <div className="flex items-center gap-1 text-xs text-green-600">
              <TrendingUp className="h-3 w-3" />
              <span>Stable</span>
            </div>
          </div>
        ))}
      </div>

      {metrics.length === 0 && <div className="text-sm text-muted-foreground">No metrics available</div>}

      <div className="text-xs text-muted-foreground">
        Source: HealthMonitor • Updated: {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}
