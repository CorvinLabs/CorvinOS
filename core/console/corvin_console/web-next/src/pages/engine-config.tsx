/**
 * Engine Configuration Console — Model Selection Dashboard
 * Route: /app/engine-config
 *
 * Real data (2026-09-10), replacing the Phase-1 K=1 mock. Same visual
 * design (4-section layout, per-task-type confidence, external provider
 * modal) — backed end-to-end by real persistence + a real signal source:
 *
 *   - GET/PUT /v1/engine/config — core/console/corvin_console/routes/
 *     engine_api.py, persisting to core.models.model_selection_config
 *     (a tenant JSON file). A saved choice is consulted by the classifier
 *     on the next real turn (ModelSelector(overrides=...)), not cosmetic.
 *   - confidence_score / run_count — aggregated from the tenant's real
 *     hash-chained audit chain (skill.model_selector.classified events),
 *     emitted by a SHADOW classification wired into
 *     operator/bridges/shared/adapter.py's two real turn call sites
 *     (model_selector_shadow.py). Shadow = advisory only: it never decides
 *     which model actually serves a turn, it only observes + records.
 *     0 samples is the honest, expected state until real turns accrue.
 *   - External Providers — the same live ADR-0181 registry the Engine
 *     Configuration provider/model picker uses (getEngineProviders,
 *     getProviderModels — real GET /v1/models etc. per provider).
 *   - "View Analytics" / "Reset Learning" — real core.learning.
 *     model_selection_optimizer.ConfidenceOptimizer (ADR-0644, Bayesian).
 *     Currently always 0 samples too: nothing feeds it outcome/quality
 *     feedback yet — a separate, larger effort (process_feedback has no
 *     production caller). Shown honestly, not hidden.
 *
 * ADR-0641: Engine Configuration Console
 * ADR-0642: Model Selector Skill
 * ADR-0644: Confidence Optimizer
 * ADR-0007: Tenant isolation (all settings per-tenant)
 * ADR-0181: Model Providers
 */

import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  BarChart3,
  Check,
  Database,
  Info,
  Loader2,
  Plus,
  RotateCcw,
  X,
  Zap,
} from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Select } from '@/components/ui/select';
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
import { useAuth } from '@/lib/auth';
import {
  getEngineConfig,
  setEngineConfig,
  testExternalProvider,
  getModelSelectionAnalytics,
  resetModelSelectionLearning,
  getEngineProviders,
  getProviderModels,
  detectEngines,
  type TaskType,
  type TaskModelConfig,
} from '@/lib/api/engines';

