import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Building2,
  Check,
  ChevronRight,
  Copy,
  KeyRound,
  Loader2,
  Network,
  Plus,
  Shield,
  Trash2,
  UserMinus,
  UserPlus,
  Users,
  X,
} from "lucide-react";
import {
  addOrgMember,
  affiliateOrgAgent,
  createOrg,
  createOrgGrant,
  deaffiliateOrgAgent,
  dissolveOrg,
  getOrg,
  getOrgNetwork,
  listOrgs,
  removeOrgMember,
  revokeOrgGrant,
  updateOrgMemberRole,
  type Grant,
  type OrgEndorsement,
  type OrgMember,
  type OrgNetworkEdge,
  type OrgNetworkNode,
  type OrgSummary,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth";

// ── Helpers ───────────────────────────────────────────────────────────────

function fmtDate(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

type OrgRole = "owner" | "admin" | "editor" | "agent";

const ROLE_META: Record<OrgRole, { cls: string }> = {
  owner: { cls: "border-purple-500/40 bg-purple-500/10 text-purple-700 dark:text-purple-400" },
  admin: { cls: "border-blue-500/40 bg-blue-500/10 text-blue-700 dark:text-blue-400" },
  editor: { cls: "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" },
  agent: { cls: "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-400" },
};

function RoleBadge({ role }: { role: string }) {
  const meta = ROLE_META[role as OrgRole] ?? { cls: "border-border text-muted-foreground" };
  return <Badge className={cn("text-[10px]", meta.cls)}>{role}</Badge>;
}

// ── Copy-to-clipboard chip ────────────────────────────────────────────────

function ActorIdChip({ id }: { id: string }) {
  const [copied, setCopied] = React.useState(false);
  const short = id.length > 42 ? id.slice(0, 20) + "…" + id.slice(-14) : id;

  const copy = () => {
    void navigator.clipboard.writeText(id).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <button
      onClick={copy}
      title={id}
      className={cn(
        "group flex items-center gap-1.5 rounded-md border border-border/50 bg-muted/40",
        "px-2 py-1 font-mono text-xs text-muted-foreground transition-colors",
        "hover:border-accent/40 hover:bg-muted hover:text-foreground",
      )}
    >
      <span className="truncate max-w-[18rem]">{short}</span>
      {copied ? (
        <Check className="h-3 w-3 flex-none text-emerald-500" />
      ) : (
        <Copy className="h-3 w-3 flex-none opacity-0 group-hover:opacity-60" />
      )}
    </button>
  );
}

// ── Confirm dialog ────────────────────────────────────────────────────────

function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive,
  onConfirm,
  loading,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  title: string;
  description: string;
  confirmLabel: string;
  destructive?: boolean;
  onConfirm: () => void;
  loading?: boolean;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {destructive && <AlertTriangle className="h-5 w-5 text-destructive" />}
            {title}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            disabled={loading}
            onClick={onConfirm}
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Tag input for capabilities ────────────────────────────────────────────

const CAPABILITY_SUGGESTIONS = [
  "domain.*.read",
  "domain.*.write",
  "a2a.send",
  "a2a.receive",
  "forge.execute",
  "skill.invoke",
  "audit.read",
];

function CapabilityTagInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [input, setInput] = React.useState("");

  const add = (tag: string) => {
    const cleaned = tag.trim().toLowerCase().replace(/\s+/g, ".");
    if (cleaned && !value.includes(cleaned)) onChange([...value, cleaned]);
    setInput("");
  };

  const remove = (tag: string) => onChange(value.filter((v) => v !== tag));

  const handleKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(input);
    } else if (e.key === "Backspace" && !input && value.length > 0) {
      remove(value[value.length - 1]);
    }
  };

  const suggestions = CAPABILITY_SUGGESTIONS.filter(
    (s) => !value.includes(s) && (input ? s.includes(input.toLowerCase()) : true),
  );

  return (
    <div className="space-y-2">
      <div className="flex min-h-10 flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-3 py-2">
        {value.map((tag) => (
          <span
            key={tag}
            className="flex items-center gap-1 rounded-full bg-accent/15 px-2 py-0.5 font-mono text-[11px] text-accent"
          >
            {tag}
            <button onClick={() => remove(tag)} className="hover:text-foreground">
              <X className="h-2.5 w-2.5" />
            </button>
          </span>
        ))}
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder={value.length === 0 ? "Enter capability, press Enter…" : ""}
          className="min-w-[160px] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground/50"
        />
      </div>
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {suggestions.slice(0, 5).map((s) => (
            <button
              key={s}
              onClick={() => add(s)}
              className="rounded-full border border-border px-2 py-0.5 font-mono text-[11px] text-muted-foreground hover:border-accent/50 hover:text-foreground transition-colors"
            >
              + {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Members tab ───────────────────────────────────────────────────────────

function MembersTab({
  handle,
  members,
  csrf,
  onRefresh,
}: {
  handle: string;
  members: OrgMember[];
  csrf: string;
  onRefresh: () => void;
}) {
  const [addOpen, setAddOpen] = React.useState(false);
  const [addForm, setAddForm] = React.useState<{ actor_id: string; role: OrgRole }>({
    actor_id: "",
    role: "editor",
  });
  const [removeConfirm, setRemoveConfirm] = React.useState<string | null>(null);

  const addMut = useMutation({
    mutationFn: () => addOrgMember(handle, addForm, csrf),
    onSuccess: () => {
      onRefresh();
      setAddOpen(false);
      setAddForm({ actor_id: "", role: "editor" });
    },
  });

  const removeMut = useMutation({
    mutationFn: (actor_id: string) => removeOrgMember(handle, actor_id, csrf),
    onSuccess: () => {
      onRefresh();
      setRemoveConfirm(null);
    },
  });

  const roleMut = useMutation({
    mutationFn: ({ actor_id, role }: { actor_id: string; role: OrgRole }) =>
      updateOrgMemberRole(handle, actor_id, role, csrf),
    onSuccess: () => onRefresh(),
  });

  const ROLE_HINT: Record<OrgRole, string> = {
    owner: "Full access including deleting the org.",
    admin: "Can manage members but cannot dissolve.",
    editor: "Can create and edit content.",
    agent: "Automated access without admin rights.",
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {members.length} Member{members.length !== 1 ? "s" : ""}
        </p>
        <Button size="sm" variant="outline" onClick={() => setAddOpen(true)}>
          <UserPlus className="h-3.5 w-3.5" /> Add
        </Button>
      </div>

      {members.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border py-10 text-center">
          <Users className="h-8 w-8 text-muted-foreground/30" />
          <div className="space-y-0.5">
            <p className="text-sm font-medium text-muted-foreground">No members yet</p>
            <p className="text-xs text-muted-foreground/60">
              Members are people or actors who belong to this organisation. Add one by their
              Actor ID and assign a role (owner, admin, editor, or agent).
            </p>
          </div>
          <Button size="sm" variant="outline" onClick={() => setAddOpen(true)}>
            <UserPlus className="h-3.5 w-3.5" /> Add member
          </Button>
        </div>
      ) : (
        <div className="space-y-1">
          {members.map((m) => (
            <div
              key={m.actor_id}
              className="group flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-muted/40 transition-colors"
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
                {(m.actor_id.split("@")[1]?.[0] ?? m.actor_id[0]).toUpperCase()}
              </div>
              <div className="min-w-0 flex-1">
                <ActorIdChip id={m.actor_id} />
              </div>
              {m.role === "owner" ? (
                <RoleBadge role={m.role} />
              ) : (
                <Select
                  value={m.role}
                  className="h-7 w-[6.5rem] text-xs py-0"
                  disabled={roleMut.isPending}
                  onChange={(e) =>
                    roleMut.mutate({ actor_id: m.actor_id, role: e.target.value as OrgRole })
                  }
                >
                  <option value="admin">Admin</option>
                  <option value="editor">Editor</option>
                  <option value="agent">Agent</option>
                </Select>
              )}
              {m.role !== "owner" && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-destructive transition-all"
                  onClick={() => setRemoveConfirm(m.actor_id)}
                >
                  <UserMinus className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Add member</DialogTitle>
            <DialogDescription>
              Enter an Actor ID (e.g. <code className="font-mono text-xs">@alice@example.com</code>).
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="add-actor-id">Actor ID</Label>
              <Input
                id="add-actor-id"
                value={addForm.actor_id}
                onChange={(e) => setAddForm((f) => ({ ...f, actor_id: e.target.value }))}
                placeholder="@alice@host or https://…"
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="add-role">Role</Label>
              <Select
                id="add-role"
                value={addForm.role}
                onChange={(e) => setAddForm((f) => ({ ...f, role: e.target.value as OrgRole }))}
              >
                <option value="owner">Owner</option>
                <option value="admin">Admin</option>
                <option value="editor">Editor</option>
                <option value="agent">Agent</option>
              </Select>
              <p className="text-xs text-muted-foreground">{ROLE_HINT[addForm.role]}</p>
            </div>
          </div>
          {addMut.isError && (
            <p className="text-sm text-destructive">
              {addMut.error instanceof Error ? addMut.error.message : "Error"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAddOpen(false)}>Cancel</Button>
            <Button disabled={addMut.isPending || !addForm.actor_id} onClick={() => addMut.mutate()}>
              {addMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Add"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!removeConfirm}
        onOpenChange={(v) => !v && setRemoveConfirm(null)}
        title="Remove member"
        description={`${removeConfirm} will be removed from the organisation.`}
        confirmLabel="Remove"
        destructive
        loading={removeMut.isPending}
        onConfirm={() => removeConfirm && removeMut.mutate(removeConfirm)}
      />
    </div>
  );
}

// ── Agents tab ────────────────────────────────────────────────────────────

function AgentsTab({
  handle,
  agents,
  csrf,
  onRefresh,
}: {
  handle: string;
  agents: OrgEndorsement[];
  csrf: string;
  onRefresh: () => void;
}) {
  const [affiliateOpen, setAffiliateOpen] = React.useState(false);
  const [affiliateForm, setAffiliateForm] = React.useState({
    agent_actor_id: "",
    capabilities: [] as string[],
    ttl_days: "",
  });
  const [revokeConfirm, setRevokeConfirm] = React.useState<string | null>(null);

  const affiliateMut = useMutation({
    mutationFn: () =>
      affiliateOrgAgent(
        handle,
        {
          agent_actor_id: affiliateForm.agent_actor_id,
          scope: affiliateForm.capabilities,
          ttl_days: affiliateForm.ttl_days ? Number(affiliateForm.ttl_days) : undefined,
        },
        csrf,
      ),
    onSuccess: () => {
      onRefresh();
      setAffiliateOpen(false);
      setAffiliateForm({ agent_actor_id: "", capabilities: [], ttl_days: "" });
    },
  });

  const deaffiliateMut = useMutation({
    mutationFn: (id: string) => deaffiliateOrgAgent(handle, id, csrf),
    onSuccess: () => {
      onRefresh();
      setRevokeConfirm(null);
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {agents.length} affiliated agent{agents.length !== 1 ? "s" : ""}
        </p>
        <Button size="sm" variant="outline" onClick={() => setAffiliateOpen(true)}>
          <Plus className="h-3.5 w-3.5" /> Affiliate agent
        </Button>
      </div>

      {agents.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border py-10 text-center">
          <Building2 className="h-8 w-8 text-muted-foreground/30" />
          <div className="space-y-0.5">
            <p className="text-sm font-medium text-muted-foreground">No affiliated agents yet</p>
            <p className="text-xs text-muted-foreground/60">
              Affiliate an agent actor to grant it automated access within a defined set of
              capabilities on behalf of this organisation.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {agents.map((e) => (
            <div
              key={e.endorsement_id}
              className="group flex items-start gap-3 rounded-lg border border-border/50 px-3 py-3 hover:border-border transition-colors"
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent/15 text-xs font-semibold text-accent">
                {(e.agent_actor_id[0] === "@"
                  ? e.agent_actor_id.split("@")[1]?.[0]
                  : e.agent_actor_id[0])?.toUpperCase() ?? "A"}
              </div>
              <div className="min-w-0 flex-1 space-y-1.5">
                <ActorIdChip id={e.agent_actor_id} />
                <div className="flex flex-wrap items-center gap-1">
                  {e.scope.map((s) => (
                    <span key={s} className="rounded-full bg-accent/10 px-2 py-0.5 font-mono text-[10px] text-accent/80">
                      {s}
                    </span>
                  ))}
                  {e.expires_at && (
                    <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">
                      expires {fmtDate(e.expires_at)}
                    </span>
                  )}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-destructive transition-all"
                onClick={() => setRevokeConfirm(e.endorsement_id)}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      <Dialog open={affiliateOpen} onOpenChange={setAffiliateOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Affiliate agent</DialogTitle>
            <DialogDescription>
              Endorse an agent actor with defined capabilities.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="aff-id">Agent Actor ID</Label>
              <Input
                id="aff-id"
                value={affiliateForm.agent_actor_id}
                onChange={(e) => setAffiliateForm((f) => ({ ...f, agent_actor_id: e.target.value }))}
                placeholder="@bot@partner.example"
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label>Capabilities</Label>
              <CapabilityTagInput
                value={affiliateForm.capabilities}
                onChange={(v) => setAffiliateForm((f) => ({ ...f, capabilities: v }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="aff-ttl">Validity period in days (optional)</Label>
              <Input
                id="aff-ttl"
                type="number"
                min={1}
                max={3650}
                value={affiliateForm.ttl_days}
                onChange={(e) => setAffiliateForm((f) => ({ ...f, ttl_days: e.target.value }))}
                placeholder="365"
              />
            </div>
          </div>
          {affiliateMut.isError && (
            <p className="text-sm text-destructive">
              {affiliateMut.error instanceof Error ? affiliateMut.error.message : "Error"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setAffiliateOpen(false)}>Cancel</Button>
            <Button
              disabled={affiliateMut.isPending || !affiliateForm.agent_actor_id}
              onClick={() => affiliateMut.mutate()}
            >
              {affiliateMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Affiliate"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!revokeConfirm}
        onOpenChange={(v) => !v && setRevokeConfirm(null)}
        title="Revoke endorsement"
        description="The affiliation of this agent will be permanently revoked."
        confirmLabel="Revoke"
        destructive
        loading={deaffiliateMut.isPending}
        onConfirm={() => revokeConfirm && deaffiliateMut.mutate(revokeConfirm)}
      />
    </div>
  );
}

// ── Grants tab ────────────────────────────────────────────────────────────

function OrgGrantsTab({
  handle,
  grants,
  csrf,
  onRefresh,
}: {
  handle: string;
  grants: Grant[];
  csrf: string;
  onRefresh: () => void;
}) {
  const [issueOpen, setIssueOpen] = React.useState(false);
  const [form, setForm] = React.useState({ grantee_actor: "", capabilities: [] as string[] });
  const [revokeConfirm, setRevokeConfirm] = React.useState<string | null>(null);

  const issueMut = useMutation({
    mutationFn: () =>
      createOrgGrant(handle, { grantee_actor: form.grantee_actor, capabilities: form.capabilities }, csrf),
    onSuccess: () => {
      onRefresh();
      setIssueOpen(false);
      setForm({ grantee_actor: "", capabilities: [] });
    },
  });

  const revokeMut = useMutation({
    mutationFn: (id: string) => revokeOrgGrant(handle, id, csrf),
    onSuccess: () => {
      onRefresh();
      setRevokeConfirm(null);
    },
  });

  const active = grants.filter((g) => !g.revoked_at);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {active.length} active grant{active.length !== 1 ? "s" : ""}
        </p>
        <Button size="sm" variant="outline" onClick={() => setIssueOpen(true)}>
          <Shield className="h-3.5 w-3.5" /> Issue grant
        </Button>
      </div>

      {active.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border py-10 text-center">
          <KeyRound className="h-8 w-8 text-muted-foreground/30" />
          <div className="space-y-0.5">
            <p className="text-sm font-medium text-muted-foreground">No active grants</p>
            <p className="text-xs text-muted-foreground/60">
              Grants allow other actors to use capabilities on behalf of this org.
            </p>
          </div>
        </div>
      ) : (
        <div className="space-y-2">
          {active.map((g) => (
            <div
              key={g.grant_id}
              className="group flex items-start gap-3 rounded-lg border border-border/50 px-3 py-3 hover:border-border transition-colors"
            >
              <div className="min-w-0 flex-1 space-y-1.5">
                <ActorIdChip id={g.grantee_actor} />
                <div className="flex flex-wrap gap-1">
                  {g.capabilities.map((c) => (
                    <span key={c} className="rounded-full bg-accent/10 px-2 py-0.5 font-mono text-[10px] text-accent/80">
                      {c}
                    </span>
                  ))}
                </div>
                <p className="text-xs text-muted-foreground">Issued {fmtDate(g.issued_at)}</p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100 hover:text-destructive transition-all"
                onClick={() => setRevokeConfirm(g.grant_id!)}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      <Dialog open={issueOpen} onOpenChange={setIssueOpen}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>Issue grant</DialogTitle>
            <DialogDescription>
              Grant capabilities to another actor on behalf of this organisation.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="grant-grantee">Recipient Actor ID</Label>
              <Input
                id="grant-grantee"
                value={form.grantee_actor}
                onChange={(e) => setForm((f) => ({ ...f, grantee_actor: e.target.value }))}
                placeholder="@alice@example.com"
                autoFocus
              />
            </div>
            <div className="space-y-1.5">
              <Label>Capabilities</Label>
              <CapabilityTagInput
                value={form.capabilities}
                onChange={(v) => setForm((f) => ({ ...f, capabilities: v }))}
              />
            </div>
          </div>
          {issueMut.isError && (
            <p className="text-sm text-destructive">
              {issueMut.error instanceof Error ? issueMut.error.message : "Error"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setIssueOpen(false)}>Cancel</Button>
            <Button
              disabled={issueMut.isPending || !form.grantee_actor || form.capabilities.length === 0}
              onClick={() => issueMut.mutate()}
            >
              {issueMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Issue"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={!!revokeConfirm}
        onOpenChange={(v) => !v && setRevokeConfirm(null)}
        title="Revoke grant"
        description="The grant will be permanently revoked."
        confirmLabel="Revoke"
        destructive
        loading={revokeMut.isPending}
        onConfirm={() => revokeConfirm && revokeMut.mutate(revokeConfirm)}
      />
    </div>
  );
}

// ── Org detail panel ──────────────────────────────────────────────────────

// ── A2A Network graph ─────────────────────────────────────────────────────

interface OrgNetworkViewProps {
  nodes: OrgNetworkNode[];
  edges: OrgNetworkEdge[];
}

function OrgNetworkView({ nodes, edges }: OrgNetworkViewProps) {
  const containerRef     = React.useRef<HTMLDivElement>(null);
  const networkRef       = React.useRef<import('vis-network/standalone').Network | null>(null);
  const [selectedId, setSelectedId]     = React.useState<string | null>(null);
  const [searchTerm, setSearchTerm]     = React.useState('');
  const [physicsOn, setPhysicsOn]       = React.useState(true);
  const [stabilizing, setStabilizing]   = React.useState(false);

  // Derived: selected node + related edges
  const selectedNode  = selectedId ? nodes.find((n) => n.id === selectedId) ?? null : null;
  const relatedEdges  = selectedId
    ? edges.filter((e) => e.from === selectedId || e.to === selectedId)
    : [];

  // ── Toolbar handlers ────────────────────────────────────────────────────

  function zoomIn() {
    if (!networkRef.current) return;
    const s = networkRef.current.getScale();
    networkRef.current.moveTo({ scale: s * 1.3, animation: { duration: 200, easingFunction: 'easeInOutQuad' } });
  }
  function zoomOut() {
    if (!networkRef.current) return;
    const s = networkRef.current.getScale();
    networkRef.current.moveTo({ scale: s / 1.3, animation: { duration: 200, easingFunction: 'easeInOutQuad' } });
  }
  function fitAll() {
    networkRef.current?.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
  }
  function togglePhysics() {
    const next = !physicsOn;
    setPhysicsOn(next);
    networkRef.current?.setOptions({ physics: { enabled: next } });
  }
  function handleSearch(term: string) {
    setSearchTerm(term);
    const net = networkRef.current;
    if (!net) return;
    if (!term.trim()) { net.unselectAll(); return; }
    const lower = term.toLowerCase();
    const matching = nodes.filter((n) => n.label.toLowerCase().includes(lower) || n.id.toLowerCase().includes(lower));
    if (matching.length === 0) { net.unselectAll(); return; }
    net.selectNodes(matching.map((n) => n.id));
    if (matching.length === 1) {
      net.focus(matching[0].id, { animation: { duration: 300, easingFunction: 'easeInOutQuad' }, scale: 1.6 });
    } else {
      net.fit({ nodes: matching.map((n) => n.id), animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
    }
  }

  // ── Graph init ──────────────────────────────────────────────────────────

  React.useEffect(() => {
    if (!containerRef.current || nodes.length === 0) return;
    let destroyed = false;

    import('vis-network/standalone').then(({ Network: VisNetwork, DataSet }) => {
      if (destroyed || !containerRef.current) return;

      const visNodes = nodes.map((n) => {
        const isOrg   = n.type === 'org';
        const isAgent = n.type === 'agent';
        return {
          id:    n.id,
          label: n.label + (n.role ? `\n${n.role}` : ''),
          shape: isOrg ? 'star' : isAgent ? 'triangle' : 'dot',
          size:  isOrg ? 28 : 18,
          color: {
            background: isOrg ? '#0d419d' : isAgent ? '#2e1065' : '#0a3622',
            border:     isOrg ? '#388bfd' : isAgent ? '#8b5cf6' : '#238636',
            highlight:  {
              background: isOrg ? '#1a5dc8' : isAgent ? '#4c1d95' : '#145229',
              border:     isOrg ? '#79c0ff' : isAgent ? '#c4b5fd' : '#3fb950',
            },
          },
          font: { color: isOrg ? '#79c0ff' : isAgent ? '#c4b5fd' : '#3fb950', size: 11, multi: 'html' as const },
          title: `${n.type}${n.role ? ' · ' + n.role : ''}\n${n.id}`,
        };
      });

      const visEdges = edges.map((e, i) => ({
        id:    `e${i}`,
        from:  e.from,
        to:    e.to,
        color: {
          color:     e.type === 'grant' ? '#e3b341' : e.type === 'agent' ? '#8b5cf6' : '#238636',
          highlight: e.type === 'grant' ? '#f0c040' : e.type === 'agent' ? '#a78bfa' : '#3fb950',
        },
        dashes:  e.type === 'grant',
        arrows:  { to: { enabled: e.type === 'grant', scaleFactor: 0.6 } },
        title:   e.caps.length > 0 ? `Capabilities: ${e.caps.join(', ')}` : e.type,
        width:   1.5,
        selectionWidth: 2.5,
      }));

      setStabilizing(true);
      const net = new VisNetwork(
        containerRef.current!,
        { nodes: new DataSet(visNodes), edges: new DataSet(visEdges) },
        {
          physics: {
            enabled: true,
            solver: 'forceAtlas2Based',
            forceAtlas2Based: { gravitationalConstant: -60, centralGravity: 0.02, springConstant: 0.08, springLength: 90 },
            stabilization: { iterations: 200, fit: true },
          },
          interaction: { hover: true, tooltipDelay: 100, zoomView: true, dragView: true, multiselect: false },
          nodes: { borderWidth: 2, borderWidthSelected: 3 },
          edges: { smooth: true, selectionWidth: 2.5 },
        },
      );
      networkRef.current = net;

      // Auto-freeze after stabilization
      net.on('stabilizationIterationsDone', () => {
        setStabilizing(false);
        net.setOptions({ physics: { enabled: false } });
        setPhysicsOn(false);
        net.fit({ animation: { duration: 300, easingFunction: 'easeInOutQuad' } });
      });

      net.on('selectNode', ({ nodes: sel }: { nodes: string[] }) => {
        setSelectedId(sel[0] ?? null);
      });
      net.on('deselectNode', () => setSelectedId(null));
    });

    return () => {
      destroyed = true;
      networkRef.current?.destroy();
      networkRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(nodes), JSON.stringify(edges)]);

  if (nodes.length === 0) {
    return (
      <div className="py-8 text-center text-sm text-muted-foreground">
        No network data — add members or affiliate agents to see the graph.
      </div>
    );
  }

  return (
    <div className="relative w-full select-none" style={{ height: '420px' }}>
      {/* Toolbar — top left */}
      <div className="absolute top-2 left-2 z-10 flex items-center gap-1">
        {/* Search */}
        <input
          type="text"
          placeholder="Search nodes…"
          value={searchTerm}
          onChange={(e) => handleSearch(e.target.value)}
          onKeyDown={(e) => e.stopPropagation()}
          className="h-7 w-36 rounded border border-[#30363d] bg-[#0d1117]/90 px-2 text-[11px] text-gray-300 placeholder-gray-600 focus:border-blue-500 focus:outline-none"
        />
        <button onClick={zoomIn}  title="Zoom in"  className="flex h-7 w-7 items-center justify-center rounded bg-[#21262d] text-xs text-gray-400 hover:bg-[#30363d] hover:text-gray-200 transition-colors">＋</button>
        <button onClick={zoomOut} title="Zoom out" className="flex h-7 w-7 items-center justify-center rounded bg-[#21262d] text-xs text-gray-400 hover:bg-[#30363d] hover:text-gray-200 transition-colors">－</button>
        <button onClick={fitAll}  title="Fit all"  className="flex h-7 w-7 items-center justify-center rounded bg-[#21262d] text-xs text-gray-400 hover:bg-[#30363d] hover:text-gray-200 transition-colors">⊡</button>
        <button
          onClick={togglePhysics}
          title={physicsOn ? 'Freeze layout' : 'Resume physics'}
          className={`flex h-7 w-7 items-center justify-center rounded text-xs transition-colors ${
            physicsOn
              ? 'bg-blue-700 text-white hover:bg-blue-600'
              : 'bg-[#21262d] text-gray-400 hover:bg-[#30363d] hover:text-gray-200'
          }`}
        >{physicsOn ? '⏸' : '▶'}</button>
        {stabilizing && (
          <span className="ml-1 animate-pulse text-[10px] text-gray-500">Stabilizing…</span>
        )}
      </div>

      {/* Graph canvas */}
      <div ref={containerRef} className="w-full h-full rounded-lg bg-[#0d1117] border border-[#30363d]" />

      {/* Selected node detail panel */}
      {selectedNode && (
        <div className="absolute bottom-2 left-2 right-2 z-10 rounded-lg border border-[#30363d] bg-[#161b22]/95 px-3 py-2 backdrop-blur-sm">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-sm text-gray-200">{selectedNode.label}</span>
                <span className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                  selectedNode.type === 'org'   ? 'bg-blue-900/50 text-blue-400'   :
                  selectedNode.type === 'agent' ? 'bg-purple-900/50 text-purple-400':
                  'bg-green-900/50 text-green-400'
                }`}>{selectedNode.type}</span>
                {selectedNode.role && (
                  <span className="text-[10px] text-gray-400">{selectedNode.role}</span>
                )}
              </div>
              <div className="mt-0.5 font-mono text-[10px] text-gray-500 truncate">{selectedNode.id}</div>
              {relatedEdges.length > 0 && (
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5">
                  {relatedEdges.map((e, i) => (
                    <span key={i} className="text-[10px] text-gray-400">
                      <span className={`font-medium ${
                        e.type === 'grant' ? 'text-yellow-500' :
                        e.type === 'agent' ? 'text-purple-400' : 'text-green-400'
                      }`}>{e.type}</span>
                      {e.caps.length > 0 && `: ${e.caps.slice(0, 4).join(', ')}${e.caps.length > 4 ? '…' : ''}`}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <button
              onClick={() => { setSelectedId(null); networkRef.current?.unselectAll(); }}
              className="shrink-0 text-xs text-gray-600 hover:text-gray-400 transition-colors"
            >✕</button>
          </div>
        </div>
      )}

      {/* Legend — top right */}
      <div className="absolute top-2 right-2 z-10 flex flex-col gap-1 rounded bg-[#0d1117]/90 px-2 py-1.5 text-[10px] text-gray-400">
        {[
          { color: '#388bfd', label: 'Org' },
          { color: '#238636', label: 'Member' },
          { color: '#8b5cf6', label: 'Agent' },
        ].map(({ color, label }) => (
          <span key={label} className="flex items-center gap-1.5">
            <span className="inline-block w-2 h-2 rounded-full" style={{ backgroundColor: color }} />
            {label}
          </span>
        ))}
        <span className="flex items-center gap-1.5 mt-0.5">
          <span className="inline-block w-4 border-t border-dashed border-[#e3b341]" />
          Grant
        </span>
      </div>

      {/* Keyboard hint */}
      <div className="absolute bottom-2 right-2 z-10 text-[9px] text-gray-600 pointer-events-none">
        scroll zoom · drag pan · click node for details
      </div>
    </div>
  );
}

function OrgDetailPanel({ handle, csrf }: { handle: string; csrf: string }) {
  const qc = useQueryClient();
  const [dissolveOpen, setDissolveOpen] = React.useState(false);

  const orgQ = useQuery({
    queryKey: ["org-detail", handle],
    queryFn: ({ signal }) => getOrg(handle, signal),
    staleTime: 15_000,
  });

  const networkQ = useQuery({
    queryKey: ["org-network", handle],
    queryFn: ({ signal }) => getOrgNetwork(handle, signal),
    staleTime: 30_000,
    enabled: false,
  });

  const dissolveMut = useMutation({
    mutationFn: () => dissolveOrg(handle, csrf),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["orgs"] });
      void qc.removeQueries({ queryKey: ["org-detail", handle] });
    },
  });

  function refresh() {
    void qc.invalidateQueries({ queryKey: ["org-detail", handle] });
  }

  if (orgQ.isLoading) {
    return (
      <div className="flex flex-1 flex-col gap-4">
        <Skeleton className="h-14 w-full" />
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (orgQ.isError || !orgQ.data) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-destructive">
        Error loading organisation.
      </div>
    );
  }

  const org = orgQ.data;

  return (
    <div className="flex flex-1 flex-col gap-6 overflow-y-auto">
      {/* Header */}
      <div className="flex items-start gap-4">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-accent/20 text-lg font-bold text-accent">
          {org.actor.display_name?.slice(0, 1).toUpperCase() ?? org.handle[0].toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="font-serif text-xl font-light">
            {org.actor.display_name ?? org.handle}
          </h2>
          <div className="flex flex-wrap items-center gap-2 mt-0.5">
            <span className="font-mono text-sm text-muted-foreground">@{org.handle}</span>
            {org.actor.verified_domain && (
              <Badge className="border-emerald-500/40 bg-emerald-500/10 text-[10px] text-emerald-700 dark:text-emerald-400">
                <Check className="mr-1 h-2.5 w-2.5" />
                {org.actor.verified_domain}
              </Badge>
            )}
          </div>
          {org.actor.summary && (
            <p className="mt-1.5 text-sm text-muted-foreground">{org.actor.summary}</p>
          )}
        </div>
      </div>

      {/* Actor ID */}
      {org.actor.id && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">Federated Actor ID</p>
          <ActorIdChip id={org.actor.id} />
        </div>
      )}

      {/* Tabs */}
      <Tabs defaultValue="members" className="flex-1" onValueChange={(v) => {
        if (v === "network") void networkQ.refetch();
      }}>
        <TabsList>
          <TabsTrigger value="members">
            <Users className="h-3.5 w-3.5" />
            Members ({org.members.length})
          </TabsTrigger>
          <TabsTrigger value="agents">
            <Building2 className="h-3.5 w-3.5" />
            Agents ({org.agents.length})
          </TabsTrigger>
          <TabsTrigger value="grants">
            <KeyRound className="h-3.5 w-3.5" />
            Grants ({org.grants.filter((g) => !g.revoked_at).length})
          </TabsTrigger>
          <TabsTrigger value="network">
            <Network className="h-3.5 w-3.5" />
            Network
          </TabsTrigger>
        </TabsList>
        <TabsContent value="members" className="mt-4">
          <MembersTab handle={org.handle} members={org.members} csrf={csrf} onRefresh={refresh} />
        </TabsContent>
        <TabsContent value="agents" className="mt-4">
          <AgentsTab handle={org.handle} agents={org.agents} csrf={csrf} onRefresh={refresh} />
        </TabsContent>
        <TabsContent value="grants" className="mt-4">
          <OrgGrantsTab handle={org.handle} grants={org.grants} csrf={csrf} onRefresh={refresh} />
        </TabsContent>
        <TabsContent value="network" className="mt-4">
          {networkQ.isLoading ? (
            <div className="text-sm text-muted-foreground py-8 text-center">Loading network…</div>
          ) : networkQ.isError ? (
            <div className="text-sm text-destructive py-4">Failed to load network graph.</div>
          ) : (
            <OrgNetworkView
              nodes={networkQ.data?.nodes ?? []}
              edges={networkQ.data?.edges ?? []}
            />
          )}
        </TabsContent>
      </Tabs>

      {/* Danger zone */}
      <Card className="border-destructive/20 bg-destructive/3 mt-4">
        <CardHeader className="pb-2 pt-4 px-4">
          <CardTitle className="flex items-center gap-2 text-sm text-destructive">
            <AlertTriangle className="h-4 w-4" />
            Danger zone
          </CardTitle>
        </CardHeader>
        <CardContent className="px-4 pb-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-medium">Dissolve organisation</p>
              <p className="text-xs text-muted-foreground mt-0.5">
                Permanently deletes all members, agents, and grants.
              </p>
            </div>
            <Button
              variant="outline"
              size="sm"
              className="shrink-0 border-destructive/40 text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => setDissolveOpen(true)}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Dissolve
            </Button>
          </div>
        </CardContent>
      </Card>

      <ConfirmDialog
        open={dissolveOpen}
        onOpenChange={setDissolveOpen}
        title={`Dissolve "${org.handle}"`}
        description="This action is irreversible. All data for this organisation will be deleted."
        confirmLabel="Dissolve permanently"
        destructive
        loading={dissolveMut.isPending}
        onConfirm={() => dissolveMut.mutate()}
      />
    </div>
  );
}

// ── Org list card ─────────────────────────────────────────────────────────

function OrgCard({ org, selected, onSelect }: { org: OrgSummary; selected: boolean; onSelect: () => void }) {
  return (
    <button
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-3 rounded-lg px-3 py-3 text-left transition-colors",
        selected ? "bg-accent/15 ring-1 ring-accent/30" : "hover:bg-muted/60",
      )}
    >
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent/20 text-sm font-bold text-accent">
        {org.display_name.slice(0, 1).toUpperCase()}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium">{org.display_name}</div>
        <div className="font-mono text-[11px] text-muted-foreground">@{org.handle}</div>
      </div>
      <div className="shrink-0 flex flex-col items-end gap-0.5">
        <span className="text-[10px] text-muted-foreground">{org.member_count} mbr.</span>
        <span className="text-[10px] text-muted-foreground">{org.agent_count} ag.</span>
      </div>
      <ChevronRight className={cn(
        "h-4 w-4 shrink-0 text-muted-foreground/50 transition-transform",
        selected && "rotate-90 text-accent",
      )} />
    </button>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────

export function OrgsPage() {
  const { session } = useAuth();
  const qc = useQueryClient();
  const csrf = session?.csrf_token ?? "";

  const orgsQ = useQuery({
    queryKey: ["orgs"],
    queryFn: ({ signal }) => listOrgs(signal),
    staleTime: 20_000,
  });

  const [selected, setSelected] = React.useState<string | null>(null);
  const [createOpen, setCreateOpen] = React.useState(false);
  const [createForm, setCreateForm] = React.useState({
    handle: "",
    display_name: "",
    summary: "",
    host: "",
  });

  const createMut = useMutation({
    mutationFn: () =>
      createOrg(
        {
          handle: createForm.handle,
          display_name: createForm.display_name,
          summary: createForm.summary || undefined,
          host: createForm.host || undefined,
        },
        csrf,
      ),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: ["orgs"] });
      setCreateOpen(false);
      setSelected(data.org.handle);
      setCreateForm({ handle: "", display_name: "", summary: "", host: "" });
    },
  });

  const orgs = orgsQ.data?.orgs ?? [];

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-3xl font-light tracking-tight">Organisations</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage org actors, agent endorsements and B2B grants.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-3.5 w-3.5" /> New organisation
        </Button>
      </div>

      {/* Two-pane — stacks on mobile */}
      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="flex flex-col gap-1 rounded-xl border border-border/60 bg-card/40 p-3">
          {orgsQ.isLoading ? (
            <div className="space-y-2 p-2">
              {[1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full rounded-lg" />)}
            </div>
          ) : orgs.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-14 text-center">
              <Building2 className="h-10 w-10 text-muted-foreground/20" />
              <div className="space-y-0.5">
                <p className="text-sm font-medium text-muted-foreground">No organisations yet</p>
                <p className="text-xs text-muted-foreground/60">Create your first org unit.</p>
              </div>
              <Button size="sm" variant="outline" onClick={() => setCreateOpen(true)}>
                <Plus className="h-3.5 w-3.5" /> Create organisation
              </Button>
            </div>
          ) : (
            orgs.map((org) => (
              <OrgCard
                key={org.handle}
                org={org}
                selected={selected === org.handle}
                onSelect={() => setSelected(org.handle)}
              />
            ))
          )}
        </div>

        <div className="flex min-h-[480px] rounded-xl border border-border/60 bg-card/40 p-5">
          {selected ? (
            <OrgDetailPanel handle={selected} csrf={csrf} />
          ) : (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
              <Building2 className="h-10 w-10 opacity-20" />
              <p>Select an organisation to view details.</p>
              {orgs.length > 0 && (
                <p className="text-xs opacity-60">
                  {orgs.length} organisation{orgs.length !== 1 ? "s" : ""}
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Create dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Create organisation</DialogTitle>
            <DialogDescription>
              Creates an org actor with an Ed25519 key pair. You will automatically become owner.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="org-handle">Handle</Label>
                <Input
                  id="org-handle"
                  value={createForm.handle}
                  onChange={(e) =>
                    setCreateForm((f) => ({
                      ...f,
                      handle: e.target.value.toLowerCase().replace(/[^a-z0-9_-]/g, "-"),
                    }))
                  }
                  placeholder="my-company"
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="org-display-name">Display name</Label>
                <Input
                  id="org-display-name"
                  value={createForm.display_name}
                  onChange={(e) => setCreateForm((f) => ({ ...f, display_name: e.target.value }))}
                  placeholder="My Company Ltd"
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="org-summary">Description (optional)</Label>
              <Input
                id="org-summary"
                value={createForm.summary}
                onChange={(e) => setCreateForm((f) => ({ ...f, summary: e.target.value }))}
                placeholder="Short description of the organisation"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="org-host">
                Host <span className="text-xs text-muted-foreground font-normal">(optional, for federated Actor ID)</span>
              </Label>
              <Input
                id="org-host"
                value={createForm.host}
                onChange={(e) => setCreateForm((f) => ({ ...f, host: e.target.value }))}
                placeholder="company.example.com"
              />
            </div>
          </div>
          {createMut.isError && (
            <p className="text-sm text-destructive">
              {createMut.error instanceof Error ? createMut.error.message : "Creation failed"}
            </p>
          )}
          <DialogFooter>
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>Cancel</Button>
            <Button
              disabled={createMut.isPending || !createForm.handle || !createForm.display_name}
              onClick={() => createMut.mutate()}
            >
              {createMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
