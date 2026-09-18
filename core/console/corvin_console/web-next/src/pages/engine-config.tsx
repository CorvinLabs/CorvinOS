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
 *     corvin_operator/bridges/shared/adapter.py's two real turn call sites
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
 *     can predict. There is deliberately NO model list in this file. Each
 *     source's state is summarised on ONE line (count when it answered, short
 *     reason when it did not) with the untruncated text on hover — visible
 *     enough to tell a live list from a shipped snapshot, quiet enough not to
 *     read as breakage when a keyless Anthropic source is the normal state.
 *   - A tier with no learned outcomes yet shows two REAL numbers instead of a
 *     bare placeholder: its own share of all classified turns (why it is empty)
 *     and what the audit chain measured for the model it points at, explicitly
 *     labelled as that model's usage across all task types rather than as this
 *     tier's own.
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
import { BarChart3, Check, Database, Info, Loader2, RotateCcw, AlertTriangle } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useAuth } from '@/lib/auth';
import {
  getEngineConfig,
  setEngineConfig,
  getModelSelectionAnalytics,
  resetModelSelectionLearning,
  type TaskType,
} from '@/lib/api/engines';
// ADR-0885 step 2: the building blocks live in the Models console now; this
// page is a thin composition of them until step 3 deletes it.
import {
  ClaudeCodeAuthStatus,
  ModelUsagePanel,
  TaskTypeCard,
  fmtInt,
} from '@/pages/models/components/engine-parts';

function LearningStatusBar({ csrf }: { csrf: string }) {
  const qc = useQueryClient();
  const [showAnalytics, setShowAnalytics] = useState(false);

  const configQ = useQuery({
    queryKey: ['engine-config'],
    queryFn: ({ signal }) => getEngineConfig(signal),
    staleTime: 5_000,
    refetchInterval: 30_000,
  });
  const analyticsQ = useQuery({
    queryKey: ['model-selection-analytics'],
    queryFn: ({ signal }) => getModelSelectionAnalytics(signal),
    enabled: showAnalytics,
    staleTime: 5_000,
    refetchInterval: 30_000,
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
                {lastUpdate ? `Last update: ${new Date(lastUpdate).toLocaleString('en-US')} • ` : ''}
                {/* Two different periods sat side by side here with nothing to
                    tell them apart: classified turns are narrowed by the
                    counting window, the optimizer's outcome samples are
                    lifetime. "6 classified · 241 outcome samples" invited the
                    reading that 235 turns were classified but not learned. */}
                <span className="tabular-nums">{fmtInt(samples)}</span> classified in this window
                {' · '}
                <span className="tabular-nums">{fmtInt(learnedSamples)}</span> outcome
                sample{learnedSamples === 1 ? '' : 's'} learned (lifetime)
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
                No outcome-feedback samples yet — this Bayesian confidence tracker
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
    staleTime: 5_000,
    refetchInterval: 30_000,
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
          reflects real classified turns.
        </p>
      </div>

      <ClaudeCodeAuthStatus />

      <LearningStatusBar csrf={csrf} />

      <ModelUsagePanel />

      <div>
        <h2 className="text-xl font-semibold mb-4">CorvinOS Model Selection</h2>
        <TaskTypeCard
          config={config.models.corvinOS}
          totalClassified={config.total_samples}
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
              totalClassified={config.total_samples}
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
