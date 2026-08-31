import { useVibeData } from './vibe-engineering/hooks/useVibeData';
import { ContextIntelligence } from './vibe-engineering/components/ContextIntelligence';
import { Loader2 } from 'lucide-react';

export function ContextIntelligencePage() {
  const data = useVibeData(5000);

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
        <p className="text-sm text-red-700">Error: {data.error}</p>
      </div>
    );
  }

  return (
    <ContextIntelligence
      data={data}
      onQualityGateChange={(policy) => {
        console.log('Quality gate changed to:', policy);
      }}
    />
  );
}

export default ContextIntelligencePage;
