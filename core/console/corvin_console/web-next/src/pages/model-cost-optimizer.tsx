/**
 * Model Cost Optimizer — Token Savings & Cost Analysis Dashboard
 * Route: /app/model-cost-optimizer
 *
 * Real data: cost metrics (baseline vs. v2.0) from model selection feedback loop,
 * token efficiency analysis by model/task-type, and savings trends over time.
 *
 * ADR-0760: Usage Counting Epoch (narrowed totals with window context)
 * ADR-0761: Charts & Cost Visualisation (encodings, ordinal scales, small multiples)
 * ADR-0845: OS Model Selector Architecture (cost drivers)
 */

import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  BarChart3,
  Calendar,
  DollarSign,
  Loader2,
  TrendingDown,
  TrendingUp,
  Zap,
  AlertCircle,
  Info,
  RotateCcw,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Select,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'
// API functions for fetching cost metrics and trends
const getCostMetrics = (signal?: AbortSignal) =>
  fetch('/api/v1/engine/cost-metrics', { signal }).then(r => r.json())

const getCostTrend = (signal?: AbortSignal) =>
  fetch('/api/v1/engine/cost-trend', { signal }).then(r => r.json())

const getCostByModelAndTask = (signal?: AbortSignal) =>
  fetch('/api/v1/engine/cost-by-task', { signal }).then(r => r.json())

const getTokenEfficiency = (signal?: AbortSignal) =>
  fetch('/api/v1/engine/token-efficiency', { signal }).then(r => r.json())

const resetCostWindow = (csrf: string) =>
  fetch('/api/v1/engine/cost-window/reset', { method: 'POST', headers: { 'x-csrf-token': csrf } }).then(r => r.json())

interface CostMetrics {
  baseline_cost_cents: number
  optimized_cost_cents: number
  haiku_utilization_pct: number
  window?: { active: boolean; since_iso: string }
}

function fmtInt(n: number): string {
  return n.toLocaleString('en-US')
}

function fmtCurrency(cents: number): string {
  return '$' + (cents / 100).toFixed(2)
}

function fmtPct(value: number): string {
  return (value * 100).toFixed(1) + '%'
}

/**
 * Horizontally-stacked cost bar showing the split between Haiku/Sonnet/Opus.
 * Width of each segment is proportional to its share of total cost.
 */
function CostCompositionBar({
  haiku_pct,
  sonnet_pct,
  opus_pct,
}: {
  haiku_pct: number
  sonnet_pct: number
  opus_pct: number
}) {
  const total = haiku_pct + sonnet_pct + opus_pct || 1
  const h = (haiku_pct / total) * 100
  const s = (sonnet_pct / total) * 100
  const o = (opus_pct / total) * 100

  return (
    <div className="flex h-2 rounded-full overflow-hidden bg-muted">
      {h > 1 && <div className="bg-blue-500" style={{ width: `${h}%` }} title={`Haiku: ${fmtPct(haiku_pct)}`} />}
      {s > 1 && <div className="bg-amber-500" style={{ width: `${s}%` }} title={`Sonnet: ${fmtPct(sonnet_pct)}`} />}
      {o > 1 && <div className="bg-red-500" style={{ width: `${o}%` }} title={`Opus: ${fmtPct(opus_pct)}`} />}
    </div>
  )
}

/**
 * Overall cost metrics: baseline vs. v2.0, showing savings achieved.
 */
