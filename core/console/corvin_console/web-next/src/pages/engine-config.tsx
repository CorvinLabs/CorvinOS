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
 *   - External Providers — the live ADR-0181 registry itself, both the
 *     provider list and each provider's models (getEngineProviders,
 *     getProviderModels — real GET /v1/models etc. per provider). The
 *     "external" set is the registry minus the native-Claude sources, so a
 *     provider added to the registry shows up without a frontend change.
 *   - Default Model — GET /v1/engine/claude-models, the UNION of three real
 *     sources, each reporting its own reachability: the curated ADR-0119
 *     registry, Anthropic's live GET /v1/models, and Bedrock's
 *     ListFoundationModels + ListInferenceProfiles (SigV4-signed). On a
 *     Bedrock install the selectable ids are this AWS account's
 *     `us.anthropic.claude-*` inference profiles — which is exactly the set
 *     Claude Code's own /model menu offers, and which no shipped constant
 *     can predict. There is deliberately NO model list in this file.
 *   - Model Usage — GET /v1/engine/model-usage, per-model and per-provider
 *     shares counted from the tenant's hash-chained audit chain
 *     (engine.span.start/end + os_turn.completed). Counting unit is the span,
 *     so a delegated worker turn is attributed to the model that ran it
 *     rather than merged into its parent OS turn. Each row carries
 *     provider_source, so a catalogue hit is distinguishable from a registry
 *     inference and an unattributable id stays "unknown" instead of being
 *     pattern-matched into something plausible.
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
  getClaudeModels,
  getModelUsage,
  detectEngines,
  type TaskType,
  type TaskModelConfig,
  type ClaudeModelsResponse,
  type ProviderSpec,
} from '@/lib/api/engines';

// No model list and no provider list live in this file. Both are fetched:
// models from /v1/engine/claude-models (curated registry ∪ Anthropic /v1/models ∪
// Bedrock ListFoundationModels+ListInferenceProfiles), providers from the
// ADR-0181 registry. A constant here would be wrong on any Bedrock host, where
// the selectable ids are `us.anthropic.claude-*` inference profiles.

function useClaudeModels() {
  return useQuery({
    queryKey: ['claude-models'],
    queryFn: ({ signal }) => getClaudeModels(signal),
    staleTime: 5 * 60_000,
  });
}

function useProviders() {
  return useQuery({
    queryKey: ['engine-providers'],
    queryFn: ({ signal }) => getEngineProviders(signal),
    staleTime: 60_000,
  });
}

/** Provider ids that serve "native Claude" — derived from the live source list,
 *  so a source added on the backend does not have to be repeated here. */
function claudeNativeProviders(catalog: ClaudeModelsResponse | undefined): Set<string> {
  return new Set(
    (catalog?.sources ?? [])
      .filter((s) => s.live)
      .map((s) => s.id.replace(/_live$/, '')),
  );
}

function providerLabelOf(
  providerId: string | null,
  providers: Record<string, ProviderSpec> | undefined,
): string | null {
  if (!providerId) return null;
  return providers?.[providerId]?.label ?? providerId;
}

/**
 * Provenance for the model list, per source. Not decoration: a list of 4 ids and
 * a list of 158 look identical in a <select>, and only this line distinguishes
 * "Bedrock answered with the account's inference profiles" from "Bedrock was
 * unreachable, so you are seeing the shipped registry snapshot".
 */
