import { useState } from 'react';
import { Loader2 } from 'lucide-react';
import { useVibeData } from './hooks/useVibeData';
import { BrainStatus } from './components/BrainStatus';
import { ContextIntelligence } from './components/ContextIntelligence';
import { LearningHub } from './components/LearningHub';

export function Dashboard() {
  const data = useVibeData(5000); // Poll every 5s
  const [activeTab, setActiveTab] = useState<'dashboard' | 'brain' | 'context' | 'learning' | 'sessions'>('dashboard');

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
    <div className="space-y-6">
      {/* Navigation Tabs */}
      <div className="flex gap-2 border-b">
        {(['dashboard', 'brain', 'context', 'learning', 'sessions'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-2 text-sm font-medium transition-colors ${
              activeTab === tab
                ? 'border-b-2 border-primary text-primary'
                : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Dashboard View (Primary) */}
      {activeTab === 'dashboard' && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <div className="space-y-4">
            <BrainStatus data={data} />
          </div>
          <div className="space-y-4">
            <ContextIntelligence
              data={data}
              onQualityGateChange={(policy) => {
                // TODO: call API to update quality gate
                console.log('Quality gate changed to:', policy);
              }}
            />
          </div>
          <div className="space-y-4">
            <LearningHub data={data} />
          </div>
        </div>
      )}

      {/* Brain Monitor View */}
      {activeTab === 'brain' && (
        <div className="rounded-lg border p-6 text-center text-muted-foreground">
          <p>Brain Monitor view coming soon...</p>
        </div>
      )}

      {/* Context Intelligence Detail */}
      {activeTab === 'context' && (
        <div className="rounded-lg border p-6 text-center text-muted-foreground">
          <p>Context Intelligence detail view coming soon...</p>
        </div>
      )}

      {/* Learning Hub Detail */}
      {activeTab === 'learning' && (
        <div className="rounded-lg border p-6 text-center text-muted-foreground">
          <p>Learning Hub detail view coming soon...</p>
        </div>
      )}

      {/* Session Explorer */}
      {activeTab === 'sessions' && (
        <div className="rounded-lg border p-6 text-center text-muted-foreground">
          <p>Session Explorer view coming soon...</p>
        </div>
      )}
    </div>
  );
}

export default Dashboard;
