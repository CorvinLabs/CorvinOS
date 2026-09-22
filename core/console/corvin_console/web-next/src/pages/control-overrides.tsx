/**
 * Control Plane — Override Authority Panel (ADR-2029 Stream 3)
 * Create, approve, revoke, and manage override requests with TTL timer
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Trash2,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Lock,
  LockOpen,
  Copy,
  RefreshCw,
  AlertCircle,
  Eye,
  EyeOff,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { toast } from "@/hooks/use-toast";

const API_BASE = "/v1/console/control-plane/overrides";

interface Override {
  override_id: string;
  subsystem_id: string;
  requested_by: string;
  reason: string;
  status: "pending" | "approved" | "revoked" | "expired";
  requested_at: string;
  approved_at?: string;
  expires_at: string;
  ttl_seconds: number;
  constraint: string;
}

interface OverridesData {
  timestamp: string;
  active_count: number;
  pending_count: number;
  revoked_count: number;
  overrides: Override[];
}

export default function ControlOverridesPage() {
  const queryClient = useQueryClient();
  const [newDialogOpen, setNewDialogOpen] = React.useState(false);
  const [autoRefresh, setAutoRefresh] = React.useState(true);
  const [showReason, setShowReason] = React.useState<Record<string, boolean>>({});
  const [newOverride, setNewOverride] = React.useState({
    subsystem_id: "",
    reason: "",
    ttl_seconds: 3600,
  });

  // Fetch overrides
  const { data: overridesData, isLoading, error } = useQuery<OverridesData>({
    queryKey: ["control-overrides"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}`);
      if (!res.ok) throw new Error("Failed to load overrides");
      return res.json();
    },
    refetchInterval: autoRefresh ? 5000 : false,
    staleTime: 1000,
  });

  // Create override
  const createMutation = useMutation({
    mutationFn: async (data: typeof newOverride) => {
      const res = await fetch(`${API_BASE}/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error("Failed to create override");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-overrides"] });
      setNewDialogOpen(false);
      setNewOverride({ subsystem_id: "", reason: "", ttl_seconds: 3600 });
      toast({ title: "Override request created" });
    },
    onError: () => {
      toast({ title: "Failed to create override", variant: "destructive" });
    },
  });

  // Approve override
  const approveMutation = useMutation({
    mutationFn: async (overrideId: string) => {
      const res = await fetch(`${API_BASE}/${overrideId}/approve`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to approve override");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-overrides"] });
      toast({ title: "Override approved" });
    },
    onError: () => {
      toast({ title: "Failed to approve override", variant: "destructive" });
    },
  });

  // Revoke override
  const revokeMutation = useMutation({
    mutationFn: async (overrideId: string) => {
      const res = await fetch(`${API_BASE}/${overrideId}/revoke`, { method: "POST" });
      if (!res.ok) throw new Error("Failed to revoke override");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-overrides"] });
      toast({ title: "Override revoked" });
    },
    onError: () => {
      toast({ title: "Failed to revoke override", variant: "destructive" });
    },
  });

  // Manual refresh
  const refreshMutation = useMutation({
    mutationFn: async () => {
      await queryClient.invalidateQueries({ queryKey: ["control-overrides"] });
    },
  });

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast({ title: "Copied to clipboard" });
  };

  const calculateRemainingTime = (expiresAt: string): string => {
    const now = new Date();
    const expires = new Date(expiresAt);
    const diffMs = expires.getTime() - now.getTime();
    if (diffMs <= 0) return "Expired";
    const hours = Math.floor(diffMs / 3600000);
    const minutes = Math.floor((diffMs % 3600000) / 60000);
    if (hours > 0) return `${hours}h ${minutes}m remaining`;
    return `${minutes}m remaining`;
  };

  if (error) {
    return (
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <header>
          <h1 className="font-serif text-3xl font-light tracking-tight">Override Authority</h1>
        </header>
        <Card className="border-destructive">
          <CardContent className="flex items-center gap-3 pt-6">
            <AlertCircle className="h-5 w-5 text-destructive" />
            <div>
              <p className="font-medium">Failed to load overrides</p>
              <p className="text-sm text-muted-foreground">
                {error instanceof Error ? error.message : "Unknown error"}
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 p-6">
      <header>
        <h1 className="font-serif text-3xl font-light tracking-tight">Override Authority</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Request, approve, and manage security overrides with time-limited TTL controls
        </p>
      </header>

      {/* Control Bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Dialog open={newDialogOpen} onOpenChange={setNewDialogOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create Override
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create Override Request</DialogTitle>
              <DialogDescription>
                Request a temporary override with auto-expiration
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div>
                <label className="text-sm font-medium">Subsystem ID</label>
                <Input
                  placeholder="e.g., skill-router"
                  value={newOverride.subsystem_id}
                  onChange={(e) =>
                    setNewOverride({ ...newOverride, subsystem_id: e.target.value })
                  }
                  className="mt-1"
                  aria-label="Subsystem ID"
                />
              </div>
              <div>
                <label className="text-sm font-medium">Reason</label>
                <textarea
                  className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  placeholder="Explain why this override is needed"
                  value={newOverride.reason}
                  onChange={(e) =>
                    setNewOverride({ ...newOverride, reason: e.target.value })
                  }
                  rows={3}
                  aria-label="Override reason"
                />
              </div>
              <div>
                <label className="text-sm font-medium">TTL (seconds)</label>
                <Input
                  type="number"
                  min="60"
                  max="86400"
                  value={newOverride.ttl_seconds}
                  onChange={(e) =>
                    setNewOverride({
                      ...newOverride,
                      ttl_seconds: parseInt(e.target.value),
                    })
                  }
                  className="mt-1"
                  aria-label="TTL in seconds"
                />
                <p className="mt-1 text-xs text-muted-foreground">
                  {newOverride.ttl_seconds < 3600
                    ? `${Math.floor(newOverride.ttl_seconds / 60)}m`
                    : `${Math.floor(newOverride.ttl_seconds / 3600)}h`}{" "}
                  validity
                </p>
              </div>
              <Button
                onClick={() => createMutation.mutate(newOverride)}
                disabled={
                  createMutation.isPending ||
                  !newOverride.subsystem_id ||
                  !newOverride.reason
                }
                className="w-full"
              >
                {createMutation.isPending ? "Creating..." : "Create Override"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>

        <Button
          variant="outline"
          size="sm"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          aria-label="Manually refresh overrides list"
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
      </div>

      {/* Status Summary */}
      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : overridesData ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Card>
            <CardContent className="pt-6">
              <p className="text-2xl font-semibold text-green-600">
                {overridesData.active_count}
              </p>
              <p className="text-sm text-muted-foreground">Active</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-2xl font-semibold text-yellow-600">
                {overridesData.pending_count}
              </p>
              <p className="text-sm text-muted-foreground">Pending Approval</p>
            </CardContent>
          </Card>
          <Card>
            <CardContent className="pt-6">
              <p className="text-2xl font-semibold text-muted-foreground">
                {overridesData.revoked_count}
              </p>
              <p className="text-sm text-muted-foreground">Revoked</p>
            </CardContent>
          </Card>
        </div>
      ) : null}

      {/* Overrides List */}
      <div className="space-y-4">
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-40 w-full" />
            ))}
          </div>
        ) : (
          <div className="grid gap-4">
            {overridesData?.overrides.map((override) => (
              <Card
                key={override.override_id}
                className={
                  override.status === "revoked" ? "opacity-60" : ""
                }
              >
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline">
                          {override.subsystem_id}
                        </Badge>
                        <Badge
                          variant={
                            override.status === "approved"
                              ? "default"
                              : override.status === "pending"
                                ? "secondary"
                                : "destructive"
                          }
                        >
                          {override.status.charAt(0).toUpperCase() +
                            override.status.slice(1)}
                        </Badge>
                      </div>
                    </div>
                    <div className="flex gap-1">
                      {override.status === "approved" ? (
                        <LockOpen className="h-5 w-5 text-blue-600" />
                      ) : (
                        <Lock className="h-5 w-5 text-muted-foreground" />
                      )}
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="rounded-md bg-muted p-2">
                    <p className="text-sm">
                      {showReason[override.override_id] ? override.reason : "•••"}
                    </p>
                  </div>

                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <div>
                      <p className="text-xs text-muted-foreground">Requested By</p>
                      <p className="text-sm font-mono">{override.requested_by}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">TTL</p>
                      <p className="text-sm font-semibold">
                        {Math.floor(override.ttl_seconds / 60)}m
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Expires</p>
                      <p className="text-sm">
                        {calculateRemainingTime(override.expires_at)}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Constraint</p>
                      <p className="text-sm font-mono">
                        {override.constraint.substring(0, 20)}...
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2 border-t pt-3">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        setShowReason({
                          ...showReason,
                          [override.override_id]: !showReason[override.override_id],
                        })
                      }
                      aria-label="Toggle reason visibility"
                    >
                      {showReason[override.override_id] ? (
                        <>
                          <EyeOff className="mr-2 h-4 w-4" />
                          Hide Reason
                        </>
                      ) : (
                        <>
                          <Eye className="mr-2 h-4 w-4" />
                          Show Reason
                        </>
                      )}
                    </Button>

                    {override.status === "pending" && (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => approveMutation.mutate(override.override_id)}
                          disabled={approveMutation.isPending}
                          aria-label="Approve override"
                        >
                          <CheckCircle2 className="mr-2 h-4 w-4" />
                          Approve
                        </Button>
                      </>
                    )}

                    {(override.status === "approved" ||
                      override.status === "pending") && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => revokeMutation.mutate(override.override_id)}
                        disabled={revokeMutation.isPending}
                        aria-label="Revoke override"
                      >
                        <Trash2 className="mr-2 h-4 w-4" />
                        Revoke
                      </Button>
                    )}

                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => copyToClipboard(override.override_id)}
                      className="ml-auto"
                      aria-label="Copy override ID"
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
            {!overridesData?.overrides ||
              (overridesData.overrides.length === 0 && (
                <Card>
                  <CardContent className="flex items-center gap-3 pt-6">
                    <AlertCircle className="h-5 w-5 text-muted-foreground" />
                    <p className="text-muted-foreground">No overrides found</p>
                  </CardContent>
                </Card>
              ))}
          </div>
        )}
      </div>

      {overridesData && (
        <p className="text-xs text-muted-foreground">
          Last updated: {new Date(overridesData.timestamp).toUTCString()}
        </p>
      )}
    </div>
  );
}