function CostSummary() {
  const metricsQ = useQuery({
    queryKey: ['cost-metrics'],
    queryFn: ({ signal }) => getCostMetrics(signal),
    staleTime: 30_000,
  })

  const data = metricsQ.data

  if (metricsQ.isLoading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin" /> Loading cost summary…
          </div>
        </CardContent>
      </Card>
    )
  }

  if (metricsQ.error || !data) {
    return (
      <Card>
        <CardContent className="pt-6">
          <div className="text-sm text-destructive flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            Cost metrics could not be loaded.
          </div>
        </CardContent>
      </Card>
    )
  }

  const baselineTotal = data.baseline_cost_cents
  const optimizedTotal = data.optimized_cost_cents
  const savings = baselineTotal - optimizedTotal
  const savingsPct = baselineTotal > 0 ? (savings / baselineTotal) * 100 : 0

  const windowLabel = data.window?.active
    ? `since ${new Date(data.window.since_iso).toLocaleDateString('en-US')}`
    : 'all-time (no window set)'

  return (
    <div className="grid gap-6 grid-cols-1 md:grid-cols-2 lg:grid-cols-4">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Baseline Cost</CardTitle>
          <CardDescription className="text-xs">{windowLabel}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold font-mono">{fmtCurrency(baselineTotal)}</div>
          <p className="text-xs text-muted-foreground mt-1">Before optimization</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Optimized Cost</CardTitle>
          <CardDescription className="text-xs">{windowLabel}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold font-mono text-emerald-600 dark:text-emerald-400">{fmtCurrency(optimizedTotal)}</div>
          <p className="text-xs text-muted-foreground mt-1">With Skill Forge v2.0</p>
        </CardContent>
      </Card>

      <Card className="border-emerald-500/30 bg-emerald-500/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Total Savings</CardTitle>
          <CardDescription className="text-xs">{windowLabel}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-baseline gap-2">
            <div className="text-2xl font-bold font-mono text-emerald-700 dark:text-emerald-400">
              {fmtCurrency(savings)}
            </div>
            <div className="text-lg font-semibold text-emerald-700 dark:text-emerald-400">
              -{fmtPct(savingsPct / 100)}
            </div>
          </div>
          <div className="flex items-center gap-1 text-xs text-emerald-700/70 dark:text-emerald-400/70 mt-1">
            <TrendingDown className="w-3 h-3" /> Cost reduction achieved
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Haiku Utilization</CardTitle>
          <CardDescription className="text-xs">Model selection shift</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{fmtPct(data.haiku_utilization_pct)}</div>
          <p className="text-xs text-muted-foreground mt-1">of tasks running Haiku</p>
        </CardContent>
      </Card>
    </div>
  )
}

/**
 * Token efficiency per model: how much work gets done per token.
 * Higher is better — more tokens to Haiku is more efficient (smaller models).
 */
