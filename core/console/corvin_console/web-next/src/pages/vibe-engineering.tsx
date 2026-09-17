/**
 * Vibe Engineering — Learning Loops Management & Observability
 * Route: /app/vibe-engineering
 *
 * Complete Learning Loops display: Skill Forge v2.0 learning integration,
 * confidence convergence, feedback loop status, and optimizer activity.
 *
 * ADR-0314: Learning Infrastructure (feedback, confidence, outcome tracking)
 * ADR-0693: Skill Learning Bridge (async integration)
 * ADR-0694: Optimizer with Bounds (convergence detection, PII scrubbing)
 * ADR-0845: OS Model Selector (learning-integrated variants)
 */

import React, { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Clock,
  Database,
  Info,
  Loader2,
  RefreshCw,
  TrendingUp,
  Zap,
  BarChart3,
  Target,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Progress } from '@/components/ui/progress'
import { cn } from '@/lib/utils'
import { useAuth } from '@/lib/auth'

// API functions for learning loops
const getLearningLoopsStatus = (signal?: AbortSignal) =>
  fetch('/api/v1/learning/loops', { signal }).then(r => r.json())

const getLearningFeedbackMetrics = (signal?: AbortSignal) =>
  fetch('/api/v1/learning/feedback-metrics', { signal }).then(r => r.json())

const getSkillLearningStatus = (signal?: AbortSignal) =>
  fetch('/api/v1/learning/skills', { signal }).then(r => r.json())

const getOptimizerConvergence = (signal?: AbortSignal) =>
  fetch('/api/v1/learning/convergence', { signal }).then(r => r.json())

const triggerManualOptimization = (csrf: string) =>
  fetch('/api/v1/learning/optimize', { method: 'POST', headers: { 'x-csrf-token': csrf } }).then(r => r.json())

function fmtInt(n: number): string {
  return n.toLocaleString('en-US')
}

function fmtPct(value: number): string {
  return (value * 100).toFixed(1) + '%'
}

/**
 * Learning Loop status overview: which loops are active, converged, or waiting.
 */
function LearningLoopsOverview() {
  const statusQ = useQuery({
    queryKey: ['learning-loops-status'],
    queryFn: ({ signal }) => getLearningLoopsStatus(signal),
    staleTime: 10_000,
    refetchInterval: 30_000,
  })

  const data = statusQ.data

  if (statusQ.isLoading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <Loader2 className="w-4 h-4 animate-spin" />
        </CardContent>
      </Card>
    )
  }

  if (statusQ.error || !data) {
    return null
  }

  const loopStats = {
    active: data.loops.filter((l) => l.status === 'active').length,
    converged: data.loops.filter((l) => l.status === 'converged').length,
    learning: data.loops.filter((l) => l.status === 'learning').length,
    idle: data.loops.filter((l) => l.status === 'idle').length,
  }

  return (
    <div className="grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-5">
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Total Loops</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{data.loops.length}</div>
          <p className="text-xs text-muted-foreground">Learning loop instances</p>
        </CardContent>
      </Card>

      <Card className="border-emerald-500/30 bg-emerald-500/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-emerald-700 dark:text-emerald-400">Converged</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold text-emerald-700 dark:text-emerald-400">{loopStats.converged}</div>
          <p className="text-xs text-emerald-600/70 dark:text-emerald-400/70">Confidence stable</p>
        </CardContent>
      </Card>

      <Card className="border-amber-500/30 bg-amber-500/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-amber-700 dark:text-amber-400">Learning</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold text-amber-700 dark:text-amber-400">{loopStats.learning}</div>
          <p className="text-xs text-amber-600/70 dark:text-amber-400/70">Accumulating feedback</p>
        </CardContent>
      </Card>

      <Card className="border-blue-500/30 bg-blue-500/10">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium text-blue-700 dark:text-blue-400">Active</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold text-blue-700 dark:text-blue-400">{loopStats.active}</div>
          <p className="text-xs text-blue-600/70 dark:text-blue-400/70">Processing feedback</p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-medium">Idle</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-2xl font-bold">{loopStats.idle}</div>
          <p className="text-xs text-muted-foreground">Waiting for data</p>
        </CardContent>
      </Card>
    </div>
  )
}

