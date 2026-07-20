import * as React from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  CheckCircle2,
  Clock,
  Copy,
  Globe2,
  Key,
  Link2,
  Loader2,
  Pencil,
  RefreshCw,
  Shield,
  ShieldCheck,
  Trash2,
  UserPlus,
  XCircle,
  Zap,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  type A2AEvent,
  type A2AOrigin,
  type A2AEndpoint,
  type A2ARedeemResponse,
  type InviteListEntry,
  type FriendshipConnection,
  type FriendshipImportResponse,
  type FriendshipCreateResponse,
  getA2ALog,
  getA2AOrigins,
  getA2AEndpoints,
  getA2APairMyInfo,
  generateA2AInvite,
  redeemA2AInvite,
  listA2AInvites,
  revokeA2AInvite,
  getMyA2AUrl,
  setMyA2AUrl,
  createFriendshipToken,
  importFriendshipToken,
  setFriendshipUrl,
  revokeFriendshipToken,
  listFriendshipConnections,
  patchA2AOrigin,
  deleteA2AOrigin,
  patchA2AEndpoint,
  deleteA2AEndpoint,
  getLicenseInfo,
  type LicenseInfo,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";

// ── helpers ────────────────────────────────────────────────────────

function fmtTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString();
}

function fmtDuration(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function SeverityDot({ severity }: { severity: string }) {
  if (severity === "CRITICAL")
    return <span className="inline-block h-2 w-2 rounded-full bg-red-500" />;
  if (severity === "WARNING")
    return <span className="inline-block h-2 w-2 rounded-full bg-amber-400" />;
  return <span className="inline-block h-2 w-2 rounded-full bg-emerald-400" />;
}

function EventTypeBadge({ type }: { type: string }) {
  const short = type.replace("A2A.", "");
  const isRejected = short.includes("rejected");
  const isSpawned = short.includes("spawned") || short.includes("sent");
  return (
    <Badge
      variant="outline"
      className={
        isRejected
          ? "border-red-500/40 text-red-600 dark:text-red-400 font-mono text-[10px]"
          : isSpawned
            ? "border-emerald-500/40 text-emerald-700 dark:text-emerald-400 font-mono text-[10px]"
            : "font-mono text-[10px]"
      }
    >
      {short}
    </Badge>
  );
}

function CopyChip({ value, short }: { value: string; short?: string }) {
  const [copied, setCopied] = React.useState(false);
  const display = short ?? (value.length > 24 ? value.slice(0, 10) + "…" + value.slice(-8) : value);
  return (
    <button
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1800);
        });
      }}
      title={value}
      className="group flex items-center gap-1 rounded border border-border/50 bg-muted/40 px-2 py-0.5 font-mono text-[11px] text-muted-foreground transition-colors hover:border-accent/40 hover:text-foreground"
    >
      <span className="truncate max-w-[14rem]">{display}</span>
      {copied
        ? <CheckCircle2 className="h-2.5 w-2.5 flex-none text-emerald-500" />
        : <Copy className="h-2.5 w-2.5 flex-none opacity-0 group-hover:opacity-60" />}
    </button>
  );
}

function StateBadge({ state }: { state: "PENDING" | "ACTIVE" | string }) {
  if (state === "ACTIVE") {
    return (
      <Badge className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-emerald-500/30 text-[10px]">
        ACTIVE
      </Badge>
    );
  }
  return (
    <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30 text-[10px]">
      PENDING
    </Badge>
  );
}

// ── Permission chip & inline editor ───────────────────────────────

function PermBadge({ spawnWorker, enabled, personas }: {
  spawnWorker: boolean;
  enabled: boolean;
  personas?: string[];
}) {
  if (!enabled) {
    return (
      <Badge variant="outline" className="text-muted-foreground gap-1 text-[10px]">
        <XCircle className="h-2.5 w-2.5" /> Disabled
      </Badge>
    );
  }
  if (!spawnWorker) {
    return (
      <Badge className="bg-amber-500/15 text-amber-700 dark:text-amber-400 border-amber-500/30 gap-1 text-[10px]">
        <Shield className="h-2.5 w-2.5" /> Observer
      </Badge>
    );
  }
  if (personas && personas.length > 0) {
    return (
      <Badge className="bg-violet-500/15 text-violet-700 dark:text-violet-400 border-violet-500/30 gap-1 text-[10px]">
        <Zap className="h-2.5 w-2.5" />
        Restricted · {personas.slice(0, 2).join(", ")}{personas.length > 2 ? ` +${personas.length - 2}` : ""}
      </Badge>
    );
  }
  return (
    <Badge className="bg-blue-500/15 text-blue-700 dark:text-blue-400 border-blue-500/30 gap-1 text-[10px]">
      <Zap className="h-2.5 w-2.5" /> Full Executor
    </Badge>
  );
}

type PermPreset = "observer" | "restricted" | "executor";

function presetFromOrigin(o: A2AOrigin): PermPreset {
  if (!o.spawn_worker) return "observer";
  if (o.allowed_personas.length > 0) return "restricted";
  return "executor";
}

