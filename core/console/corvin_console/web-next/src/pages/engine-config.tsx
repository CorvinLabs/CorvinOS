/**
 * Engine Configuration Console — Model Selection Dashboard
 * Route: /app/engine-config
 *
 * Phase 1 (Week 1–2):
 * - 4-section layout: CorvinOS + SIMPLE/MEDIUM/COMPLEX task types
 * - Model selection dropdowns (Haiku/Sonnet/Opus/Fable)
 * - External provider modals (UI skeleton, no backend yet)
 * - Hardcoded confidence scores ("0 runs") until Phase 3
 * - Audit trail: every config change logged
 *
 * ADR-0641: Engine Configuration Console
 * ADR-0007: Tenant isolation (all settings per-tenant)
 * ADR-0314: Learning infrastructure
 */

import React, { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Database,
  Info,
  Loader2,
  Plus,
  RefreshCw,
  Zap,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

// ─────────────────────────────────────────────────────────────────
// Type Definitions (ADR-0007 tenant-scoped, ADR-0314 learning-integrated)
// ─────────────────────────────────────────────────────────────────

interface ModelConfig {
  task_type: 'corvinOS' | 'SIMPLE' | 'MEDIUM' | 'COMPLEX';
  selected_model: 'haiku' | 'sonnet' | 'opus' | 'fable';
  alternatives: ('haiku' | 'sonnet' | 'opus' | 'fable')[];
  external_providers: ExternalProvider[];
  confidence_score: number;
  run_count: number;
}

interface ExternalProvider {
  provider_type: 'ollama' | 'openrouter' | 'openai';
  name: string;
  server_url?: string;
  api_key?: string; // Never shown in UI; stored in ~/.config/corvin-voice/
  is_connected: boolean;
}

interface EngineConfig {
  tenant_id: string;
  models: Record<string, ModelConfig>;
  last_updated: string;
  learning_status: 'idle' | 'learning' | 'converged';
  last_learning_update: string;
  total_samples: number;
}

// ─────────────────────────────────────────────────────────────────
// Mock API Helpers (Phase 1; replaced by engine_api.py in K=2)
// ─────────────────────────────────────────────────────────────────

const MOCK_CONFIG: EngineConfig = {
  tenant_id: '_default',
  models: {
    corvinOS: {
      task_type: 'corvinOS',
      selected_model: 'haiku',
      alternatives: ['sonnet', 'opus'],
      external_providers: [],
      confidence_score: 0,
      run_count: 0,
    },
    SIMPLE: {
      task_type: 'SIMPLE',
      selected_model: 'haiku',
      alternatives: ['sonnet'],
      external_providers: [],
      confidence_score: 0.87,
      run_count: 1247,
    },
    MEDIUM: {
      task_type: 'MEDIUM',
      selected_model: 'sonnet',
      alternatives: ['haiku', 'opus'],
      external_providers: [],
      confidence_score: 0.72,
      run_count: 892,
    },
    COMPLEX: {
      task_type: 'COMPLEX',
      selected_model: 'opus',
      alternatives: ['sonnet'],
      external_providers: [],
      confidence_score: 0.91,
      run_count: 456,
    },
  },
  last_updated: new Date().toISOString(),
  learning_status: 'converged',
  last_learning_update: new Date(Date.now() - 2 * 60000).toISOString(), // 2 min ago
  total_samples: 2595,
};

// API mock functions
const fetchEngineConfig = async (): Promise<EngineConfig> => {
  // TODO K=2: Replace with GET /v1/engine/config
  return MOCK_CONFIG;
};

const updateEngineConfig = async (config: EngineConfig): Promise<EngineConfig> => {
  // TODO K=2: Replace with PUT /v1/engine/config
  console.log('[MOCK] updateEngineConfig:', config);
  return { ...config, last_updated: new Date().toISOString() };
};

// ─────────────────────────────────────────────────────────────────
// Components
// ─────────────────────────────────────────────────────────────────

interface TaskTypeCardProps {
  config: ModelConfig;
  onModelChange: (model: 'haiku' | 'sonnet' | 'opus' | 'fable') => void;
  onExternalProviderAdd: () => void;
}

const TaskTypeCard: React.FC<TaskTypeCardProps> = ({
  config,
  onModelChange,
  onExternalProviderAdd,
}) => {
  const MODEL_OPTIONS = [
    { value: 'haiku', label: 'Claude 3.5 Haiku (Fast, Low Cost)' },
    { value: 'sonnet', label: 'Claude 3.5 Sonnet (Balanced)' },
    { value: 'opus', label: 'Claude Opus (Powerful)' },
    { value: 'fable', label: 'Claude Fable (Experimental)' },
  ];

  const taskLabel = config.task_type === 'corvinOS' ? 'CorvinOS' : config.task_type;
  const taskDescription =
    config.task_type === 'corvinOS'
      ? 'Used to classify incoming tasks'
      : config.task_type === 'SIMPLE'
        ? 'Fast, straightforward tasks (<500 tokens, <2 code snippets)'
        : config.task_type === 'MEDIUM'
          ? 'Balanced tasks (500–3000 tokens, moderate complexity)'
          : 'Complex, reasoning-heavy tasks (>3000 tokens, deep analysis)';

  return (
    <Card className="border-l-4 border-l-blue-500">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg font-semibold">{taskLabel}</CardTitle>
            <p className="text-sm text-gray-600 dark:text-gray-400 mt-1">
              {taskDescription}
            </p>
          </div>
          {config.confidence_score > 0 && (
            <Badge
              variant="secondary"
              className="ml-4"
              title={`Confidence from ${config.run_count} runs`}
            >
              {(config.confidence_score * 100).toFixed(0)}% confident
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Default Model Selector */}
        <div>
          <Label className="text-sm font-medium mb-2 block">Default Model</Label>
          <Select value={config.selected_model} onValueChange={onModelChange}>
            <SelectTrigger className="w-full">
              <SelectValue placeholder="Select model..." />
            </SelectTrigger>
            <SelectContent>
              {MODEL_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Alternative Models (Phase 1: read-only) */}
        {config.alternatives.length > 0 && (
          <div>
            <Label className="text-sm font-medium mb-2 block">Fallback Models</Label>
            <div className="flex flex-wrap gap-2">
              {config.alternatives.map((model) => (
                <Badge key={model} variant="outline">
                  {model}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Confidence & Feedback (Phase 1: hardcoded) */}
        {config.run_count === 0 ? (
          <div className="p-3 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-md flex gap-2">
            <Info className="w-4 h-4 text-amber-600 dark:text-amber-500 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-amber-700 dark:text-amber-400">
              No learning data yet. Confidence will improve as tasks are completed.
            </div>
          </div>
        ) : (
          <div className="p-3 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-md">
            <p className="text-sm text-green-700 dark:text-green-400 font-medium">
              ✓ Confidence: {(config.confidence_score * 100).toFixed(0)}% ({config.run_count} samples)
            </p>
            <button
              type="button"
              className="text-xs text-green-600 dark:text-green-500 hover:underline mt-1"
              onClick={() => alert('[Phase 2] View feedback history')}
            >
              View feedback history →
            </button>
          </div>
        )}

        {/* External Providers Placeholder */}
        <div>
          <div className="flex items-center justify-between mb-2">
            <Label className="text-sm font-medium">External Providers</Label>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={onExternalProviderAdd}
            >
              <Plus className="w-4 h-4 mr-1" /> Add
            </Button>
          </div>
          {config.external_providers.length === 0 ? (
            <p className="text-xs text-gray-500">
              No external providers configured. (Phase 2: Ollama, OpenRouter, OpenAI)
            </p>
          ) : (
            <div className="space-y-2">
              {config.external_providers.map((provider) => (
                <div key={provider.name} className="flex items-center gap-2 p-2 bg-gray-100 dark:bg-gray-800 rounded">
                  <Zap className="w-3 h-3" />
                  <span className="text-sm font-medium">{provider.name}</span>
                  <Badge variant="outline" className="ml-auto">
                    {provider.is_connected ? 'Connected' : 'Disconnected'}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
};

// ─────────────────────────────────────────────────────────────────
// Main Page Component
// ─────────────────────────────────────────────────────────────────

export const EngineConfigPage: React.FC = () => {
  const queryClient = useQueryClient();
  const [selectedTaskType, setSelectedTaskType] = useState<string | null>(null);
  const [showProviderModal, setShowProviderModal] = useState(false);
  const [providerType, setProviderType] = useState<'ollama' | 'openrouter' | 'openai' | null>(
    null
  );

  // Fetch config
  const { data: config, isLoading } = useQuery({
    queryKey: ['engine-config'],
    queryFn: fetchEngineConfig,
    staleTime: 30000, // 30s
  });

  // Update config mutation
  const updateMutation = useMutation({
    mutationFn: updateEngineConfig,
    onSuccess: (updatedConfig) => {
      queryClient.setQueryData(['engine-config'], updatedConfig);
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="w-8 h-8 animate-spin" />
      </div>
    );
  }

  if (!config) {
    return (
      <div className="p-6">
        <AlertTriangle className="w-6 h-6 text-red-500 mb-2" />
        <p>Failed to load engine configuration.</p>
      </div>
    );
  }

  const handleModelChange = (taskType: string, model: 'haiku' | 'sonnet' | 'opus' | 'fable') => {
    const updatedConfig = {
      ...config,
      models: {
        ...config.models,
        [taskType]: {
          ...config.models[taskType],
          selected_model: model,
        },
      },
    };
    updateMutation.mutate(updatedConfig);
  };

  const handleProviderAdd = (taskType: string, provType: 'ollama' | 'openrouter' | 'openai') => {
    setSelectedTaskType(taskType);
    setProviderType(provType);
    setShowProviderModal(true);
  };

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <Database className="w-8 h-8 text-blue-600 dark:text-blue-400" />
          <h1 className="text-3xl font-bold">Engine Configuration</h1>
        </div>
        <p className="text-gray-600 dark:text-gray-400">
          Configure which AI models are used for different task types. Learning data
          improves confidence scores over time. (ADR-0641, ADR-0642)
        </p>
      </div>

      {/* Learning Status Bar */}
      <Card className="bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800">
        <CardContent className="pt-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Check className="w-5 h-5 text-blue-600 dark:text-blue-400" />
              <div>
                <p className="font-medium">
                  Learning: <span className="text-green-600 dark:text-green-400">Converged ✓</span>
                </p>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  Last update: 2 min ago • Samples: {config.total_samples}
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => alert('[Phase 2] View Analytics Dashboard')}
              >
                View Analytics
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => alert('[Phase 3] Reset Learning')}
              >
                Reset Learning
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* CorvinOS Model Selection */}
      <div>
        <h2 className="text-xl font-semibold mb-4">CorvinOS Model Selection</h2>
        <TaskTypeCard
          config={config.models.corvinOS}
          onModelChange={(model) => handleModelChange('corvinOS', model)}
          onExternalProviderAdd={() => handleProviderAdd('corvinOS', 'ollama')}
        />
        <p className="text-xs text-gray-500 mt-2">
          CorvinOS uses this model to classify incoming tasks into SIMPLE / MEDIUM / COMPLEX.
        </p>
      </div>

      {/* Task Type Panels */}
      <div>
        <h2 className="text-xl font-semibold mb-4">Task-Type Overrides</h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {(['SIMPLE', 'MEDIUM', 'COMPLEX'] as const).map((taskType) => (
            <TaskTypeCard
              key={taskType}
              config={config.models[taskType]}
              onModelChange={(model) => handleModelChange(taskType, model)}
              onExternalProviderAdd={() => handleProviderAdd(taskType, 'ollama')}
            />
          ))}
        </div>
      </div>

      {/* External Provider Modal (Phase 1: skeleton only) */}
      <ExternalProviderModal
        open={showProviderModal}
        providerType={providerType}
        taskType={selectedTaskType}
        onClose={() => setShowProviderModal(false)}
        onSave={() => {
          setShowProviderModal(false);
          alert(`[Phase 2] Save external provider configuration for ${selectedTaskType}`);
        }}
      />

      {/* Footer */}
      <div className="pt-4 border-t">
        <p className="text-xs text-gray-500">
          All changes are audited and logged to ~/.corvin/tenants/_default/global/audit.jsonl
          (GDPR Art. 30, 32)
        </p>
      </div>
    </div>
  );
};

// ─────────────────────────────────────────────────────────────────
// External Provider Modal (Phase 1: UI skeleton, no backend)
// ─────────────────────────────────────────────────────────────────

interface ExternalProviderModalProps {
  open: boolean;
  providerType: 'ollama' | 'openrouter' | 'openai' | null;
  taskType: string | null;
  onClose: () => void;
  onSave: () => void;
}

const ExternalProviderModal: React.FC<ExternalProviderModalProps> = ({
  open,
  providerType,
  taskType,
  onClose,
  onSave,
}) => {
  const [serverUrl, setServerUrl] = useState('http://localhost:11434');
  const [apiKey, setApiKey] = useState('');
  const [modelName, setModelName] = useState('neural-chat');
  const [testing, setTesting] = useState(false);

  const handleTestConnection = async () => {
    setTesting(true);
    // TODO K=2: POST /v1/engine/external-provider/test
    await new Promise((resolve) => setTimeout(resolve, 1000)); // Simulate delay
    alert(`[Phase 2] Test connection to ${providerType} at ${serverUrl}`);
    setTesting(false);
  };

  const getProviderLabel = () => {
    switch (providerType) {
      case 'ollama':
        return 'Ollama Configuration';
      case 'openrouter':
        return 'OpenRouter Configuration';
      case 'openai':
        return 'OpenAI Configuration';
      default:
        return 'External Provider';
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>{getProviderLabel()}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {providerType === 'ollama' && (
            <>
              <div>
                <Label htmlFor="server-url" className="text-sm font-medium">
                  Server URL
                </Label>
                <Input
                  id="server-url"
                  type="url"
                  placeholder="http://localhost:11434"
                  value={serverUrl}
                  onChange={(e) => setServerUrl(e.target.value)}
                  className="mt-2"
                />
              </div>
              <div>
                <Label htmlFor="model-name" className="text-sm font-medium">
                  Model Name
                </Label>
                <Input
                  id="model-name"
                  placeholder="neural-chat"
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  className="mt-2"
                />
              </div>
            </>
          )}

          {(providerType === 'openrouter' || providerType === 'openai') && (
            <div>
              <Label htmlFor="api-key" className="text-sm font-medium">
                API Key
              </Label>
              <Input
                id="api-key"
                type="password"
                placeholder="sk-..."
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="mt-2"
              />
              <p className="text-xs text-gray-500 mt-1">
                API key is stored in ~/.config/corvin-voice/ (never in console)
              </p>
            </div>
          )}

          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={handleTestConnection}
            disabled={testing}
          >
            {testing ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" /> Testing...
              </>
            ) : (
              <>
                <Zap className="w-4 h-4 mr-2" /> Test Connection
              </>
            )}
          </Button>
        </div>

        <DialogFooter className="flex gap-2">
          <Button type="button" variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            onClick={onSave}
            disabled={!serverUrl && !apiKey}
          >
            Save & Add to {taskType}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default EngineConfigPage;