/**
 * Feedback metrics: how much feedback the loop is receiving and processing.
 */
function FeedbackMetricsPanel() {
  const metricsQ = useQuery({
    queryKey: ['learning-feedback-metrics'],
    queryFn: ({ signal }) => getLearningFeedbackMetrics(signal),
    staleTime: 15_000,
  })

  const data = metricsQ.data

  if (metricsQ.isLoading) {
    return null
  }

  if (metricsQ.error || !data) {
    return null
  }

  const feedbackRate = data.feedback_events_24h > 0 ? data.feedback_events_24h / 24 : 0

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="w-4 h-4" />
          Feedback Metrics
        </CardTitle>
        <CardDescription>Last 24 hours: feedback flow and processing</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-4 grid-cols-2 md:grid-cols-4">
          <div>
            <p className="text-xs text-muted-foreground mb-1">Feedback Events (24h)</p>
            <p className="text-2xl font-bold">{fmtInt(data.feedback_events_24h)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1">Avg Events / Hour</p>
            <p className="text-2xl font-bold">{feedbackRate.toFixed(1)}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1">Processing Lag</p>
            <p className="text-2xl font-bold">{data.avg_processing_lag_ms}ms</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground mb-1">Queue Depth</p>
            <p className="text-2xl font-bold">{data.queue_depth}</p>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium">Processing Capacity</span>
            <span className="text-sm text-muted-foreground">{fmtPct(data.queue_depth / data.max_queue_depth)}</span>
          </div>
          <Progress value={(data.queue_depth / data.max_queue_depth) * 100} className="h-2" />
        </div>

        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Success Rate (last 1h):</span>
            <span className="font-semibold">{fmtPct(data.processing_success_rate_1h)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">Failed Events (24h):</span>
            <span className={cn('font-semibold', data.failed_events_24h > 10 ? 'text-red-600' : '')}>{data.failed_events_24h}</span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * Per-Skill learning status: how each Skill's confidence is developing.
 */
function SkillLearningStatusPanel() {
  const skillsQ = useQuery({
    queryKey: ['skills-learning-status'],
    queryFn: ({ signal }) => getSkillLearningStatus(signal),
    staleTime: 20_000,
  })

  const data = skillsQ.data

  if (skillsQ.isLoading) {
    return (
      <Card>
        <CardContent className="pt-6">
          <Loader2 className="w-4 h-4 animate-spin" />
        </CardContent>
      </Card>
    )
  }

  if (skillsQ.error || !data || data.skills.length === 0) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Zap className="w-4 h-4" />
          Skill Learning Status
        </CardTitle>
        <CardDescription>Confidence scores and feedback accumulation per Skill</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="space-y-6">
          {data.skills.map((skill) => (
            <div key={skill.skill_id} className="border-l-2 border-accent/30 pl-4 py-2">
              <div className="flex items-start justify-between mb-2">
                <div>
                  <h4 className="font-semibold flex items-center gap-2">
                    {skill.skill_id}
                    {skill.status === 'converged' ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    ) : skill.status === 'learning' ? (
                      <TrendingUp className="w-4 h-4 text-amber-600" />
                    ) : (
                      <Info className="w-4 h-4 text-muted-foreground" />
                    )}
                  </h4>
                  <p className="text-sm text-muted-foreground">{skill.skill_version}</p>
                </div>
                <Badge
                  variant={
                    skill.status === 'converged'
                      ? 'default'
                      : skill.status === 'learning'
                        ? 'secondary'
                        : 'outline'
                  }
                >
                  {skill.status}
                </Badge>
              </div>

              <div className="space-y-3">
                <div>
                  <div className="flex items-baseline justify-between mb-1 text-sm">
                    <span className="font-medium">Confidence</span>
                    <span className="tabular-nums">{fmtPct(skill.confidence_score)}</span>
                  </div>
                  <Progress value={skill.confidence_score * 100} className="h-2" />
                </div>

                <div className="grid gap-2 grid-cols-3 text-xs">
                  <div>
                    <p className="text-muted-foreground">Feedback Events</p>
                    <p className="font-semibold">{fmtInt(skill.feedback_count)}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Success Rate</p>
                    <p className="font-semibold">{fmtPct(skill.success_rate)}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">Last Updated</p>
                    <p className="font-semibold">
                      {skill.last_update
                        ? new Date(skill.last_update).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
                        : '—'}
                    </p>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * Optimizer convergence: slope-based detection showing if learning is stabilizing.
 */
function OptimizerConvergencePanel() {
  const qc = useQueryClient()
  const convergenceQ = useQuery({
    queryKey: ['optimizer-convergence'],
    queryFn: ({ signal }) => getOptimizerConvergence(signal),
    staleTime: 30_000,
  })

  const { session } = useAuth()
  const triggerMut = useMutation({
    mutationFn: () => triggerManualOptimization(session?.csrf_token ?? ''),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['optimizer-convergence'] })
      qc.invalidateQueries({ queryKey: ['skills-learning-status'] })
    },
  })

  const data = convergenceQ.data

  if (convergenceQ.isLoading) {
    return null
  }

  if (convergenceQ.error || !data) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Target className="w-4 h-4" />
          Optimizer Convergence
        </CardTitle>
        <CardDescription>Confidence slope detection and parameter optimization</CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="grid gap-4 grid-cols-1 md:grid-cols-3">
          <div>
            <p className="text-xs text-muted-foreground mb-1">Convergence Status</p>
            <Badge
              variant={data.is_converged ? 'default' : 'secondary'}
              className={cn(data.is_converged && 'bg-emerald-600')}
            >
              {data.is_converged ? 'Converged' : 'Learning'}
            </Badge>
            <p className="text-xs text-muted-foreground mt-1">
              {data.days_to_convergence && !data.is_converged
                ? `Est. ${data.days_to_convergence.toFixed(1)} days`
                : data.is_converged
                  ? 'Stable'
                  : '—'}
            </p>
          </div>

          <div>
            <p className="text-xs text-muted-foreground mb-1">Mean Slope (confidence/day)</p>
            <p className="text-2xl font-bold">
              {data.mean_slope_per_day.toFixed(3)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {Math.abs(data.mean_slope_per_day) < 0.01 ? 'Flat' : data.mean_slope_per_day > 0 ? 'Improving' : 'Declining'}
            </p>
          </div>

          <div>
            <p className="text-xs text-muted-foreground mb-1">Std Dev (variance)</p>
            <p className="text-2xl font-bold">{data.std_dev_slope.toFixed(3)}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {data.std_dev_slope < 0.05 ? 'Stable' : 'Volatile'}
            </p>
          </div>
        </div>

        <div>
          <div className="flex items-center justify-between mb-2">
            <span className="text-sm font-medium">Overall Confidence</span>
            <span className="text-sm text-muted-foreground">{fmtPct(data.current_confidence)}</span>
          </div>
          <Progress value={data.current_confidence * 100} className="h-3" />
        </div>

        {data.is_converged && (
          <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-lg flex gap-2">
            <CheckCircle2 className="w-5 h-5 text-emerald-600 shrink-0" />
            <div className="text-sm text-emerald-700 dark:text-emerald-300">
              Learning has converged. Confidence is stable and parameter optimization is complete.
            </div>
          </div>
        )}

        <div className="flex gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() => triggerMut.mutate()} disabled={triggerMut.isPending}>
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
            {triggerMut.isPending ? 'Running…' : 'Trigger Optimization'}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

/**
 * Learning decisions: recent model selection decisions, outcomes, and confidence.
 */
function RecentDecisionsPanel() {
  const [limit, setLimit] = useState(10)

  const decisionsQ = useQuery({
    queryKey: ['recent-learning-decisions', limit],
    queryFn: ({ signal }) =>
      fetch(`/api/v1/learning/decisions?limit=${limit}`, { signal }).then((r) => r.json()),
    staleTime: 20_000,
  })

  const decisions = decisionsQ.data?.decisions ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4" />
          Recent Learning Decisions
        </CardTitle>
        <CardDescription>Last model selection decisions with outcome feedback</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b">
                <th className="text-left py-2 px-2 font-medium">Timestamp</th>
                <th className="text-left py-2 px-2 font-medium">Skill</th>
                <th className="text-left py-2 px-2 font-medium">Decision</th>
                <th className="text-left py-2 px-2 font-medium">Confidence</th>
                <th className="text-left py-2 px-2 font-medium">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {decisions.length === 0 ? (
                <tr>
                  <td colSpan={5} className="text-center py-4 text-muted-foreground">
                    No decisions recorded yet
                  </td>
                </tr>
              ) : (
                decisions.map((d: any, idx: number) => (
                  <tr key={idx} className="border-b hover:bg-muted/50">
                    <td className="py-2 px-2 text-xs text-muted-foreground">
                      {new Date(d.timestamp).toLocaleTimeString('en-US')}
                    </td>
                    <td className="py-2 px-2 font-mono text-xs">{d.skill_id}</td>
                    <td className="py-2 px-2">
                      <Badge variant="outline">{d.selected_value}</Badge>
                    </td>
                    <td className="py-2 px-2">
                      <div className="flex items-center gap-1">
                        <div className="w-16 bg-muted rounded h-1.5">
                          <div
                            className="bg-accent h-1.5 rounded"
                            style={{ width: `${d.confidence * 100}%` }}
                          />
                        </div>
                        <span className="text-xs">{fmtPct(d.confidence)}</span>
                      </div>
                    </td>
                    <td className="py-2 px-2">
                      {d.outcome === 'success' ? (
                        <Badge variant="outline" className="bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">
                          ✓ Success
                        </Badge>
                      ) : d.outcome === 'failed' ? (
                        <Badge variant="outline" className="bg-red-500/10 text-red-700 dark:text-red-300">
                          ✗ Failed
                        </Badge>
                      ) : (
                        <Badge variant="outline">Pending</Badge>
                      )}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {decisions.length > 0 && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="mt-4 w-full"
            onClick={() => setLimit(limit + 10)}
          >
            <Loader2 className="w-3.5 h-3.5 mr-1.5" />
            Load More
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

/**
 * Learning infrastructure health: audit chain, persistence, async queue.
 */
function LearningHealthPanel() {
  const healthQ = useQuery({
    queryKey: ['learning-health'],
    queryFn: ({ signal }) =>
      fetch('/api/v1/learning/health', { signal }).then((r) => r.json()),
    staleTime: 15_000,
  })

  const health = healthQ.data

  if (healthQ.isLoading) {
    return null
  }

  if (healthQ.error || !health) {
    return null
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="w-4 h-4" />
          Learning Infrastructure Health
        </CardTitle>
        <CardDescription>Audit chain, persistence, and async processing status</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-4">
          <div className="flex items-center gap-2">
            {health.audit_chain_ok ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            ) : (
              <AlertCircle className="w-4 h-4 text-red-600" />
            )}
            <div className="text-sm">
              <p className="font-medium">Audit Chain</p>
              <p className="text-xs text-muted-foreground">{health.audit_chain_ok ? 'Healthy' : 'Degraded'}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {health.persistence_ok ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            ) : (
              <AlertCircle className="w-4 h-4 text-red-600" />
            )}
            <div className="text-sm">
              <p className="font-medium">Persistence</p>
              <p className="text-xs text-muted-foreground">{health.persistence_ok ? 'Online' : 'Offline'}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {health.async_queue_ok ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            ) : (
              <AlertCircle className="w-4 h-4 text-red-600" />
            )}
            <div className="text-sm">
              <p className="font-medium">Async Queue</p>
              <p className="text-xs text-muted-foreground">{health.async_queue_ok ? 'Processing' : 'Blocked'}</p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            {health.pii_scrubber_ok ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-600" />
            ) : (
              <AlertCircle className="w-4 h-4 text-red-600" />
            )}
            <div className="text-sm">
              <p className="font-medium">PII Scrubber</p>
              <p className="text-xs text-muted-foreground">{health.pii_scrubber_ok ? 'Active' : 'Inactive'}</p>
            </div>
          </div>
        </div>

        {!health.audit_chain_ok && (
          <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-lg flex gap-2">
            <AlertCircle className="w-4 h-4 text-red-600 mt-0.5 shrink-0" />
            <div className="text-sm text-red-700 dark:text-red-300">
              Audit chain is degraded. Learning events may not be persisted correctly.
            </div>
          </div>
        )}

        {health.async_queue_blocked && (
          <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg flex gap-2">
            <AlertCircle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
            <div className="text-sm text-amber-700 dark:text-amber-300">
              Async queue is blocked. Feedback processing is suspended.
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default function VibeEngineeringPage() {
  return (
    <div className="max-w-7xl mx-auto p-6 space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-3 mb-2">
          <Zap className="w-8 h-8 text-accent" />
          <h1 className="text-3xl font-bold">Vibe Engineering</h1>
        </div>
        <p className="text-muted-foreground">
          Learning loops observability: feedback processing, confidence convergence, and optimizer status.
          Real-time monitoring of Skill Forge v2.0 learning infrastructure.
        </p>
      </div>

      {/* Overview Cards */}
      <LearningLoopsOverview />

      {/* Tabs for detailed views */}
      <Tabs defaultValue="feedback" className="w-full">
        <TabsList className="grid w-full grid-cols-5">
          <TabsTrigger value="feedback" className="flex items-center gap-1">
            <Activity className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Feedback</span>
          </TabsTrigger>
          <TabsTrigger value="skills" className="flex items-center gap-1">
            <Zap className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Skills</span>
          </TabsTrigger>
          <TabsTrigger value="optimizer" className="flex items-center gap-1">
            <Target className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Optimizer</span>
          </TabsTrigger>
          <TabsTrigger value="decisions" className="flex items-center gap-1">
            <BarChart3 className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Decisions</span>
          </TabsTrigger>
          <TabsTrigger value="health" className="flex items-center gap-1">
            <Database className="w-3.5 h-3.5" />
            <span className="hidden sm:inline">Health</span>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="feedback">
          <FeedbackMetricsPanel />
        </TabsContent>

        <TabsContent value="skills">
          <SkillLearningStatusPanel />
        </TabsContent>

        <TabsContent value="optimizer">
          <OptimizerConvergencePanel />
        </TabsContent>

        <TabsContent value="decisions">
          <RecentDecisionsPanel />
        </TabsContent>

        <TabsContent value="health">
          <LearningHealthPanel />
        </TabsContent>
      </Tabs>

      {/* Footer */}
      <div className="pt-4 border-t space-y-2">
        <p className="text-xs text-muted-foreground">
          Learning infrastructure: ADR-0314 (feedback), ADR-0693 (Skill Learning Bridge), ADR-0694 (Optimizer),
          ADR-0845 (Model Selector).
        </p>
        <p className="text-xs text-muted-foreground">
          All feedback events are audited and persisted (GDPR Art. 30, 32). Convergence detection uses slope-based
          analysis with ±1σ bounds checking. PII scrubbing is fail-closed.
        </p>
      </div>
    </div>
  )
}