function OriginPermEditor({
  origin,
  csrf,
  onSaved,
  onCancel,
}: {
  origin: A2AOrigin;
  csrf: string;
  onSaved: (updated: Partial<A2AOrigin>) => void;
  onCancel: () => void;
}) {
  const [preset, setPreset] = React.useState<PermPreset>(presetFromOrigin(origin));
  const [personas, setPersonas] = React.useState(origin.allowed_personas.join(", "));
  const [maxTtl, setMaxTtl] = React.useState(origin.max_ttl_s ? String(origin.max_ttl_s) : "");
  const [enabled, setEnabled] = React.useState(origin.enabled);
  const [label, setLabel] = React.useState(origin.label ?? "");
  const [toolFlags, setToolFlags] = React.useState({
    allow_bash: Boolean(origin.allow_bash),
    allow_network: Boolean(origin.allow_network),
    allow_read_files: Boolean(origin.allow_read_files),
    allow_write_files: Boolean(origin.allow_write_files),
    allow_subagents: Boolean(origin.allow_subagents),
  });
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");

  const parsedPersonas = personas
    .split(",")
    .map((p) => p.trim())
    .filter(Boolean);

  async function handleSave() {
    setSaving(true);
    setError("");
    const spawnWorker = preset !== "observer";
    const allowedPersonas = preset === "restricted" ? parsedPersonas : [];
    if (preset === "restricted" && allowedPersonas.length === 0) {
      setError("Restricted mode requires at least one persona.");
      setSaving(false);
      return;
    }
    const ttl = maxTtl ? Number(maxTtl) : null;
    if (ttl !== null && (Number.isNaN(ttl) || ttl < 10 || ttl > 86400)) {
      setError("Max TTL must be between 10 and 86400 seconds.");
      setSaving(false);
      return;
    }
    try {
      const res = await patchA2AOrigin(
        origin.origin_id,
        {
          spawn_worker: spawnWorker,
          enabled,
          allowed_personas: allowedPersonas,
          max_ttl_s: ttl,
          label: label.trim(),
          ...toolFlags,
        },
        csrf,
      );
      onSaved({
        spawn_worker: res.spawn_worker,
        enabled: res.enabled,
        allowed_personas: res.allowed_personas,
        max_ttl_s: res.max_ttl_s,
        label: res.label,
        allow_bash: res.allow_bash,
        allow_network: res.allow_network,
        allow_read_files: res.allow_read_files,
        allow_write_files: res.allow_write_files,
        allow_subagents: res.allow_subagents,
      });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  const presets: { id: PermPreset; icon: React.ReactNode; label: string; desc: string; color: string }[] = [
    {
      id: "observer",
      icon: <Shield className="h-4 w-4 text-amber-500" />,
      label: "Observer",
      desc: "Read-only. No task execution.",
      color: "amber",
    },
    {
      id: "restricted",
      icon: <Zap className="h-4 w-4 text-violet-500" />,
      label: "Restricted",
      desc: "Tasks on specific personas only.",
      color: "violet",
    },
    {
      id: "executor",
      icon: <Zap className="h-4 w-4 text-blue-500" />,
      label: "Full Executor",
      desc: "All personas, full execution.",
      color: "blue",
    },
  ];

  return (
    <div className="mt-2 rounded-md border border-border bg-muted/30 p-3 space-y-3">
      <p className="text-xs font-semibold text-foreground">Edit connection</p>

      {/* Connection name */}
      <div className="space-y-1">
        <label className="text-xs font-medium text-foreground">
          Connection name{" "}
          <span className="text-muted-foreground">— shown in the console and to the agent</span>
        </label>
        <Input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="e.g. Papa Laptop"
          maxLength={80}
          className="h-8 text-xs"
        />
      </div>

      {/* Preset cards */}
      <div className="grid grid-cols-3 gap-2">
        {presets.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setPreset(p.id)}
            className={cn(
              "rounded-md border p-2.5 text-left transition-colors",
              preset === p.id
                ? p.color === "amber"
                  ? "border-amber-500/50 bg-amber-500/8"
                  : p.color === "violet"
                  ? "border-violet-500/50 bg-violet-500/8"
                  : "border-blue-500/50 bg-blue-500/8"
                : "border-border hover:border-border/80",
            )}
          >
            <div className="flex items-center gap-1.5 mb-0.5">{p.icon}
              <span className="text-xs font-medium">{p.label}</span>
            </div>
            <p className="text-[10px] text-muted-foreground">{p.desc}</p>
          </button>
        ))}
      </div>

      {/* Restricted: persona list */}
      {preset === "restricted" && (
        <div className="space-y-1">
          <label className="text-xs font-medium text-foreground">
            Allowed personas <span className="text-muted-foreground">(comma-separated)</span>
          </label>
          <Input
            value={personas}
            onChange={(e) => setPersonas(e.target.value)}
            placeholder="assistant, coder"
            className="h-8 text-xs font-mono"
          />
          {parsedPersonas.length > 0 && (
            <p className="text-[10px] text-muted-foreground">
              {parsedPersonas.length} persona{parsedPersonas.length !== 1 ? "s" : ""}:{" "}
              {parsedPersonas.join(" · ")}
            </p>
          )}
        </div>
      )}

      {/* Tool permissions (M2 worker policy, deny-by-default) */}
      {preset !== "observer" && (
        <div className="space-y-1.5">
          <label className="text-xs font-medium text-foreground">
            Tool permissions{" "}
            <span className="text-muted-foreground">— everything is blocked unless granted</span>
          </label>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3">
            {([
              ["allow_bash", "Shell (Bash)"],
              ["allow_network", "Network access"],
              ["allow_read_files", "Read files"],
              ["allow_write_files", "Write files"],
              ["allow_subagents", "Subagents"],
            ] as const).map(([key, title]) => (
              <label key={key} className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={toolFlags[key]}
                  onChange={(e) =>
                    setToolFlags((prev) => ({ ...prev, [key]: e.target.checked }))
                  }
                  className="rounded"
                />
                <span className="text-xs">{title}</span>
              </label>
            ))}
          </div>
          <p className="text-[10px] text-muted-foreground">
            Honest limits: Shell (Bash) effectively includes network access
            (curl/wget run in the shell) — the Network checkbox only gates the
            built-in web tools. Subagents may run with default tool access:
            the engine does not guarantee that the other denies above bind
            inside subagent workers, so enabling Subagents can effectively
            grant them all.
          </p>
        </div>
      )}

      {/* Max TTL */}
      {preset !== "observer" && (
        <div className="space-y-1">
          <label className="text-xs font-medium text-foreground">
            Max task TTL (seconds){" "}
            <span className="text-muted-foreground">— leave empty for no limit</span>
          </label>
          <Input
            type="number"
            value={maxTtl}
            onChange={(e) => setMaxTtl(e.target.value)}
            placeholder="e.g. 300"
            min={10}
            max={86400}
            className="h-8 text-xs w-36"
          />
        </div>
      )}

      {/* Enabled toggle */}
      <label className="flex items-center gap-2 text-sm cursor-pointer">
        <input
          type="checkbox"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="rounded"
        />
        <span className="text-xs">Origin enabled</span>
      </label>

      {error && (
        <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>
      )}

      <div className="flex gap-2">
        <Button size="sm" disabled={saving} className="h-7 px-3 text-xs" onClick={handleSave}>
          {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
        </Button>
        <Button size="sm" variant="ghost" className="h-7 px-3 text-xs" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

// ── OriginRow with inline permission editing + delete ─────────────

function OriginRow({
  origin: initialOrigin,
  csrf,
  onDeleted,
}: {
  origin: A2AOrigin;
  csrf: string;
  onDeleted?: () => void;
}) {
  const [origin, setOrigin] = React.useState(initialOrigin);
  const [editing, setEditing] = React.useState(false);
  const [deletePending, setDeletePending] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [deleteError, setDeleteError] = React.useState("");
  const initials = origin.origin_id.slice(0, 2).toUpperCase();
  const isFriendship = Boolean(origin._friendship);

  async function handleDelete() {
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteA2AOrigin(origin.origin_id, csrf);
      onDeleted?.();
    } catch (err: unknown) {
      setDeleteError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
      setDeletePending(false);
    }
  }

  return (
    <div className="rounded-lg border border-border/50 p-3 hover:border-border transition-colors">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-sky-500/15 text-xs font-bold text-sky-500">
          {initials}
        </div>
        <div className="min-w-0 flex-1 space-y-1.5">
          <div className="flex items-center gap-2 flex-wrap">
            <CopyChip value={origin.origin_id} />
            <Badge variant="outline" className="text-[10px] border-sky-500/30 text-sky-600 dark:text-sky-400">
              ← inbound
            </Badge>
            {origin.label && (
              <span className="text-xs text-muted-foreground">{origin.label}</span>
            )}
            {isFriendship && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">token</Badge>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <PermBadge
              spawnWorker={origin.spawn_worker}
              enabled={origin.enabled}
              personas={origin.allowed_personas}
            />
            {origin.max_ttl_s !== null && (
              <span className="text-[10px] text-muted-foreground">max {origin.max_ttl_s}s TTL</span>
            )}
            {origin.enabled && origin.spawn_worker && (
              <span className="text-[10px] text-muted-foreground">
                tools:{" "}
                {[
                  origin.allow_bash && "bash",
                  origin.allow_network && "network",
                  origin.allow_read_files && "read",
                  origin.allow_write_files && "write",
                  origin.allow_subagents && "subagents",
                ]
                  .filter(Boolean)
                  .join(", ") || "none"}
              </span>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0">
          {!deletePending ? (
            <>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs text-muted-foreground hover:text-foreground"
                onClick={() => { setEditing((v) => !v); setDeletePending(false); }}
              >
                {editing ? "×" : "Permissions"}
              </Button>
              {!isFriendship && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                  title="Delete origin"
                  onClick={() => { setDeletePending(true); setEditing(false); }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </>
          ) : (
            <div className="flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
              <span className="text-xs text-destructive">Delete?</span>
              <Button
                size="sm"
                variant="destructive"
                className="h-6 px-2 text-[10px]"
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : "Delete"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[10px]"
                onClick={() => { setDeletePending(false); setDeleteError(""); }}
              >
                Cancel
              </Button>
            </div>
          )}
        </div>
      </div>

      {deleteError && (
        <p className="mt-1 rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{deleteError}</p>
      )}

      {editing && (
        <OriginPermEditor
          origin={origin}
          csrf={csrf}
          onSaved={(updated) => {
            setOrigin((prev) => ({ ...prev, ...updated }));
            setEditing(false);
          }}
          onCancel={() => setEditing(false)}
        />
      )}
    </div>
  );
}

function EndpointRow({
  endpoint: initialEndpoint,
  csrf,
  onDeleted,
}: {
  endpoint: A2AEndpoint;
  csrf: string;
  onDeleted?: () => void;
}) {
  const [endpoint, setEndpoint] = React.useState(initialEndpoint);
  const [editing, setEditing] = React.useState(false);
  const [deletePending, setDeletePending] = React.useState(false);
  const [deleting, setDeleting] = React.useState(false);
  const [deleteError, setDeleteError] = React.useState("");

  // Edit form state
  const [urlInput, setUrlInput] = React.useState(endpoint.url ?? "");
  const [labelInput, setLabelInput] = React.useState(endpoint.label ?? "");
  const [enabledInput, setEnabledInput] = React.useState(endpoint.enabled);
  const [ttlInput, setTtlInput] = React.useState(endpoint.default_ttl_s ? String(endpoint.default_ttl_s) : "");
  const [saving, setSaving] = React.useState(false);
  const [saveError, setSaveError] = React.useState("");

  const isFriendship = Boolean(endpoint._friendship);
  const initials = endpoint.endpoint_id.slice(0, 2).toUpperCase();

  async function handleSave() {
    setSaving(true);
    setSaveError("");
    const ttlNum = ttlInput ? Number(ttlInput) : null;
    if (ttlNum !== null && (Number.isNaN(ttlNum) || ttlNum < 10 || ttlNum > 86400)) {
      setSaveError("TTL must be between 10 and 86400 seconds.");
      setSaving(false);
      return;
    }
    try {
      const res = await patchA2AEndpoint(
        endpoint.endpoint_id,
        {
          label: labelInput || null,
          url: urlInput || null,
          enabled: enabledInput,
          default_ttl_s: ttlNum,
        },
        csrf,
      );
      setEndpoint((prev) => ({ ...prev, ...res }));
      setEditing(false);
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDeleteError("");
    try {
      await deleteA2AEndpoint(endpoint.endpoint_id, csrf);
      onDeleted?.();
    } catch (err: unknown) {
      setDeleteError(err instanceof Error ? err.message : String(err));
      setDeleting(false);
      setDeletePending(false);
    }
  }

  return (
    <div className="rounded-lg border border-border/50 p-3 hover:border-border transition-colors space-y-2">
      <div className="flex items-start gap-3">
        <div className={cn(
          "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-xs font-bold",
          endpoint.enabled ? "bg-emerald-500/15 text-emerald-500" : "bg-muted text-muted-foreground",
        )}>
          {initials}
        </div>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <CopyChip value={endpoint.endpoint_id} />
            {endpoint.label && (
              <span className="text-xs font-medium text-foreground">{endpoint.label}</span>
            )}
            {isFriendship && (
              <Badge variant="outline" className="text-[10px] text-muted-foreground">token</Badge>
            )}
          </div>
          {endpoint.url && (
            <CopyChip
              value={endpoint.url}
              short={endpoint.url.replace(/^https?:\/\//, "").slice(0, 32) + "…"}
            />
          )}
          {!endpoint.url && (
            <span className="text-[10px] text-muted-foreground italic">No URL set</span>
          )}
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge variant="outline" className="text-[10px] border-accent/30 text-accent">
              → outbound
            </Badge>
            {endpoint.enabled ? (
              <span className="flex items-center gap-0.5 text-[10px] text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="h-2.5 w-2.5" /> active
              </span>
            ) : (
              <span className="flex items-center gap-0.5 text-[10px] text-muted-foreground">
                <XCircle className="h-2.5 w-2.5" /> inactive
              </span>
            )}
            {endpoint.instance_id_pin && (
              <span className="font-mono text-[10px] text-muted-foreground">
                pin: {endpoint.instance_id_pin.slice(0, 8)}…
              </span>
            )}
            {endpoint.default_ttl_s && (
              <span className="text-[10px] text-muted-foreground">TTL {endpoint.default_ttl_s}s</span>
            )}
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0">
          {!deletePending ? (
            <>
              {!isFriendship && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0 text-muted-foreground hover:text-foreground"
                  title="Edit endpoint"
                  onClick={() => { setEditing((v) => !v); setDeletePending(false); }}
                >
                  {editing ? <XCircle className="h-3.5 w-3.5" /> : <Pencil className="h-3.5 w-3.5" />}
                </Button>
              )}
              {!isFriendship && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                  title="Delete endpoint"
                  onClick={() => { setDeletePending(true); setEditing(false); }}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              )}
            </>
          ) : (
            <div className="flex items-center gap-1.5">
              <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
              <span className="text-xs text-destructive">Delete?</span>
              <Button
                size="sm"
                variant="destructive"
                className="h-6 px-2 text-[10px]"
                disabled={deleting}
                onClick={handleDelete}
              >
                {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : "Delete"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-6 px-2 text-[10px]"
                onClick={() => { setDeletePending(false); setDeleteError(""); }}
              >
                Cancel
              </Button>
            </div>
          )}
        </div>
      </div>

      {deleteError && (
        <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{deleteError}</p>
      )}

      {/* Inline edit form */}
      {editing && (
        <div className="mt-1 rounded-md border border-border bg-muted/30 p-3 space-y-2.5">
          <p className="text-xs font-semibold">Edit endpoint</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Label</label>
              <Input
                value={labelInput}
                onChange={(e) => setLabelInput(e.target.value)}
                placeholder="Friendly name"
                className="h-8 text-xs"
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Default TTL (s)</label>
              <Input
                type="number"
                value={ttlInput}
                onChange={(e) => setTtlInput(e.target.value)}
                placeholder="e.g. 300"
                min={10}
                max={86400}
                className="h-8 text-xs"
              />
            </div>
          </div>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">URL</label>
            <Input
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="https://peer.example.com"
              className="h-8 text-xs font-mono"
            />
          </div>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={enabledInput}
              onChange={(e) => setEnabledInput(e.target.checked)}
              className="rounded"
            />
            <span className="text-xs">Endpoint enabled</span>
          </label>
          {saveError && (
            <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{saveError}</p>
          )}
          <div className="flex gap-2">
            <Button size="sm" disabled={saving} className="h-7 px-3 text-xs" onClick={handleSave}>
              {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
            </Button>
            <Button size="sm" variant="ghost" className="h-7 px-3 text-xs" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Peers tab ──────────────────────────────────────────────────────

function PeersTab() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";

  const origins = useQuery({
    queryKey: ["a2a", "origins"],
    queryFn: ({ signal }) => getA2AOrigins(signal),
    refetchInterval: 15_000,
  });
  const endpoints = useQuery({
    queryKey: ["a2a", "endpoints"],
    queryFn: ({ signal }) => getA2AEndpoints(signal),
    refetchInterval: 15_000,
  });
  const licInfo = useQuery<LicenseInfo>({
    queryKey: ["license", "info"],
    queryFn: ({ signal }) => getLicenseInfo(signal),
    staleTime: 60_000,
  });

  const peersMax = licInfo.data?.limits?.a2a_peers_max as number | null | undefined;
  const peersCount = origins.data?.origins.length ?? 0;
  const atPeersLimit = peersMax != null && peersCount >= peersMax;

  const allEmpty =
    origins.data?.origins.length === 0 && endpoints.data?.endpoints.length === 0;

  return (
    <div className="space-y-6">
      <div className="rounded-md border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
        <ShieldCheck className="inline h-3.5 w-3.5 mr-1.5 text-accent" />
        All connections are protected by HMAC-SHA256 envelope signing, ±300 s time window, and nonce replay protection.
        Click <strong className="text-foreground">Permissions</strong> on any inbound origin to change its access level.
      </div>

      {/* Inbound origins */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ArrowDownToLine className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Inbound — agents that can reach us</CardTitle>
            {peersMax != null && (
              <Badge
                variant={atPeersLimit ? "danger" : "secondary"}
                className="ml-auto text-xs font-mono"
              >
                {peersCount}/{peersMax} peer{peersMax !== 1 ? "s" : ""}
              </Badge>
            )}
          </div>
          <CardDescription>
            Each origin can be set to <strong>Observer</strong> (read-only queries) or{" "}
            <strong>Executor</strong> (full task execution). Default is Observer.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {atPeersLimit && (
            <div className="mb-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
              Free tier limit reached ({peersMax} inbound peer{peersMax !== 1 ? "s" : ""}).
              Remove an existing connection or{" "}
              <a
                href="https://corvin-labs.com/pricing"
                target="_blank"
                rel="noopener noreferrer"
                className="underline font-semibold"
              >
                upgrade
              </a>{" "}
              for more peers.
            </div>
          )}
          {origins.isLoading && (
            <div className="space-y-2">
              {[1, 2].map((n) => <Skeleton key={n} className="h-16 rounded-md" />)}
            </div>
          )}
          {origins.isError && (
            <p className="text-sm text-destructive">Failed to load origins.</p>
          )}
          {origins.data && origins.data.origins.length === 0 && (
            <EmptyState
              icon={<ArrowDownToLine className="h-8 w-8" />}
              title="No inbound connections"
              hint='Use the Connect tab to pair with another agent.'
            />
          )}
          {origins.data && origins.data.origins.length > 0 && (
            <div className="space-y-2">
              {origins.data.origins.map((o) => (
                <OriginRow
                  key={o.origin_id}
                  origin={o}
                  csrf={csrf}
                  onDeleted={() => void origins.refetch()}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Outbound endpoints */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ArrowUpFromLine className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">Outbound — agents we can reach</CardTitle>
          </div>
          <CardDescription>
            Remote instances reachable via{" "}
            <code className="rounded bg-muted px-1 text-[11px]">corvin-a2a send</code> or the A2A API.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {endpoints.isLoading && (
            <div className="space-y-2">
              {[1, 2].map((n) => <Skeleton key={n} className="h-12 rounded-md" />)}
            </div>
          )}
          {endpoints.isError && (
            <p className="text-sm text-destructive">Failed to load endpoints.</p>
          )}
          {endpoints.data && endpoints.data.endpoints.length === 0 && (
            <EmptyState
              icon={<ArrowUpFromLine className="h-8 w-8" />}
              title="No outbound connections"
              hint='Use the Connect tab to pair with another agent.'
            />
          )}
          {endpoints.data && endpoints.data.endpoints.length > 0 && (
            <div className="grid gap-2 sm:grid-cols-2">
              {endpoints.data.endpoints.map((e) => (
                <EndpointRow
                  key={e.endpoint_id}
                  endpoint={e}
                  csrf={csrf}
                  onDeleted={() => void endpoints.refetch()}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {allEmpty && origins.data && endpoints.data && (
        <div className="rounded-md border border-dashed border-border px-6 py-8 text-center text-muted-foreground">
          <Globe2 className="mx-auto mb-2 h-8 w-8 opacity-20" />
          <p className="text-sm font-medium">No connections yet</p>
          <p className="mt-1 text-xs">Open the <strong>Connect</strong> tab to pair this instance with another Corvin agent.</p>
        </div>
      )}
    </div>
  );
}

// ── Live Feed tab ──────────────────────────────────────────────────

function LiveFeedTab() {
  const [autoRefresh, setAutoRefresh] = React.useState(true);

  const log = useQuery({
    queryKey: ["a2a", "log"],
    queryFn: ({ signal }) => getA2ALog({ limit: 100 }, signal),
    refetchInterval: autoRefresh ? 5_000 : false,
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Last 100 A2A audit events — newest first.
          {log.data && (
            <span className="ml-2 font-mono text-xs">
              {log.data.count} event{log.data.count !== 1 ? "s" : ""}
            </span>
          )}
        </p>
        <Button
          variant={autoRefresh ? "secondary" : "outline"}
          size="sm"
          onClick={() => setAutoRefresh((v) => !v)}
          className="gap-1.5"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${autoRefresh ? "animate-spin" : ""}`} style={autoRefresh ? { animationDuration: "3s" } : {}} />
          {autoRefresh ? "Live" : "Paused"}
        </Button>
      </div>

      <Card>
        <CardContent className="p-0">
          {log.isLoading && (
            <div className="space-y-px p-4">
              {[1, 2, 3, 4, 5].map((n) => <Skeleton key={n} className="h-10 rounded" />)}
            </div>
          )}
          {log.isError && (
            <p className="p-4 text-sm text-destructive">Failed to load events.</p>
          )}
          {log.data && log.data.events.length === 0 && (
            <div className="flex flex-col items-center gap-2 py-12 text-muted-foreground">
              <Clock className="h-8 w-8 opacity-30" />
              <p className="text-sm">No A2A events yet.</p>
            </div>
          )}
          {log.data && log.data.events.length > 0 && (
            <div className="divide-y divide-border">
              {log.data.events.map((ev, i) => (
                <EventRow key={i} event={ev} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function EventRow({ event: ev }: { event: A2AEvent }) {
  const peer = ev.origin_id ?? ev.endpoint_id ?? "?";
  return (
    <div className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-muted/40">
      <SeverityDot severity={ev.severity} />
      <span className="w-20 shrink-0 font-mono text-xs text-muted-foreground">
        {fmtTs(ev.ts)}
      </span>
      <EventTypeBadge type={ev.event_type} />
      <span className="min-w-0 flex-1 truncate font-mono text-xs text-muted-foreground">
        {peer}
      </span>
      {ev.status && (
        <Badge
          variant="outline"
          className={`shrink-0 text-[10px] ${ev.status === "ok" ? "text-emerald-600" : "text-red-500"}`}
        >
          {ev.status}
        </Badge>
      )}
      <span className="shrink-0 text-xs text-muted-foreground">
        {fmtDuration(ev.duration_ms)}
      </span>
    </div>
  );
}

// ── My URL banner ──────────────────────────────────────────────────

function MyUrlBanner() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const myUrl = useQuery({
    queryKey: ["a2a", "my-url"],
    queryFn: ({ signal }) => getMyA2AUrl(signal),
    staleTime: 60_000,
  });

  const [editing, setEditing] = React.useState(false);
  const [urlInput, setUrlInput] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [copied, setCopied] = React.useState(false);

  const url = myUrl.data?.url ?? null;
  const suggested = myUrl.data?.suggested ?? null;

  function isPrivateUrl(u: string | null): boolean {
    if (!u) return false;
    return /^https?:\/\/(localhost|127\.|::1|10\.|172\.(1[6-9]|2\d|3[01])\.|192\.168\.)/i.test(u);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      await setMyA2AUrl(urlInput.trim(), csrf);
      void qc.invalidateQueries({ queryKey: ["a2a", "my-url"] });
      setEditing(false);
    } finally {
      setSaving(false);
    }
  }

  async function handleAcceptSuggested() {
    if (!suggested) return;
    setSaving(true);
    try {
      await setMyA2AUrl(suggested, csrf);
      void qc.invalidateQueries({ queryKey: ["a2a", "my-url"] });
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="rounded-md border border-border bg-muted/30 px-4 py-3">
      <div className="flex items-center gap-2 mb-1.5">
        <Globe2 className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          My A2A URL
        </span>
      </div>

      {!editing ? (
        <div className="flex items-center gap-2 flex-wrap">
          {url ? (
            <>
              <code className="text-sm font-mono flex-1 truncate min-w-0">{url}</code>
              <Button size="sm" variant="ghost" className="h-7 px-2 gap-1 text-xs shrink-0"
                onClick={() => {
                  void navigator.clipboard.writeText(url).then(() => {
                    setCopied(true);
                    setTimeout(() => setCopied(false), 2000);
                  });
                }}>
                <Copy className="h-3 w-3" />
                {copied ? "Copied!" : "Copy"}
              </Button>
              <Button size="sm" variant="ghost" className="h-7 px-2 text-xs shrink-0"
                onClick={() => { setUrlInput(url); setEditing(true); }}>
                Edit
              </Button>
            </>
          ) : (
            <div className="flex-1 space-y-2">
              {suggested ? (
                <>
                  <div className={cn(
                    "flex items-center gap-2 rounded-md border px-3 py-2",
                    isPrivateUrl(suggested)
                      ? "border-amber-500/40 bg-amber-500/5"
                      : "border-accent/30 bg-accent/5"
                  )}>
                    <div className="flex-1 min-w-0">
                      <p className="text-[11px] text-muted-foreground mb-0.5">
                        Detected IP of this instance:
                      </p>
                      <code className="text-sm font-mono">{suggested}</code>
                      {isPrivateUrl(suggested) && (
                        <p className="text-[11px] text-amber-700 dark:text-amber-400 mt-1">
                          ⚠ Local address — not reachable by external peers.
                          Enter the public IP or domain below.
                        </p>
                      )}
                    </div>
                    {!isPrivateUrl(suggested) && (
                      <Button size="sm" disabled={saving} className="h-7 px-3 text-xs shrink-0"
                        onClick={handleAcceptSuggested}>
                        {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Use this URL"}
                      </Button>
                    )}
                  </div>
                  <Button size="sm" variant="ghost" className="h-6 px-2 text-xs text-muted-foreground"
                    onClick={() => { setUrlInput(suggested); setEditing(true); }}>
                    Enter a different URL…
                  </Button>
                </>
              ) : (
                <Button size="sm" variant="outline" className="h-7 px-3 text-xs"
                  onClick={() => { setUrlInput(""); setEditing(true); }}>
                  Enter URL
                </Button>
              )}
            </div>
          )}
        </div>
      ) : (
        <form onSubmit={handleSave} className="flex gap-2 mt-1">
          <Input
            autoFocus
            placeholder="https://my-corvin.example.com"
            value={urlInput}
            onChange={e => setUrlInput(e.target.value)}
            className="h-8 text-sm flex-1"
            required
          />
          <Button type="submit" size="sm" disabled={saving} className="h-8 px-3 shrink-0">
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
          </Button>
          <Button type="button" size="sm" variant="ghost" className="h-8 px-3 shrink-0"
            onClick={() => setEditing(false)}>
            ✕
          </Button>
        </form>
      )}

      <p className="mt-2 text-[11px] text-muted-foreground">
        The A2A receiver runs on the same server as this console
        (<code className="rounded bg-muted px-1">/v1/a2a/receive</code>).
        Share this URL — the peer enters it when importing the token.
      </p>
    </div>
  );
}

// ── Permission selector used in pairing forms ──────────────────────

function PermissionSelector({
  value,
  onChange,
}: {
  value: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <div className="space-y-1.5">
      <Label>Permission level</Label>
      <div className="grid grid-cols-3 gap-2">
        <label className={cn(
          "flex cursor-pointer flex-col gap-1 rounded-md border p-2.5 transition-colors",
          !value ? "border-amber-500/50 bg-amber-500/5" : "border-border hover:border-border/80"
        )}>
          <input type="radio" checked={!value} onChange={() => onChange(false)} className="sr-only" />
          <span className="text-xs font-medium flex items-center gap-1">
            <Shield className="h-3.5 w-3.5 text-amber-500" /> Observer
          </span>
          <p className="text-[10px] text-muted-foreground">Read-only. No execution.</p>
        </label>
        <label className={cn(
          "flex cursor-pointer flex-col gap-1 rounded-md border p-2.5 transition-colors",
          "border-border hover:border-border/80 opacity-60",
        )} title="Restrict personas after pairing via Peers tab">
          <input type="radio" disabled className="sr-only" />
          <span className="text-xs font-medium flex items-center gap-1">
            <Zap className="h-3.5 w-3.5 text-violet-500" /> Restricted
          </span>
          <p className="text-[10px] text-muted-foreground">Configure in Peers tab.</p>
        </label>
        <label className={cn(
          "flex cursor-pointer flex-col gap-1 rounded-md border p-2.5 transition-colors",
          value ? "border-blue-500/50 bg-blue-500/5" : "border-border hover:border-border/80"
        )}>
          <input type="radio" checked={value} onChange={() => onChange(true)} className="sr-only" />
          <span className="text-xs font-medium flex items-center gap-1">
            <Zap className="h-3.5 w-3.5 text-blue-500" /> Full Executor
          </span>
          <p className="text-[10px] text-muted-foreground">All tasks, all personas.</p>
        </label>
      </div>
      <p className="text-[11px] text-muted-foreground">
        You can refine persona restrictions any time in the <strong>Peers</strong> tab.
      </p>
    </div>
  );
}

// ── Friendship connections list ────────────────────────────────────

function FriendshipConnectionsList() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const conns = useQuery({
    queryKey: ["a2a", "friendship-connections"],
    queryFn: ({ signal }) => listFriendshipConnections(signal),
    refetchInterval: 15_000,
  });

  const [setUrlKid, setSetUrlKid] = React.useState<string | null>(null);
  const [urlInput, setUrlInput] = React.useState("");
  const [urlSaving, setUrlSaving] = React.useState(false);
  const [deletePendingKid, setDeletePendingKid] = React.useState<string | null>(null);
  const [deleting, setDeleting] = React.useState(false);
  const [deleteError, setDeleteError] = React.useState("");

  async function handleSetUrl(kid: string) {
    setUrlSaving(true);
    try {
      await setFriendshipUrl(kid, urlInput.trim(), csrf);
      void qc.invalidateQueries({ queryKey: ["a2a", "friendship-connections"] });
      void qc.invalidateQueries({ queryKey: ["a2a", "endpoints"] });
      setSetUrlKid(null);
      setUrlInput("");
    } finally {
      setUrlSaving(false);
    }
  }

  async function handleRevoke(kid: string) {
    setDeleting(true);
    setDeleteError("");
    try {
      await revokeFriendshipToken(kid, csrf);
      void qc.invalidateQueries({ queryKey: ["a2a", "friendship-connections"] });
      void qc.invalidateQueries({ queryKey: ["a2a", "origins"] });
      void qc.invalidateQueries({ queryKey: ["a2a", "endpoints"] });
      setDeletePendingKid(null);
    } catch (err: unknown) {
      setDeleteError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  }

  if (conns.isLoading) {
    return (
      <Card>
        <CardContent className="p-4 space-y-2">
          {[1, 2].map(n => <Skeleton key={n} className="h-10 rounded" />)}
        </CardContent>
      </Card>
    );
  }

  const connections = conns.data?.connections ?? [];

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm">Active token connections</CardTitle>
          <Button variant="ghost" size="sm" className="h-7 gap-1 px-2 text-xs"
            onClick={() => void qc.invalidateQueries({ queryKey: ["a2a", "friendship-connections"] })}>
            <RefreshCw className="h-3 w-3" /> Refresh
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {connections.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-8 text-muted-foreground">
            <UserPlus className="h-6 w-6 opacity-20" />
            <p className="text-sm">No token connections yet.</p>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {connections.map((c: FriendshipConnection) => (
              <div key={c.kid} className="px-4 py-3 text-sm">
                <div className="flex items-center gap-3 flex-wrap">
                  <StateBadge state={c.state} />
                  {c.label && (
                    <span className="font-medium text-sm">{c.label}</span>
                  )}
                  <span className="font-mono text-[10px] text-muted-foreground truncate max-w-xs">
                    {c.kid}
                  </span>
                  {c.expires && (
                    <span className="text-[10px] text-muted-foreground shrink-0">
                      until {new Date(c.expires * 1000).toLocaleDateString()}
                    </span>
                  )}
                  <div className="ml-auto flex gap-1.5 shrink-0">
                    {c.state === "PENDING" && deletePendingKid !== c.kid && (
                      <Button size="sm" variant="outline" className="h-7 px-2.5 text-xs"
                        onClick={() => { setSetUrlKid(c.kid); setUrlInput(c.url ?? ""); }}>
                        Set URL
                      </Button>
                    )}
                    {deletePendingKid === c.kid ? (
                      <div className="flex items-center gap-1.5">
                        <AlertTriangle className="h-3.5 w-3.5 text-destructive shrink-0" />
                        <span className="text-xs text-destructive">Delete?</span>
                        <Button size="sm" variant="destructive" className="h-6 px-2 text-[10px]"
                          disabled={deleting}
                          onClick={() => handleRevoke(c.kid)}>
                          {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : "Delete"}
                        </Button>
                        <Button size="sm" variant="ghost" className="h-6 px-2 text-[10px]"
                          onClick={() => { setDeletePendingKid(null); setDeleteError(""); }}>
                          Cancel
                        </Button>
                      </div>
                    ) : (
                      <Button size="sm" variant="ghost"
                        className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                        onClick={() => { setDeletePendingKid(c.kid); setSetUrlKid(null); }}
                        title="Delete connection">
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </div>
                {deleteError && deletePendingKid === c.kid && (
                  <p className="mt-1 rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{deleteError}</p>
                )}
                {c.url && deletePendingKid !== c.kid && (
                  <p className="mt-1 font-mono text-[10px] text-muted-foreground">{c.url}</p>
                )}
                {setUrlKid === c.kid && (
                  <div className="mt-2 flex gap-2">
                    <Input
                      autoFocus
                      placeholder="https://max.example.com"
                      value={urlInput}
                      onChange={e => setUrlInput(e.target.value)}
                      className="h-8 text-xs flex-1"
                    />
                    <Button size="sm" disabled={urlSaving || !urlInput.trim()}
                      className="h-8 px-3 shrink-0 text-xs"
                      onClick={() => handleSetUrl(c.kid)}>
                      {urlSaving ? <Loader2 className="h-3 w-3 animate-spin" /> : "Save"}
                    </Button>
                    <Button size="sm" variant="ghost" className="h-8 px-3 text-xs shrink-0"
                      onClick={() => setSetUrlKid(null)}>
                      ✕
                    </Button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Pending invite-code list (compact, used inside Connect) ────────

function PendingInvitesList() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const invites = useQuery({
    queryKey: ["a2a", "invites"],
    queryFn: ({ signal }) => listA2AInvites(signal),
    refetchInterval: 30_000,
  });

  async function handleRevoke(ikey: string) {
    try {
      await revokeA2AInvite(ikey, csrf);
      void qc.invalidateQueries({ queryKey: ["a2a", "invites"] });
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : String(err));
    }
  }

  const pending = invites.data?.invites.filter((i) => i.status === "pending") ?? [];
  if (pending.length === 0) return null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm text-muted-foreground">Pending invite codes ({pending.length})</CardTitle>
      </CardHeader>
      <CardContent className="p-0">
        <div className="divide-y divide-border">
          {pending.map((inv: InviteListEntry) => (
            <div key={inv.ikey} className="flex items-center gap-3 px-4 py-2.5 text-sm hover:bg-muted/40">
              <span className="font-mono text-[11px] text-muted-foreground truncate max-w-[10rem]">
                {inv.ikey}
              </span>
              <span className="min-w-0 flex-1 truncate text-xs font-medium">{inv.oid}</span>
              {inv.lbl && (
                <span className="text-xs text-muted-foreground truncate max-w-[8rem]">{inv.lbl}</span>
              )}
              <span className="text-[10px] text-muted-foreground shrink-0">
                {inv.exp ? new Date(inv.exp * 1000).toLocaleTimeString() : "∞"}
              </span>
              <Button variant="ghost" size="sm" className="h-7 w-7 p-0 text-muted-foreground hover:text-destructive"
                onClick={() => handleRevoke(inv.ikey)} title="Revoke">
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Connect tab — unified (Token + Invite Code) ────────────────────

function TokenConnectSection() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const myUrl = useQuery({
    queryKey: ["a2a", "my-url"],
    queryFn: ({ signal }) => getMyA2AUrl(signal),
    staleTime: 60_000,
  });
  const storedMyUrl = myUrl.data?.url ?? "";

  // ── Generate form
  const [genUrl, setGenUrl] = React.useState("");
  const [genLabel, setGenLabel] = React.useState("");
  const [genTtl, setGenTtl] = React.useState("30d");
  const [genResult, setGenResult] = React.useState<FriendshipCreateResponse | null>(null);
  const [genLoading, setGenLoading] = React.useState(false);
  const [genError, setGenError] = React.useState("");
  const [genCopied, setGenCopied] = React.useState(false);
  const [genRemember, setGenRemember] = React.useState(false);

  React.useEffect(() => {
    if (storedMyUrl && !genUrl) setGenUrl(storedMyUrl);
  }, [storedMyUrl, genUrl]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setGenError(""); setGenResult(null); setGenLoading(true);
    const ttlMap: Record<string, number> = { "7d": 168, "30d": 720, "90d": 2160, "never": 0 };
    try {
      const res = await createFriendshipToken({
        url: genUrl.trim() || undefined,
        label: genLabel.trim() || undefined,
        ttl_hours: ttlMap[genTtl] ?? 720,
        remember_url: genRemember && !!genUrl.trim(),
      }, csrf);
      setGenResult(res);
      if (genRemember && genUrl.trim()) {
        void qc.invalidateQueries({ queryKey: ["a2a", "my-url"] });
      }
    } catch (err: unknown) {
      setGenError(err instanceof Error ? err.message : String(err));
    } finally { setGenLoading(false); }
  }

  // ── Import form
  const [impToken, setImpToken] = React.useState("");
  const [impUrl, setImpUrl] = React.useState("");
  const [impOverwrite, setImpOverwrite] = React.useState(false);
  const [impSpawnWorker, setImpSpawnWorker] = React.useState(false);
  const [impResult, setImpResult] = React.useState<FriendshipImportResponse | null>(null);
  const [impLoading, setImpLoading] = React.useState(false);
  const [impError, setImpError] = React.useState("");

  async function handleImport(e: React.FormEvent) {
    e.preventDefault();
    setImpError(""); setImpResult(null); setImpLoading(true);
    try {
      const res = await importFriendshipToken({
        token: impToken.trim(),
        peer_url: impUrl.trim() || undefined,
        overwrite: impOverwrite,
        spawn_worker: impSpawnWorker,
      }, csrf);
      setImpResult(res);
      void qc.invalidateQueries({ queryKey: ["a2a", "friendship-connections"] });
      void qc.invalidateQueries({ queryKey: ["a2a", "origins"] });
      void qc.invalidateQueries({ queryKey: ["a2a", "endpoints"] });
    } catch (err: unknown) {
      setImpError(err instanceof Error ? err.message : String(err));
    } finally { setImpLoading(false); }
  }

  return (
    <div className="space-y-6">
      <MyUrlBanner />

      <div className="rounded-md border border-accent/20 bg-accent/5 px-4 py-3 text-sm text-muted-foreground">
        <strong className="text-foreground">How it works:</strong>{" "}
        Generate a token and share it with the peer. Both sides import the same token.
        No server-to-server handshake needed — the connection becomes active once both URLs are known.
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ── Generate token ── */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Key className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">Generate token</CardTitle>
            </div>
            <CardDescription>Create a token and share it with the peer over a secure channel.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleCreate} className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="ft-gen-url">Your URL <span className="text-muted-foreground">(optional)</span></Label>
                <Input id="ft-gen-url" placeholder="https://host:8000"
                  value={genUrl} onChange={e => setGenUrl(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-gen-label">Label <span className="text-muted-foreground">(optional)</span></Label>
                <Input id="ft-gen-label" placeholder="e.g. For Max"
                  value={genLabel} onChange={e => setGenLabel(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ft-gen-ttl">Validity</Label>
                <select id="ft-gen-ttl" value={genTtl} onChange={e => setGenTtl(e.target.value)}
                  className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm">
                  <option value="7d">7 days</option>
                  <option value="30d">30 days</option>
                  <option value="90d">90 days</option>
                  <option value="never">No expiry</option>
                </select>
              </div>
              {genUrl.trim() && (
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="checkbox" checked={genRemember} onChange={e => setGenRemember(e.target.checked)} className="rounded" />
                  Save URL as "My URL"
                </label>
              )}
              {genError && (
                <p className="rounded bg-destructive/10 px-3 py-2 text-xs text-destructive">{genError}</p>
              )}
              <Button type="submit" disabled={genLoading} className="w-full">
                {genLoading ? <><Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />Generating…</> : "Generate token"}
              </Button>
            </form>

            {genResult && (
              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-muted-foreground">
                    Token
                    {genResult.expires && (
                      <span className="ml-2 text-amber-600 dark:text-amber-400">
                        · expires {new Date(genResult.expires * 1000).toLocaleDateString()}
                      </span>
                    )}
                  </p>
                  <Button size="sm" variant="ghost" className="h-6 gap-1 px-2 text-xs"
                    onClick={() => { void navigator.clipboard.writeText(genResult.token).then(() => { setGenCopied(true); setTimeout(() => setGenCopied(false), 2000); }); }}>
                    <Copy className="h-3 w-3" />
                    {genCopied ? "Copied!" : "Copy"}
                  </Button>
                </div>
                <Textarea readOnly value={genResult.token}
                  className="font-mono text-[10px] h-24 resize-none"
                  onClick={e => (e.target as HTMLTextAreaElement).select()} />
                <p className="font-mono text-[10px] text-muted-foreground">kid: {genResult.kid}</p>
                <div className="rounded bg-amber-500/10 border border-amber-500/20 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-400">
                  Share only over encrypted channels (Signal, age-encrypted file).
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ── Import token ── */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <UserPlus className="h-4 w-4 text-emerald-500" />
              <CardTitle className="text-base">Import token</CardTitle>
            </div>
            <CardDescription>Paste a token you received. Set the permission level for this peer.</CardDescription>
          </CardHeader>
          <CardContent>
            {impResult ? (
              <div className="flex flex-col items-center gap-3 py-4 text-center">
                <CheckCircle2 className="h-10 w-10 text-emerald-500" />
                <p className="text-sm font-medium">
                  {impResult.state === "ACTIVE" ? "Connected!" : "Imported (URL pending)"}
                </p>
                <div className={cn(
                  "w-full rounded-md border p-3 text-left text-xs space-y-1",
                  impResult.state === "ACTIVE"
                    ? "border-emerald-500/30 bg-emerald-500/5"
                    : "border-amber-500/30 bg-amber-500/5"
                )}>
                  <div className="flex gap-2">
                    <span className="text-muted-foreground w-20 shrink-0">kid</span>
                    <code className="font-mono text-[10px] break-all">{impResult.kid}</code>
                  </div>
                  <div className="flex gap-2">
                    <span className="text-muted-foreground w-20 shrink-0">Status</span>
                    <StateBadge state={impResult.state} />
                  </div>
                  {impResult.state === "PENDING" && (
                    <p className="text-amber-700 dark:text-amber-400 text-[11px] mt-2">
                      Enter the peer URL in the connections list below to activate.
                    </p>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">
                  Adjust permissions anytime in the <strong>Peers</strong> tab.
                </p>
                <Button variant="outline" size="sm" onClick={() => { setImpResult(null); setImpToken(""); setImpUrl(""); setImpSpawnWorker(false); }}>
                  Import another
                </Button>
              </div>
            ) : (
              <form onSubmit={handleImport} className="space-y-3">
                <div className="space-y-1">
                  <Label htmlFor="ft-imp-token">Token *</Label>
                  <Textarea id="ft-imp-token" placeholder="corvin-a2a:ft1:…"
                    value={impToken} onChange={e => setImpToken(e.target.value)}
                    className="font-mono text-[10px] h-24 resize-none" required />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="ft-imp-url">Peer URL <span className="text-muted-foreground">(optional)</span></Label>
                  <Input id="ft-imp-url" placeholder="https://host:8000"
                    value={impUrl} onChange={e => setImpUrl(e.target.value)} />
                </div>
                <PermissionSelector value={impSpawnWorker} onChange={setImpSpawnWorker} />
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="checkbox" checked={impOverwrite} onChange={e => setImpOverwrite(e.target.checked)} className="rounded" />
                  Overwrite existing connection
                </label>
                {impError && (
                  <p className="rounded bg-destructive/10 px-3 py-2 text-xs text-destructive">{impError}</p>
                )}
                <Button type="submit" disabled={impLoading} className="w-full">
                  {impLoading ? <><Loader2 className="h-3.5 w-3.5 mr-2 animate-spin" />Importing…</> : "Import token"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      </div>

      <FriendshipConnectionsList />
    </div>
  );
}

function InviteCodeConnectSection() {
  const { session } = useAuth();
  const csrf = session?.csrf_token ?? "";
  const qc = useQueryClient();

  const myInfo = useQuery({
    queryKey: ["a2a", "pair", "my-info"],
    queryFn: ({ signal }) => getA2APairMyInfo(signal),
  });

  // ── Generate form
  const [genLabel, setGenLabel] = React.useState("");
  const [genUrl, setGenUrl] = React.useState("");
  const [genConsoleUrl, setGenConsoleUrl] = React.useState("");
  const [genOriginId, setGenOriginId] = React.useState("");
  const [inviteCode, setInviteCode] = React.useState("");
  const [genExpiry, setGenExpiry] = React.useState<number | null>(null);
  const [genLoading, setGenLoading] = React.useState(false);
  const [genError, setGenError] = React.useState("");
  const [copied, setCopied] = React.useState(false);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setGenError(""); setInviteCode(""); setGenLoading(true);
    try {
      const res = await generateA2AInvite(
        { label: genLabel, url: genUrl, console_url: genConsoleUrl, peer_origin_id: genOriginId },
        csrf,
      );
      setInviteCode(res.invite_code);
      setGenExpiry(res.expires_at);
    } catch (err: unknown) {
      setGenError(err instanceof Error ? err.message : String(err));
    } finally { setGenLoading(false); }
  }

  // ── Redeem form
  const [redeemCode, setRedeemCode] = React.useState("");
  const [redeemUrl, setRedeemUrl] = React.useState("");
  const [redeemConsoleUrl, setRedeemConsoleUrl] = React.useState("");
  const [redeemOriginId, setRedeemOriginId] = React.useState("");
  const [redeemLabel, setRedeemLabel] = React.useState("");
  const [redeemSpawnWorker, setRedeemSpawnWorker] = React.useState(false);
  const [redeemLoading, setRedeemLoading] = React.useState(false);
  const [redeemResult, setRedeemResult] = React.useState<A2ARedeemResponse | null>(null);
  const [redeemError, setRedeemError] = React.useState("");

  async function handleRedeem(e: React.FormEvent) {
    e.preventDefault();
    setRedeemError(""); setRedeemResult(null); setRedeemLoading(true);
    try {
      const res = await redeemA2AInvite(
        {
          invite_code: redeemCode.trim(),
          our_url: redeemUrl,
          our_console_url: redeemConsoleUrl,
          our_label: redeemLabel,
          our_origin_id: redeemOriginId,
          spawn_worker: redeemSpawnWorker,
        },
        csrf,
      );
      setRedeemResult(res);
      void qc.invalidateQueries({ queryKey: ["a2a", "origins"] });
      void qc.invalidateQueries({ queryKey: ["a2a", "endpoints"] });
    } catch (err: unknown) {
      setRedeemError(err instanceof Error ? err.message : String(err));
    } finally { setRedeemLoading(false); }
  }

  return (
    <div className="space-y-6">
      {/* Instance identity */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Globe2 className="h-4 w-4 text-accent" />
            <CardTitle className="text-base">This instance</CardTitle>
          </div>
          <CardDescription>Your local identity — share the Instance ID with peers for verification.</CardDescription>
        </CardHeader>
        <CardContent>
          {myInfo.isLoading && <Skeleton className="h-8 w-80" />}
          {myInfo.data && (
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground w-24 shrink-0">Instance ID</span>
                <code className="rounded bg-muted px-2 py-0.5 text-xs font-mono">
                  {myInfo.data.instance_id || "—"}
                </code>
              </div>
              {myInfo.data.label && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground w-24 shrink-0">Label</span>
                  <span className="text-sm">{myInfo.data.label}</span>
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="rounded-md border border-border bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
        <strong className="text-foreground">How it works:</strong>{" "}
        Instance A generates an invite code (valid 60 min, single-use) and shares it out-of-band.
        Instance B redeems it — both sides are paired automatically over a server-to-server HMAC handshake.
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* ── Generate ── */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-accent" />
              <CardTitle className="text-base">1. Generate invite code</CardTitle>
            </div>
            <CardDescription>Fill in your connection details, then share the code with the peer.</CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleGenerate} className="space-y-3">
              <div className="space-y-1">
                <Label htmlFor="gen-label">Your label (optional)</Label>
                <Input id="gen-label" placeholder="e.g. Cloud Server A" value={genLabel} onChange={(e) => setGenLabel(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="gen-url">Your A2A receive URL *</Label>
                <Input id="gen-url" placeholder="https://host:8000/v1/a2a/receive" value={genUrl} onChange={(e) => setGenUrl(e.target.value)} required />
              </div>
              <div className="space-y-1">
                <Label htmlFor="gen-console-url">Your console base URL *</Label>
                <Input id="gen-console-url" placeholder="https://host:8000" value={genConsoleUrl} onChange={(e) => setGenConsoleUrl(e.target.value)} required />
              </div>
              <div className="space-y-1">
                <Label htmlFor="gen-origin-id">Origin ID (what the peer calls you) *</Label>
                <Input id="gen-origin-id" placeholder="e.g. cloud-server-a"
                  pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}"
                  value={genOriginId} onChange={(e) => setGenOriginId(e.target.value)} required />
                <p className="text-[11px] text-muted-foreground">Letters, digits, dots, hyphens, underscores.</p>
              </div>
              {genError && (
                <p className="rounded bg-destructive/10 px-3 py-2 text-xs text-destructive">{genError}</p>
              )}
              <Button type="submit" disabled={genLoading} className="w-full">
                {genLoading ? "Generating…" : "Generate invite code"}
              </Button>
            </form>

            {inviteCode && (
              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-xs font-medium text-muted-foreground">
                    Invite code
                    {genExpiry && (
                      <span className="ml-2 text-amber-600 dark:text-amber-400">
                        · expires {new Date(genExpiry * 1000).toLocaleTimeString()}
                      </span>
                    )}
                  </p>
                  <Button size="sm" variant="ghost" className="h-6 gap-1 px-2 text-xs"
                    onClick={() => { navigator.clipboard.writeText(inviteCode).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }); }}>
                    <Copy className="h-3 w-3" />
                    {copied ? "Copied!" : "Copy"}
                  </Button>
                </div>
                <Textarea readOnly value={inviteCode}
                  className="font-mono text-[10px] h-24 resize-none"
                  onClick={(e) => (e.target as HTMLTextAreaElement).select()} />
                <p className="text-[11px] text-muted-foreground">
                  Valid for 60 minutes, single-use.
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* ── Redeem ── */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Link2 className="h-4 w-4 text-emerald-500" />
              <CardTitle className="text-base">2. Redeem invite code</CardTitle>
            </div>
            <CardDescription>Paste the code from the peer and set the permission level for this connection.</CardDescription>
          </CardHeader>
          <CardContent>
            {redeemResult ? (
              <div className="flex flex-col items-center gap-3 py-6 text-center">
                <CheckCircle2 className="h-10 w-10 text-emerald-500" />
                <p className="text-sm font-medium">Connected!</p>
                <div className="w-full rounded-md border border-emerald-500/30 bg-emerald-500/5 p-3 text-left text-xs space-y-1">
                  <div className="flex gap-2">
                    <span className="text-muted-foreground w-24 shrink-0">Peer name</span>
                    <code className="font-mono">{redeemResult.paired_with}</code>
                  </div>
                  <div className="flex gap-2">
                    <span className="text-muted-foreground w-24 shrink-0">Peer label</span>
                    <span>{redeemResult.issuer_label}</span>
                  </div>
                  <div className="flex gap-2">
                    <span className="text-muted-foreground w-24 shrink-0">Our ID there</span>
                    <code className="font-mono">{redeemResult.our_origin_id}</code>
                  </div>
                  <div className="flex gap-2">
                    <span className="text-muted-foreground w-24 shrink-0">Direction</span>
                    <span className="text-emerald-600 dark:text-emerald-400">Bidirectional ↔</span>
                  </div>
                </div>
                <p className="text-xs text-muted-foreground">Adjust permissions anytime in the <strong>Peers</strong> tab.</p>
                <Button variant="outline" size="sm" onClick={() => setRedeemResult(null)}>Pair another agent</Button>
              </div>
            ) : (
              <form onSubmit={handleRedeem} className="space-y-3">
                <div className="space-y-1">
                  <Label htmlFor="redeem-code">Invite code (from peer) *</Label>
                  <Textarea id="redeem-code" placeholder="Paste the invite code here…"
                    value={redeemCode} onChange={(e) => setRedeemCode(e.target.value)}
                    className="font-mono text-[10px] h-20 resize-none" required />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="redeem-url">Your A2A receive URL *</Label>
                  <Input id="redeem-url" placeholder="https://host:8000/v1/a2a/receive" value={redeemUrl} onChange={(e) => setRedeemUrl(e.target.value)} required />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="redeem-console-url">Your console base URL *</Label>
                  <Input id="redeem-console-url" placeholder="https://host:8000" value={redeemConsoleUrl} onChange={(e) => setRedeemConsoleUrl(e.target.value)} required />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="redeem-origin-id">Your origin ID (how the peer identifies you) *</Label>
                  <Input id="redeem-origin-id" placeholder="e.g. laptop-b"
                    pattern="[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}"
                    value={redeemOriginId} onChange={(e) => setRedeemOriginId(e.target.value)} required />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="redeem-label">Your label (optional)</Label>
                  <Input id="redeem-label" placeholder="e.g. Local Laptop B" value={redeemLabel} onChange={(e) => setRedeemLabel(e.target.value)} />
                </div>
                <PermissionSelector value={redeemSpawnWorker} onChange={setRedeemSpawnWorker} />
                {redeemError && (
                  <p className="rounded bg-destructive/10 px-3 py-2 text-xs text-destructive">{redeemError}</p>
                )}
                <Button type="submit" disabled={redeemLoading} className="w-full">
                  {redeemLoading ? "Connecting…" : "Connect agent"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>
      </div>

      <PendingInvitesList />
    </div>
  );
}

function ConnectTab() {
  return (
    <div className="space-y-4">
      <Tabs defaultValue="token">
        <TabsList>
          <TabsTrigger value="token" className="gap-1.5">
            <Key className="h-3.5 w-3.5" /> Token <Badge variant="secondary" className="ml-1 text-[10px] px-1.5 py-0">Simple</Badge>
          </TabsTrigger>
          <TabsTrigger value="invite" className="gap-1.5">
            <Link2 className="h-3.5 w-3.5" /> Invite Code <Badge variant="outline" className="ml-1 text-[10px] px-1.5 py-0">Advanced</Badge>
          </TabsTrigger>
        </TabsList>

        <TabsContent value="token" className="mt-4">
          <TokenConnectSection />
        </TabsContent>

        <TabsContent value="invite" className="mt-4">
          <InviteCodeConnectSection />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ── layout helpers ─────────────────────────────────────────────────

function EmptyState({
  icon,
  title,
  hint,
}: {
  icon: React.ReactNode;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
      <div className="opacity-20">{icon}</div>
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-md text-xs">{hint}</p>
    </div>
  );
}

// ── page root ──────────────────────────────────────────────────────

export function AgentHubPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header>
        <div className="flex items-center gap-2">
          <Globe2 className="h-5 w-5 text-accent" />
          <h1 className="font-serif text-3xl font-light tracking-tight">Agent Hub</h1>
        </div>
        <p className="mt-1 text-sm text-muted-foreground">
          Connect this Corvin instance to other agents over HMAC-signed task envelopes.
          Manage permissions per peer, monitor live activity.
        </p>
      </header>

      <Tabs defaultValue="peers">
        <TabsList className="flex-wrap h-auto gap-1">
          <TabsTrigger value="peers">Peers</TabsTrigger>
          <TabsTrigger value="connect">Connect</TabsTrigger>
          <TabsTrigger value="feed">Live Feed</TabsTrigger>
        </TabsList>

        <TabsContent value="peers" className="mt-4">
          <PeersTab />
        </TabsContent>

        <TabsContent value="connect" className="mt-4">
          <ConnectTab />
        </TabsContent>

        <TabsContent value="feed" className="mt-4">
          <LiveFeedTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