const TASK_TYPES: TaskType[] = ['corvinOS', 'SIMPLE', 'MEDIUM', 'COMPLEX'];
const CLAUDE_MODELS = [
  { value: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5 (Fast, Low Cost)' },
  { value: 'claude-sonnet-5', label: 'Claude Sonnet 5 (Balanced)' },
  { value: 'claude-opus-5', label: 'Claude Opus 5 (Powerful)' },
  { value: 'claude-fable-5', label: 'Claude Fable 5 (Creative)' },
];
type ExternalProviderId = 'ollama_local' | 'ollama_cloud' | 'openrouter' | 'openai';
const EXTERNAL_PROVIDERS: { id: ExternalProviderId; label: string }[] = [
  { id: 'ollama_local', label: 'Ollama (local)' },
  { id: 'ollama_cloud', label: 'Ollama Cloud' },
  { id: 'openrouter', label: 'OpenRouter' },
  { id: 'openai', label: 'OpenAI' },
];

// ─────────────────────────────────────────────────────────────────
// Task Type Card
// ─────────────────────────────────────────────────────────────────

interface TaskTypeCardProps {
  config: TaskModelConfig;
  onSave: (patch: { selected_model: string; provider: string | null }) => void;
  saving: boolean;
}

const TaskTypeCard: React.FC<TaskTypeCardProps> = ({ config, onSave, saving }) => {
  const [model, setModel] = useState(config.selected_model);
  const [showProviderModal, setShowProviderModal] = useState(false);

  React.useEffect(() => setModel(config.selected_model), [config.selected_model]);

  const isCorvinOS = config.task_type === 'corvinOS';
  const taskLabel = isCorvinOS ? 'CorvinOS' : config.task_type;
  const taskDescription = isCorvinOS
    ? 'Used to classify incoming tasks — a deterministic rule (token count, code blocks, keywords), no model call needed. The choice below only matters if that ever changes.'
    : config.task_type === 'SIMPLE'
      ? 'Fast, straightforward tasks (<500 tokens, <2 code snippets)'
      : config.task_type === 'MEDIUM'
        ? 'Balanced tasks (500–3000 tokens, moderate complexity)'
        : 'Complex, reasoning-heavy tasks (>3000 tokens, deep analysis)';

  const dirty = model !== config.selected_model;
  const providerLabel = config.provider
    ? EXTERNAL_PROVIDERS.find((p) => p.id === config.provider)?.label ?? config.provider
    : null;

  return (
    <Card className="border-l-4 border-l-accent">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="text-lg font-semibold">{taskLabel}</CardTitle>
            <p className="text-sm text-muted-foreground mt-1">{taskDescription}</p>
          </div>
          {config.run_count > 0 && (
            <Badge
              variant="secondary"
              className={cn('ml-4', config.is_converged && 'border-emerald-500/40 text-emerald-700 dark:text-emerald-400')}
              title={`Learned from ${config.run_count} real outcome${config.run_count === 1 ? '' : 's'}`}
            >
              {(config.confidence_score * 100).toFixed(0)}% confident{config.is_converged ? ' · converged' : ''}
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {providerLabel ? (
          <div>
            <Label className="text-sm font-medium mb-2 block">Model source</Label>
            <div className="flex items-center gap-2 p-2 bg-accent/10 rounded-md border border-accent/30">
              <Zap className="w-3.5 h-3.5 text-accent" />
              <span className="text-sm font-medium">{providerLabel}</span>
              <span className="text-xs text-muted-foreground truncate">{config.selected_model}</span>
              <Button
                type="button" size="sm" variant="ghost" className="ml-auto h-6 px-2"
                onClick={() => setShowProviderModal(true)}
              >
                Change
              </Button>
              <Button
                type="button" size="sm" variant="ghost" className="h-6 px-2 text-destructive"
                onClick={() => onSave({ selected_model: CLAUDE_MODELS[1].value, provider: null })}
                title="Remove external provider — back to native Anthropic"
              >
                <X className="w-3 h-3" />
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Label className="text-sm font-medium mb-2 block">Default Model</Label>
            <Select value={model} onChange={(e) => setModel(e.target.value)}>
              {CLAUDE_MODELS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </Select>
          </div>
        )}

        {config.alternatives.length > 0 && (
          <div>
            <Label className="text-sm font-medium mb-2 block">Fallback Models</Label>
            <div className="flex flex-wrap gap-2">
              {config.alternatives.map((m) => (
                <Badge key={m} variant="outline">{m}</Badge>
              ))}
            </div>
          </div>
        )}

        {config.run_count === 0 ? (
          <div className="p-3 border border-amber-500/30 bg-amber-500/10 rounded-md flex gap-2">
            <Info className="w-4 h-4 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-amber-700 dark:text-amber-300">
              {isCorvinOS
                ? 'Classification is rule-based (token/keyword heuristics) — there is no model choice here to learn from.'
                : 'No real outcomes learned for this model yet. Confidence updates live as real turns complete.'}
            </div>
          </div>
        ) : (
          <div className="p-3 border border-emerald-500/30 bg-emerald-500/10 rounded-md">
            <p className="text-sm text-emerald-700 dark:text-emerald-300 font-medium">
              ✓ Learned confidence: {(config.confidence_score * 100).toFixed(0)}% ({config.run_count} real outcome{config.run_count === 1 ? '' : 's'})
              {config.is_converged && ' — converged'}
            </p>
          </div>
        )}

        {!providerLabel && (
          <div>
            <div className="flex items-center justify-between mb-2">
              <Label className="text-sm font-medium">External Provider</Label>
              <Button type="button" size="sm" variant="ghost" onClick={() => setShowProviderModal(true)}>
                <Plus className="w-4 h-4 mr-1" /> Add
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              None configured — using native Anthropic.
            </p>
          </div>
        )}

        {!providerLabel && (
          <div className="flex justify-end">
            <Button
              type="button" size="sm"
              disabled={!dirty || saving}
              onClick={() => onSave({ selected_model: model, provider: null })}
            >
              {saving ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : null}
              Save
            </Button>
          </div>
        )}
      </CardContent>

      <ExternalProviderModal
        open={showProviderModal}
        taskType={config.task_type}
        currentProvider={config.provider}
        currentModel={config.provider ? config.selected_model : null}
        onClose={() => setShowProviderModal(false)}
        onSave={(provider, modelId) => {
          setShowProviderModal(false);
          onSave({ selected_model: modelId, provider });
        }}
      />
    </Card>
  );
};

// ─────────────────────────────────────────────────────────────────
// External Provider Modal — real live model fetch + real connection test
// ─────────────────────────────────────────────────────────────────

interface ExternalProviderModalProps {
  open: boolean;
  taskType: TaskType;
  currentProvider: string | null;
  currentModel: string | null;
  onClose: () => void;
  onSave: (provider: string, modelId: string) => void;
}

const ExternalProviderModal: React.FC<ExternalProviderModalProps> = ({
  open, currentProvider, currentModel, onClose, onSave,
}) => {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? '';
  const [provider, setProvider] = useState<ExternalProviderId>(
    (currentProvider as ExternalProviderId) || 'ollama_local',
  );
  const [modelId, setModelId] = useState(currentModel ?? '');

  const providersQ = useQuery({
    queryKey: ['engine-providers'],
    queryFn: ({ signal }) => getEngineProviders(signal),
    staleTime: 60_000,
    enabled: open,
  });
  const modelsQ = useQuery({
    queryKey: ['provider-models', provider],
    queryFn: () => getProviderModels(provider),
    enabled: open,
    staleTime: 15_000,
  });
  const testMut = useMutation({
    mutationFn: () => testExternalProvider(provider, csrf),
  });

  const spec = providersQ.data?.[provider];
  const models = modelsQ.data?.models ?? [];

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>External Provider</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label className="text-sm font-medium mb-2 block">Provider</Label>
            <Select
              value={provider}
              onChange={(e) => { setProvider(e.target.value as ExternalProviderId); setModelId(''); }}
            >
              {EXTERNAL_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </Select>
            {spec?.credential_env && (
              <p className="text-xs text-muted-foreground mt-1">
                Needs {spec.credential_env} — set it under Settings → API Keys.
              </p>
            )}
          </div>

          <div>
            <Label className="text-sm font-medium mb-2 block">Model</Label>
            {modelsQ.isFetching ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Fetching live models…
              </div>
            ) : models.length > 0 ? (
              <Select value={modelId} onChange={(e) => setModelId(e.target.value)} placeholder="Choose a model…">
                {models.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}</option>
                ))}
              </Select>
            ) : (
              <Input
                placeholder="e.g. mistral:7b"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
              />
            )}
            {modelsQ.data?.error && (
              <p className="text-xs text-amber-700 dark:text-amber-300 mt-1 flex items-start gap-1">
                <Info className="w-3 h-3 mt-0.5 shrink-0" /> {modelsQ.data.error}
              </p>
            )}
          </div>

          <Button
            type="button"
            variant="outline"
            className="w-full"
            onClick={() => testMut.mutate()}
            disabled={testMut.isPending}
          >
            {testMut.isPending ? (
              <><Loader2 className="w-4 h-4 mr-2 animate-spin" /> Testing…</>
            ) : (
              <><Zap className="w-4 h-4 mr-2" /> Test Connection</>
            )}
          </Button>
          {testMut.data && (
            <p className={cn(
              'text-sm flex items-center gap-1.5',
              testMut.data.is_connected ? 'text-emerald-600 dark:text-emerald-400' : 'text-destructive',
            )}>
              {testMut.data.is_connected
                ? <Check className="w-4 h-4" />
                : <AlertTriangle className="w-4 h-4" />}
              {testMut.data.is_connected
                ? `Reachable — ${testMut.data.model_count} model(s), ${testMut.data.latency_ms?.toFixed(0)}ms`
                : testMut.data.error_message ?? 'Not reachable'}
            </p>
          )}
        </div>

        <DialogFooter className="flex gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="button" onClick={() => onSave(provider, modelId)} disabled={!modelId}>
            Save & Assign to task type
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

// ─────────────────────────────────────────────────────────────────
// Claude Code Authentication — real detection, all 5 login methods
// (Claude subscription, Anthropic Console API key, Amazon Bedrock,
// Google Vertex AI, Microsoft Foundry — the exact set Claude Code's own
// `/login` menu offers).
// ─────────────────────────────────────────────────────────────────

const AUTH_METHOD_LABEL: Record<string, string> = {
  subscription: 'Claude subscription',
  env_var: 'Anthropic Console (API key)',
  bedrock: 'Amazon Bedrock',
  vertex: 'Google Vertex AI',
  foundry: 'Microsoft Foundry',
};

function ClaudeCodeAuthStatus() {
  const detectQ = useQuery({
    queryKey: ['engine-detect'],
    queryFn: ({ signal }) => detectEngines(signal),
    staleTime: 3 * 60_000,
    refetchOnWindowFocus: false,
  });

  const probe = detectQ.data?.results?.find((r) => r.engine_id === 'claude_code');
  if (detectQ.isLoading || !probe) return null;

  const methodLabel = probe.credential_source ? AUTH_METHOD_LABEL[probe.credential_source] ?? probe.credential_source : null;

  return (
    <Card>
      <CardContent className="pt-4 pb-3 flex items-center gap-2 text-sm">
        {probe.authenticated
          ? <Check className="w-4 h-4 text-emerald-600 dark:text-emerald-400 shrink-0" />
          : <AlertTriangle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0" />}
        <span className="text-muted-foreground">Claude Code:</span>
        <span className="font-medium">
          {probe.authenticated ? `Authenticated via ${methodLabel}` : 'Not authenticated'}
        </span>
        {probe.detail && <span className="text-xs text-muted-foreground truncate">— {probe.detail}</span>}
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────
// Learning Status Bar — real, honest state
// ─────────────────────────────────────────────────────────────────

function LearningStatusBar({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const [showAnalytics, setShowAnalytics] = useState(false);

  const configQ = useQuery({
    queryKey: ['engine-config'],
    queryFn: ({ signal }) => getEngineConfig(signal),
  });
  const analyticsQ = useQuery({
    queryKey: ['model-selection-analytics'],
    queryFn: ({ signal }) => getModelSelectionAnalytics(signal),
    enabled: showAnalytics,
  });
  const resetMut = useMutation({
    mutationFn: () => resetModelSelectionLearning(csrf),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['engine-config'] });
      qc.invalidateQueries({ queryKey: ['model-selection-analytics'] });
    },
  });

  const status = configQ.data?.learning_status ?? 'idle';
  const samples = configQ.data?.total_samples ?? 0;
  const learnedSamples = configQ.data?.total_learned_samples ?? 0;
  const lastUpdate = configQ.data?.last_learning_update;

  const statusLabel =
    status === 'converged' ? 'Converged ✓' : status === 'learning' ? 'Learning (real outcomes)' : 'No turns yet';
  const statusColor =
    status === 'converged'
      ? 'text-emerald-600 dark:text-emerald-400'
      : status === 'learning'
        ? 'text-accent'
        : 'text-muted-foreground';

  return (
    <Card className="bg-accent/10 border-accent/30">
      <CardContent className="pt-6">
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-3">
            {status !== 'idle'
              ? <Check className="w-5 h-5 text-accent" />
              : <Info className="w-5 h-5 text-muted-foreground" />}
            <div>
              <p className="font-medium">
                Learning: <span className={statusColor}>{statusLabel}</span>
              </p>
              <p className="text-sm text-muted-foreground">
                {lastUpdate ? `Last update: ${new Date(lastUpdate).toLocaleString()} • ` : ''}
                {samples} classified · {learnedSamples} outcome sample{learnedSamples === 1 ? '' : 's'} learned
              </p>
            </div>
          </div>
          <div className="flex gap-2">
            <Button type="button" variant="outline" size="sm" onClick={() => setShowAnalytics((v) => !v)}>
              <BarChart3 className="w-3.5 h-3.5 mr-1.5" /> View Analytics
            </Button>
            <Button
              type="button" variant="outline" size="sm"
              onClick={() => { if (confirm('Reset all learned confidence data? This cannot be undone.')) resetMut.mutate(); }}
              disabled={resetMut.isPending}
            >
              <RotateCcw className="w-3.5 h-3.5 mr-1.5" /> Reset Learning
            </Button>
          </div>
        </div>

        {showAnalytics && (
          <div className="mt-4 pt-4 border-t border-accent/30 text-sm">
            {analyticsQ.isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : analyticsQ.data && analyticsQ.data.total_samples > 0 ? (
              <div className="space-y-1">
                <p>Total outcome samples: {analyticsQ.data.total_samples}</p>
                {analyticsQ.data.top_model && (
                  <p>Top model: {analyticsQ.data.top_model} ({((analyticsQ.data.top_confidence ?? 0) * 100).toFixed(0)}%)</p>
                )}
              </div>
            ) : (
              <p className="text-muted-foreground flex items-start gap-1.5">
                <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                No outcome-feedback samples yet — this Bayesian confidence tracker (ADR-0644)
                needs real quality feedback per turn, which isn't wired into a live turn yet.
                The classification counts above (from shadow-mode classification) are a
                separate, already-real signal.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─────────────────────────────────────────────────────────────────
// Main Page Component
// ─────────────────────────────────────────────────────────────────

export const EngineConfigPage: React.FC = () => {
  const qc = useQueryClient();
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? '';

  const configQ = useQuery({
    queryKey: ['engine-config'],
    queryFn: ({ signal }) => getEngineConfig(signal),
  });

  const saveMut = useMutation({
    mutationFn: (args: { taskType: TaskType; selected_model: string; provider: string | null }) =>
      setEngineConfig(
        {
          [args.taskType]: {
            task_type: args.taskType,
            selected_model: args.selected_model,
            provider: args.provider,
            alternatives: configQ.data?.models[args.taskType]?.alternatives ?? [],
          },
        },
        csrf,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['engine-config'] }),
  });

  if (configQ.isLoading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <Loader2 className="w-8 h-8 animate-spin" />
      </div>
    );
  }

  if (!configQ.data) {
    return (
      <div className="p-6">
        <AlertTriangle className="w-6 h-6 text-destructive mb-2" />
        <p>Failed to load engine configuration.</p>
      </div>
    );
  }

  const config = configQ.data;

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-8">
      <div>
        <div className="flex items-center gap-3 mb-2">
          <Database className="w-8 h-8 text-accent" />
          <h1 className="text-3xl font-bold">Engine Configuration</h1>
        </div>
        <p className="text-muted-foreground">
          Configure which AI models are used for different task types. Confidence
          reflects real classified turns. (ADR-0641, ADR-0642)
        </p>
      </div>

      <ClaudeCodeAuthStatus />

      <LearningStatusBar csrf={csrf} />

      <div>
        <h2 className="text-xl font-semibold mb-4">CorvinOS Model Selection</h2>
        <TaskTypeCard
          config={config.models.corvinOS}
          saving={saveMut.isPending}
          onSave={(patch) => saveMut.mutate({ taskType: 'corvinOS', ...patch })}
        />
      </div>

      <div>
        <h2 className="text-xl font-semibold mb-4">Task-Type Overrides</h2>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {(['SIMPLE', 'MEDIUM', 'COMPLEX'] as const).map((taskType) => (
            <TaskTypeCard
              key={taskType}
              config={config.models[taskType]}
              saving={saveMut.isPending}
              onSave={(patch) => saveMut.mutate({ taskType, ...patch })}
            />
          ))}
        </div>
      </div>

      <div className="pt-4 border-t">
        <p className="text-xs text-muted-foreground">
          All changes are audited and logged to the tenant's audit chain
          (GDPR Art. 30, 32)
        </p>
      </div>
    </div>
  );
};

export default EngineConfigPage;
