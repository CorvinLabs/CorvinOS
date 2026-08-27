import { useState, useEffect } from 'react';

const API_BASE = '/v1/console';

export interface WorkerStatus {
  name: string;
  status: 'running' | 'thinking' | 'blocked' | 'idle';
  latency_ms?: number;
  error_count?: number;
}

export interface Decision {
  id: string;
  type: string;
  confidence: number;
  outcome?: string;
  timestamp: string;
}

export interface ContextLayer {
  id: string;
  text: string;
  tier: 'tier_1' | 'tier_2' | 'tier_3';
  source: string;
  confidence: number;
  timestamp: string;
}

export interface OriginalContextData {
  task_description: string;
  user_intent: string;
  hash_sha256: string;
  is_valid: boolean;
  created_at: string;
}

export interface PipelineContextData {
  entropy_score: number;
  tier_1_count: number;
  tier_2_count: number;
  tier_3_count: number;
  recent_additions: ContextLayer[];
}

export interface TalentData {
  score: number;
  context_relevance: number;
  decision_quality: number;
  outcome_accuracy: number;
  sparkline: number[];
}

export interface VibeData {
  active_task?: {
    title: string;
    phase: string;
    elapsed_seconds: number;
  };
  workers: WorkerStatus[];
  decision_queue: Decision[];
  recent_decisions: Decision[];
  original_context: OriginalContextData;
  pipeline_context: PipelineContextData;
  talent: TalentData;
  quality_gate_policy: 'tier_1' | 'tier_2' | 'tier_3';
  /** Present only when the state call is made with ?debug=true. DebugPanel
   *  renders null without it, which is why it must be carried through here. */
  debug?: {
    events_count: number;
    latest_event?: unknown;
    all_events?: unknown[];
  };
  loading: boolean;
  error?: string;
}

/**
 * Fetch live Vibe Engineering data from backend.
 * Polls /v1/console/vibe-engineering/state + /v1/console/vibe-engineering/config every 5s.
 */
export function useVibeData(pollIntervalMs = 5000): VibeData {
  const [data, setData] = useState<VibeData>({
    workers: [],
    decision_queue: [],
    recent_decisions: [],
    original_context: {
      task_description: '',
      user_intent: '',
      hash_sha256: '',
      is_valid: true,
      created_at: new Date().toISOString(),
    },
    pipeline_context: {
      entropy_score: 0,
      tier_1_count: 0,
      tier_2_count: 0,
      tier_3_count: 0,
      recent_additions: [],
    },
    talent: {
      score: 0,
      context_relevance: 0,
      decision_quality: 0,
      outcome_accuracy: 0,
      sparkline: [],
    },
    quality_gate_policy: 'tier_1',
    loading: true,
  });

  useEffect(() => {
    const fetchData = async () => {
      try {
        // The console API is mounted under /v1/console (see src/lib/api.ts); a bare
        // '/vibe-engineering/...' hits the SPA mount and 404s. Session auth rides on
        // the corvin_console_sid cookie, so credentials must be included.
        const [stateRes, configRes] = await Promise.all([
          fetch(`${API_BASE}/vibe-engineering/state?debug=true&limit=50`, { credentials: 'include' }),
          fetch(`${API_BASE}/vibe-engineering/config`, { credentials: 'include' }),
        ]);

        if (!stateRes.ok || !configRes.ok) {
          throw new Error(`API error: ${stateRes.status} ${configRes.status}`);
        }

        const state = await stateRes.json();
        const config = await configRes.json();

        setData({
          active_task: state.active_task || {
            title: 'No active task',
            phase: 'idle',
            elapsed_seconds: 0,
          },
          workers: state.workers || [],
          decision_queue: state.decision_queue || [],
          recent_decisions: state.recent_decisions || [],
          original_context: state.original_context || {
            task_description: '',
            user_intent: '',
            hash_sha256: '',
            is_valid: true,
            created_at: new Date().toISOString(),
          },
          pipeline_context: state.pipeline_context || {
            entropy_score: 0,
            tier_1_count: 0,
            tier_2_count: 0,
            tier_3_count: 0,
            recent_additions: [],
          },
          talent: state.talent || {
            score: 0,
            context_relevance: 0,
            decision_quality: 0,
            outcome_accuracy: 0,
            sparkline: [],
          },
          quality_gate_policy: config.quality_gate_policy || 'tier_1',
          debug: state.debug,
          loading: false,
        });
      } catch (err) {
        setData(prev => ({
          ...prev,
          error: err instanceof Error ? err.message : 'Unknown error',
          loading: false,
        }));
      }
    };

    fetchData();
    const interval = setInterval(fetchData, pollIntervalMs);
    return () => clearInterval(interval);
  }, [pollIntervalMs]);

  return data;
}