function TokenEfficiencyPanel() {
  const effQ = useQuery({
    queryKey: ['token-efficiency'],
    queryFn: ({ signal }) => getTokenEfficiency(signal),
    staleTime: 60_000,
  })

  const data = effQ.data

  if (effQ.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Token Efficiency by Model</CardTitle>
        </CardHeader>
        <CardContent>
          <Loader2 className="w-4 h-4 animate-spin" />
        </CardContent>
      </Card>
    )
  }

  if (effQ.error || !data) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Zap className="w-4 h-4" />
          Token Efficiency by Model
        </CardTitle>
        <CardDescription>
          Cost per 1M tokens: lower is more efficient. Haiku typically 70% of Sonnet cost.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-4">
          {data.models.map((m) => {
            const maxCost = Math.max(...data.models.map((x) => x.cost_per_1m_tokens))
            const widthPct = (m.cost_per_1m_tokens / maxCost) * 100

            return (
              <div key={m.model_id}>
                <div className="flex items-baseline justify-between gap-2 mb-1">
                  <span className="font-medium text-sm">{m.model_label}</span>
                  <span className="text-sm font-mono">${m.cost_per_1m_tokens.toFixed(1)}</span>
                </div>
                <div className="h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className={cn(
                      'h-full rounded-full',
                      m.model_id.includes('haiku')
                        ? 'bg-blue-500'
                        : m.model_id.includes('sonnet')
                          ? 'bg-amber-500'
                          : 'bg-red-500'
                    )}
                    style={{ width: `${widthPct}%` }}
                  />
                </div>
                <div className="flex flex-wrap gap-2 mt-1 text-xs text-muted-foreground">
                  <span>{fmtInt(m.turn_count)} turns</span>
                  <span>·</span>
                  <span>{fmtInt(m.total_tokens)} tokens</span>
                  <span>·</span>
                  <span>{fmtPct(m.success_rate)}</span>
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * Cost breakdown by model type and task complexity.
 * Small multiples: one grid per task type, showing Haiku/Sonnet/Opus split.
 */
function CostByModelAndTaskPanel() {
  const costQ = useQuery({
    queryKey: ['cost-by-model-task'],
    queryFn: ({ signal }) => getCostByModelAndTask(signal),
    staleTime: 60_000,
  })

  const data = costQ.data

  if (costQ.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Cost by Task Type</CardTitle>
        </CardHeader>
        <CardContent>
          <Loader2 className="w-4 h-4 animate-spin" />
        </CardContent>
      </Card>
    )
  }

  if (costQ.error || !data) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4" />
          Cost Breakdown by Task Type
        </CardTitle>
        <CardDescription>
          Model cost per task type (v2.0 with learning). Composition bar shows Haiku/Sonnet/Opus split.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-6 grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
          {data.task_types.map((task) => {
            const totalCost = task.haiku_cost + task.sonnet_cost + task.opus_cost

            return (
              <div key={task.task_type} className="p-4 border rounded-lg space-y-3">
                <div className="flex items-baseline justify-between">
                  <h4 className="font-medium">{task.task_type_label}</h4>
                  <span className="text-sm font-mono font-bold">{fmtCurrency(totalCost)}</span>
                </div>

                <CostCompositionBar
                  haiku_pct={task.haiku_cost}
                  sonnet_pct={task.sonnet_cost}
                  opus_pct={task.opus_cost}
                />

                <div className="grid grid-cols-3 gap-2 text-xs">
                  <div className="flex items-center gap-1">
                    <div className="w-2 h-2 rounded-full bg-blue-500" />
                    <span>{fmtPct(task.haiku_cost / totalCost)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-2 h-2 rounded-full bg-amber-500" />
                    <span>{fmtPct(task.sonnet_cost / totalCost)}</span>
                  </div>
                  <div className="flex items-center gap-1">
                    <div className="w-2 h-2 rounded-full bg-red-500" />
                    <span>{fmtPct(task.opus_cost / totalCost)}</span>
                  </div>
                </div>

                <div className="text-xs text-muted-foreground">
                  {task.turn_count} turns · {fmtInt(task.total_tokens)} tokens
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * Historical trend: cost over time (baseline vs. optimized).
 * Shows the learning loop's optimization converging over time.
 */
function CostTrendPanel() {
  const trendQ = useQuery({
    queryKey: ['cost-trend'],
    queryFn: ({ signal }) => getCostTrend(signal),
    staleTime: 60_000,
  })

  const data = trendQ.data

  if (trendQ.isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Cost Trends</CardTitle>
        </CardHeader>
        <CardContent>
          <Loader2 className="w-4 h-4 animate-spin" />
        </CardContent>
      </Card>
    )
  }

  if (trendQ.error || !data || data.points.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Cost Trends</CardTitle>
          <CardDescription>Historical cost progression</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="text-sm text-muted-foreground flex items-start gap-2">
            <Info className="w-4 h-4 mt-0.5 shrink-0" />
            Trend data requires multiple days of history. Check back after the learning loop has run for longer.
          </div>
        </CardContent>
      </Card>
    )
  }

  const maxCost = Math.max(...data.points.map((p) => Math.max(p.baseline_cost, p.optimized_cost)))
  const minCost = Math.min(...data.points.map((p) => Math.min(p.baseline_cost, p.optimized_cost)))
  const costRange = maxCost - minCost || 1

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <TrendingDown className="w-4 h-4" />
          Cost Trends
        </CardTitle>
        <CardDescription>Daily average cost: baseline (gray) vs. optimized (emerald)</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-6">
          {data.points.map((point, idx) => {
            const baselineH = ((point.baseline_cost - minCost) / costRange) * 100 || 5
            const optimizedH = ((point.optimized_cost - minCost) / costRange) * 100 || 5

            return (
              <div key={idx}>
                <div className="flex items-baseline justify-between text-sm mb-2">
                  <span className="font-medium">
                    {new Date(point.date).toLocaleDateString('en-US', {
                      month: 'short',
                      day: 'numeric',
                    })}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {fmtCurrency(point.baseline_cost)} → {fmtCurrency(point.optimized_cost)}
                  </span>
                </div>
                <div className="space-y-1">
                  <div className="h-6 bg-muted rounded flex items-center px-1">
                    <div
                      className="bg-gray-400 dark:bg-gray-600 h-3 rounded"
                      style={{ width: `${baselineH}%` }}
                      title={`Baseline: ${fmtCurrency(point.baseline_cost)}`}
                    />
                  </div>
                  <div className="h-6 bg-emerald-500/10 rounded flex items-center px-1">
                    <div
                      className="bg-emerald-600 dark:bg-emerald-400 h-3 rounded"
                      style={{ width: `${optimizedH}%` }}
                      title={`Optimized: ${fmtCurrency(point.optimized_cost)}`}
                    />
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * Window controls: set or clear the counting epoch for narrowed cost metrics.
 */
function WindowControls() {
  const { session } = useAuth()
  const csrf = session?.csrf_token ?? ''
  const qc = useQueryClient()

  const metricsQ = useQuery({
    queryKey: ['cost-metrics'],
    queryFn: ({ signal }) => getCostMetrics(signal),
    staleTime: 30_000,
  })

  const resetMut = useMutation({
    mutationFn: () => resetCostWindow(csrf),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cost-metrics'] })
      qc.invalidateQueries({ queryKey: ['cost-trend'] })
      qc.invalidateQueries({ queryKey: ['cost-by-model-task'] })
    },
  })

  const window = metricsQ.data?.window

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2">
          <Calendar className="w-4 h-4" />
          Counting Window
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm text-muted-foreground">
          {window?.active
            ? `All cost metrics above are narrowed to ${new Date(window.since_iso).toLocaleDateString('en-US')} onward.`
            : 'All cost metrics above are all-time (no window set).'}
        </p>
        {window?.active && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => {
              if (confirm('Clear the counting window? Cost metrics will revert to all-time.')) {
                resetMut.mutate()
              }
            }}
            disabled={resetMut.isPending}
          >
            <RotateCcw className="w-3.5 h-3.5 mr-1.5" />
            {resetMut.isPending ? 'Clearing…' : 'Clear Window'}
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

export default function ModelCostOptimizerPage() {
  return (
    <div className="max-w-7xl mx-auto p-6 space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <DollarSign className="w-8 h-8 text-accent" />
          <h1 className="text-3xl font-bold">Model Cost Optimizer</h1>
        </div>
        <p className="text-muted-foreground">
          Token cost analysis and savings visualization. Learning loop automatically optimizes model selection.
        </p>
      </div>

      {/* Summary Cards */}
      <CostSummary />

      {/* Window Controls */}
      <WindowControls />

      {/* Token Efficiency */}
      <TokenEfficiencyPanel />

      {/* Cost by Task Type */}
      <CostByModelAndTaskPanel />

      {/* Trends */}
      <CostTrendPanel />

      {/* Footer */}
      <div className="pt-4 border-t">
        <p className="text-xs text-muted-foreground">
          Cost metrics calculated from the tenant's audit chain (ADR-0760). All numbers are real,
          not estimates. Baseline uses historical model selection; optimized uses Skill Forge v2.0 with learning.
        </p>
      </div>
    </div>
  )
}
