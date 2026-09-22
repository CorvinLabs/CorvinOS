/**
 * Control Plane — Operator Override Authority Panel (ADR-2029 Stream 3)
 * Request, approve, deny pending operations with full audit trail
 */

import * as React from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, X, Clock, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "@/hooks/use-toast";

const API_BASE = "/v1/console/control-plane/approvals";

interface Approval {
  override_id: string;
  override_type: string;
  target_id: string;
  reason: string;
  requestor_id: string;
  approval_status: "pending" | "approved" | "rejected" | "expired";
  created_at: string;
  approved_at?: string;
  approver_id?: string;
}

export default function ControlPlaneOverridesPage() {
  const queryClient = useQueryClient();
  const [selectedApproval, setSelectedApproval] = React.useState<Approval | null>(null);
  const [decision, setDecision] = React.useState<"approve" | "deny" | null>(null);
  const [reason, setReason] = React.useState("");

  // Fetch pending approvals
  const { data: approvals, isLoading } = useQuery<Approval[]>({
    queryKey: ["control-plane-approvals"],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}?status_filter=pending`);
      if (!res.ok) throw new Error("Failed to load approvals");
      return res.json();
    },
    refetchInterval: 5000, // Refresh every 5s
  });

  // Approve override
  const approveMutation = useMutation({
    mutationFn: async ({
      overrideId,
      reason,
    }: {
      overrideId: string;
      reason: string;
    }) => {
      const res = await fetch(`${API_BASE}/${overrideId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) throw new Error("Failed to approve override");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["control-plane-approvals"],
      });
      setSelectedApproval(null);
      setReason("");
      setDecision(null);
      toast({ title: "Override approved successfully" });
    },
    onError: () => {
      toast({ title: "Failed to approve override", variant: "destructive" });
    },
  });

  // Deny override
  const denyMutation = useMutation({
    mutationFn: async ({
      overrideId,
      reason,
    }: {
      overrideId: string;
      reason: string;
    }) => {
      const res = await fetch(`${API_BASE}/${overrideId}/deny`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) throw new Error("Failed to deny override");
      return res.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["control-plane-approvals"],
      });
      setSelectedApproval(null);
      setReason("");
      setDecision(null);
      toast({ title: "Override denied successfully" });
    },
    onError: () => {
      toast({ title: "Failed to deny override", variant: "destructive" });
    },
  });

  const getStatusColor = (status: string) => {
    switch (status) {
      case "pending":
        return "bg-yellow-100 text-yellow-900";
      case "approved":
        return "bg-green-100 text-green-900";
      case "rejected":
        return "bg-red-100 text-red-900";
      case "expired":
        return "bg-gray-100 text-gray-900";
      default:
        return "bg-gray-100";
    }
  };

  const pendingApprovals = (approvals || []).filter(
    (a) => a.approval_status === "pending"
  );

  return (
    <div className="container mx-auto p-6">
      <div className="mb-6">
        <h1 className="text-3xl font-bold">Operator Overrides</h1>
        <p className="mt-1 text-sm text-gray-600">
          Review and approve pending override requests (admin-only)
        </p>
      </div>

      {/* Approval Queue */}
      <div className="space-y-3">
        {isLoading ? (
          <>
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </>
        ) : pendingApprovals.length === 0 ? (
          <div className="rounded-lg border border-dashed p-8 text-center">
            <p className="text-gray-600">No pending approvals</p>
          </div>
        ) : (
          pendingApprovals.map((approval) => (
            <div
              key={approval.override_id}
              className="rounded-lg border p-4 shadow-sm"
            >
              <div className="mb-3 flex items-start justify-between">
                <div className="flex-1">
                  <h3 className="font-semibold">{approval.override_type}</h3>
                  <p className="mt-1 text-sm text-gray-600">
                    Target: <code className="text-xs">{approval.target_id}</code>
                  </p>
                </div>
                <Badge className={getStatusColor(approval.approval_status)}>
                  {approval.approval_status.charAt(0).toUpperCase() +
                    approval.approval_status.slice(1)}
                </Badge>
              </div>

              <div className="mb-3 rounded-md bg-gray-50 p-3">
                <p className="text-sm text-gray-700">
                  <strong>Reason:</strong> {approval.reason}
                </p>
              </div>

              <div className="mb-3 flex gap-4 text-xs text-gray-600">
                <div>
                  <strong>Requested by:</strong> {approval.requestor_id}
                </div>
                <div>
                  <strong>At:</strong> {new Date(approval.created_at).toLocaleString()}
                </div>
              </div>

              <div className="flex gap-2">
                <Button
                  size="sm"
                  className="flex-1 bg-green-600 hover:bg-green-700"
                  onClick={() => {
                    setSelectedApproval(approval);
                    setDecision("approve");
                  }}
                >
                  <Check className="mr-2 h-4 w-4" /> Approve
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  className="flex-1"
                  onClick={() => {
                    setSelectedApproval(approval);
                    setDecision("deny");
                  }}
                >
                  <X className="mr-2 h-4 w-4" /> Deny
                </Button>
              </div>
            </div>
          ))
        )}
      </div>

      {/* Decision Dialog */}
      {selectedApproval && decision && (
        <Dialog
          open={!!selectedApproval}
          onOpenChange={() => {
            setSelectedApproval(null);
            setReason("");
            setDecision(null);
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>
                {decision === "approve"
                  ? "Approve Override"
                  : "Deny Override"}
              </DialogTitle>
            </DialogHeader>

            <div className="space-y-4">
              <div className="rounded-md bg-gray-50 p-3">
                <p className="text-sm">
                  <strong>Type:</strong> {selectedApproval.override_type}
                </p>
                <p className="text-sm">
                  <strong>Target:</strong> {selectedApproval.target_id}
                </p>
                <p className="mt-2 text-sm">
                  <strong>Reason:</strong> {selectedApproval.reason}
                </p>
              </div>

              <div>
                <label className="text-sm font-medium">Decision Reason</label>
                <Textarea
                  placeholder={
                    decision === "approve"
                      ? "Why you're approving this override..."
                      : "Why you're denying this override..."
                  }
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={4}
                />
              </div>

              <div className="flex gap-2">
                <Button
                  onClick={() => {
                    if (decision === "approve") {
                      approveMutation.mutate({
                        overrideId: selectedApproval.override_id,
                        reason,
                      });
                    } else {
                      denyMutation.mutate({
                        overrideId: selectedApproval.override_id,
                        reason,
                      });
                    }
                  }}
                  disabled={
                    !reason.trim() ||
                    approveMutation.isPending ||
                    denyMutation.isPending
                  }
                  className={
                    decision === "approve"
                      ? "bg-green-600 hover:bg-green-700"
                      : ""
                  }
                  variant={decision === "deny" ? "destructive" : "default"}
                >
                  {decision === "approve" ? "Approve" : "Deny"} Override
                </Button>
                <Button
                  variant="outline"
                  onClick={() => {
                    setSelectedApproval(null);
                    setReason("");
                    setDecision(null);
                  }}
                >
                  Cancel
                </Button>
              </div>
            </div>
          </DialogContent>
        </Dialog>
      )}

      {/* Compliance Warning */}
      <div className="mt-8 rounded-lg bg-red-50 p-4 text-sm text-red-900">
        <AlertTriangle className="mb-2 h-4 w-4" />
        <p>
          <strong>Security Notice:</strong> You cannot override audit chain,
          consent gates, or house rules. Approval authority is limited to
          permitted operations only.
        </p>
      </div>
    </div>
  );
}
