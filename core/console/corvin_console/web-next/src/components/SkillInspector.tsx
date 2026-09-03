/**
 * Generic Skill Inspector (ADR-0561 Phase 3)
 *
 * Standard panel for all Skills: config + audit trail + learning metrics.
 * Renders automatically when a Skill registers via manifest.
 */

import React from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"

interface SkillInspectorProps {
  skillId: string
  title?: string
  version?: string
  confidenceScore?: number
  lastExecuted?: string
  executionCount?: number
  metrics?: Record<string, any>
  recentEvents?: any[]
}

export function SkillInspector({
  skillId,
  title = skillId,
  version = "1.0.0",
  confidenceScore = 0.85,
  lastExecuted = "2 minutes ago",
  executionCount = 42,
  metrics = {},
  recentEvents = [],
}: SkillInspectorProps) {
  const [activeTab, setActiveTab] = React.useState<"config" | "learning" | "audit">("learning")

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{title}</h1>
          <p className="text-muted-foreground text-sm">
            Skill: {skillId} <Badge variant="secondary" className="ml-2">{version}</Badge>
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <div className="text-sm font-medium">
            Confidence: <span className="text-primary">{(confidenceScore * 100).toFixed(1)}%</span>
          </div>
          <div className="text-xs text-muted-foreground">
            {executionCount} executions • {lastExecuted}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b">
        <button
          onClick={() => setActiveTab("learning")}
          className={`px-3 py-2 text-sm font-medium transition-colors ${
            activeTab === "learning"
              ? "border-b-2 border-primary text-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Learning
        </button>
        <button
          onClick={() => setActiveTab("config")}
          className={`px-3 py-2 text-sm font-medium transition-colors ${
            activeTab === "config"
              ? "border-b-2 border-primary text-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Configuration
        </button>
        <button
          onClick={() => setActiveTab("audit")}
          className={`px-3 py-2 text-sm font-medium transition-colors ${
            activeTab === "audit"
              ? "border-b-2 border-primary text-foreground"
              : "text-muted-foreground hover:text-foreground"
          }`}
        >
          Audit ({recentEvents.length})
        </button>
      </div>

      {/* Tab: Learning */}
      {activeTab === "learning" && (
        <Card>
          <CardHeader>
            <CardTitle>Learning Metrics</CardTitle>
            <CardDescription>
              Skill performance and confidence over time (ADR-0314)
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-4">
              <div className="flex-1">
                <div className="text-sm font-medium mb-2">Confidence Score</div>
                <div className="w-full bg-muted rounded-full h-2">
                  <div
                    className="bg-primary h-2 rounded-full transition-all"
                    style={{ width: `${confidenceScore * 100}%` }}
                  />
                </div>
              </div>
              <div className="text-2xl font-bold text-primary">
                {(confidenceScore * 100).toFixed(0)}%
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="p-3 rounded-md bg-muted/50">
                <div className="text-xs text-muted-foreground">Executions</div>
                <div className="text-xl font-bold">{executionCount}</div>
              </div>
              <div className="p-3 rounded-md bg-muted/50">
                <div className="text-xs text-muted-foreground">Last Executed</div>
                <div className="text-sm font-medium">{lastExecuted}</div>
              </div>
            </div>

            {Object.keys(metrics).length > 0 && (
              <div className="space-y-2">
                <div className="text-sm font-medium">Additional Metrics</div>
                {Object.entries(metrics).map(([key, value]) => (
                  <div key={key} className="flex justify-between text-sm">
                    <span className="text-muted-foreground">{key}</span>
                    <span className="font-medium">{String(value)}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Tab: Config */}
      {activeTab === "config" && (
        <Card>
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>
              Skill parameters and settings
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground text-sm">
              Configuration (to be implemented in Phase 4)
            </p>
          </CardContent>
        </Card>
      )}

      {/* Tab: Audit */}
      {activeTab === "audit" && (
        <Card>
          <CardHeader>
            <CardTitle>Audit Trail</CardTitle>
            <CardDescription>
              Skill execution events (hash-chained, ADR-0232)
            </CardDescription>
          </CardHeader>
          <CardContent>
            {recentEvents.length === 0 ? (
              <p className="text-muted-foreground text-sm">No events</p>
            ) : (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {recentEvents.map((event, i) => (
                  <div key={i} className="text-xs p-2 rounded-md bg-muted/50">
                    <div className="font-mono text-xs">{JSON.stringify(event, null, 2)}</div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
