/**
 * Control Plane — Snapshots & Rollback Panel (ADR-2029 Stream 4)
 * Create, restore, compare, delete snapshots of Control Plane state
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  RotateCcw,
  Trash2,
  Download,
  AlertCircle,
  CheckCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/hooks/use-toast";

const API_BASE = "/v1/console/control-plane/snapshots";

interface Snapshot {
  snapshot_id: string;
  timestamp: string;
  name: string;
  description: string;
  checksum: string;
  size_bytes: number;
  created_by: string;
}

export default function ControlPlaneSnapshotsPage() {
  const queryClient = useQueryClient();
  const [createDialogOpen, setCreateDialogOpen] = React.useState(false);
  const [selectedSnapshot, setSelectedSnapshot] = React.useState<Snapshot | null>(null);
  const [restoreConfirm, setRestoreConfirm] = React.useState(false);
  const [newSnapshot, setNewSnapshot] = React.useState({
    name: "",
    description: "",
  });

  // Fetch snapshots
  const { data: snapshots, isLoading } = useQuery<Snapshot[]>({
    queryKey: ["control-plane-snapshots"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}`);
      if (!res.ok) throw new Error("Failed to load snapshots");
      return res.json();
    },
  });

  // Create snapshot
  const createMutation = useMutation({
    mutationFn: async (snapshot: typeof newSnapshot) => {
      const res = await fetch(`${API_BASE}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(snapshot),
      });
      if (!res.ok) throw new Error("Failed to create snapshot");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-plane-snapshots"] });
      setCreateDialogOpen(false);
      setNewSnapshot({ name: "", description: "" });
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
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      });
      if (!res.ok) throw new Error("Failed to restore snapshot");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["control-plane-snapshots"] });
      setSelectedSnapshot(null);
      setRestoreConfirm(false);
      toast({ title: "Snapshot restored successfully" });
    },
    onError: () => {
      toast({ title: "Failed to restore snapshot", variant: "destructive" });
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
      queryClient.invalidateQueries({ queryKey: ["control-plane-snapshots"] });
      setSelectedSnapshot(null);
      toast({ title: "Snapshot deleted successfully" });
    },
    onError: () => {
      toast({ title: "Failed to delete snapshot", variant: "destructive" });
    },
  });

  const formatBytes = (bytes: number) => {
    const kb = bytes / 1024;
    if (kb < 1024) return `${kb.toFixed(2)} KB`;
    const mb = kb / 1024;
    return `${mb.toFixed(2)} MB`;
  };

  const getTimeAgo = (timestamp: string) => {
    const date = new Date(timestamp);
    const now = new Date();
    const seconds = Math.floor((now.getTime() - date.getTime()) / 1000);

    if (seconds < 60) return "Just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
  };

  return (
    <div className="container mx-auto p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Snapshots</h1>
          <p className="mt-1 text-sm text-gray-600">
            Save and restore Control Plane state (atomic, immutable)
          </p>
        </div>
        <Button onClick={() => setCreateDialogOpen(true)}>
          <Plus className="mr-2 h-4 w-4" /> Create Snapshot
        </Button>
      </div>

      {/* Snapshots List */}
      <div className="space-y-3">
        {isLoading ? (
          <>
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
            <Skeleton className="h-20" />
          </>
        ) : (snapshots || []).length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center">
            <p className="text-gray-600">No snapshots yet</p>
          </div>
        ) : (
          (snapshots || []).map((snapshot) => (
            <div
              key={snapshot.snapshot_id}
              className="rounded-lg border p-4 shadow-sm"
            >
              <div className="mb-3 flex items-start justify-between">
                <div className="flex-1">
                  <h3 className="font-semibold">{snapshot.name}</h3>
                  <p className="mt-1 text-sm text-gray-600">
                    {snapshot.description}
                  </p>
                </div>
                <div className="text-right">
                  <Badge variant="outline">{getTimeAgo(snapshot.timestamp)}</Badge>
                </div>
              </div>

              <div className="mb-3 flex gap-4 text-xs text-gray-600">
                <div>
                  <strong>ID:</strong>{" "}
                  <code className="text-xs">{snapshot.snapshot_id}</code>
                </div>
                <div>
                  <strong>Size:</strong> {formatBytes(snapshot.size_bytes)}
                </div>
                <div>
                  <strong>By:</strong> {snapshot.created_by}
                </div>
              </div>

              {/* Checksum */}
              <div className="mb-3 rounded-md bg-gray-50 p-2 font-mono text-xs text-gray-700">
                SHA256: {snapshot.checksum.substring(0, 32)}...
              </div>

              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={() => {
                    setSelectedSnapshot(snapshot);
                    setRestoreConfirm(false);
                  }}
                  className="flex-1 bg-blue-600 hover:bg-blue-700"
                >
                  <RotateCcw className="mr-2 h-4 w-4" /> Restore
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    // In Phase 9b.4, diff would show state changes
                    toast({
                      title: "Diff feature coming in Phase 9c",
                    });
                  }}
                  className="flex-1"
                >
                  <AlertCircle className="mr-2 h-4 w-4" /> Compare
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() =>
                    deleteMutation.mutate(snapshot.snapshot_id)
                  }
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Create Dialog */}
      <Dialog open={createDialogOpen} onOpenChange={setCreateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create Snapshot</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <div>
              <label className="text-sm font-medium">Snapshot Name</label>
              <Input
                placeholder="e.g., Before major deployment"
                value={newSnapshot.name}
                onChange={(e) =>
                  setNewSnapshot({ ...newSnapshot, name: e.target.value })
                }
              />
            </div>
            <div>
              <label className="text-sm font-medium">Description</label>
              <Textarea
                placeholder="What prompted this snapshot..."
                value={newSnapshot.description}
                onChange={(e) =>
                  setNewSnapshot({
                    ...newSnapshot,
                    description: e.target.value,
                  })
                }
                rows={3}
              />
            </div>
            <div className="flex gap-2">
              <Button
                onClick={() => createMutation.mutate(newSnapshot)}
                disabled={
                  createMutation.isPending ||
                  !newSnapshot.name ||
                  !newSnapshot.description
                }
              >
                Create
              </Button>
              <Button
                variant="outline"
                onClick={() => setCreateDialogOpen(false)}
              >
                Cancel
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Restore Confirmation Dialog */}
      {selectedSnapshot && (
        <Dialog
          open={!!selectedSnapshot}
          onOpenChange={() => {
            setSelectedSnapshot(null);
            setRestoreConfirm(false);
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Restore Snapshot</DialogTitle>
            </DialogHeader>
            <div className="space-y-4">
              <div className="rounded-md bg-yellow-50 p-3">
                <p className="text-sm text-yellow-900">
                  <AlertCircle className="mb-2 h-4 w-4" />
                  <strong>Warning:</strong> Restoring will rollback all Control
                  Plane state to the snapshot time. This is atomic and
                  immutable.
                </p>
              </div>

              <div className="rounded-md bg-gray-50 p-3">
                <p className="text-sm">
                  <strong>Snapshot:</strong> {selectedSnapshot.name}
                </p>
                <p className="text-sm">
                  <strong>Created:</strong>{" "}
                  {new Date(selectedSnapshot.timestamp).toLocaleString()}
                </p>
                <p className="text-sm">
                  <strong>Size:</strong>{" "}
                  {formatBytes(selectedSnapshot.size_bytes)}
                </p>
              </div>

              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={restoreConfirm}
                  onChange={(e) => setRestoreConfirm(e.target.checked)}
                  className="h-4 w-4"
                />
                <span className="text-sm">
                  I understand the risks. Proceed with restore.
                </span>
              </label>

              <div className="flex gap-2">
                <Button
                  onClick={() =>
                    restoreMutation.mutate(selectedSnapshot.snapshot_id)
                  }
                  disabled={
                    !restoreConfirm || restoreMutation.isPending
                  }
                  className="flex-1 bg-red-600 hover:bg-red-700"
                >
                  Restore Snapshot
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSelectedSnapshot(null);
                    setRestoreConfirm(false);
                  }}
                  className="flex-1"
                >
                  Cancel
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {/* Atomicity Note */}
      <div className="mt-8 rounded-lg bg-blue-50 p-4 text-sm text-blue-900">
        <CheckCircle className="mb-2 h-4 w-4" />
        <p>
          <strong>Atomicity Guarantee:</strong> All snapshot operations are
          atomic and all-or-nothing. Failed restores roll back automatically and
          audit seam the chain to maintain immutability.
        </p>
      </div>
    </div>
  );
}
