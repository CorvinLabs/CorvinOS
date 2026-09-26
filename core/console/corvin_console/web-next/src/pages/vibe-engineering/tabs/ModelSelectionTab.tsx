import { useEffect, useState, useMemo } from 'react';
import { Loader2, AlertCircle, BarChart3, DollarSign, Zap } from 'lucide-react';

interface ModelInfo {
  model_id: string;
  name: string;
  cost_per_1k_tokens: number;
  avg_latency_ms: number;
  quality_score: number;
  context_window: number;
}

interface ModelsResponse {
  models: ModelInfo[];
  default_model: string;
  last_updated: string;
}

const getCostColor = (cost: number): string => {
  if (cost < 0.0001) return 'text-green-600';
  if (cost < 0.0005) return 'text-blue-600';
  if (cost < 0.001) return 'text-yellow-600';
  return 'text-red-600';
};

const getQualityColor = (score: number): string => {
  if (score >= 0.9) return 'bg-green-500/20 text-green-700';
  if (score >= 0.75) return 'bg-blue-500/20 text-blue-700';
  if (score >= 0.6) return 'bg-yellow-500/20 text-yellow-700';
  return 'bg-red-500/20 text-red-700';
};

export function ModelSelectionTab() {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [costFilter, setCostFilter] = useState<'all' | 'cheap' | 'balanced' | 'premium'>('all');

  useEffect(() => {
    const fetchModels = async () => {
      try {
        const res = await fetch('/v1/console/v1/models/available');
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data: ModelsResponse = await res.json();
        setModels(data.models || []);
        setSelectedModel(data.default_model || '');
      } catch (e) {
        setError(e instanceof Error ? e.message : 'Failed to load models');
      } finally {
        setLoading(false);
      }
    };

    fetchModels();
  }, []);

  const filteredModels = useMemo(() => {
    return models.filter((m) => {
      if (costFilter === 'cheap') return m.cost_per_1k_tokens < 0.0001;
      if (costFilter === 'balanced') return m.cost_per_1k_tokens >= 0.0001 && m.cost_per_1k_tokens < 0.0005;
      if (costFilter === 'premium') return m.cost_per_1k_tokens >= 0.0005;
      return true;
    });
  }, [models, costFilter]);

  const selectedModelData = models.find((m) => m.model_id === selectedModel);

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div>
        <h3 className="text-lg font-semibold">Model Selection & Cost Analysis</h3>
        <p className="text-sm text-muted-foreground">
          Available models with cost, latency, and quality metrics
        </p>
      </div>

      {/* Error Alert */}
      {error && (
        <div className="flex items-center gap-2 p-3 rounded bg-red-500/10 text-red-700 text-sm border border-red-500/20">
          <AlertCircle className="h-4 w-4" />
          {error}
        </div>
      )}

      {/* Cost Filter */}
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium text-muted-foreground">Filter by Cost:</span>
        {(['all', 'cheap', 'balanced', 'premium'] as const).map((filter) => (
          <button
            key={filter}
            onClick={() => setCostFilter(filter)}
            className={`px-3 py-1 rounded text-sm font-medium capitalize ${
              costFilter === filter
                ? 'bg-primary text-primary-foreground'
                : 'bg-muted text-muted-foreground hover:bg-muted/80'
            }`}
          >
            {filter}
          </button>
        ))}
      </div>

      {/* Model Matrix */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {filteredModels.map((model) => (
          <div
            key={model.model_id}
            onClick={() => setSelectedModel(model.model_id)}
            className={`p-4 rounded-lg border cursor-pointer transition-all ${
              selectedModel === model.model_id
                ? 'border-primary bg-primary/5'
                : 'border-muted hover:border-primary/50'
            }`}
          >
            <div className="flex items-start justify-between mb-3">
              <h4 className="font-medium text-sm">{model.name}</h4>
              {selectedModel === model.model_id && (
                <span className="px-2 py-1 rounded text-xs font-medium bg-primary text-primary-foreground">
                  Selected
                </span>
              )}
            </div>

            <div className="space-y-2 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground flex items-center gap-1">
                  <DollarSign className="h-3 w-3" />
                  Cost / 1K tokens
                </span>
                <span className={`font-mono font-semibold ${getCostColor(model.cost_per_1k_tokens)}`}>
                  ${model.cost_per_1k_tokens.toFixed(6)}
                </span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground flex items-center gap-1">
                  <Zap className="h-3 w-3" />
                  Avg Latency
                </span>
                <span className="font-mono font-semibold text-blue-600">
                  {model.avg_latency_ms.toFixed(0)}ms
                </span>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-muted-foreground flex items-center gap-1">
                  <BarChart3 className="h-3 w-3" />
                  Quality Score
                </span>
                <span className={`px-2 py-0.5 rounded font-medium ${getQualityColor(model.quality_score)}`}>
                  {(model.quality_score * 100).toFixed(0)}%
                </span>
              </div>

              <div className="flex items-center justify-between pt-2 border-t">
                <span className="text-muted-foreground">Context Window</span>
                <span className="font-mono font-semibold">{model.context_window.toLocaleString()} tokens</span>
              </div>
            </div>
          </div>
        ))}
      </div>

      {filteredModels.length === 0 && (
        <div className="text-center py-8 text-muted-foreground">
          No models match the selected cost filter
        </div>
      )}

      {/* Selected Model Details */}
      {selectedModelData && (
        <div className="p-4 rounded-lg border bg-muted/30 space-y-3">
          <h4 className="font-semibold text-sm">💡 Recommendation for "{selectedModelData.name}"</h4>
          <p className="text-xs text-muted-foreground leading-relaxed">
            This model is best suited for {
              selectedModelData.cost_per_1k_tokens < 0.0001
                ? 'cost-sensitive workloads with high throughput requirements.'
                : selectedModelData.quality_score > 0.85
                  ? 'high-quality outputs where accuracy is paramount.'
                  : 'balanced use cases with good cost-quality tradeoffs.'
            }
          </p>
          <div className="text-xs font-mono text-muted-foreground">
            Model ID: {selectedModelData.model_id}
          </div>
        </div>
      )}

      <div className="text-xs text-muted-foreground pt-2 border-t">
        Source: Model Registry • Updated: {new Date().toLocaleTimeString()}
      </div>
    </div>
  );
}

export default ModelSelectionTab;
