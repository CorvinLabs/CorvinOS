/**
 * Generic Plugin Inspector (ADR-0560 Phase 1)
 *
 * Standard panel for all plugins: config editor + audit trail + enable/disable.
 * Replaces custom UI for 90% of plugins; custom bundle (iframe) is optional.
 */

import React from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

interface GenericPluginInspectorProps {
  pluginId: string
  title: string
  version: string
  enabled: boolean
  onToggleEnabled: (enabled: boolean) => void
  config?: Record<string, any>
  onConfigChange?: (delta: Record<string, any>) => void
  auditEvents?: any[]
}

export function GenericPluginInspector({
  pluginId,
  title,
  version,
  enabled,
  onToggleEnabled,
  config = {},
  onConfigChange,
  auditEvents = [],
}: GenericPluginInspectorProps) {
  const [activeTab, setActiveTab] = React.useState<"config" | "audit">("config")

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{title}</h1>
          <p className="text-muted-foreground text-sm">
            Plugin: {pluginId} <Badge variant="secondary" className="ml-2">{version}</Badge>
          </p>
        </div>
        <Button
          variant={enabled ? "default" : "outline"}
          onClick={() => onToggleEnabled(!enabled)}
        >
          {enabled ? "Disable" : "Enable"}
        </Button>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 border-b">
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
          Audit Trail ({auditEvents.length})
        </button>
      </div>

      {/* Tab Content */}
      {activeTab === "config" && (
        <Card>
          <CardHeader>
            <CardTitle>Configuration</CardTitle>
            <CardDescription>
              Plugin settings and parameters
            </CardDescription>
          </CardHeader>
          <CardContent>
            {Object.keys(config).length === 0 ? (
              <p className="text-muted-foreground text-sm">No configuration options</p>
            ) : (
              <div className="space-y-4">
                {Object.entries(config).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between">
                    <label className="text-sm font-medium">{key}</label>
                    <input
                      type="text"
                      value={String(value)}
                      onChange={(e) => onConfigChange?.({ [key]: e.target.value })}
                      className="rounded-md border border-border px-2 py-1 text-sm"
                    />
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {activeTab === "audit" && (
        <Card>
          <CardHeader>
            <CardTitle>Audit Trail</CardTitle>
            <CardDescription>
              Plugin events and state changes
            </CardDescription>
          </CardHeader>
          <CardContent>
            {auditEvents.length === 0 ? (
              <p className="text-muted-foreground text-sm">No audit events</p>
            ) : (
              <div className="space-y-2 max-h-96 overflow-y-auto">
                {auditEvents.map((event, i) => (
                  <div key={i} className="text-xs p-2 rounded-md bg-muted/50">
                    <div className="font-mono">{JSON.stringify(event, null, 2)}</div>
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
