import { useEffect, useState } from 'react';
import { Loader2, AlertCircle, TrendingUp, CheckCircle } from 'lucide-react';

interface LearningEvent {
  timestamp: string;
  event_type: string;
  skill_id: string;
  confidence: number;
  feedback_received: boolean;
  outcome: 'success' | 'partial' | 'failed';
}

interface LearningMetrics {
  total_events: number;
  avg_confidence: number;
  success_rate: number;
  feedback_rate: number;
}

export function LearningLoopIntegrationTab() {
  const [events, setEvents] = useState<LearningEvent[]>([]);
  const [metrics, setMetrics] = useState<LearningMetrics | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchLearningData = async () => {
      try {
        // Fetch learning loop events
        const res = await fetch('/v1/console/v1/learning/events?limit=20&sort=timestamp_desc');
        if (!res.ok && res.status !== 404) throw new Error(`API ${res.status}`);

        if (res.ok) {
          const data = await res.json();
          setEvents(data.events || []);

          // Compute metrics
          if (data.events && data.events.length > 0) {
            const total = data.events.length;
            const successful = data.events.filter((e: LearningEvent) => e.outcome === 'success').length;
            const withFeedback = data.events.filter((e: LearningEvent) => e.feedback_received).length;
            const avgConf = data.events.reduce((sum: number, e: LearningEvent) => sum + e.confidence, 0) / total;

            setMetrics({
              total_events: total,
              avg_confidence: avgConf,
              success_rate: (successful / total) * 100,
              feedback_rate: (withFeedback / total) * 100,
            });
          }
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load');
      } finally {
        setLoading(false);
      }
    };

    fetchLearningData();
    const interval = setInterval(fetchLearningData, 30000);
    return () => clearInterval(interval);
  }, []);

  if (loading) return <div className="flex justify-center py-8"><Loader2 className="h-5 w-5 animate-spin" /></div>;

  return (
    <div className="space-y-4 p-6">
      <div>
        <h3 className="text-lg font-semibold">Learning Loop Status</h3>
        <p className="text-sm text-muted-foreground">Skill confidence, feedback loops, and optimization progress</p>
      </div>

      {error && <div className="flex items-center gap-2 p-3 rounded bg-yellow-500/10 text-yellow-700 text-sm border border-yellow-500/20"><AlertCircle className="h-4 w-4" />{error}</div>}

      {/* Metrics Cards */}
      {metrics && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="border rounded-lg p-3 space-y-1">
            <span className="text-xs text-muted-foreground">Total Events</span>
            <div className="text-2xl font-bold">{metrics.total_events}</div>
          </div>
          <div className="border rounded-lg p-3 space-y-1">
            <span className="text-xs text-muted-foreground">Avg Confidence</span>
            <div className="text-2xl font-bold">{(metrics.avg_confidence * 100).toFixed(0)}%</div>
          </div>
          <div className="border rounded-lg p-3 space-y-1">
            <span className="text-xs text-muted-foreground">Success Rate</span>
            <div className="text-2xl font-bold text-green-600">{metrics.success_rate.toFixed(0)}%</div>
          </div>
          <div className="border rounded-lg p-3 space-y-1">
            <span className="text-xs text-muted-foreground">Feedback Rate</span>
            <div className="text-2xl font-bold text-blue-600">{metrics.feedback_rate.toFixed(0)}%</div>
          </div>
        </div>
      )}

      {/* Recent Events */}
      <div className="border rounded-lg">
        <div className="bg-muted/50 px-4 py-2 border-b"><h4 className="text-sm font-medium">Recent Learning Events</h4></div>
        {events.length === 0 ? (
          <div className="p-4 text-sm text-muted-foreground text-center">No learning events yet</div>
        ) : (
          <div className="divide-y">
            {events.map((evt, i) => (
              <div key={`${evt.timestamp}-${i}`} className="p-3 text-xs hover:bg-muted/30">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono">{evt.skill_id}</span>
                  {evt.outcome === 'success' && <CheckCircle className="h-4 w-4 text-green-600" />}
                </div>
                <div className="flex items-center justify-between text-muted-foreground">
                  <span>{evt.event_type}</span>
                  <span>{evt.feedback_received ? '💬 Feedback' : '—'}</span>
                  <span className="font-mono">{(evt.confidence * 100).toFixed(0)}%</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="text-xs text-muted-foreground">Source: Learning Event Store • Last updated: {new Date().toLocaleTimeString()}</div>
    </div>
  );
}

export default LearningLoopIntegrationTab;
