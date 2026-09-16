import { useState, useEffect } from "react";
import { Gauge, RefreshCw, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface Metric {
  name: string;
  value: number;
  unit: string;
  timestamp: string;
}

interface AlertRule {
  id: string;
  name: string;
  query: string;
  threshold: number;
  active: boolean;
}

const formatMetric = (value: number, unit: string): string =>
  // Event counts are integers; only rates and durations want decimals.
  Number.isInteger(value) && unit !== '%' ? value.toLocaleString('en-US') : value.toFixed(2);

export function OTELTelemetryPage() {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [alerts, setAlerts] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [timeRange, setTimeRange] = useState("1h");

  useEffect(() => {
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 15000);
    return () => clearInterval(interval);
  }, [timeRange]);

  const fetchMetrics = async () => {
    setLoading(true);
    try {
      const response = await fetch(`/v1/console/v1/monitoring/metrics?range=${timeRange}`);
      const data = await response.json();
      setMetrics(data.metrics || []);
      setAlerts(data.alerts || []);
    } catch (error) {
      console.error("Failed to fetch metrics:", error);
      setMetrics([]);
    } finally {
      setLoading(false);
    }
  };

  const mainMetrics = metrics.slice(0, 4);
  const activeAlerts = alerts.filter((a) => a.active);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Gauge className="h-6 w-6" />
          <h1 className="text-2xl font-bold">OTEL Telemetry</h1>
        </div>
        <div className="flex gap-2">
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value)}
            className="px-3 py-2 border rounded-md text-sm"
          >
            <option value="1h">Last 1 Hour</option>
            <option value="6h">Last 6 Hours</option>
            <option value="24h">Last 24 Hours</option>
          </select>
          <Button
            variant="outline"
            size="sm"
            onClick={fetchMetrics}
            disabled={loading}
          >
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {activeAlerts.length > 0 && (
        <Card className="p-4 bg-red-50 border-red-200">
          <div className="flex gap-3">
            <AlertTriangle className="h-5 w-5 text-red-600 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <h3 className="font-semibold text-red-900">
                {activeAlerts.length} Active Alert{activeAlerts.length !== 1 ? "s" : ""}
              </h3>
              <div className="mt-2 space-y-1">
                {activeAlerts.map((alert) => (
                  <div key={alert.id} className="text-sm text-red-800">
                    {alert.name} — {alert.query} &gt; {alert.threshold}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        {mainMetrics.map((metric) => (
          <Card key={metric.name} className="p-4">
            <div className="space-y-2">
              <div className="text-sm font-medium text-muted-foreground">
                {metric.name}
              </div>
              <div className="text-2xl font-bold">
                {formatMetric(metric.value, metric.unit)}
                <span className="text-sm ml-1 font-normal">{metric.unit}</span>
              </div>
              <div className="text-xs text-muted-foreground">
                {new Date(metric.timestamp).toLocaleTimeString()}
              </div>
            </div>
          </Card>
        ))}
      </div>

      <Card className="p-4">
        <h3 className="text-lg font-semibold mb-4">Alert Rules</h3>
        {loading ? (
          <div className="text-center py-8 text-muted-foreground">
            Loading alert rules...
          </div>
        ) : (
          <div className="space-y-3">
            {alerts.length > 0 ? (
              alerts.map((alert) => (
                <div
                  key={alert.id}
                  className="flex items-center justify-between p-3 border rounded-lg"
                >
                  <div className="flex-1">
                    <div className="font-medium">{alert.name}</div>
                    <div className="text-sm text-muted-foreground">
                      {alert.query} &gt; {alert.threshold}
                    </div>
                  </div>
                  <Badge variant={alert.active ? "default" : "secondary"}>
                    {alert.active ? "Active" : "Inactive"}
                  </Badge>
                </div>
              ))
            ) : (
              <div className="text-center py-8 text-muted-foreground">
                No alert rules configured
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}
