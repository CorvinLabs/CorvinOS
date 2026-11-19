/**
 * Generic Plugin Inspector (ADR-0560 Phase 1)
 *
 * Standard panel for a plugin: enable/disable, configuration, audit trail.
 *
 * Every piece of state is OPTIONAL and nothing is defaulted: a section whose
 * state was not loaded says "not available on this build" instead of rendering
 * an empty-but-plausible value ("No configuration options", "Audit Trail (0)",
 * a Disable button that does nothing). The manifest route passes no state today.
 */

import React from "react"
import { Link } from "react-router-dom"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"

interface GenericPluginInspectorProps {
  pluginId: string
  title: string
  version: string
  /** Real enabled state. Omit when it was not loaded. */
  enabled?: boolean
  /** Real toggle. The button renders only when both this and `enabled` are given. */
  onToggleEnabled?: (enabled: boolean) => void
  /** Real configuration. Omit when it was not loaded. */
  config?: Record<string, unknown>
  onConfigChange?: (delta: Record<string, unknown>) => void
  /** Real audit events. Omit when they were not loaded. */
  auditEvents?: Record<string, unknown>[]
}

const NOT_AVAILABLE = "Not available on this build."

export function GenericPluginInspector({
  pluginId,
  title,
  version,
  enabled,
  onToggleEnabled,
  config,
  onConfigChange,
  auditEvents,
}: GenericPluginInspectorProps) {
  const [activeTab, setActiveTab] = React.useState<"config" | "audit">("config")
  const canToggle = typeof enabled === "boolean" && typeof onToggleEnabled === "function"

  return (
    <div className="flex flex-col gap-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">{title}</h1>
          <p className="text-muted-foreground text-sm">
            Plugin: {pluginId} <Badge variant="secondary" className="ml-2">{version}</Badge>
          </p>
        </div>
        {canToggle ? (
          <Button
            variant={enabled ? "default" : "outline"}
            onClick={() => onToggleEnabled(!enabled)}
          >
            {enabled ? "Disable" : "Enable"}
          </Button>
        ) : (
          <Button asChild variant="outline">
            <Link to="/app/marketplace?tab=installed">Manage in Marketplace</Link>
          </Button>
        )}
      </div>

      {!canToggle && (
        <p className="text-sm text-muted-foreground" role="status">
          Enabling or disabling this plugin from here is not available on this build.
          The Marketplace shows its real state.
        </p>
      )}

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
          {auditEvents ? `Audit Trail (${auditEvents.length})` : "Audit Trail"}
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
            {config === undefined ? (
              <p className="text-muted-foreground text-sm">{NOT_AVAILABLE}</p>
            ) : Object.keys(config).length === 0 ? (
              <p className="text-muted-foreground text-sm">No configuration options</p>
            ) : (
              <div className="space-y-4">
                {Object.entries(config).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between">
                    <label className="text-sm font-medium">{key}</label>
                    <input
                      type="text"
                      value={String(value)}
                      readOnly={!onConfigChange}
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
            {auditEvents === undefined ? (
              <p className="text-muted-foreground text-sm">{NOT_AVAILABLE}</p>
            ) : auditEvents.length === 0 ? (
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
