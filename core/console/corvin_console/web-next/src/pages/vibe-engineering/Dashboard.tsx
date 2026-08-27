/**
 * Vibe Engineering Dashboard — the group's primary view (ADR-0400).
 *
 * Three columns: BrainStatus · ContextIntelligence · LearningHub, over the live
 * /vibe-engineering/state poll. The secondary views (Brain Monitor, Context
 * Intelligence detail, Learning Hub detail, Session Explorer) used to be stub
 * tabs here saying "coming soon"; they are real sidebar panels now, so this page
 * links to them instead of pretending to host them.
 */
import { Loader2 } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useVibeData } from './hooks/useVibeData';
import { BrainStatus } from './components/BrainStatus';
import { ContextIntelligence } from './components/ContextIntelligence';
import { LearningHub } from './components/LearningHub';
import { DebugPanel } from './components/DebugPanel';

const SECONDARY = [
  { to: '/app/brain-monitor', label: 'Brain Monitor', hint: 'per-stage telemetry + grading' },
  { to: '/app/context-intelligence', label: 'Context Intelligence', hint: 'pipeline layers + entropy' },
  { to: '/app/learning-hub', label: 'Learning Hub', hint: 'talent + feedback loops' },
  { to: '/app/session-explorer', label: 'Session Explorer', hint: 'turn history + drill-down' },
];

export function Dashboard() {
  const data = useVibeData(5000); // Poll every 5s

  if (data.loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (data.error) {
    return (
      <div className="rounded-lg border border-red-500/50 bg-red-500/5 p-4">
        <p className="text-sm text-red-700">Error loading Vibe Engineering data: {data.error}</p>
      </div>
    );
  }

  return (
    <div className="space-y-6" data-testid="vibe-dashboard">
      <header className="space-y-1">
        <h1 className="text-xl font-semibold">Vibe Engineering Dashboard</h1>
        <p className="text-sm text-muted-foreground">
          What the brain is doing right now — and why.
        </p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        <div className="space-y-4">
          <BrainStatus data={data} />
        </div>
        <div className="space-y-4">
          <ContextIntelligence
            data={data}
            onQualityGateChange={(policy) => {
              // The /config endpoint is read-only today; the selector is a local
              // preview until a PUT lands. Deliberately not faking a persist.
              console.log('Quality gate changed to:', policy);
            }}
          />
        </div>
        <div className="space-y-4">
          <LearningHub data={data} />
        </div>
      </div>

      <nav className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
        {SECONDARY.map((s) => (
          <Link
            key={s.to}
            to={s.to}
            className="rounded-lg border border-border p-3 transition hover:border-primary/50"
          >
            <p className="text-sm font-medium">{s.label}</p>
            <p className="text-xs text-muted-foreground">{s.hint}</p>
          </Link>
        ))}
      </nav>

      {/* Debug Panel — Real Data Inspector (retired as its own sidebar entry) */}
      <div className="mt-8 border-t pt-6">
        <DebugPanel data={data} />
      </div>
    </div>
  );
}

export default Dashboard;
