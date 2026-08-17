/**
 * TreeOfThoughts Learning Dashboard Page
 * Route: /learning
 */
import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import LearningDashboard from '../components/LearningDashboard';

interface FetchResponse {
  nodes: any[];
}

export default function LearningPage() {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Fetch learning nodes from backend
  const { data, isLoading, error } = useQuery<FetchResponse>({
    queryKey: ['learning-nodes'],
    queryFn: async () => {
      const response = await fetch('/learning/nodes');
      if (!response.ok) throw new Error('Failed to fetch learning nodes');
      return response.json();
    },
    refetchInterval: 30000, // Refresh every 30s
  });

  if (!mounted) return null;

  if (isLoading) return <div style={{ padding: '20px' }}>Loading...</div>;
  if (error) return <div style={{ padding: '20px', color: 'red' }}>Error: {String(error)}</div>;

  return (
    <div data-testid="learning-page">
      <LearningDashboard nodes={data?.nodes || []} />
    </div>
  );
}

// Export for testing
export const LearningPageTest = { queryKey: 'learning-nodes', url: '/learning/nodes' };
