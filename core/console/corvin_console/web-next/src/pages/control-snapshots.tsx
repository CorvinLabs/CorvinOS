/**
 * Control Plane — Snapshots Manager Panel (ADR-2029 Stream 4)
 * Create, list, restore, and validate state snapshots with integrity checking
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Trash2,
  RotateCcw,
  Copy,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Clock,
  FileText,
  BarChart3,
  Zap,
  Download,
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

const API_BASE = "/v1/console/control-plane/snapshots";

interface Snapshot {
  snapshot_id: string;
  name: string;
  description: string;
  created_by: string;
  created_at: string;
  size_bytes: number;
  state_hash: string;
  integrity_status: "valid" | "warning" | "corrupted";
  tags: string[];
  state_summary: {
    subsystems_count: number;
    plugins_enabled: number;
    config_entries: number;
    events_captured: number;
  };
}

interface SnapshotsData {
  timestamp: string;
  total_snapshots: number;
  total_size_mb: number;
  snapshots: Snapshot[];
}

export default function ControlSnapshotsPage() {
  const queryClient = useQueryClient();
  const [newDialogOpen, setNewDialogOpen] = React.useState(false);
  const [compareDialogOpen, setCompareDialogOpen] = React.useState(false);
  const [autoRefresh, setAutoRefresh] = React.useState(true);
  const [selectedSnapshot, setSelectedSnapshot] = React.useState<Snapshot | null>(null);
  const [newSnapshot, setNewSnapshot] = React.useState({
    name: "",
    description: "",
    tags: "",
  });

  // Fetch snapshots
  const { data: snapshotsData, isLoading, error } = useQuery<SnapshotsData>({
    queryKey: ["control-snapshots"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}`);
      if (!res.ok) throw new Error("Failed to load snapshots");
      return res.json();
    },
    refetchInterval: autoRefresh ? 8000 : false,
    staleTime: 1000,
  });

  // Create snapshot
  const createMutation = useMutation({
    mutationFn: async (data: typeof newSnapshot) => {
      const res = await fetch(`${API_BASE}/create`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...data,
          tags: data.tags.split(",").map((t) => t.trim()),
        }),
      });
      if (!res.ok) throw new Error("Failed to create snapshot");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-snapshots"] });
      setNewDialogOpen(false);
      setNewSnapshot({ name: "", description: "", tags: "" });
      toast({ title: "Snapshot created successfully" });
    },
    onError: () => {
      toast({ title: "Failed to create snapshot", variant: "destructive" });
    },
  });

  // Restore snapshot
  const restoreMutation = useMutation({
    mutationFn: async (snapshotId: string) => {
      const res = await fetch(`${API_BASE}/${snapshotId}/restore`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("Failed to restore snapshot");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-snapshots"] });
      toast({ title: "Snapshot restored successfully" });
    },
    onError: () => {
      toast({ title: "Failed to restore snapshot", variant: "destructive" });
    },
  });

  // Verify snapshot integrity
  const verifyMutation = useMutation({
    mutationFn: async (snapshotId: string) => {
      const res = await fetch(`${API_BASE}/${snapshotId}/verify`, {
        method: "POST",
      });
      if (!res.ok) throw new Error("Verification failed");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-snapshots"] });
      toast({ title: "Snapshot integrity verified" });
    },
    onError: () => {
      toast({ title: "Verification failed", variant: "destructive" });
    },
  });

  // Delete snapshot
  const deleteMutation = useMutation({
    mutationFn: async (snapshotId: string) => {
      const res = await fetch(`${API_BASE}/${snapshotId}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error("Failed to delete snapshot");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-snapshots"] });
      toast({ title: "Snapshot deleted" });
    },
    onError: () => {
      toast({ title: "Failed to delete snapshot", variant: "destructive" });
    },
  });

  // Manual refresh
  const refreshMutation = useMutation({
    mutationFn: async () => {
      await queryClient.invalidateQueries({ queryKey: ["control-snapshots"] });
    },
  });

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    toast({ title: "Copied to clipboard" });
  };

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return "0 B";
    const k = 1024;
    const sizes = ["B", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return (bytes / Math.pow(k, i)).toFixed(2) + " " + sizes[i];
  };

  if (error) {
    return (
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <header>
          <h1 className="font-serif text-3xl font-light tracking-tight">Snapshots</h1>
        </header>
        <Card className="border-destructive">
          <CardContent className="flex items-center gap-3 pt-6">
            <AlertCircle className="h-5 w-5 text-destructive" />
            <div>
              <p className="font-medium">Failed to load snapshots</p>
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
        <h1 className="font-serif text-3xl font-light tracking-tight">Snapshots Manager</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Create, restore, and manage state snapshots with integrity validation
        </p>
      </header>

      {/* Control Bar */}
      <div className="flex flex-wrap items-center gap-2">
        <Dialog open={newDialogOpen} onOpenChange={setNewDialogOpen}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Create Snapshot
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create New Snapshot</DialogTitle>
              <DialogDescription>
                Capture the current system state for rollback capability
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div>
                <label className="text-sm font-medium">Name</label>
                <Input
                  placeholder="e.g., Pre-deployment-2026-09-22"
                  value={newSnapshot.name}
                  onChange={(e) =>
                    setNewSnapshot({ ...newSnapshot, name: e.target.value })
                  }
                  className="mt-1"
                  aria-label="Snapshot name"
                />
              </div>
              <div>
                <label className="text-sm font-medium">Description</label>
                <textarea
                  className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  placeholder="Describe the system state being captured"
                  value={newSnapshot.description}
                  onChange={(e) =>
                    setNewSnapshot({ ...newSnapshot, description: e.target.value })
                  }
                  rows={3}
                  aria-label="Snapshot description"
                />
              </div>
              <div>
                <label className="text-sm font-medium">Tags (comma-separated)</label>
                <Input
                  placeholder="e.g., production, pre-release, testing"
                  value={newSnapshot.tags}
                  onChange={(e) =>
                    setNewSnapshot({ ...newSnapshot, tags: e.target.value })
                  }
                  className="mt-1"
                  aria-label="Snapshot tags"
                />
              </div>
              <Button
                onClick={() => createMutation.mutate(newSnapshot)}
                disabled={createMutation.isPending || !newSnapshot.name}
                className="w-full"
              >
                {createMutation.isPending ? "Creating..." : "Create Snapshot"}
              </Button>
            </div>
          </DialogContent>
        </Dialog>

        <Button
          variant="outline"
          size="sm"
          onClick={() => refreshMutation.mutate()}
          disabled={refreshMutation.isPending}
          aria-label="Manually refresh snapshots list"
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

      {/* Storage Summary */}
      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : snapshotsData ? (
        <Card>
          <CardContent className="pt-6">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div>
                <p className="text-sm text-muted-foreground">Total Snapshots</p>
                <p className="text-2xl font-semibold">{snapshotsData.total_snapshots}</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Storage Used</p>
                <p className="text-2xl font-semibold">{snapshotsData.total_size_mb.toFixed(1)} MB</p>
              </div>
              <div>
                <p className="text-sm text-muted-foreground">Avg Size</p>
                <p className="text-2xl font-semibold">
                  {snapshotsData.total_snapshots > 0
                    ? formatBytes(
                        (snapshotsData.total_size_mb * 1024 * 1024) /
                          snapshotsData.total_snapshots,
                      )
                    : "N/A"}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      ) : null}

      {/* Snapshots List */}
      <div className="space-y-4">
        <h2 className="font-semibold">Available Snapshots</h2>
        {isLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-48 w-full" />
            ))}
          </div>
        ) : (
          <div className="grid gap-4">
            {snapshotsData?.snapshots.map((snapshot) => (
              <Card key={snapshot.snapshot_id}>
                <CardHeader className="pb-3">
                  <div className="flex items-start justify-between">
                    <div className="flex-1">
                      <CardTitle>{snapshot.name}</CardTitle>
                      <CardDescription>{snapshot.description}</CardDescription>
                    </div>
                    <Badge
                      variant={
                        snapshot.integrity_status === "valid"
                          ? "default"
                          : snapshot.integrity_status === "warning"
                            ? "secondary"
                            : "destructive"
                      }
                    >
                      {snapshot.integrity_status === "valid" ? (
                        <>
                          <CheckCircle2 className="mr-1 h-3 w-3" />
                          Valid
                        </>
                      ) : snapshot.integrity_status === "warning" ? (
                        <>
                          <AlertCircle className="mr-1 h-3 w-3" />
                          Warning
                        </>
                      ) : (
                        <>
                          <AlertCircle className="mr-1 h-3 w-3" />
                          Corrupted
                        </>
                      )}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <div>
                      <p className="text-xs text-muted-foreground">Created By</p>
                      <p className="text-sm font-mono">{snapshot.created_by}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Size</p>
                      <p className="text-sm font-semibold">{formatBytes(snapshot.size_bytes)}</p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Created</p>
                      <p className="text-sm">
                        {new Date(snapshot.created_at).toLocaleDateString("en-US")}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-muted-foreground">Hash</p>
                      <p className="font-mono text-xs">{snapshot.state_hash.substring(0, 12)}...</p>
                    </div>
                  </div>

                  {/* State Summary */}
                  <div className="rounded-md bg-muted p-3">
                    <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                      <div>
                        <p className="text-muted-foreground">Subsystems</p>
                        <p className="font-semibold">
                          {snapshot.state_summary.subsystems_count}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Plugins</p>
                        <p className="font-semibold">
                          {snapshot.state_summary.plugins_enabled}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Config</p>
                        <p className="font-semibold">
                          {snapshot.state_summary.config_entries}
                        </p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Events</p>
                        <p className="font-semibold">
                          {snapshot.state_summary.events_captured}
                        </p>
                      </div>
                    </div>
                  </div>

                  {/* Tags */}
                  {snapshot.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {snapshot.tags.map((tag) => (
                        <Badge key={tag} variant="outline" className="text-xs">
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  )}

                  {/* Actions */}
                  <div className="flex flex-wrap gap-2 border-t pt-3">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => restoreMutation.mutate(snapshot.snapshot_id)}
                      disabled={
                        restoreMutation.isPending ||
                        snapshot.integrity_status === "corrupted"
                      }
                      aria-label="Restore snapshot"
                    >
                      <RotateCcw className="mr-2 h-4 w-4" />
                      Restore
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => verifyMutation.mutate(snapshot.snapshot_id)}
                      disabled={verifyMutation.isPending}
                      aria-label="Verify snapshot integrity"
                    >
                      <CheckCircle2 className="mr-2 h-4 w-4" />
                      Verify
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => copyToClipboard(snapshot.snapshot_id)}
                      aria-label="Copy snapshot ID"
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => deleteMutation.mutate(snapshot.snapshot_id)}
                      disabled={deleteMutation.isPending}
                      aria-label="Delete snapshot"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
            {!snapshotsData?.snapshots ||
              (snapshotsData.snapshots.length === 0 && (
                <Card>
                  <CardContent className="flex items-center gap-3 pt-6">
                    <FileText className="h-5 w-5 text-muted-foreground" />
                    <p className="text-muted-foreground">No snapshots created yet</p>
                  </CardContent>
                </Card>
              ))}
          </div>
        )}
      </div>

      {snapshotsData && (
        <p className="text-xs text-muted-foreground">
          Last updated: {new Date(snapshotsData.timestamp).toUTCString()}
        </p>
      )}
    </div>
  );
}
