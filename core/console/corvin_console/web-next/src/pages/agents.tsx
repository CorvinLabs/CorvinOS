import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, Bot, CheckCircle, Clock, PlusCircle, Shield,
  ShieldOff, UserCheck, XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card, CardContent, CardDescription, CardHeader, CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  addAgentSignOff,
  createAgentCharter,
  disableAgent,
  listAgents,
  revokeAgentSignOff,
  type AgentCharter,
  type AgentStatus,
  type CreateAgentCharterRequest,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

// ── Status helpers ─────────────────────────────────────────────────────────────

function statusBadge(status: AgentStatus) {
  switch (status) {
    case "active":
      return <Badge variant="default" className="gap-1"><CheckCircle className="h-3 w-3" />Active</Badge>;
    case "review_pending":
      return <Badge variant="outline" className="gap-1 border-amber-500/60 text-amber-600 dark:text-amber-300"><Clock className="h-3 w-3" />Review Soon</Badge>;
    case "review_overdue":
      return <Badge variant="outline" className="gap-1 border-orange-500/60 text-orange-600 dark:text-orange-300"><AlertTriangle className="h-3 w-3" />Review Overdue</Badge>;
    case "pending_sunset":
      return <Badge variant="danger" className="gap-1"><AlertTriangle className="h-3 w-3" />Pending Sunset</Badge>;
    case "disabled":
      return <Badge variant="danger" className="gap-1"><XCircle className="h-3 w-3" />Disabled</Badge>;
    case "orphan":
      return <Badge variant="outline" className="gap-1 border-red-500/60 text-red-600"><ShieldOff className="h-3 w-3" />Orphan</Badge>;
    default:
      return <Badge variant="outline">{status}</Badge>;
  }
}

function scopeLabel(scope: string) {
  switch (scope) {
    case "project":     return "Project";
    case "user":        return "User";
    case "tenant_wide": return "Tenant-Wide";
    default:            return scope;
  }
}

// ── Charter create modal ───────────────────────────────────────────────────────

const INITIAL_FORM: CreateAgentCharterRequest = {
  agent_id: "",
  name: "",
  kind: "forge_tool",
  scope: "project",
  problem: "",
  success_metric: "",
  baseline: 0,
  target: 0,
  unit: "",
  it_owner: "",
  business_owner: "",
  compliance_owner: "",
  review_date: "",
  sunset_date: "",
  data_class: "INTERNAL",
  egress_zone: "eu_cloud",
  engine_allowlist: [],
};

