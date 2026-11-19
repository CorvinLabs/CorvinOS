/**
 * Control Plane — Subsystem Management Panel (ADR-2029 Stream 2)
 * Start, pause, resume, stop subsystems with state tracking
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Play,
  Pause,
  Square,
  RotateCcw,
  AlertCircle,
  CheckCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/hooks/use-toast";
import {
  controlPlaneErrorText,
  controlPlaneFetch,
  controlPlanePollInterval,
  isControlPlaneUnavailable,
} from "@/lib/control-plane";

const API_BASE = "/v1/console/control-plane/subsystems";

interface Subsystem {
  subsystem_id: string;
  state: "running" | "paused" | "stopped";
  started_at?: string;
  paused_at?: string;
  stopped_at?: string;
}

export default function ControlPlaneSubsystemsPage() {
  const queryClient = useQueryClient();

  // Fetch subsystems
  const { data: subsystems, isLoading, error } = useQuery<Subsystem[]>({
    queryKey: ["control-plane-subsystems"],
    queryFn: () => controlPlaneFetch<Subsystem[]>(API_BASE),
    retry: false,
    // Refresh every 5s for state updates — but never poll a route that has
    // answered "not implemented": it will not start existing between polls.
    refetchInterval: (q) => controlPlanePollInterval(q.state.error, 5000),
  });
  const unavailable = isControlPlaneUnavailable(error);

  // Start subsystem
  const startMutation = useMutation({
    mutationFn: async (subsystemId: string) => {
      return controlPlaneFetch(`${API_BASE}/${encodeURIComponent(subsystemId)}/start`, {
        method: "PATCH",
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["control-plane-subsystems"],
      });
      toast({ title: "Subsystem started successfully" });
    },
    onError: (err) => {
      toast({
        title: "Subsystem was not started",
        description: controlPlaneErrorText(err),
        variant: "destructive",
      });
    },
  });

  // Pause subsystem
  const pauseMutation = useMutation({
    mutationFn: async (subsystemId: string) => {
      return controlPlaneFetch(`${API_BASE}/${encodeURIComponent(subsystemId)}/pause`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ timeout_s: 30 }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["control-plane-subsystems"],
      });
      toast({ title: "Subsystem paused successfully" });
    },
    onError: (err) => {
      toast({
        title: "Subsystem was not paused",
        description: controlPlaneErrorText(err),
        variant: "destructive",
      });
    },
  });

  // Resume subsystem
  const resumeMutation = useMutation({
    mutationFn: async (subsystemId: string) => {
      return controlPlaneFetch(`${API_BASE}/${encodeURIComponent(subsystemId)}/resume`, {
        method: "PATCH",
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["control-plane-subsystems"],
      });
      toast({ title: "Subsystem resumed successfully" });
    },
    onError: (err) => {
      toast({
        title: "Subsystem was not resumed",
        description: controlPlaneErrorText(err),
        variant: "destructive",
      });
    },
  });

  // Stop subsystem
  const stopMutation = useMutation({
    mutationFn: async (subsystemId: string) => {
      return controlPlaneFetch(`${API_BASE}/${encodeURIComponent(subsystemId)}/stop`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force: false, timeout_s: 30 }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["control-plane-subsystems"],
      });
      toast({ title: "Subsystem stopped successfully" });
    },
    onError: (err) => {
      toast({
        title: "Subsystem was not stopped",
        description: controlPlaneErrorText(err),
        variant: "destructive",
      });
    },
  });

  const getStateColor = (state: string) => {
    switch (state) {
      case "running":
        return "bg-green-600";
      case "paused":
        return "bg-yellow-600";
      case "stopped":
        return "bg-gray-600";
      default:
        return "bg-gray-400";
    }
  };

  const getStateIcon = (state: string) => {
    switch (state) {
      case "running":
        return <CheckCircle className="h-5 w-5" />;
      case "paused":
        return <AlertCircle className="h-5 w-5" />;
      case "stopped":
        return <Square className="h-5 w-5" />;
      default:
        return null;
    }
  };

  return (
    <div className="container mx-auto p-6">
      <div className="mb-6">
        <h1 className="text-3xl font-bold">Subsystems</h1>
        <p className="mt-1 text-sm text-gray-600">
          Manage CorvinOS subsystem lifecycle (start, pause, resume, stop)
        </p>
      </div>

      {/* Subsystems Grid */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {unavailable ? (
          <div
            className="col-span-full rounded-lg border border-dashed p-8 text-center"
            data-testid="control-plane-subsystems-unavailable"
            role="status"
          >
            <p className="text-gray-600">
              Subsystem lifecycle control is not available on this build.
            </p>
          </div>
        ) : error ? (
          <div className="col-span-full rounded-lg border border-destructive p-8 text-center" role="alert">
            <p className="font-medium">Subsystems could not be loaded</p>
            <p className="mt-1 text-sm text-gray-600">{controlPlaneErrorText(error)}</p>
          </div>
        ) : isLoading ? (
          <>
            <Skeleton className="h-48" />
            <Skeleton className="h-48" />
            <Skeleton className="h-48" />
          </>
        ) : (subsystems || []).length === 0 ? (
          <div className="col-span-full rounded-lg border border-dashed p-8 text-center">
            <p className="text-gray-600">No subsystems found</p>
          </div>
        ) : (
          (subsystems || []).map((subsystem) => (
            <div
              key={subsystem.subsystem_id}
              className="rounded-lg border p-4 shadow-sm"
            >
              <div className="mb-4 flex items-start justify-between">
                <div className="flex-1">
                  <h3 className="font-semibold">{subsystem.subsystem_id}</h3>
                  <div className="mt-2 flex items-center gap-2">
                    <div className={`rounded-full p-1 text-white ${getStateColor(subsystem.state)}`}>
                      {getStateIcon(subsystem.state)}
                    </div>
                    <Badge className={getStateColor(subsystem.state)}>
                      {subsystem.state.charAt(0).toUpperCase() +
                        subsystem.state.slice(1)}
                    </Badge>
                  </div>
                </div>
              </div>

              {/* Timestamps */}
              <div className="mb-4 space-y-1 text-xs text-gray-600">
                {subsystem.started_at && (
                  <p>Started: {new Date(subsystem.started_at).toLocaleString()}</p>
                )}
                {subsystem.paused_at && (
                  <p>Paused: {new Date(subsystem.paused_at).toLocaleString()}</p>
                )}
                {subsystem.stopped_at && (
                  <p>Stopped: {new Date(subsystem.stopped_at).toLocaleString()}</p>
                )}
              </div>

              {/* Controls */}
              <div className="flex gap-2">
                {subsystem.state === "stopped" && (
                  <Button
                    size="sm"
                    onClick={() => startMutation.mutate(subsystem.subsystem_id)}
                    disabled={startMutation.isPending}
                    className="flex-1"
                  >
                    <Play className="mr-2 h-4 w-4" /> Start
                  </Button>
                )}

                {subsystem.state === "running" && (
                  <>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => pauseMutation.mutate(subsystem.subsystem_id)}
                      disabled={pauseMutation.isPending}
                      className="flex-1"
                    >
                      <Pause className="mr-2 h-4 w-4" /> Pause
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => stopMutation.mutate(subsystem.subsystem_id)}
                      disabled={stopMutation.isPending}
                      className="flex-1"
                    >
                      <Square className="mr-2 h-4 w-4" /> Stop
                    </Button>
                  </>
                )}

                {subsystem.state === "paused" && (
                  <>
                    <Button
                      size="sm"
                      onClick={() =>
                        resumeMutation.mutate(subsystem.subsystem_id)
                      }
                      disabled={resumeMutation.isPending}
                      className="flex-1"
                    >
                      <RotateCcw className="mr-2 h-4 w-4" /> Resume
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => stopMutation.mutate(subsystem.subsystem_id)}
                      disabled={stopMutation.isPending}
                      className="flex-1"
                    >
                      <Square className="mr-2 h-4 w-4" /> Stop
                    </Button>
                  </>
                )}
              </div>
            </div>
          ))
        )}
      </div>

      {/* Info Panel — describes a control that must exist to be described */}
      {!unavailable && <div className="mt-8 rounded-lg bg-blue-50 p-4 text-sm text-blue-900">
        <AlertCircle className="mb-2 h-4 w-4" />
        <p>
          <strong>Graceful Shutdown:</strong> Subsystems have 30 seconds to
          flush state before forced shutdown. Monitor subsystem logs for
          shutdown progress.
        </p>
      </div>}
    </div>
  );
}
