/**
 * Control Plane — Subsystem Manager Panel (ADR-2029 Stream 2)
 * Manage CorvinOS subsystems: enable/disable, health status, real-time updates
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Power,
  PowerOff,
  AlertTriangle,
  CheckCircle2,
  Pause,
  Activity,
  Heart,
  Copy,
  RefreshCw,
  Clock,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/hooks/use-toast";

const API_BASE = "/v1/console/control-plane/subsystems";

interface Subsystem {
  subsystem_id: string;
  name: string;
  description: string;
  status: "healthy" | "degraded" | "unhealthy" | "offline";
  enabled: boolean;
  version: string;
  uptime_seconds: number;
  memory_usage_mb: number;
  error_count: number;
  last_health_check: string;
  dependencies: string[];
}

interface SubsystemsData {
  timestamp: string;
  count_healthy: number;
  count_degraded: number;
  count_unhealthy: number;
  count_offline: number;
  subsystems: Subsystem[];
}

const statusColors: Record<Subsystem["status"], string> = {
  healthy: "text-green-600",
  degraded: "text-yellow-600",
  unhealthy: "text-orange-600",
  offline: "text-red-600",
};

const statusLabels: Record<Subsystem["status"], string> = {
  healthy: "Healthy",
  degraded: "Degraded",
  unhealthy: "Unhealthy",
  offline: "Offline",
};

export default function ControlSubsystemsPage() {
  const queryClient = useQueryClient();
  const [autoRefresh, setAutoRefresh] = React.useState(true);
  const [filterStatus, setFilterStatus] = React.useState<Subsystem["status"] | "all">("all");

  // Fetch subsystems
  const { data: subsystemsData, isLoading, error } = useQuery<SubsystemsData>({
    queryKey: ["control-subsystems"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}`);
      if (!res.ok) throw new Error("Failed to load subsystems");
      return res.json();
    },
    refetchInterval: autoRefresh ? 5000 : false,
    staleTime: 1000,
  });

  // Toggle subsystem
  const toggleMutation = useMutation({
    mutationFn: async ({
      subsystemId,
      enable,
    }: {
      subsystemId: string;
      enable: boolean;
    }) => {
      const res = await fetch(`${API_BASE}/${subsystemId}/${enable ? "enable" : "disable"}`, {
        method: "PATCH",
      });
      if (!res.ok) throw new Error(`Failed to ${enable ? "enable" : "disable"} subsystem`);
      return res.json();
    },
    onSuccess: (_, { enable, subsystemId }) => {
      queryClient.invalidateQueries({ queryKey: ["control-subsystems"] });
      toast({
        title: enable ? "Subsystem enabled" : "Subsystem disabled",
        description: `ID: ${subsystemId}`,
      });
    },
    onError: () => {
      toast({ title: "Failed to toggle subsystem", variant: "destructive" });
    },
  });

  // Force health check
  const healthCheckMutation = useMutation({
    mutationFn: async (subsystemId: string) => {
      const res = await fetch(`${API_BASE}/${subsystemId}/health-check`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("Health check failed");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-subsystems"] });
      toast({ title: "Health check completed" });
    },
    onError: () => {
      toast({ title: "Health check failed", variant: "destructive" });
    },
  });

  // Manual refresh
  const refreshMutation = useMutation({
    mutationFn: async () => {
      await queryClient.invalidateQueries({ queryKey: ["control-subsystems"] });
    },
  });

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast({ title: "Copied to clipboard" });
  };

  if (error) {
    return (
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <header>
          <h1 className="font-serif text-3xl font-light tracking-tight">Subsystem Manager</h1>
        </header>
        <Card className="border-destructive">
          <CardContent className="flex items-center gap-3 pt-6">
            <AlertCircle className="h-5 w-5 text-destructive" />
            <div>
              <p className="font-medium">Failed to load subsystems</p>
              <p className="text-sm text-muted-foreground">
                {error instanceof Error ? error.message : "Unknown error"}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  const filteredSubsystems =
    filterStatus === "all"
      ? subsystemsData?.subsystems || []
      : (subsystemsData?.subsystems || []).filter((s) => s.status === filterStatus);

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header>
        <h1 className="font-serif text-3xl font-light tracking-tight">Subsystem Manager</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Monitor and control all CorvinOS subsystems with real-time status updates
        </p>
      </header>

      {/* Control Bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          aria-label="Manually refresh subsystems list"
        >
          <RefreshCw className="mr-2 h-4 w-4" />
          Refresh
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setAutoRefresh(!autoRefresh)}
          aria-label={autoRefresh ? "Pause auto-refresh" : "Resume auto-refresh"}
        >
          {autoRefresh ? "Pause" : "Resume"} Auto-Refresh
        </Button>
        <div className="ml-auto flex gap-2">
          <select
            className="rounded-md border border-input bg-background px-3 py-1.5 text-sm"
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value as any)}
            aria-label="Filter by status"
          >
            <option value="all">All Subsystems</option>
            <option value="healthy">Healthy</option>
            <option value="degraded">Degraded</option>
            <option value="unhealthy">Unhealthy</option>
            <option value="offline">Offline</option>
          </select>
        </div>
      </div>

      {/* Status Summary */}
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : subsystemsData ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
          <Card>
            <CardContent className="pt-6">
              <p className="text-2xl font-semibold text-green-600">
                {subsystemsData.count_healthy}
              </p>
              <p className="text-sm text-muted-foreground">Healthy</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-2xl font-semibold text-yellow-600">
                {subsystemsData.count_degraded}
              </p>
              <p className="text-sm text-muted-foreground">Degraded</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-2xl font-semibold text-orange-600">
                {subsystemsData.count_unhealthy}
              </p>
              <p className="text-sm text-muted-foreground">Unhealthy</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-2xl font-semibold text-red-600">
                {subsystemsData.count_offline}
              </p>
              <p className="text-sm text-muted-foreground">Offline</p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* Subsystems List */}
      <div className="space-y-4">
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-48 w-full" />
            ))}
          </div>
        ) : (
          <div className="grid gap-4">
            {filteredSubsystems.map((subsystem) => (
              <Card
                key={subsystem.subsystem_id}
                className={subsystem.status === "offline" ? "border-destructive/30" : ""}
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <CardTitle>{subsystem.name}</CardTitle>
                        <Badge
                          variant="outline"
                          className={statusColors[subsystem.status]}
                        >
                          {statusLabels[subsystem.status]}
                        </Badge>
                      </div>
                      <CardDescription>{subsystem.description}</CardDescription>
                    </div>
                    <div className="flex gap-1">
                      <Switch
                        checked={subsystem.enabled}
                        onCheckedChange={(enabled) =>
                          toggleMutation.mutate({
                            subsystemId: subsystem.subsystem_id,
                            enable: enabled,
                          })
                        }
                        disabled={toggleMutation.isPending}
                        aria-label={`Toggle ${subsystem.name}`}
                      />
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                    <div>
                      <p className="text-sm text-muted-foreground">Version</p>
                      <p className="font-mono text-sm">{subsystem.version}</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Uptime</p>
                      <p className="text-sm font-semibold">
                        {Math.floor(subsystem.uptime_seconds / 3600)}h
                      </p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Memory</p>
                      <p className="text-sm font-semibold">{subsystem.memory_usage_mb.toFixed(1)}MB</p>
                    </div>
                    <div>
                      <p className="text-sm text-muted-foreground">Errors</p>
                      <p
                        className={`text-sm font-semibold ${
                          subsystem.error_count > 0 ? "text-destructive" : ""
                        }`}
                      >
                        {subsystem.error_count}
                      </p>
                    </div>
                  </div>

                  <div>
                    <p className="mb-2 text-sm text-muted-foreground">Dependencies</p>
                    <div className="flex flex-wrap gap-1">
                      {subsystem.dependencies.length > 0 ? (
                        subsystem.dependencies.map((dep) => (
                          <Badge key={dep} variant="secondary">
                            {dep}
                          </Badge>
                        ))
                      ) : (
                        <span className="text-sm text-muted-foreground">No dependencies</span>
                      )}
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2 border-t pt-3">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => healthCheckMutation.mutate(subsystem.subsystem_id)}
                      disabled={healthCheckMutation.isPending}
                      aria-label="Run health check"
                    >
                      <Heart className="mr-2 h-4 w-4" />
                      Health Check
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => copyToClipboard(subsystem.subsystem_id)}
                      aria-label="Copy subsystem ID"
                    >
                      <Copy className="mr-2 h-4 w-4" />
                      Copy ID
                    </Button>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {new Date(subsystem.last_health_check).toLocaleTimeString("en-US")}
                    </span>
                  </div>
                </CardContent>
              </Card>
            ))}
            {filteredSubsystems.length === 0 && (
              <Card>
                <CardContent className="flex items-center gap-3 pt-6">
                  <AlertCircle className="h-5 w-5 text-muted-foreground" />
                  <p className="text-muted-foreground">No subsystems found with selected filter</p>
                </CardContent>
              </Card>
            )}
          </div>
        )}
      </div>

      {subsystemsData && (
        <p className="text-xs text-muted-foreground">
          Last updated: {new Date(subsystemsData.timestamp).toUTCString()}
        </p>
      )}
    </div>
  );
}