function CreateCharterDialog({
  open,
  onClose,
  csrf,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  csrf: string;
  onCreated: () => void;
}) {
  const [form, setForm] = React.useState<CreateAgentCharterRequest>(INITIAL_FORM);
  const [errors, setErrors] = React.useState<string[]>([]);
  const [busy, setBusy] = React.useState(false);

  const set = (key: keyof CreateAgentCharterRequest, value: unknown) =>
    setForm((f) => ({ ...f, [key]: value }));

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrors([]);
    setBusy(true);
    try {
      await createAgentCharter(form, csrf);
      onCreated();
      onClose();
      setForm(INITIAL_FORM);
    } catch (err: unknown) {
      const detail = (err as { detail?: { errors?: string[] } })?.detail;
      setErrors(detail?.errors ?? [(err as Error).message]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>New Agent Charter</DialogTitle>
          <DialogDescription>
            Required for any agent promoted beyond session scope. All fields are mandatory.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          <div className="grid grid-cols-2 gap-3">
            <FormField label="Agent ID" hint="kind:scope:name">
              <Input
                value={form.agent_id}
                onChange={(e) => set("agent_id", e.target.value)}
                placeholder="forge_tool:project:lead-scoring"
                required
              />
            </FormField>
            <FormField label="Display Name">
              <Input
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="Lead Scoring Agent"
                required
              />
            </FormField>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <FormField label="Kind">
              <select
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                value={form.kind}
                onChange={(e) => set("kind", e.target.value)}
              >
                <option value="forge_tool">Forge Tool</option>
                <option value="skill">Skill</option>
              </select>
            </FormField>
            <FormField label="Scope">
              <select
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                value={form.scope}
                onChange={(e) => set("scope", e.target.value)}
              >
                <option value="project">Project</option>
                <option value="user">User</option>
                <option value="tenant_wide">Tenant-Wide</option>
              </select>
            </FormField>
          </div>

          <FormField label="Problem Statement">
            <Input
              value={form.problem}
              onChange={(e) => set("problem", e.target.value)}
              placeholder="Manual lead qualification: 12 h/week per sales rep"
              required
            />
          </FormField>

          <FormField label="Success Metric">
            <Input
              value={form.success_metric}
              onChange={(e) => set("success_metric", e.target.value)}
              placeholder="Lead qualification time ≤ 7 h/week"
              required
            />
          </FormField>

          <div className="grid grid-cols-3 gap-3">
            <FormField label="Baseline">
              <Input type="number" step="any"
                value={form.baseline}
                onChange={(e) => set("baseline", parseFloat(e.target.value) || 0)}
                required
              />
            </FormField>
            <FormField label="Target">
              <Input type="number" step="any"
                value={form.target}
                onChange={(e) => set("target", parseFloat(e.target.value) || 0)}
                required
              />
            </FormField>
            <FormField label="Unit">
              <Input
                value={form.unit}
                onChange={(e) => set("unit", e.target.value)}
                placeholder="h/week"
                required
              />
            </FormField>
          </div>

          <div className="grid grid-cols-3 gap-3">
            <FormField label="IT Owner">
              <Input
                value={form.it_owner}
                onChange={(e) => set("it_owner", e.target.value)}
                placeholder="admin:alice"
                required
              />
            </FormField>
            <FormField label="Business Owner">
              <Input
                value={form.business_owner}
                onChange={(e) => set("business_owner", e.target.value)}
                placeholder="member:bob"
                required
              />
            </FormField>
            <FormField label="Compliance Owner">
              <Input
                value={form.compliance_owner}
                onChange={(e) => set("compliance_owner", e.target.value)}
                placeholder="admin:carol"
                required
              />
            </FormField>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <FormField label="Review Date">
              <Input
                type="date"
                value={form.review_date}
                onChange={(e) => set("review_date", e.target.value)}
                required
              />
            </FormField>
            <FormField label="Sunset Date">
              <Input
                type="date"
                value={form.sunset_date}
                onChange={(e) => set("sunset_date", e.target.value)}
                required
              />
            </FormField>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <FormField label="Data Class">
              <select
                className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
                value={form.data_class}
                onChange={(e) => set("data_class", e.target.value)}
              >
                <option value="PUBLIC">PUBLIC</option>
                <option value="INTERNAL">INTERNAL</option>
                <option value="CONFIDENTIAL">CONFIDENTIAL</option>
                <option value="SECRET">SECRET</option>
              </select>
            </FormField>
            <FormField label="Egress Zone">
              <Input
                value={form.egress_zone}
                onChange={(e) => set("egress_zone", e.target.value)}
                placeholder="eu_cloud"
                required
              />
            </FormField>
          </div>

          <FormField label="Engine Allowlist (comma-separated)">
            <Input
              value={(form.engine_allowlist ?? []).join(", ")}
              onChange={(e) =>
                set("engine_allowlist", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
              }
              placeholder="claude_code, hermes"
            />
          </FormField>

          {errors.length > 0 && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
              {errors.map((e, i) => <div key={i}>{e}</div>)}
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy}>
              {busy ? "Creating…" : "Create Charter"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function FormField({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-xs font-medium text-muted-foreground">
        {label}{hint ? <span className="ml-1 opacity-60">({hint})</span> : null}
      </Label>
      {children}
    </div>
  );
}

// ── Sign-off modal ─────────────────────────────────────────────────────────────

function SignOffDialog({
  charter,
  open,
  onClose,
  csrf,
  onDone,
}: {
  charter: AgentCharter;
  open: boolean;
  onClose: () => void;
  csrf: string;
  onDone: () => void;
}) {
  const availableRoles = React.useMemo(() => {
    const alreadySigned = charter.sign_offs.map((s) => s.role);
    return (["it", "business", "compliance"] as const).filter(
      (r) => !alreadySigned.includes(r)
    );
  }, [charter.sign_offs]);

  const [role, setRole] = React.useState<"it" | "business" | "compliance">(
    availableRoles[0] ?? "it"
  );
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  // Reset role to first available when dialog opens or available roles change
  React.useEffect(() => {
    if (open && availableRoles.length > 0) {
      setRole(availableRoles[0]);
    }
  }, [open, availableRoles]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await addAgentSignOff(charter.agent_id, { scope_target: charter.scope, role }, csrf);
      onDone();
      onClose();
    } catch (ex: unknown) {
      setErr((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add Sign-Off — {charter.name}</DialogTitle>
          <DialogDescription>
            Required roles for <strong>{scopeLabel(charter.scope)}</strong> scope:{" "}
            {charter.required_roles.join(", ")}
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 py-2">
          <FormField label="Role">
            <select
              className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm"
              value={role}
              onChange={(e) => setRole(e.target.value as typeof role)}
            >
              {availableRoles.length === 0 ? (
                <option value="" disabled>All roles already signed</option>
              ) : availableRoles.map((r) => (
                <option key={r} value={r}>{r.toUpperCase()} ({
                  r === "it" ? charter.it_owner
                  : r === "business" ? charter.business_owner
                  : charter.compliance_owner
                })</option>
              ))}
            </select>
          </FormField>
          {err && <p className="text-sm text-destructive">{err}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
            <Button type="submit" disabled={busy || availableRoles.length === 0}>
              {busy ? "Signing…" : "Add Sign-Off"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ── Agent row ──────────────────────────────────────────────────────────────────

function AgentRow({
  charter,
  csrf,
  onRefresh,
}: {
  charter: AgentCharter;
  csrf: string;
  onRefresh: () => void;
}) {
  const [signOpen, setSignOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [toast, setToast] = React.useState("");

  const handleRevoke = async (role: string) => {
    if (!confirm(`Revoke ${role.toUpperCase()} sign-off for ${charter.name}?`)) return;
    setBusy(true);
    try {
      await revokeAgentSignOff(charter.agent_id, role, csrf);
      onRefresh();
    } catch (ex: unknown) {
      setToast((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleDisable = async () => {
    if (!confirm(`Hard-disable agent "${charter.name}"? This cannot be undone without a new charter.`)) return;
    setBusy(true);
    try {
      await disableAgent(charter.agent_id, csrf);
      onRefresh();
    } catch (ex: unknown) {
      setToast((ex as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="rounded-lg border border-border/60 bg-card/40 p-4 space-y-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <Bot className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="font-medium">{charter.name}</span>
              <Badge variant="outline" className="font-mono text-[10px]">{charter.kind}</Badge>
              <Badge variant="outline" className="text-[10px]">{scopeLabel(charter.scope)}</Badge>
              {statusBadge(charter.status)}
            </div>
            <p className="mt-1 text-xs text-muted-foreground font-mono">{charter.agent_id}</p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {!charter.disabled && (
              <>
                <Button size="sm" variant="outline" onClick={() => setSignOpen(true)} disabled={busy}>
                  <UserCheck className="h-3 w-3 mr-1" />Sign
                </Button>
                <Button size="sm" variant="ghost" className="text-destructive hover:text-destructive"
                  onClick={handleDisable} disabled={busy}>
                  <ShieldOff className="h-3 w-3 mr-1" />Disable
                </Button>
              </>
            )}
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-4">
          <div>
            <span className="font-medium text-foreground">IT:</span> {charter.it_owner}
          </div>
          <div>
            <span className="font-medium text-foreground">Business:</span> {charter.business_owner}
          </div>
          <div>
            <span className="font-medium text-foreground">Review:</span>{" "}
            {charter.review_date}
            {charter.days_to_review < 30 && charter.days_to_review >= 0 && (
              <span className="ml-1 text-amber-600 dark:text-amber-400">({charter.days_to_review}d)</span>
            )}
          </div>
          <div>
            <span className="font-medium text-foreground">Sunset:</span>{" "}
            {charter.sunset_date}
          </div>
        </div>

        {charter.sign_offs.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {charter.sign_offs.map((s) => (
              <div key={s.role}
                className="flex items-center gap-1 rounded-full border border-emerald-500/40 bg-emerald-500/5 px-2.5 py-0.5 text-xs text-emerald-700 dark:text-emerald-300">
                <Shield className="h-3 w-3" />
                {s.role.toUpperCase()} · {s.signer}
                <button
                  onClick={() => handleRevoke(s.role)}
                  className="ml-0.5 opacity-50 hover:opacity-100"
                  title={`Revoke ${s.role} sign-off`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}

        {charter.required_roles.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {charter.required_roles
              .filter((r) => !charter.sign_offs.find((s) => s.role === r))
              .map((r) => (
                <div key={r}
                  className="flex items-center gap-1 rounded-full border border-border/60 px-2.5 py-0.5 text-xs text-muted-foreground">
                  <Shield className="h-3 w-3" />
                  {r.toUpperCase()} pending
                </div>
              ))}
          </div>
        )}

        {toast && (
          <p className="text-xs text-destructive">{toast}</p>
        )}
      </div>

      <SignOffDialog
        charter={charter}
        open={signOpen}
        onClose={() => setSignOpen(false)}
        csrf={csrf}
        onDone={onRefresh}
      />
    </>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export function AgentsPage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["agents"],
    queryFn: ({ signal }) => listAgents(signal),
    refetchInterval: 60_000,
  });
  const [createOpen, setCreateOpen] = React.useState(false);
  const [toast, setToast] = React.useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  const refresh = React.useCallback(async () => {
    await qc.invalidateQueries({ queryKey: ["agents"] });
    setToast({ kind: "ok", msg: "Updated." });
    setTimeout(() => setToast(null), 2500);
  }, [qc]);

  if (q.isLoading || (q.isError && q.isFetching)) {
    return (
      <div className="mx-auto max-w-5xl space-y-4">
        <Skeleton className="h-10 w-1/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (q.isError || !q.data) {
    return (
      <Card className="border-destructive/40 bg-destructive/5">
        <CardContent className="py-4 text-sm text-destructive">
          Agent list failed to load: {(q.error as Error | undefined)?.message ?? "unknown"}
        </CardContent>
      </Card>
    );
  }

  const governed = q.data.filter((a) => !a.disabled);
  const disabled = q.data.filter((a) => a.disabled);
  const critical = q.data.filter(
    (a) =>
      a.status === "pending_sunset" ||
      a.status === "review_overdue" ||
      a.status === "orphan"
  );
  const csrf = session?.csrf_token ?? "";

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Agent Governance</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Charter-based lifecycle governance. Every agent beyond session scope requires
            a valid charter and stakeholder sign-offs.
          </p>
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <PlusCircle className="h-4 w-4 mr-2" />
          New Charter
        </Button>
      </div>

      {critical.length > 0 && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex items-start gap-3 py-3">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <div className="text-sm">
              <span className="font-medium text-destructive">{critical.length} agent(s) need attention</span>
              <span className="text-muted-foreground">
                {" "}— review overdue, pending sunset, or ownership gap.
              </span>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Governed Agents ({governed.length})
          </CardTitle>
          <CardDescription>
            Agents with a charter at project scope or above. Session-scope sandbox agents are excluded.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {governed.length === 0 ? (
            <p className="text-sm text-muted-foreground py-2">
              No agents yet. Agents are autonomous workers governed by a charter. Create a charter
              to promote your first agent beyond session scope.
            </p>
          ) : (
            governed.map((a) => (
              <AgentRow key={a.agent_id} charter={a} csrf={csrf} onRefresh={refresh} />
            ))
          )}
        </CardContent>
      </Card>

      {disabled.length > 0 && (
        <Card className="border-border/40">
          <CardHeader>
            <CardTitle className="text-base text-muted-foreground">
              Disabled Agents ({disabled.length})
            </CardTitle>
            <CardDescription>
              Sunset or manually disabled. Charters retained for audit trail.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {disabled.map((a) => (
              <div key={a.agent_id}
                className="flex items-center justify-between rounded-md border border-border/40 bg-muted/20 px-3 py-2 text-sm opacity-60">
                <div className="flex items-center gap-2">
                  <XCircle className="h-3.5 w-3.5" />
                  <span className="font-mono">{a.agent_id}</span>
                </div>
                <span className="text-xs">{a.name}</span>
              </div>
            ))}
          </CardContent>
        </Card>
      )}

      <CreateCharterDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        csrf={csrf}
        onCreated={refresh}
      />

      {toast && (
        <div
          className={
            toast.kind === "ok"
              ? "fixed bottom-6 right-6 z-50 rounded-md border border-emerald-500/30 bg-emerald-500/10 px-4 py-2 text-sm text-emerald-700 shadow-lg dark:text-emerald-300"
              : "fixed bottom-6 right-6 z-50 rounded-md border border-destructive/30 bg-destructive/10 px-4 py-2 text-sm text-destructive shadow-lg"
          }
          onClick={() => setToast(null)}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}
