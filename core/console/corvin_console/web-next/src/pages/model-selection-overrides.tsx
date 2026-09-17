/**
 * Model Selection Overrides Panel — Tier 3 Variant D UI.
 *
 * Allows operators to override model selection on a per-task basis.
 * (Stub implementation — full version integrated in engine-config.tsx and vibe-engineering.tsx)
 */

import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Info } from 'lucide-react'

export default function ModelSelectionOverridesPage() {
  return (
    <div className="max-w-4xl mx-auto p-6 space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Model Selection Overrides</h1>
        <p className="text-muted-foreground mt-2">
          Manage model overrides per task type (see Engine Config for detailed settings)
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Operator Overrides</CardTitle>
        </CardHeader>
        <CardContent className="flex items-start gap-3">
          <Info className="w-5 h-5 text-muted-foreground mt-0.5" />
          <div className="text-sm text-muted-foreground">
            <p>Task-type model overrides are managed in:</p>
            <ul className="list-disc list-inside mt-2 space-y-1">
              <li><strong>Engine Config</strong> — Model selection per task type</li>
              <li><strong>Vibe Engineering</strong> — Learning loop convergence + optimizer controls</li>
              <li><strong>Model Cost Optimizer</strong> — Cost analysis and token efficiency</li>
            </ul>
            <p className="mt-3">All overrides are audited and hash-chained per ADR-0760/0761.</p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