function ClaudeSourceLine({
  catalog,
  error,
}: {
  catalog: ClaudeModelsResponse | undefined;
  error?: unknown;
}) {
  if (error) {
    return (
      <p className="text-xs text-destructive mt-1.5 flex items-start gap-1">
        <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
        Model sources could not be queried: {String((error as Error)?.message ?? error)}
      </p>
    );
  }
  if (!catalog) return null;
  return (
    <div className="mt-1.5 space-y-0.5">
      <p className="text-xs text-muted-foreground">
        {catalog.count} model{catalog.count === 1 ? '' : 's'} from {catalog.sources.length} source
        {catalog.sources.length === 1 ? '' : 's'}:
      </p>
      {catalog.sources.map((s) => (
        <p
          key={s.id}
          className={cn(
            'text-xs flex items-start gap-1',
            s.reachable ? 'text-muted-foreground' : 'text-amber-700 dark:text-amber-300',
          )}
        >
          {s.reachable
            ? <Check className="w-3 h-3 mt-0.5 shrink-0 text-emerald-600 dark:text-emerald-400" />
            : <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />}
          <span>
            <span className="font-medium">{s.label}</span>
            {s.live ? ' (live)' : ' (shipped)'} — {s.reachable ? `${s.count} Claude model${s.count === 1 ? '' : 's'}` : 'unreachable'}
            {s.detail ? ` · ${s.detail}` : ''}
            {s.error ? ` · ${s.error}` : ''}
          </span>
        </p>
      ))}
    </div>
  );
}

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

  // Shared queryKeys — TanStack dedupes, so all four cards drive ONE fetch each.
  const claudeQ = useClaudeModels();
  const providersQ = useProviders();

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
  const providerLabel = providerLabelOf(config.provider, providersQ.data);

  const catalogue = claudeQ.data?.models ?? [];
  // The saved id is always offerable even when no source lists it (a model
  // retired upstream, or a fetch that is still in flight). Dropping it would
  // make the <select> silently display — and on the next Save, persist — a
  // DIFFERENT model than the one the tenant is configured with.
  const options = catalogue.some((m) => m.id === config.selected_model)
    ? catalogue
    : [{ id: config.selected_model, label: `${config.selected_model} (configured)`, sources: [], providers: [] },
       ...catalogue];
  // The registry's own default, never a positional guess into a shipped array.
  const nativeDefault = claudeQ.data?.default_model_id ?? null;

  return (
    <Card>
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
                disabled={!nativeDefault}
                onClick={() => nativeDefault && onSave({ selected_model: nativeDefault, provider: null })}
                title={
                  nativeDefault
                    ? `Remove external provider — back to ${nativeDefault}`
                    : 'Cannot reset: the engine registry declares no default Claude model'
                }
              >
                <X className="w-3 h-3" />
              </Button>
            </div>
          </div>
        ) : (
          <div>
            <Label className="text-sm font-medium mb-2 block">Default Model</Label>
            {claudeQ.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Querying every model source…
              </div>
            ) : (
              <Select value={model} onChange={(e) => setModel(e.target.value)}>
                {options.map((m) => (
                  <option key={m.id} value={m.id}>{m.label}</option>
                ))}
              </Select>
            )}
            <ClaudeSourceLine catalog={claudeQ.data} error={claudeQ.error} />
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

        {isCorvinOS ? (
          // corvinOS's "task type" is the classifier step itself — a
          // deterministic token/keyword rule (ModelSelector.classify(),
          // no LLM call), never one of the SIMPLE/MEDIUM/COMPLEX outputs
          // shadow_classify_task() feeds into the confidence optimizer.
          // run_count is structurally always 0 here, not "not yet" — an amber
          // "waiting for data" box (same styling as the other three cards
          // legitimately still filling in) misrepresented a permanent,
          // by-design state as a temporary gap. Neutral note instead,
          // matching the muted empty-state style used elsewhere (e.g.
          // ModelCostOptimizer's "no data yet" cards).
          <div className="p-3 border border-dashed border-border rounded-md flex gap-2">
            <Info className="w-4 h-4 text-muted-foreground flex-shrink-0 mt-0.5" />
            <div className="text-sm text-muted-foreground">
              Classification is rule-based (token/keyword heuristics) — there is no
              model choice here to learn a confidence score from.
            </div>
          </div>
        ) : config.run_count === 0 ? (
          <div className="p-3 border border-amber-500/30 bg-amber-500/10 rounded-md flex gap-2">
            <Info className="w-4 h-4 text-amber-600 dark:text-amber-400 flex-shrink-0 mt-0.5" />
            <div className="text-sm text-amber-700 dark:text-amber-300">
              No real outcomes learned for this model yet. Confidence updates live as real turns complete.
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
              None configured — using the Claude model selected above, through
              whichever backend Claude Code is authenticated against.
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
  const [provider, setProvider] = useState<string>(currentProvider ?? '');
  const [modelId, setModelId] = useState(currentModel ?? '');

  const providersQ = useProviders();
  const claudeQ = useClaudeModels();

  // "External" = every provider in the ADR-0181 registry that is NOT one of the
  // native-Claude sources the Default-Model picker above already covers. Derived,
  // so a provider added to the registry appears here without a frontend release.
  const nativeIds = claudeNativeProviders(claudeQ.data);
  const external = Object.entries(providersQ.data ?? {})
    .filter(([id]) => !nativeIds.has(id))
    .map(([id, s]) => ({ id, label: s.label || id }))
    .sort((a, b) => a.label.localeCompare(b.label));

  // Nothing is preselected until the real list arrives — a hardcoded fallback id
  // would fetch models for a provider that may not exist on this install.
  React.useEffect(() => {
    if (!provider && external.length > 0) setProvider(currentProvider ?? external[0].id);
  }, [provider, currentProvider, external]);

  const modelsQ = useQuery({
    queryKey: ['provider-models', provider],
    queryFn: () => getProviderModels(provider),
    enabled: open && !!provider,
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
            {providersQ.isLoading ? (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading providers…
              </div>
            ) : external.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                The engine registry declares no external providers on this install.
              </p>
            ) : (
              <Select
                value={provider}
                onChange={(e) => { setProvider(e.target.value); setModelId(''); }}
              >
                {external.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </Select>
            )}
            {spec && (
              <p className="text-xs text-muted-foreground mt-1">
                {spec.kind === 'local' ? 'Runs locally' : 'Cloud provider'} · {spec.base_url}
                {spec.credential_env
                  ? ` · needs ${spec.credential_env} (Settings → API Keys)`
                  : ''}
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
          <Button type="button" onClick={() => onSave(provider, modelId)} disabled={!modelId || !provider}>
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
// Model Usage — real shares, counted from the tenant's audit chain
// ─────────────────────────────────────────────────────────────────

const PROVENANCE_NOTE: Record<string, string> = {
  live_catalog: 'strongest — this provider answered with this id when last asked',
  tenant_config: 'you assigned this model to this provider on this page',
  registry: 'from the shipped ADR-0119 engine registry',
  id_prefix: 'parsed from the model id itself (<provider>/<model>)',
  engine_config: "inferred from the running engine's current provider assignment",
  unresolved: 'no source claims this id — provider genuinely unknown',
};

function fmtInt(n: number): string {
  return n.toLocaleString();
}

/** Horizontal share bar. Width is the percentage itself — nothing is normalised
 *  to the largest row, so 100% means 100% of real turns, not "the top row". */
function ShareBar({ pct, className }: { pct: number; className?: string }) {
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
      <div
        className={cn('h-full rounded-full bg-accent', className)}
        style={{ width: `${Math.min(100, Math.max(0, pct))}%` }}
      />
    </div>
  );
}

function ModelUsagePanel() {
  const usageQ = useQuery({
    queryKey: ['model-usage'],
    queryFn: ({ signal }) => getModelUsage(signal),
    staleTime: 30_000,
  });

  const data = usageQ.data;

  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div>
            <CardTitle className="text-xl font-semibold">Model Usage</CardTitle>
            <p className="text-sm text-muted-foreground mt-1">
              Which model served which share of real work — counted from this
              tenant's hash-chained audit chain (engine spans + OS turns), across
              every provider. No separate counter, no estimate.
            </p>
          </div>
          {data && data.totals.turns > 0 && (
            <Badge variant="secondary" title="Engine spans counted from the audit chain">
              {fmtInt(data.totals.turns)} turn{data.totals.turns === 1 ? '' : 's'} ·{' '}
              {fmtInt(data.totals.total_tokens)} tokens
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {usageQ.isLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" /> Reading the audit chain…
          </div>
        ) : usageQ.error ? (
          <p className="text-sm text-destructive flex items-start gap-1.5">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            Usage could not be read: {String((usageQ.error as Error)?.message ?? usageQ.error)}
          </p>
        ) : !data ? null : !data.chain_readable ? (
          // Three different real states, told apart instead of collapsed into
          // one "no data" box: an unresolvable path is a defect, an absent file
          // is a fresh install, and an empty file is simply no work yet.
          <p className="text-sm text-muted-foreground flex items-start gap-1.5">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            {data.chain_path_resolved
              ? 'No audit chain on disk yet for this tenant — usage appears after the first real turn.'
              : 'The tenant audit chain path could not be resolved, so usage cannot be counted.'}
          </p>
        ) : data.models.length === 0 ? (
          <p className="text-sm text-muted-foreground flex items-start gap-1.5">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            The audit chain holds no engine spans or OS turns yet — no model has
            served real work on this install.
          </p>
        ) : (
          <>
            <div>
              <Label className="text-sm font-medium mb-3 block">By provider</Label>
              <div className="space-y-3">
                {data.providers.map((p) => (
                  <div key={p.provider}>
                    <div className="flex items-baseline justify-between gap-2 text-sm">
                      <span className="font-medium truncate">{p.provider_label}</span>
                      <span className="tabular-nums shrink-0">
                        {p.share_pct.toFixed(1)}%
                        <span className="text-muted-foreground">
                          {' '}· {fmtInt(p.turns)} turn{p.turns === 1 ? '' : 's'} ·{' '}
                          {p.models} model{p.models === 1 ? '' : 's'} ·{' '}
                          {p.token_share_pct.toFixed(1)}% of tokens
                        </span>
                      </span>
                    </div>
                    <ShareBar pct={p.share_pct} />
                  </div>
                ))}
              </div>
            </div>

            <div>
              <Label className="text-sm font-medium mb-3 block">By model</Label>
              <div className="space-y-4">
                {data.models.map((m) => (
                  <div key={m.model_id}>
                    <div className="flex items-baseline justify-between gap-2 text-sm">
                      <span className="font-medium truncate" title={m.model_id}>{m.model_id}</span>
                      <span className="tabular-nums shrink-0">{m.share_pct.toFixed(1)}%</span>
                    </div>
                    <ShareBar pct={m.share_pct} />
                    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                      <Badge
                        variant="outline"
                        className="font-normal"
                        title={PROVENANCE_NOTE[m.provider_source] ?? m.provider_source}
                      >
                        {m.provider_label}
                        <span className="ml-1 opacity-60">({m.provider_source})</span>
                      </Badge>
                      <span className="tabular-nums">
                        {fmtInt(m.turns)} turn{m.turns === 1 ? '' : 's'}
                      </span>
                      {(m.ok > 0 || m.failed > 0) && (
                        <span className="tabular-nums">
                          {m.success_pct.toFixed(0)}% ok ({m.ok}/{m.ok + m.failed})
                        </span>
                      )}
                      {m.unfinished > 0 && (
                        <span className="tabular-nums" title="Started, no end event — still running or the process died">
                          {m.unfinished} unfinished
                        </span>
                      )}
                      {m.avg_duration_ms > 0 && (
                        <span className="tabular-nums">
                          ⌀ {(m.avg_duration_ms / 1000).toFixed(1)}s
                        </span>
                      )}
                      <span
                        className="tabular-nums"
                        title={
                          `in ${fmtInt(m.input_tokens)} · out ${fmtInt(m.output_tokens)} · ` +
                          `cache read ${fmtInt(m.cache_read_tokens)} · cache write ${fmtInt(m.cache_write_tokens)}`
                        }
                      >
                        {fmtInt(m.total_tokens)} tokens ({m.token_share_pct.toFixed(1)}%)
                      </span>
                      {Object.entries(m.roles).map(([role, n]) => (
                        <Badge key={role} variant="secondary" className="font-normal">
                          {role} ×{n}
                        </Badge>
                      ))}
                      {m.engines.map((engine) => (
                        <Badge key={engine} variant="outline" className="font-normal">
                          {engine}
                        </Badge>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {data.models.some((m) => m.provider_source === 'unresolved') && (
              <p className="text-xs text-muted-foreground flex items-start gap-1.5">
                <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                Rows marked <span className="font-mono">unresolved</span> ran a model
                no provider catalogue or registry currently claims — the turns are
                real, only the provider attribution is unknown. Fetching that
                provider's live models once resolves them.
              </p>
            )}
          </>
        )}
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

      <ModelUsagePanel />

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
