import { useVibeData } from './vibe-engineering/hooks/useVibeData';
import { BrainStatus } from './vibe-engineering/components/BrainStatus';
import { Loader2 } from 'lucide-react';

export function BrainStatusPage() {
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

  return <BrainStatus data={data} />;
}

export default BrainStatusPage;
